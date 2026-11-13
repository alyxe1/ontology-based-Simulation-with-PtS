"""Analysis of a completed run's signed ontology journal."""
from collections import Counter, defaultdict
from functools import lru_cache
from math import floor

from .common import EPS
from .graph import decode_record


ACTIVE = {"queued", "processing", "setup", "transfer"}
TRACKED = {"Workpiece", "AssetState", "FixtureReservation", "Inspection"}


def branch_changes(graph, task_id):
    """Read only this run's changes, even if its baseline contains an older run."""
    pages = []
    through = graph.sequence
    while through:
        after = max(0, through - 256)
        changes = []
        for entry in reversed(graph.records(after=after, through=through)):
            event = entry["payload"]
            if event["kind"] == "branch_created" and any(
                row["after"].get("branch") == task_id for row in event["changes"]
            ):
                pages.append(changes)
                return [event for page in reversed(pages) for event in reversed(page)]
            rows = [
                (decode_record(row["before"]), decode_record(row["after"]))
                for row in event["changes"] if row["after"]["type"] in TRACKED
            ]
            if rows:
                changes.append((event["simulation_time_s"], rows))
        pages.append(changes)
        through = after
    raise ValueError("运行档案缺少方案分支记录，无法重建统计过程")


def in_shift_seconds(start, end, hours):
    def cumulative(at):
        day = floor(at / 86400)
        shift = hours * 3600
        return day * shift + min(at % 86400, shift)
    return cumulative(end) - cumulative(start)


def checkpoint_state(events, final_rows):
    initial = {row["id"]: row for row in final_rows}
    for _, changes in reversed(events):
        for before, after in reversed(changes):
            if before:
                initial[before["id"]] = before
            else:
                initial.pop(after["id"], None)
    return initial


def queue_key(row):
    if row.get("status") == "queued":
        return row["assigned"], row["op_index"], row.get("attempt", 0)
    return None


def record_point(points, time_s, *values):
    point = [time_s, *values]
    if points and abs(points[-1][0] - time_s) <= EPS:
        points[-1] = point
    elif not points or points[-1][1:] != point[1:]:
        points.append(point)


@lru_cache(maxsize=12)
def analyze_result(graph, task_id):
    """Derive window metrics without replaying the native model or mutating the run."""
    policy = graph.get("policy")
    start, end = policy["window_start_s"], graph.get("clock")["time_s"]
    window = end - start
    if window <= EPS:
        raise ValueError("运行统计窗口为空")
    equipment = {row["id"]: row for row in graph.entities("Equipment")}
    pools = {row["id"]: row for row in graph.entities("FixturePool")}
    jobs = {row["id"]: row for row in graph.entities("Workpiece")}
    operations = {row["id"]: row for row in graph.entities("OperationPlan")}
    products = {row["id"]: row["name"] for row in graph.entities("Product")}
    inspections = graph.entities("Inspection")
    events = branch_changes(graph, task_id)
    initial = checkpoint_state(events, [
        *jobs.values(), *graph.entities("AssetState"), *graph.entities("FixtureReservation"), *inspections,
    ])

    queues, reservations = Counter(), Counter()
    queue_open, maintenance_open = {}, {}
    queue_spells, maintenance, window_inspections = [], [], []
    total_in, total_out, total_scrap = 0, 0, 0
    wip = sum(row["type"] == "Workpiece" and row["status"] in ACTIVE for row in initial.values())
    for row in initial.values():
        if row["type"] == "Workpiece" and queue_key(row):
            queue_open[row["id"]] = (start, queue_key(row))
            queues[row["assigned"]] += 1
        elif row["type"] == "AssetState" and row["status"] == "maintenance":
            maintenance_open[row["resource"]] = start
        elif row["type"] == "FixtureReservation" and row["status"] == "reserved":
            reservations[row["fixture"]] += 1

    flow_curve, queue_curves, fixture_curves = [], defaultdict(list), defaultdict(list)

    def snapshot(at):
        record_point(flow_curve, at, total_in, total_out, total_scrap, wip, sum(queues.values()))
        for rid in equipment:
            record_point(queue_curves[rid], at, queues[rid])
        for fid in pools:
            record_point(fixture_curves[fid], at, reservations[fid])

    def close_queue(jid, at, open_at_end=False):
        entered, (resource, op_index, attempt) = queue_open.pop(jid)
        if at > entered + EPS:
            queue_spells.append(dict(job=jid, resource=resource, op_index=op_index, attempt=attempt,
                                     start_s=entered, end_s=at, open_at_end=open_at_end))
        queues[resource] -= 1

    def close_maintenance(resource, at):
        entered = maintenance_open.pop(resource)
        if at > entered + EPS:
            maintenance.append(dict(resource=resource, kind="maintenance", start_s=entered, end_s=at,
                                    id=resource + "/state", job=""))

    snapshot(start)
    last_time = start
    completion_times = []
    for time_s, changes in events:
        at = min(end, max(start, time_s))
        if at > last_time + EPS:
            snapshot(last_time)
        last_time = at
        for before, after in changes:
            kind, identity = after["type"], after["id"]
            if kind == "Workpiece":
                old_key, new_key = queue_key(before), queue_key(after)
                if old_key != new_key:
                    if identity in queue_open:
                        close_queue(identity, at)
                    if new_key:
                        queue_open[identity] = (at, new_key)
                        queues[new_key[0]] += 1
                wip += int(after["status"] in ACTIVE) - int(before.get("status") in ACTIVE)
                if before.get("released_at", -1) < 0 <= after.get("released_at", -1):
                    total_in += 1
                if before.get("status") != after["status"]:
                    if after["status"] == "done":
                        completion_times.append(at)
                        total_out += 1
                    elif after["status"] == "scrapped":
                        total_scrap += 1
            elif kind == "AssetState":
                rid = after["resource"]
                if after["status"] == "maintenance":
                    maintenance_open.setdefault(rid, at)
                elif rid in maintenance_open:
                    close_maintenance(rid, at)
            elif kind == "FixtureReservation":
                if before.get("status") == "reserved":
                    reservations[before["fixture"]] -= 1
                if after["status"] == "reserved":
                    reservations[after["fixture"]] += 1
            elif not before:
                window_inspections.append(after["id"])
    snapshot(last_time)
    # Keep the terminal observation in every chart, including an unchanged interval.
    for points in [flow_curve, *queue_curves.values(), *fixture_curves.values()]:
        if points[-1][0] < end:
            points.append([end, *points[-1][1:]])
    for jid in list(queue_open):
        close_queue(jid, end, True)
    for rid in list(maintenance_open):
        close_maintenance(rid, end)

    # The journal reconstructs the state even when a job's last queue entry or
    # maintenance was already active at the checkpoint.
    segments = []
    for row in graph.entities("Execution"):
        first = max(start, row["start_s"])
        last = min(end, row.get("actual_end_s", row["end_s"]))
        if last <= first + EPS:
            continue
        segments.append(dict(id=row["id"], job=row["job"], resource=row["resource"], kind=row["kind"],
                             operation=row["operation"], operation_name=operations.get(row["operation"], {}).get("name", row["operation"]),
                             start_s=first, end_s=last, actual_start_s=row["start_s"],
                             completed=row["status"] == "completed" and row.get("actual_end_s", row["end_s"]) <= end + EPS))

    def area(points, column):
        return sum(left[column] * (right[0] - left[0]) for left, right in zip(points, points[1:]))

    stats = []
    for rid, resource in equipment.items():
        intervals = [row for row in segments + maintenance if row["resource"] == rid]
        duration = Counter()
        shift = policy.get("shift_hours") or resource["shift_hours"]
        occupied_off = 0.
        for row in intervals:
            first, last = row["start_s"], row["end_s"]
            duration[row["kind"]] += last - first
            occupied_off += last - first - in_shift_seconds(first, last, shift)
        off_shift = max(0., window - in_shift_seconds(start, end, shift) - occupied_off)
        idle = max(0., window - sum(duration.values()) - off_shift)
        curve = queue_curves[rid]
        stats.append(dict(id=rid, name=resource["name"], processing_s=duration["processing"], setup_s=duration["setup"],
                          maintenance_s=duration["maintenance"], off_shift_s=off_shift, idle_s=idle, shift_hours=shift,
                          utilization=duration["processing"]/window, queue_area_s=area(curve, 1),
                          average_queue=area(curve, 1)/window, peak_queue=max(p[1] for p in curve), end_queue=curve[-1][1],
                          operations=sum(r["kind"] == "processing" and r["completed"] for r in intervals),
                          maintenance_periods=sum(r["kind"] == "maintenance" for r in intervals), queue_curve=curve))

    job_times = defaultdict(float)
    for row in queue_spells:
        job_times[row["job"]] += row["end_s"] - row["start_s"]
    executions = {row["id"]: row for row in graph.entities("Execution")}
    inspected = set(window_inspections)
    quality = [
        dict(**row, attempt=executions.get(row["execution"], {}).get("attempt"), product=jobs[row["job"]]["product"])
        for row in inspections if row["id"] in inspected
    ]
    fixtures = [
        dict(id=fid, name=pool["name"], quantity=pool["quantity"], average=area(fixture_curves[fid], 1)/window,
             peak=max(point[1] for point in fixture_curves[fid]), curve=fixture_curves[fid])
        for fid, pool in pools.items()
    ]
    return dict(start_s=start, end_s=end, graph_head=graph.head, curve=flow_curve, completion_times=completion_times,
                average_wip=area(flow_curve, 4)/window, peak_wip=max(point[4] for point in flow_curve),
                resources=stats, segments=sorted(segments + maintenance, key=lambda row: (row["start_s"], row["id"])),
                queue_spells=queue_spells, job_wait_s=dict(job_times), fixtures=fixtures,
                inspections=quality, products=products)

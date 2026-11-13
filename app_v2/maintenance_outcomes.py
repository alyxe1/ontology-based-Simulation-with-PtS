"""Follow the exact cohort recorded at a maintenance baseline into a finished run."""
from collections import Counter

from .common import EPS
from .runtime import OntologyRuntime


def maintenance_outcomes(baseline, graph, metadata):
    incident_id = metadata.get("incident_id")
    incident = baseline.get(incident_id) if incident_id else None
    if not incident or incident["type"] != "AssetState" or incident["status"] != "maintenance":
        return None

    start = baseline.get("clock")["time_s"]
    end = graph.get("clock")["time_s"]
    resource = incident["resource"]
    affected = [baseline.get(identity) for identity in metadata.get("incident_jobs", [])]
    if any(row is None or row["type"] != "Workpiece" for row in affected):
        raise ValueError("基线影响清单引用了缺失的工件")

    cohort = {row["id"]: row for row in affected}
    baseline_runtime = OntologyRuntime(baseline)
    executions = {}
    for step in graph.entities("Execution"):
        original = cohort.get(step["job"])
        if not original or step["kind"] != "processing" or step["attempt"] != original["attempt"]:
            continue
        operation = graph.get(step["operation"])
        if operation["index"] != original["op_index"] or step["start_s"] > end + EPS:
            continue
        # Ignore a stage already completed before the frozen start.
        if step.get("actual_end_s", step["end_s"]) < start - EPS:
            continue
        prior = executions.get(step["job"])
        if prior is None or (step["start_s"], step["id"]) < (prior["start_s"], prior["id"]):
            executions[step["job"]] = step

    jobs = []
    for original in affected:
        identity = original["id"]
        final = graph.get(identity)
        if not final:
            raise ValueError("方案记录缺少基线工件：" + identity)
        step = executions.get(identity)
        operation = baseline_runtime.operation(original)
        operation_end = step.get("actual_end_s") if step else None
        jobs.append(dict(
            id=identity, order=original["order"], batch=original["batch"], operation=operation["name"],
            initial_status=original["status"], initial_resource=original["assigned"],
            destination=step["resource"] if step else None, execution=step["id"] if step else None,
            started_s=step["start_s"] if step else None,
            operation_completed_s=operation_end if operation_end is not None and operation_end <= end + EPS else None,
            final_status=final["status"], final_resource=final["assigned"],
            waiting_original_operation=final["status"] == "queued" and final["op_index"] == original["op_index"]
            and final["attempt"] == original["attempt"],
            completed_s=final["completed_at"] if final["status"] == "done" else None,
        ))

    counts = Counter(row["order"] for row in jobs)
    orders = []
    for order in OntologyRuntime(graph).summary()["orders"]:
        if order["id"] not in counts:
            continue
        orders.append({**order, "affected": counts[order["id"]],
                       "affected_completed": sum(j["order"] == order["id"] and j["final_status"] == "done" for j in jobs)})
    return dict(
        resource=resource, incident_id=incident_id, start_s=start, end_s=end, maintenance_until=incident["maintenance_until"],
        batches=len({row["batch"] for row in jobs}), orders=orders, jobs=jobs,
    )

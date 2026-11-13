"""Read-only inspection and validation of the ontology-to-native boundary."""
from collections import Counter
from functools import lru_cache
from hashlib import sha256

from pyoxigraph import RdfFormat
from pyshacl import validate
from rdflib import Graph, Namespace, RDF, URIRef

from .common import BASE, EPS, NS, PROGID
from .graph import STATE
from .invariants import validate_boundary
from .native import EC, SYS
from .specification import qualification

SH = Namespace("http://www.w3.org/ns/shacl#")
SHAPE_FILES = ("shapes.ttl", "simulation-shapes.ttl")
STATION_EVENTS = {"process_finished", "setup_finished"}


def shapes():
    graph = Graph()
    for name in SHAPE_FILES:
        graph.parse(BASE / "ontology/v2" / name, format="turtle")
    return graph


def rule_hashes():
    return {name: sha256((BASE / "ontology/v2" / name).read_bytes()).hexdigest() for name in SHAPE_FILES}


def shape_summary(graph, concept):
    rows = []
    targets = set(graph.subjects(SH.targetClass, concept))
    targets.update(graph.subjects(SH["class"], concept))
    if str(concept) in (NS+"Workpiece", NS+"Clock"):
        targets.add(URIRef(NS+"SimulationSnapshotShape"))
    if str(concept)==NS+"Clock":targets.add(URIRef(NS+"SimulationClockShape"))
    if str(concept)==NS+"ProductionPlan":targets.add(URIRef(NS+"SimulationPlanShape"))
    for shape in sorted(targets, key=str):
        for constraint in (*graph.objects(shape, SH.property), *graph.objects(shape, SH.sparql)):
            path = graph.value(constraint, SH.path)
            terms = ("minCount", "maxCount", "class", "datatype", "minInclusive", "maxInclusive", "minExclusive", "lessThanOrEquals", "in")
            rules = {term: graph.value(constraint, SH[term]) for term in terms}
            rows.append(dict(
                shape=str(shape).removeprefix(NS),
                path=str(path).removeprefix(NS) if path else "跨对象规则",
                message=str(graph.value(constraint, SH.message) or ""),
                rules={term: ([str(v) for v in graph.items(value)] if term == "in" else str(value).removeprefix(NS))
                       for term, value in rules.items() if value is not None},
            ))
    return rows


def validate_abox(graph):
    with graph.lock:
        sequence = graph.sequence
        snapshot = graph.store.dump(format=RdfFormat.TURTLE, from_graph=STATE)
    data = Graph().parse(data=snapshot, format="turtle")
    # The state graph explicitly types its records. Validate assertions without
    # inferring a missing business type from a relationship's domain or range.
    conforms, report, message = validate(data, shacl_graph=shapes(), inference="none")
    issues = []
    for result in report.subjects(RDF.type, SH.ValidationResult):
        issue={key: str(report.value(result, prop) or "").removeprefix(NS)
                       for key, prop in (
                           ("object", SH.focusNode), ("path", SH.resultPath),
                           ("message", SH.resultMessage), ("constraint", SH.sourceConstraintComponent),
                       )}
        value=report.value(result, SH.value)
        if issue["object"]=="clock" and isinstance(value, URIRef) and str(value).startswith(NS):
            issue["object"]=str(value).removeprefix(NS)
        issues.append(issue)
    return dict(conforms=bool(conforms), sequence=sequence, issues=issues, report=message,
                shape_files=list(SHAPE_FILES), shape_hashes=rule_hashes(),
                checks=["工艺后继及首选设备资格", "工件位置及执行一致性", "时钟、工时和设备参数", "物料与夹具字段"])


@lru_cache(maxsize=8)
def checkpoint_validation(graph, sequence):
    # Each comparison uses a sealed checkpoint graph, never the moving live graph.
    report = validate_abox(graph)
    if report["sequence"] != sequence:
        raise ValueError("校验过程中基线版本发生变化")
    return report


def require_simulation_input(runtime):
    report = checkpoint_validation(runtime.graph, runtime.graph.sequence)
    if not report["conforms"]:
        detail = "；".join(f"{i['object']} {i['path']} {i['message']}" for i in report["issues"][:5])
        raise ValueError("基线未通过 SHACL，无法提交原生仿真。"+detail)
    runtime.assert_restorable()
    validate_boundary(runtime.graph)
    return report


def build_mapping(runtime, checkpoint, plan, report):
    graph = runtime.graph
    origin, horizon = runtime.time, runtime.config["horizon_s"]
    jobs = sorted(runtime.jobs(), key=lambda job: (job["queue_seq"], job["id"]))
    assets = {state["resource"]: state for state in graph.entities("AssetState")}
    counts = Counter(job["assigned"] for job in jobs if job["status"] == "queued")
    resources = []
    for index, resource in enumerate(graph.entities("Equipment")):
        rid, frame = resource["id"], f"{SYS}.R{index}"
        state = assets[rid]
        resources.append(dict(id=rid, name=resource["name"], state_id=state["id"],
                              status=state["status"], occupied_by=state["busy"], queue_count=counts[rid],
                              speed=resource["speed"], setup_s=resource["setup_s"], shift_hours=resource["shift_hours"],
                              tool_count=state["tool_count"], maintenance_until=state["maintenance_until"],
                              frame=frame, label_field=frame+".Label", buffer=frame+".Waiting", station=frame+".Machine"))
    paths = {row["id"]: row for row in resources}
    operations = []
    for operation in sorted(graph.entities("OperationPlan"), key=lambda op:(op["product"], op["index"])):
        candidates = [dict(resource=row["resource"], reason=row["reason"], eligible=row["eligible"])
                      for row in qualification(graph, operation["id"])]
        operations.append(dict(id=operation["id"], name=operation["name"], product=operation["product"],
                               index=operation["index"], next_operation=operation.get("next_operation"),
                               operation=operation["operation"], material=operation["material"],
                               tolerance_um=operation["tolerance_um"], fixture=operation["fixture"],
                               preferred=operation["preferred"], duration_s=operation["duration_s"],
                               candidates=candidates))
    workpieces = []
    for job in jobs:
        status, device = job["status"], paths.get(job["assigned"])
        operation = runtime.operation(job) if status not in ("done", "scrapped") else None
        native_path = None
        execution = None
        if status == "queued":
            native_path = device["buffer"]
        elif status == "transfer":
            native_path = SYS+".Transit"
        elif status in ("processing", "setup"):
            native_path = device["station"]
            execution = graph.get(job["execution"])
        workpieces.append(dict(
            id=job["id"], status=status, order=job["order"], batch=job["batch"], product=job["product"],
            queue_seq=job["queue_seq"], op_index=job["op_index"], attempt=job["attempt"],
            operation=operation["id"] if operation else None, operation_name=operation["name"] if operation else None,
            duration_s=operation["duration_s"] if operation else None,
            location=job["location"], assigned=job["assigned"], native_path=native_path,
            native_field=native_path+".ProcTime" if execution else None,
            execution=execution["id"] if execution else None, end_s=execution["end_s"] if execution else None,
            time_basis=execution.get("time_basis") if execution else None,
            remaining_s=max(EPS, execution["end_s"]-origin) if execution else None,
        ))
    events = []
    for event in sorted(runtime.pending_events(), key=lambda e: (e["due_s"], e["priority"], e["id"])):
        station = event["event_kind"] in STATION_EVENTS
        delay = round(event["due_s"]-origin, 9)
        device = paths.get(event["resource"])
        target = ((device["station"]+".cont.Finished" if device else None) if station else
                  (f"{SYS}.Wake.executeIn({delay:.9f})" if delay > EPS else None))
        events.append(dict(id=event["id"], kind=event["event_kind"], job=event["job"], resource=event["resource"],
                           due_s=event["due_s"], delay_s=delay, priority=event["priority"], target=target,
                           mode="station" if station else "wake" if target else "boundary"))
    fields = [
        ("原生初始化", "Equipment.name", "R{n}.Label", "设备编号排序后创建资源 Frame、Waiting 缓冲区和 Machine 工位。"),
        ("原生初始化", "Workpiece.status / assigned / queue_seq / RDF 标识", "Part.Name / Waiting / Machine / Transit",
         "按队列序号和工件标识装载。未投产、完成、报废和待料工件没有初始原生对象。"),
        ("原生初始化", "Execution.end_s − Clock.time_s", "Machine.ProcTime",
         "换型或加工中的工件装载剩余秒数，再移入工位；保留该执行的时间依据。"),
        ("原生开工", "OperationPlan.duration_s × Equipment.speed × (0.92 + 0.16 × 配对样本)", "Machine.ProcTime",
         "控制器按工件、工序及执行次数取样，选定设备后计算时长，四舍五入至 6 位小数。"),
        ("原生换型", "Equipment.setup_s", "Machine.ProcTime", "产品族变化时写入换型时长；换型完成后才进入加工。"),
        ("原生时钟", "ProductionPlan.horizon_s / ScheduledEvent.due_s / Clock.time_s",
         "EventController.End / Wake.executeIn(Δt)", "End 是安全上限；非工位事件用 Wake 唤醒，同刻合并；到达当前边界的事件由控制器处理。"),
        ("控制器", "Policy / WorkOrder.due_s / Workpiece.queue_seq", "派工与设备改派", "原生初始化完成后应用方案；交期、队列顺序和设备资格决定后续移动及开工命令。"),
        ("控制器", "Capability / OperationPlan.material, tolerance_um, fixture / FixtureReservation", "资格与夹具门槛",
         "使用同一条设备资格匹配工序、材料和精度。共享夹具不足时工件继续等待。"),
        ("控制器", "AssetState.maintenance_until / Equipment.shift_hours / Policy.shift_hours", "开工许可",
         "维护和班次由控制器限制开工；剩余维护时间通过事件唤醒，原生工位沿用同一模型。"),
        ("原生回读", "Clock / Workpiece.location / Workpiece.status", "SimTime / cont.Name / cont.Finished / MU(i).Name / statNumIn",
         "读回原生时钟、开工工件、完工、位置及流入数；业务时间为起点加原生本地时钟。"),
    ]
    return dict(version="1.0", stage="prepared", checkpoint_id=checkpoint["id"], graph_sequence=report["sequence"],
                graph_head=graph.head, origin_s=origin, horizon_s=horizon, source_cursor=checkpoint.get("source_cursor"),
                shacl=dict(conforms=True, shape_files=report["shape_files"], shape_hashes=report["shape_hashes"]), engine=PROGID,
                native_clock=dict(path=EC+".SimTime", initial_s=0, end_field=EC+".End", end_s=max(1, horizon-origin)+1,
                                  speed=100, real_time=False),
                seeds=[5202+i for i in range(plan["repetitions"])],
                resources=resources, operations=operations, workpieces=workpieces, events=events,
                fields=[dict(stage=stage, source=source, target=target, rule=rule) for stage, source, target, rule in fields])

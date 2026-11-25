"""Seal what-if evidence and append decisions, field measurements and reviews."""
from datetime import datetime, timedelta
from hashlib import sha256
import io
import json
from pathlib import Path
from tempfile import SpooledTemporaryFile
from typing import Annotated
from zipfile import ZIP_DEFLATED, ZipFile

from pydantic import AwareDatetime, Field, StringConstraints
from rdflib import Graph, Literal, Namespace, RDF, RDFS, URIRef
from rdflib.namespace import OWL, XSD

from .common import BASE, NS, atomic_write, canonical, digest, now, uid
from .catalog_scope import record_visible, reset_sequence
from .evidence import check_signature
from .graph import AUDIT, STATE, OntologyStore, Ref, decode_record
from .result_analysis import analyze_result
from .runtime import OntologyRuntime
from .specification import Strict
from .views import impact

PROV = Namespace("http://www.w3.org/ns/prov#")
OS = Namespace(NS)
TBOX = BASE / "ontology/v2"
TBOX_FILES = ("ontology.ttl", "core.ttl", "evidence.ttl", "evidence-shapes.ttl", "catalog-v001.xml", "prov-o.ttl",
              "simulation.ttl", "simulation-shapes.ttl", "shapes.ttl")
TEXT = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class CaseCreate(Strict):
    checkpoint_id: str
    comparison_id: str
    title: TEXT
    objective: TEXT
    previous_improvement: str | None = None


class Adoption(Strict):
    run_id: str
    rationale: TEXT


class FieldExecution(Strict):
    execution_ref: TEXT
    executor: TEXT
    start_at: AwareDatetime
    end_at: AwareDatetime
    description: TEXT


class FieldOrder(Strict):
    order_id: str
    completed: int = Field(ge=0)
    completed_at: AwareDatetime | None = None


class FieldOutcome(Strict):
    source: TEXT
    source_record: TEXT | None = None
    same_scope: bool
    orders: list[FieldOrder] = Field(min_length=1, max_length=1000)


class Improvement(Strict):
    evaluation_id: str
    finding: TEXT
    change: TEXT
    validation: TEXT


def checksum(content):
    return sha256(content).hexdigest()


def create_case(w, body, actor):
    checkpoint = w.context_record(body.checkpoint_id, "Checkpoint")
    previous = None
    if body.previous_improvement:
        previous = w.catalog.get(body.previous_improvement)
        if not previous or previous["type"] != "ModelImprovement":
            raise ValueError("上一轮模型改进记录不存在")
        if case_record(w, previous["case"])["comparison_id"] == body.comparison_id:
            raise ValueError("下一轮请关联一个新的比较批次")
    tasks = []
    for task in w.tasks():
        if task["kind"] != "scenario" or task["workspace"] != checkpoint["workspace"]:
            continue
        payload = json.loads(task["payload"])
        if payload.get("checkpoint_id") == checkpoint["id"] and payload.get("comparison_id") == body.comparison_id:
            tasks.append((task, payload))
    if not tasks or any(task["status"] not in {"completed", "failed", "cancelled", "interrupted", "reconciliation_required"} for task, _ in tasks):
        raise ValueError("请选一个已经结束的比较批次")
    if not any(task["status"] == "completed" for task, _ in tasks):
        raise ValueError("该批次没有可封存的成功运行")

    case_id = uid("case")
    folder = w.root / "evidence-cases" / case_id
    folder.mkdir(parents=True)
    files = {}

    def save(name, data):
        files[name] = checksum(data)
        atomic_write(folder / name, data)

    for name in TBOX_FILES:
        save(f"tbox/{name}", (TBOX / name).read_bytes())
    base = OntologyStore.restore(checkpoint["path"])
    if base.head != checkpoint["graph_head"]:
        raise ValueError("基线与登记摘要不一致")
    save("baseline/runtime.trig", Path(checkpoint["path"]).read_bytes())
    baseline = {k: v for k, v in checkpoint.items() if k != "path"}
    baseline["configuration"] = base.get("configuration")
    baseline["ontology_contract"] = base.get("ontology_contract")
    if baseline.get("incident_id"):
        incident = next((row for row in impact(OntologyRuntime(base)) if row["id"] == baseline["incident_id"]), None)
        if incident:
            baseline["impact"] = incident
    save("baseline/metadata.json", canonical(baseline).encode())
    items = []
    for task, payload in sorted(tasks, key=lambda pair: (pair[0].get("queue_position", 0), pair[0]["created_at"])):
        prefix = task["id"]
        record = {k: v for k, v in task.items() if k not in ("output", "payload", "native")}
        inputs = {k: v for k, v in payload.items() if k != "checkpoint"}
        item = {"id": prefix, "status": task["status"], "name": inputs["patch"]["name"],
                "task": {**record, "payload": canonical(inputs)}, "inputs": inputs}
        if task["status"] == "completed":
            out = Path(task["output"])
            graph = OntologyStore.restore(out / "runtime.trig")
            result = graph.get("result/" + prefix)
            basis = graph.get("analysis_basis")
            if not result or not basis or basis["checkpoint"] != checkpoint["id"]:
                raise ValueError("运行档案缺少与该基线匹配的已签名结果")
            native = json.loads(result["native_evidence"])
            if not native.get("verified"):
                raise ValueError("运行尚未通过原生核对")
            summary = OntologyRuntime(graph).summary()
            report = {"task": item["task"], "summary": summary, "native": native}
            save(f"{prefix}/runtime.trig", (out / "runtime.trig").read_bytes())
            save(f"{prefix}/result.json", canonical(report).encode())
            save(f"{prefix}/analysis.json", canonical(analyze_result(graph, prefix)).encode())
            if (out / "final.spp").exists():
                save(f"{prefix}/native.spp", (out / "final.spp").read_bytes())
            item.update(graph_head=graph.head, kpi=summary["kpi"], engine=native.get("engine"))
        elif (Path(task["output"]) / "interrupted.trig").exists():
            interrupted = Path(task["output"]) / "interrupted.trig"
            partial = OntologyStore.restore(interrupted)
            save(f"{prefix}/runtime.trig", interrupted.read_bytes())
            item.update(graph_head=partial.head)
        items.append(item)
    identities = {checkpoint["id"], *(task["id"] for task, _ in tasks)}
    journal = []
    for event in w.catalog.records():
        p = event["payload"]
        if identities.intersection(p["used"]) or any(change["after"]["id"] in identities for change in p["changes"]):
            journal.append(event)
    save("task-history.json", canonical(journal).encode())
    row = dict(id=case_id, type="EvidenceCase", checkpoint=Ref(checkpoint["id"]), workspace=checkpoint["workspace"],
               comparison_id=body.comparison_id, name=body.title, objective=body.objective, created_at=now(),
               baseline=canonical(baseline), runs=canonical(items), files=canonical(files))
    if previous:
        row["previous_improvement"] = Ref(previous["id"])
    used = [checkpoint["id"], *(task["id"] for task, _ in tasks)]
    if previous:
        used.append(previous["id"])
    append(w, case_id, "case_sealed", actor, [row], used, body.objective)
    return case_id


def case_record(w, case_id):
    row = w.catalog.get(case_id)
    if not row or row["type"] != "EvidenceCase" or not record_visible(w.catalog, case_id, reset_sequence(w.catalog)):
        raise ValueError("证据档案不存在")
    return row


def list_cases(w):
    with w.catalog.lock:
        boundary = reset_sequence(w.catalog)
        rows = [
            {key: row[key] for key in ("id", "name", "created_at", "objective")}
            for row in w.catalog.entities("EvidenceCase")
            if record_visible(w.catalog, row["id"], boundary)
        ]
    return sorted(rows, key=lambda row: row["created_at"], reverse=True)


def case_events(w, case_id):
    with w.catalog.lock:
        case_record(w, case_id)
        return [r for r in w.catalog.records(after=max(0, reset_sequence(w.catalog)-1))
                if r["payload"]["details"].get("case_id") == case_id]


def append(w, case_id, kind, actor, rows, used, reason):
    with w.catalog.lock:
        prior = w.catalog.get(case_id + "/head")
        if any(w.catalog.get(row["id"]) for row in rows):
            raise ValueError("此证据已登记，请使用新的记录")
        head = dict(id=case_id + "/head", type="CaseHead", sequence=w.catalog.sequence + 1)
        return w.audit(kind, actor, used=used, changes=[*rows, head], reason=reason,
                       details={"case_id": case_id, "previous_case_sequence": prior["sequence"] if prior else 0})


def case_detail(w, case_id, events=None):
    row = case_record(w, case_id)
    events = case_events(w, case_id) if events is None else events
    previous = row.get("previous_improvement")
    if previous and not record_visible(w.catalog, previous, reset_sequence(w.catalog)):
        previous = None
    entries = []
    for record in events:
        p = record["payload"]
        for change in p["changes"]:
            entry = change["after"]
            if entry["type"] not in ("EvidenceCase", "CaseHead"):
                entries.append({**decode_record(entry), "actor": p["actor"], "sequence": p["sequence"], "occurred_at": p["occurred_at"]})
    return {"id": case_id, "name": row["name"], "objective": row["objective"], "created_at": row["created_at"],
            "baseline": json.loads(row["baseline"]), "runs": json.loads(row["runs"]), "entries": entries,
            "previous_improvement": previous,
            "artifacts": sorted(json.loads(row["files"])), "receipt": receipt(w, case_id, events)}


def read_artifact(w, case_id, name):
    row = case_record(w, case_id)
    checksum_expected = json.loads(row["files"]).get(name)
    if not checksum_expected:
        raise ValueError("此档案没有所选文件")
    path = w.root / "evidence-cases" / case_id / name
    if not path.is_file():
        raise ValueError("封存文件缺失：" + name)
    data = path.read_bytes()
    if checksum(data) != checksum_expected:
        raise ValueError("封存文件摘要不符：" + name)
    return data


def adopt(w, case_id, body, actor):
    case = case_record(w, case_id)
    run = next((r for r in json.loads(case["runs"]) if r["id"] == body.run_id and r["status"] == "completed"), None)
    if not run:
        raise ValueError("只能采纳本档案中已完成的运行")
    read_artifact(w, case_id, f"{run['id']}/result.json")
    read_artifact(w, case_id, f"{run['id']}/runtime.trig")
    read_artifact(w, case_id, "baseline/runtime.trig")
    row = dict(id=case_id + "/decision", type="AdoptionDecision", case=Ref(case_id),
               run=Ref(run["id"]), rationale=body.rationale)
    append(w, case_id, "case_decision_adopted", actor, [row], [case_id, run["id"]], body.rationale)


def execute(w, case_id, body, actor):
    case_record(w, case_id)
    decision = w.catalog.get(case_id + "/decision")
    if not decision:
        raise ValueError("请先记录采纳决定")
    if body.end_at <= body.start_at or body.end_at > datetime.fromisoformat(now()):
        raise ValueError("现场执行须填写已经结束的有效时间区间")
    row = dict(id=case_id + "/execution", type="FieldExecution", case=Ref(case_id),
               decision=Ref(decision["id"]), **body.model_dump(mode="json"))
    append(w, case_id, "case_field_execution_reported", actor, [row], [case_id, decision["id"]], body.description)


def observe(w, case_id, body, actor):
    case_record(w, case_id)
    decision = w.catalog.get(case_id + "/decision")
    execution = w.catalog.get(case_id + "/execution")
    if not execution:
        raise ValueError("请先登记现场执行")
    forecast = json.loads(read_artifact(w, case_id, f"{decision['run']}/result.json"))
    orders = {row["id"]: row for row in forecast["summary"]["orders"]}
    actual = {row.order_id: row for row in body.orders}
    if len(actual) != len(body.orders) or set(actual) != set(orders):
        raise ValueError("请为基线中的每个工单填写一次实际数据")
    start, end = (datetime.fromisoformat(execution[key]) for key in ("start_at", "end_at"))
    window = forecast["summary"]["kpi"]
    seconds = window["window_end_s"] - window["window_start_s"]
    comparable = body.same_scope and abs((end - start).total_seconds() - seconds) <= 1
    comparison = []
    for oid, plan in orders.items():
        measured = actual[oid]
        if measured.completed > plan["quantity"]:
            raise ValueError(f"{oid} 完工数超过基线数量")
        if measured.completed_at and (measured.completed != plan["quantity"] or measured.completed_at > end):
            raise ValueError(f"{oid} 仅在整单完成时填写完工时间，且不得晚于实测窗口终点")
        delay = None
        if comparable and measured.completed_at and plan["completion_s"] is not None:
            expected = start + timedelta(seconds=plan["completion_s"] - window["window_start_s"])
            delay = (measured.completed_at - expected).total_seconds()
        comparison.append(dict(order=oid, quantity=plan["quantity"], forecast=plan["completed"], actual=measured.completed,
                               delta=measured.completed - plan["completed"] if comparable else None, completion_delta_s=delay))
    outcome = dict(id=uid("actual"), type="FieldOutcome", case=Ref(case_id), execution=Ref(execution["id"]),
                   source_kind="manual", source=body.source, same_scope=body.same_scope,
                   orders=canonical([r.model_dump(mode="json") for r in body.orders]))
    if body.source_record:
        outcome.update(source_record=body.source_record, source_digest=checksum(body.source_record.encode()))
    evaluation = dict(id=uid("evaluation"), type="OutcomeEvaluation", case=Ref(case_id), outcome=Ref(outcome["id"]),
                      run=decision["run"], comparable=comparable, rows=canonical(comparison), forecast_seconds=seconds,
                      actual_seconds=(end - start).total_seconds())
    append(w, case_id, "case_outcome_evaluated", actor, [outcome, evaluation],
           [case_id, decision["run"], execution["id"]], body.source)


def improve(w, case_id, body, actor):
    case_record(w, case_id)
    evaluation = w.catalog.get(body.evaluation_id)
    if not evaluation or evaluation["type"] != "OutcomeEvaluation" or evaluation["case"] != case_id:
        raise ValueError("请选择本档案的实际对照结果")
    row = dict(id=uid("improvement"), type="ModelImprovement", case=Ref(case_id),
               evaluation=Ref(evaluation["id"]), finding=body.finding, change=body.change, validation=body.validation)
    append(w, case_id, "case_improvement_recorded", actor, [row], [case_id, evaluation["id"]], body.finding)


def receipt(w, case_id, events):
    return {"case_id": case_id, "sequence": events[-1]["payload"]["sequence"], "hash": events[-1]["hash"],
            "fingerprint": w.signer.fingerprint}


def prov_graph(w, case_id, events):
    case = case_record(w, case_id)
    g = Graph()
    g.bind("os", OS)
    g.bind("prov", PROV)
    root = OS[case_id]
    g.add((root, RDF.type, PROV.Collection))
    g.add((root, RDF.type, OS.EvidenceCase))
    g.add((root, RDFS.label, Literal(case["name"])))
    g.add((root, OS.objective, Literal(case["objective"])))
    baseline = OS[case_id + "/baseline"]
    g.add((baseline, RDF.type, OS.ArchivedBaseline))
    g.add((baseline, RDFS.label, Literal("封存分析基线")))
    g.add((OS[case["checkpoint"]], RDF.type, OS.Checkpoint))
    g.add((OS[case["checkpoint"]], RDFS.label, Literal("原分析基线")))
    g.add((baseline, PROV.wasDerivedFrom, OS[case["checkpoint"]]))
    g.add((root, PROV.hadMember, baseline))
    metadata = json.loads(case["baseline"])
    g.add((baseline, OS.graph_head, Literal(metadata["graph_head"])))
    if metadata.get("incident_id"):
        g.add((baseline, OS.triggeredBy, Literal(metadata["incident_id"])))
        for job in metadata.get("incident_jobs", []):
            g.add((baseline, OS.affectedWorkpiece, Literal(job)))
    for run in json.loads(case["runs"]):
        entity = OS[run["id"]]
        archived = OS[case_id + "/" + run["id"]]
        g.add((archived, RDF.type, OS.ArchivedRun))
        g.add((entity, RDF.type, PROV.Entity))
        g.add((entity, RDFS.label, Literal(run.get("name", run["id"]) + " · 结果")))
        g.add((archived, RDFS.label, Literal(run.get("name", run["id"]) + " · 存档")))
        g.add((archived, PROV.wasDerivedFrom, entity))
        g.add((entity, PROV.wasDerivedFrom, OS[case["checkpoint"]]))
        g.add((root, PROV.hadMember, archived))
        g.add((archived, OS.status, Literal(run["status"])))
        if run.get("graph_head"):
            g.add((archived, OS.graph_head, Literal(run["graph_head"])))
        if run["status"] == "completed":
            simulation = OS[run["id"] + "/simulation"]
            plan = OS[run["id"] + "/plan"]
            native = OS["service/native"]
            association = OS[run["id"] + "/simulation-association"]
            g.add((simulation, RDF.type, OS.SimulationActivity))
            g.add((simulation, RDFS.label, Literal(run.get("name", run["id"]) + " · 仿真执行")))
            g.add((native, RDFS.label, Literal("Plant Simulation")))
            g.add((native, RDF.type, PROV.SoftwareAgent))
            g.add((plan, RDF.type, PROV.Plan))
            g.add((plan, RDFS.label, Literal(run.get("name", run["id"]) + " · 方案参数")))
            g.add((plan, OS.parameters, Literal(canonical(run["inputs"]))))
            g.add((simulation, PROV.used, OS[case["checkpoint"]]))
            g.add((simulation, PROV.used, plan))
            g.add((simulation, PROV.wasAssociatedWith, native))
            g.add((simulation, PROV.qualifiedAssociation, association))
            g.add((association, RDF.type, PROV.Association))
            g.add((association, PROV.agent, native))
            g.add((association, PROV.hadPlan, plan))
            g.add((entity, PROV.wasGeneratedBy, simulation))

    for record in events:
        p = record["payload"]
        activity = OS[f"{case_id}/activity/{p['sequence']}"]
        agent = OS[p["actor"]]
        g.add((activity, RDF.type, PROV.Activity))
        g.add((activity, OS.eventKind, Literal(p["kind"])))
        g.add((activity, PROV.startedAtTime, Literal(p["occurred_at"], datatype=XSD.dateTime)))
        g.add((activity, PROV.endedAtTime, Literal(p["occurred_at"], datatype=XSD.dateTime)))
        g.add((agent, RDF.type, PROV.Person if p["actor"].startswith("user/") else PROV.SoftwareAgent))
        g.add((activity, PROV.wasAssociatedWith, agent))
        for identity in p["used"]:
            g.add((activity, PROV.used, OS[identity]))
        for change in p["changes"]:
            row = change["after"]
            kind = row["type"]
            if kind == "CaseHead":
                continue
            entity = OS[row["id"]]
            g.add((entity, RDF.type, OS[kind]))
            if kind != "EvidenceCase":
                label = {"AdoptionDecision": "采纳决定", "FieldExecution": "现场执行",
                         "FieldOutcome": "现场实测", "OutcomeEvaluation": "预测与实际对照",
                         "ModelImprovement": "模型改进建议"}.get(kind, kind)
                g.add((entity, RDFS.label, Literal(label)))
            if kind == "FieldExecution":
                executor = OS[case_id + "/executor/" + digest(row["executor"])[:20]]
                g.add((executor, RDF.type, PROV.Agent))
                g.add((executor, OS.name, Literal(row["executor"])))
                g.add((entity, PROV.wasAssociatedWith, executor))
                g.add((entity, PROV.startedAtTime, Literal(row["start_at"], datatype=XSD.dateTime)))
                g.add((entity, PROV.endedAtTime, Literal(row["end_at"], datatype=XSD.dateTime)))
                g.add((entity, OS.implementsDecision, OS[row["decision"]["ref"]]))
                g.add((activity, OS.recordsExecution, entity))
                continue
            generator = activity
            if kind == "OutcomeEvaluation":
                generator = URIRef(str(activity) + "/evaluation")
                g.add((generator, RDF.type, PROV.Activity))
                g.add((generator, RDF.type, OS.EvaluationActivity))
                g.add((generator, RDFS.label, Literal("核对统计窗口并计算偏差")))
                g.add((generator, PROV.wasAssociatedWith, OS["service/field-evaluation"]))
                g.add((OS["service/field-evaluation"], RDF.type, PROV.SoftwareAgent))
                for key in ("outcome", "run"):
                    identity = row[key]
                    if isinstance(identity, dict):
                        identity = identity["ref"]
                    g.add((generator, PROV.used, OS[identity]))
                    g.add((entity, PROV.wasDerivedFrom, OS[identity]))
            g.add((entity, PROV.wasGeneratedBy, generator))
            g.add((entity, PROV.wasAttributedTo, agent))
            if entity != root:
                g.add((root, PROV.hadMember, entity))
            for key, value in row.items():
                if key in {"id", "type", "runs", "baseline", "files"}:
                    continue
                obj = OS[value["ref"]] if isinstance(value, dict) and "ref" in value else Literal(value)
                predicate = OS.observesExecution if kind == "FieldOutcome" and key == "execution" else OS[key]
                g.add((entity, predicate, obj))
    if "baseline/runtime.trig" in json.loads(case["files"]):
        from .evidence_graph import add_manufacturing
        runtime = w.context(case_id + "~baseline")
        evaluations = [change["after"] for event in events for change in event["payload"]["changes"]
                       if change["after"]["type"] == "OutcomeEvaluation"]
        add_manufacturing(g, runtime, metadata, baseline, evaluations)
    return g


def case_graph_view(w, case_id, **kwargs):
    from .evidence_graph import graph_view
    events = case_events(w, case_id)
    detail = case_detail(w, case_id, events)
    root = OS[case_id]
    baseline = OS[case_id + "/baseline"]
    graph = prov_graph(w, case_id, events)
    view = graph_view(graph, default=baseline, **kwargs)
    shortcuts = [{"id": str(baseline), "label": "固定分析起点"}, {"id": str(root), "label": "封存档案"}]
    actions = {str(baseline): {"play": "baseline", "inspect": detail["baseline"].get("incident_id") or "clock", "context": "baseline"}}
    for run in detail["runs"]:
        identity = run["id"]
        result = str(OS[identity])
        shortcuts.append({"id": result, "label": run["name"] + " · 样本 " + str(run["inputs"].get("sample_index", 0) + 1)})
        if identity + "/runtime.trig" in detail["artifacts"]:
            action = {"play": identity}
            if run["status"] == "completed":
                action.update(analysis=identity, inspect="result/" + identity, context=identity)
            for node in (result, str(OS[identity + "/simulation"]), str(OS[case_id + "/" + identity])):
                actions[node] = action
    for entry in detail["entries"]:
        if entry["type"] in {"AdoptionDecision", "FieldExecution", "FieldOutcome", "OutcomeEvaluation", "ModelImprovement"}:
            node = str(OS[entry["id"]])
            shortcuts.append({"id": node, "label": str(graph.value(URIRef(node), RDFS.label)) + " #" + str(entry["sequence"])})
        actions[str(OS[entry["id"]])] = {"inspect": entry["id"], "context": "catalog"}
    for node in view["nodes"]:
        node.update(action=actions.get(node["id"]))
        if node["record_iri"]:
            from urllib.parse import unquote
            node["action"] = {"inspect": unquote(node["record_iri"].removeprefix(NS)), "context": "baseline"}
    view["shortcuts"] = shortcuts
    return view


def export_case(w, case_id):
    case = case_record(w, case_id)
    events = case_events(w, case_id)
    extra = {"case-events.json": canonical(events).encode(), "receipt.json": canonical(receipt(w, case_id, events)).encode(),
             "case-abox.ttl": prov_graph(w, case_id, events).serialize(format="turtle").encode()}
    manifest = dict(kind="what-if-case", case_id=case_id, public_key=w.signer.public, fingerprint=w.signer.fingerprint,
                    files={**json.loads(case["files"]), **{name: checksum(value) for name, value in extra.items()}})
    stream = SpooledTemporaryFile(max_size=8_000_000)
    try:
        with ZipFile(stream, "w", ZIP_DEFLATED) as zip_file:
            for name in manifest["files"]:
                zip_file.writestr(name, extra[name] if name in extra else read_artifact(w, case_id, name))
            zip_file.writestr("manifest.json", canonical({"manifest": manifest, "signature": w.signer.sign(manifest)}))
        stream.seek(0)
        return stream
    except BaseException:
        stream.close()
        raise


def verify_case_bundle(source, fingerprint=None, anchor=None):
    import base64
    try:
        if isinstance(source, bytes):
            source = io.BytesIO(source)
        with ZipFile(source) as zip_file:
            names = zip_file.namelist()
            signed = json.loads(zip_file.read("manifest.json"))
            manifest = signed["manifest"]
            if manifest.get("kind") != "what-if-case" or len(names) != len(set(names)) or set(names) != set(manifest["files"]) | {"manifest.json"}:
                raise ValueError("档案清单不一致")
            check_signature(manifest, signed["signature"], manifest["public_key"])
            actual = checksum(base64.b64decode(manifest["public_key"]))
            if actual != manifest["fingerprint"] or fingerprint and actual != fingerprint:
                raise ValueError("公钥指纹不符")
            for name, expected in manifest["files"].items():
                sha = sha256()
                with zip_file.open(name) as content:
                    for block in iter(lambda: content.read(1024 * 1024), b""):
                        sha.update(block)
                if sha.hexdigest() != expected:
                    raise ValueError("档案文件摘要不符：" + name)
            events = json.loads(zip_file.read("case-events.json"))
            previous = 0
            heads = {}
            for event in events:
                p = event["payload"]
                if (p["sequence"] <= previous or p["details"] != {"case_id": manifest["case_id"], "previous_case_sequence": previous}
                        or digest(p) != event["hash"] or event["public_key"] != manifest["public_key"]):
                    raise ValueError("档案事件序列不连续")
                check_signature(p, event["signature"], event["public_key"])
                previous = p["sequence"]
                heads[previous] = event["hash"]
            current = json.loads(zip_file.read("receipt.json"))
            if not heads or current != {"case_id": manifest["case_id"], "sequence": previous, "hash": heads[previous], "fingerprint": actual}:
                raise ValueError("末端回执与档案不一致")
            if anchor and (anchor["case_id"] != manifest["case_id"] or anchor["fingerprint"] != actual or heads.get(anchor["sequence"]) != anchor["hash"]):
                raise ValueError("独立回执对应的记录缺失或被更改")
            sealed = next(row["after"] for row in events[0]["payload"]["changes"] if row["after"]["type"] == "EvidenceCase")
            original = {name: checksum for name, checksum in manifest["files"].items()
                        if name not in {"case-events.json", "receipt.json", "case-abox.ttl"}}
            if sealed["id"] != manifest["case_id"] or json.loads(sealed["files"]) != original:
                raise ValueError("封存清单与原始签名记录不符")
            from pyshacl import validate
            conforms, _, report = validate(
                Graph().parse(data=zip_file.read("case-abox.ttl"), format="turtle"),
                shacl_graph=Graph().parse(data=zip_file.read("tbox/evidence-shapes.ttl"), format="turtle"),
            )
            if not conforms:
                raise ValueError(report)
        return {"valid": True, "events": len(events), "files": len(manifest["files"]), "receipt": current,
                "independently_anchored": bool(fingerprint and anchor)}
    except Exception as ex:
        return {"valid": False, "error": str(ex) or "证据签名无效"}


def ontology_layers():
    graph = Graph().parse(TBOX / "ontology.ttl")
    return {"tbox": {"iri": str(OS.WorkbenchOntology), "imports": sorted(map(str, graph.objects(OS.WorkbenchOntology, OWL.imports)))},
            "abox": {"production": STATE.value, "provenance": AUDIT.value}}

"""Bounded views of the imported TBox and the case's RDF evidence."""
from collections import defaultdict, deque
from functools import lru_cache
import json

from rdflib import Graph, Literal, RDF, RDFS, URIRef
from rdflib.namespace import OWL

from .graph import Ref, iri
from .views import RELATIONS


PROV_IRI = "http://www.w3.org/ns/prov-o#"
IMPORTS = {
    "urn:ontologysim:v2:Ontology": "core.ttl",
    "urn:ontologysim:v2:EvidenceOntology": "evidence.ttl",
    "urn:ontologysim:v2:SimulationOntology": "simulation.ttl",
    PROV_IRI: "prov-o.ttl",
}
TERMS = {
    "Entity": "实体", "Activity": "活动", "Agent": "责任主体", "Plan": "计划",
    "Person": "人员", "SoftwareAgent": "软件服务", "Collection": "集合",
    "Association": "限定关联", "used": "使用", "wasGeneratedBy": "由活动生成",
    "wasDerivedFrom": "派生自", "wasAttributedTo": "归属于",
    "wasAssociatedWith": "由…负责", "hadMember": "包含", "specializationOf": "特定版本",
    "qualifiedAssociation": "责任与计划", "agent": "责任主体", "hadPlan": "采用计划",
    "subClassOf": "是…的子类", "imports": "导入", "domain": "定义域", "range": "值域", "isDefinedBy": "由模块定义",
    "execution": "对应现场执行", "run": "引用仿真结果", "evaluation": "依据效果评估",
    "outcome": "引用实测", "previous_improvement": "承接上一轮改进",
    "forecast": "预测累计完工", "actual": "实测累计完工", "delta": "实测减预测",
    "completion_delta_s": "整单完工时差（秒）",
    "source": "来自原始记录",
}
EVENT_LABELS = {
    "case_sealed": "封存比较", "case_decision_adopted": "登记采纳",
    "case_field_execution_reported": "登记现场执行", "case_outcome_evaluated": "登记现场实测",
    "case_improvement_recorded": "登记模型改进",
}
BUSINESS_TYPES = {"AssetState", "Equipment", "Workpiece", "WorkOrder", "Batch", "Product", "OperationPlan", "Capability", "Inspection", "Observation"}


def _local(value):
    return str(value).rsplit("#", 1)[-1].rsplit(":", 1)[-1]


@lru_cache(maxsize=1)
def imported_schema():
    from .cases import TBOX
    graph = Graph().parse(TBOX / "ontology.ttl", format="turtle")
    visited = set()
    pending = list(graph.objects(None, OWL.imports))
    while pending:
        identity = str(pending.pop())
        if identity in visited:
            continue
        imported = Graph().parse(TBOX / IMPORTS[identity], format="turtle")
        if (URIRef(identity), RDF.type, OWL.Ontology) not in imported:
            raise ValueError("本体导入标识与文件不一致：" + identity)
        if identity != PROV_IRI:
            for term in imported.subjects(RDF.type, OWL.Class):
                if isinstance(term, URIRef):
                    imported.add((term, RDFS.isDefinedBy, URIRef(identity)))
        graph += imported
        pending.extend(imported.objects(None, OWL.imports))
        visited.add(identity)
    return graph


def add_manufacturing(g, runtime, metadata, baseline, evaluations):
    """Represent the frozen records as snapshot-scoped instances of manufacturing classes."""
    from .cases import OS
    graph = runtime.graph
    root = str(baseline)
    seen = {}
    orders_by_job = {}

    def add(identity, depth):
        if depth < 0:
            return
        node = URIRef(root + "/record/" + iri(identity).value.removeprefix(str(OS)))
        if seen.get(identity, -1) >= depth:
            return node
        record = graph.get(identity)
        if not record or record["type"] not in BUSINESS_TYPES | {"SourceRecord"}:
            return
        seen[identity] = depth
        g.add((node, RDF.type, OS[record["type"]]))
        label = record.get("name") or record.get("raw_id") or str(identity)
        g.add((node, RDFS.label, Literal(label)))
        g.add((node, OS.recordIRI, URIRef(iri(identity).value)))
        g.add((node, OS.inSnapshot, baseline))
        if record["type"] == "Workpiece":
            orders_by_job[identity] = record["order"]
        for field in ("status", "queue_seq", "op_index", "quantity", "due_s", "maintenance_until", "event_time", "quality"):
            if field in record:
                g.add((node, OS[field], Literal(record[field])))
        if depth > 0:
            for field in ("resource", "assigned", "order", "batch", "product", "operations", "preferred", "job", "source"):
                value = record.get(field)
                for target in (value if isinstance(value, list) else [value]):
                    if isinstance(target, Ref):
                        linked = add(target, depth - 1)
                        if linked is not None:
                            g.add((node, OS[field], linked))
        return node

    incident = metadata.get("incident_id")
    if incident:
        state = add(incident, 2)
        if state is not None:
            g.add((baseline, OS.documentsStateOf, state))
            asset = graph.get(incident)
            cursor = metadata.get("source_cursor")
            if asset["type"] == "AssetState" and cursor is not None:
                observation = add(f"observation/{cursor}/{asset['resource']}", 1)
                if observation is not None:
                    g.add((baseline, OS.basedOnObservation, observation))
    affected_orders = set(metadata.get("impact", {}).get("orders", []))
    for jid in metadata.get("incident_jobs", []):
        job = add(jid, 3)
        if job is not None:
            g.add((baseline, OS.containsAffectedWorkpiece, job))
            affected_orders.add(orders_by_job[jid])
    for oid in sorted(affected_orders):
        order = add(oid, 2)
        if order is not None:
            g.add((baseline, OS.containsAffectedOrder, order))

    for evaluation in evaluations:
        for record in json.loads(evaluation.get("rows", "[]")):
            order = add(record["order"], 2)
            if order is None:
                continue
            item = URIRef(iri(evaluation["id"] + "/order/" + record["order"]).value)
            g.add((item, RDF.type, OS.OrderComparison))
            g.add((item, RDFS.label, Literal(record["order"] + " · 预测与实测")))
            g.add((item, OS.comparesOrder, order))
            g.add((OS[evaluation["id"]], OS.hasOrderComparison, item))
            for key in ("forecast", "actual", "delta", "completion_delta_s"):
                if record.get(key) is not None:
                    g.add((item, OS[key], Literal(record[key])))


def graph_view(graph, focus=None, default=None, depth=2, limit=30, direction="both", mode="abox"):
    from .cases import OS, PROV
    schema = imported_schema()
    tbox = mode == "tbox"
    triples = []
    for s, p, o in graph:
        if not isinstance(s, URIRef) or not isinstance(o, URIRef):
            continue
        if tbox:
            if p not in (OWL.imports, RDFS.subClassOf, RDFS.domain, RDFS.range, RDFS.subPropertyOf, RDFS.isDefinedBy):
                continue
            if p == RDFS.isDefinedBy and not any((s, RDF.type, cls) in graph for cls in (OWL.Class, OWL.ObjectProperty)):
                continue
        elif p in (RDF.type, OS.recordIRI, OS.inSnapshot):
            continue
        triples.append((s, p, o))
    triples.sort()

    def name(term):
        if term in PROV:
            return TERMS.get(_local(term), _local(term))
        return str(graph.value(term, RDFS.label) or schema.value(term, RDFS.label)
                   or TERMS.get(_local(term), RELATIONS.get(_local(term), _local(term))))

    def kind(node):
        if tbox:
            if (node, RDF.type, OWL.Ontology) in graph:
                return "本体模块", "本体"
            if any((node, RDF.type, value) in graph for value in (OWL.ObjectProperty, OWL.DatatypeProperty)):
                return "属性", "属性"
            return "概念", "概念"
        types = set(graph.objects(node, RDF.type))
        manufacturing = types.intersection(OS[name] for name in BUSINESS_TYPES)
        if manufacturing:
            return name(sorted(manufacturing)[0]), "制造对象"
        parents = set(types)
        for base in types:
            parents.update(schema.transitive_objects(base, RDFS.subClassOf))
        for prov_type, label in ((PROV.Agent, "责任主体"), (PROV.Activity, "活动")):
            if prov_type in parents:
                return label, label
        return "证据实体", "证据实体"

    adjacency = defaultdict(list)
    for s, p, o in triples:
        adjacency[s].append((o, p, "out"))
        adjacency[o].append((s, p, "in"))
    candidate = URIRef(focus) if focus and len(focus) <= 400 else None
    root = candidate if candidate and (candidate in adjacency or (candidate, RDF.type, None) in graph) else default
    ordered = {root: 0}
    queue = deque([root])
    if root is None:
        return {"nodes": [], "edges": [], "focus": "", "omitted": 0, "shortcuts": []}
    low_priority = {PROV.hadMember, PROV.wasAttributedTo, PROV.qualifiedAssociation,
                    OS.containsAffectedWorkpiece, RDFS.isDefinedBy}
    while queue:
        here = queue.popleft()
        if ordered[here] >= depth:
            continue
        for neighbor, relation, way in sorted(adjacency[here], key=lambda e: (e[1] in low_priority, e)):
            if neighbor not in ordered and direction in ("both", way):
                ordered[neighbor] = ordered[here] + 1
                queue.append(neighbor)
    shown = set(list(ordered)[:limit])
    nodes = []
    for node in list(ordered)[:limit]:
        label = name(node)
        role, layer = kind(node)
        event = graph.value(node, OS.eventKind)
        if event:
            label = EVENT_LABELS.get(str(event), str(event))
        raw = graph.value(node, OS.recordIRI)
        nodes.append(dict(id=str(node), label=label, kind=role, layer=layer, focused=node == root,
                          distance=ordered[node],
                          record_iri=str(raw) if raw else None,
                          types=sorted(str(t) for t in graph.objects(node, RDF.type)),
                          properties=[{"predicate": str(p), "label": name(p), "value": str(o)[:600]}
                                      for p, o in sorted(graph.predicate_objects(node)) if isinstance(o, Literal)][:14]))
    edges = [
        {"id": f"{s}|{p}|{o}", "from": str(s), "to": str(o), "label": name(p), "predicate": str(p)}
        for s, p, o in triples if s in shown and o in shown
    ]
    return dict(nodes=nodes, edges=edges, focus=str(root), omitted=max(0, len(ordered)-limit), total=len(ordered))


def schema_view(**kwargs):
    from .cases import OS, PROV
    schema = imported_schema()
    # Include the domains/ranges of standard provenance relationships as ordinary RDF edges.
    view = graph_view(schema, default=OS.WorkbenchOntology, mode="tbox", **kwargs)
    terms = (OS.WorkbenchOntology, OS.Workpiece, OS.Execution, OS.ArchivedBaseline, OS.OutcomeEvaluation,
             PROV.Entity, PROV.Activity, PROV.Agent, PROV.used, PROV.wasGeneratedBy, PROV.wasAssociatedWith)
    view["shortcuts"] = [{"id": str(t), "label": TERMS.get(_local(t), str(schema.value(t, RDFS.label) or _local(t)))} for t in terms]
    view["version"] = str(schema.value(URIRef(PROV_IRI), OWL.versionIRI))
    return view

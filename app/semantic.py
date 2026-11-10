from __future__ import annotations
from pathlib import Path
from urllib.parse import quote
from rdflib import Graph, Dataset, Namespace, URIRef, BNode, Literal, RDF, RDFS
from pyshacl import validate
from owlrl import DeductiveClosure, OWLRL_Semantics
from pyoxigraph import Store, RdfFormat
from .contracts import ProductionSpec, digest

OS = Namespace("urn:ontologysim:")
PROV = Namespace("http://www.w3.org/ns/prov#")
BASE = Path(__file__).resolve().parents[1]

def node(id): return OS[quote(str(id), safe="_-.")]

def spec_graph(spec: ProductionSpec) -> Graph:
    g = Graph()
    g.bind("os", OS)
    for key,value in {"seed":spec.seed,"horizon":spec.horizon_s,"transfer":spec.transfer_s}.items():
        g.add((OS.RunConfiguration,OS[key],Literal(value)))
    for r in spec.resources:
        u = node(r.id)
        g.add((u, RDF.type, OS.Equipment)); g.add((u, RDFS.label, Literal(r.name)))
        g.add((u, OS.unit, Literal("s")))
        for key,value in {"shiftHours":r.shift_hours,"toolLimit":r.tool_limit,"maintenanceDuration":r.maintenance_s}.items():
            g.add((u,OS[key],Literal(value)))
        for alias in [r.mes_id, r.opc_id]: g.add((u, OS.sourceId, Literal(alias)))
        if r.tool_limit:
            tool=node(r.id+"_tool")
            g.add((u,OS.hasTool,tool));g.add((tool,RDF.type,OS.Tool));g.add((tool,OS.lifeLimit,Literal(r.tool_limit)))
        for i, c in enumerate(r.capabilities):
            v = node(f"{r.id}_cap{i}")
            g.add((u, OS.hasCapability, v)); g.add((v, RDF.type, OS.Capability))
            g.add((v, OS.operation, Literal(c.operation)))
            g.add((v, OS.tolerance, Literal(c.tolerance_um)))
            for m in c.materials: g.add((v, OS.material, Literal(m)))
    for p in spec.products:
        u = node(p.id)
        g.add((u, RDF.type, OS.Product)); g.add((u, RDFS.label, Literal(p.name)))
        for i, op in enumerate(p.operations):
            v=node(f"{p.id}_{op.id}")
            g.add((u, OS.hasOperation, v)); g.add((v, RDF.type, OS.OperationPlan))
            for key, val in {"operation":op.operation, "material":op.material,
                "tolerance":op.tolerance_um,"duration":op.duration_s,"unit":"s","index":i}.items():
                g.add((v, OS[key], Literal(val)))
            g.add((v, OS.preferred, node(op.preferred)))
            g.add((v, RDFS.label, Literal(op.name)))
    for o in spec.orders:
        u=node(o.id)
        g.add((u,RDF.type,OS.Order)); g.add((u,OS.forProduct,node(o.product)))
        g.add((u,OS.quantity,Literal(o.quantity))); g.add((u,OS.due,Literal(o.due_s)))
        g.add((u,OS.release,Literal(o.release_s)))
    return g

def compile_input(spec: ProductionSpec):
    """Resolve execution parameters from the validated graph, preserving plan order.

    This is the sole compiler boundary to COM. Labels and stable entity ordering
    are carried by the versioned spec; capability decisions come from SPARQL.
    """
    checked=validate_spec(spec)
    if not checked["conforms"]: raise ValueError("；".join(checked["errors"]) or checked["shacl"])
    g=spec_graph(spec); resolved=spec.model_dump()
    def val(subject,predicate): return g.value(subject,OS[predicate]).toPython()
    for key,pred in [("seed","seed"),("horizon_s","horizon"),("transfer_s","transfer")]:
        resolved[key]=val(OS.RunConfiguration,pred)
    for r in resolved["resources"]:
        for key,pred in [("shift_hours","shiftHours"),("tool_limit","toolLimit"),("maintenance_s","maintenanceDuration")]:
            r[key]=val(node(r["id"]),pred)
    for p in resolved["products"]:
        for op in p["operations"]:
            u=node(f"{p['id']}_{op['id']}")
            for key,pred in [("duration_s","duration"),("tolerance_um","tolerance"),("material","material"),("operation","operation")]:
                op[key]=val(u,pred)
            op["preferred"]=str(g.value(u,OS.preferred)).removeprefix(str(OS))
    for o in resolved["orders"]:
        for key,pred in [("quantity","quantity"),("due_s","due"),("release_s","release")]: o[key]=val(node(o["id"]),pred)
    compiled=ProductionSpec.model_validate(resolved)
    return {"spec":compiled.model_dump(),"candidates":checked["candidates"]}

def candidates(g: Graph, product: str, operation_id: str) -> list[str]:
    # Bind variables: no front-end text is interpolated into SPARQL.
    rows = g.query('''PREFIX os: <urn:ontologysim:>
      SELECT DISTINCT ?resource WHERE {
        ?plan os:operation ?op ; os:material ?material ; os:tolerance ?required .
        ?resource a os:Equipment ; os:hasCapability ?cap .
        ?cap os:operation ?op ; os:material ?material ; os:tolerance ?achievable .
        FILTER (?achievable <= ?required)
      } ORDER BY ?resource''', initBindings={"plan":node(f"{product}_{operation_id}")})
    return [str(row.resource).removeprefix(str(OS)) for row in rows]

def validate_spec(spec: ProductionSpec):
    g=spec_graph(spec)
    ok, report, text=validate(g, shacl_graph=str(BASE/"ontology/shapes.ttl"), inference="rdfs")
    errors=[]
    index={}
    for p in spec.products:
        for op in p.operations:
            key=f"{p.id}/{op.id}"
            index[key]=candidates(g,p.id,op.id)
            if not index[key]: errors.append(f"{key}: 没有满足工序、材料与精度要求的设备")
            if op.preferred not in index[key]:
                errors.append(f"{key}: 首选设备 {op.preferred} 不具备资格；候选 {index[key]}")
    aliases=[a for r in spec.resources for a in [r.mes_id,r.opc_id]]
    if len(aliases)!=len(set(aliases)): errors.append("跨系统映射 ID 重复")
    return {"conforms":bool(ok) and not errors,"errors":errors,"shacl":text,"candidates":index,
            "spec_hash":digest(spec.model_dump()),"triples":len(g)}

def validate_patch(spec, snapshot, patch):
    check=validate_spec(spec)
    if not check["conforms"]: raise ValueError("；".join(check["errors"]) or check["shacl"])
    products={p.id:p for p in spec.products}
    for jid,rid in patch.assignments.items():
        job=snapshot["jobs"].get(jid)
        if not job: raise ValueError(f"不存在工件 {jid}")
        if job["status"] in ["processing","done"]: raise ValueError(f"{jid} 已开工或完成，禁止改派")
        op=products[job["product"]].operations[job["op"]]
        valid=check["candidates"][f"{job['product']}/{op.id}"]
        if rid not in valid:
            raise ValueError(f"{jid} → {rid} 拒绝：要求 {op.operation}/{op.material}/≤{op.tolerance_um} μm；合格设备 {valid}")
    return check["candidates"]

def validate_snapshot_graph(spec,state):
    g=spec_graph(spec)
    for jid,j in state["jobs"].items():
        u=node(jid)
        g.add((u,RDF.type,OS.Workpiece));g.add((u,OS.forOrder,node(j["order"])))
        g.add((u,OS.locatedAt,node(j["location"])))
    ok,_,report=validate(g,shacl_graph=str(BASE/"ontology/shapes.ttl"),inference="rdfs")
    if not ok: raise ValueError("现场 SHACL 校验失败："+report)
    return {"conforms":True,"triples":len(g)}

class SemanticProjection:
    def __init__(self, path):
        self.store=Store(str(path))
        self.schema=Graph().parse(BASE/"ontology/core.ttl")
        self.meta={}

    def rebuild(self, project, spec, state=None, records=(), results=(), cursor=0, snapshot_id=None):
        ds=Dataset()
        source=ds.graph(OS["graph/source"]); canonical=ds.graph(OS["graph/linking"])
        derived=ds.graph(OS["graph/enrichment"])
        for t in self.schema: ds.graph(OS["graph/schema"]).add(t)
        for t in spec_graph(spec): canonical.add(t)
        for r in records:
            u=node("record_"+str(r["seq"]))
            source.add((u,RDF.type,OS.Observation))
            for k in ["source","raw_id","event_time","received_at","quality"]:
                source.add((u,OS[k],Literal(r[k])))
            if r.get("entity"): source.add((u,OS.observes,node(r["entity"])))
        if state:
            snap=node(snapshot_id or f"live_{project}_{cursor}")
            canonical.add((snap,RDF.type,OS.Snapshot)); canonical.add((snap,OS.time,Literal(state["time"])))
            for jid,j in state["jobs"].items():
                u=node(jid)
                canonical.add((u,RDF.type,OS.Workpiece)); canonical.add((u,OS.forOrder,node(j["order"])))
                canonical.add((u,OS.locatedAt,node(j["location"]))); canonical.add((u,OS.status,Literal(j["status"])))
                if j.get("assigned"): canonical.add((u,OS.assignedTo,node(j["assigned"])))
            for rid,a in state["assets"].items():
                canonical.add((node(rid),OS.status,Literal(a["status"])))
                if next(r for r in spec.resources if r.id==rid).tool_limit:
                    canonical.add((node(rid+"_tool"),OS.completedCount,Literal(a["tool_count"])))
                if a["maintenance_until"]>state["time"]:
                    event=node(f"maintenance_{rid}_{a['maintenance_until']}")
                    derived.add((event,RDF.type,OS.MaintenanceEvent)); derived.add((event,OS.affects,node(rid)))
                    derived.add((event,PROV.wasDerivedFrom,snap))
                    derived.add((event,OS.triggeredBy,node(rid+"_tool")))
                    for jid,j in state["jobs"].items():
                        if j.get("assigned")==rid and j["status"]!="done":
                            derived.add((event,OS.affects,node(jid)))
                            derived.add((event,OS.affects,node(j["order"])))
        # Finite rule closure over the small application TBox, never infer production events.
        closure=Graph()
        for t in self.schema: closure.add(t)
        DeductiveClosure(OWLRL_Semantics).expand(closure)
        for t in closure:
            if t not in self.schema and isinstance(t[0],(URIRef,BNode)) and isinstance(t[1],URIRef): derived.add(t)
        for result in results:
            g=ds.graph(node("graph/scenario/"+result["run_id"]))
            u=node(result["run_id"]); g.add((u,RDF.type,OS.RunResult))
            g.add((u,OS.prediction,Literal(True)))
            if result["snapshot_id"]: g.add((u,PROV.wasDerivedFrom,node(result["snapshot_id"])))
            for k,v in result["kpi"].items():
                if isinstance(v,(int,float,str)): g.add((u,OS[k],Literal(v)))
            activity=node(result["run_id"]+"_execution")
            g.add((activity,RDF.type,PROV.Activity));g.add((u,PROV.wasGeneratedBy,activity))
            g.add((u,OS.inputHash,Literal(result["input_hash"])))
            g.add((u,OS.nativeCompleted,Literal(result["native"]["completed"])))
            g.add((activity,OS.engine,Literal(result["native"]["engine"])))
            if result["snapshot_id"]:g.add((activity,PROV.used,node(result["snapshot_id"])))
            for row in result["orders"]:
                order_result=node(result["run_id"]+"_"+row["id"])
                g.add((u,OS.hasOrderResult,order_result));g.add((order_result,OS.forOrder,node(row["id"])))
                g.add((order_result,OS.completedQuantity,Literal(row["completed"])))
                g.add((order_result,OS.prediction,Literal(True)))
                if row["completion_s"] is not None:g.add((order_result,OS.completionTime,Literal(row["completion_s"])))
            for rid,utilization in result["kpi"]["utilization"].items():
                equipment_result=node(result["run_id"]+"_"+rid)
                g.add((u,OS.hasEquipmentResult,equipment_result));g.add((equipment_result,OS.forEquipment,node(rid)))
                g.add((equipment_result,OS.utilization,Literal(utilization)))
        self.store.clear()
        self.store.load(input=ds.serialize(format="trig"), format=RdfFormat.TRIG)
        self.meta={"project_id":project,"cursor":cursor,"version":"1.0","triples":len(self.store),
                   "spec_hash":digest(spec.model_dump())}

    def export(self): return self.store.dump(format=RdfFormat.TRIG).decode()

    def graph(self, view="relations"):
        nodes={}; edges=[]
        if view=="schema":
            for u in self.schema.subjects(RDF.type,URIRef("http://www.w3.org/2002/07/owl#Class")):
                nodes[str(u)]={"id":str(u),"label":str(self.schema.value(u,RDFS.label) or u),"group":"class"}
            for prop,_,domain in self.schema.triples((None,RDFS.domain,None)):
                range=self.schema.value(prop,RDFS.range)
                if str(domain) in nodes and str(range) in nodes:
                    edges.append({"from":str(domain),"to":str(range),"label":str(prop).removeprefix(str(OS))})
            return {"nodes":list(nodes.values()),"edges":edges,"meta":self.meta,"view":view}
        wanted="graph/schema" if view=="schema" else None
        by_order={};all_jobs=set()
        for q in self.store:
            if q.predicate.value==str(OS.forOrder) and q.subject.value.startswith(str(OS)+"J"):
                by_order.setdefault(q.object.value,[]).append(q.subject.value);all_jobs.add(q.subject.value)
        shown_jobs={job for jobs in by_order.values() for job in sorted(jobs)[:2]}
        impact_ids=set()
        if view=="impact":
            for q in self.store:
                if q.predicate.value==str(OS.affects): impact_ids.update([q.subject.value,q.object.value])
        allowed={str(OS[x]) for x in ["hasCapability","hasOperation","forProduct","forOrder","preferred","assignedTo","locatedAt","affects","hasTool","triggeredBy"]}|{str(PROV.wasDerivedFrom)}
        labels={q.subject.value:q.object.value for q in self.store if q.predicate.value==str(RDFS.label)}
        edge_labels={"hasCapability":"具备能力","hasOperation":"工序计划","forProduct":"生产产品","forOrder":"所属订单",
            "preferred":"计划设备","assignedTo":"已分派","locatedAt":"当前位置","affects":"影响","hasTool":"使用刀具","triggeredBy":"触发依据","wasDerivedFrom":"现场证据"}
        for q in self.store:
            graph=q.graph_name.value
            if ":graph%2Fscenario" in graph or "graph/scenario" in graph: continue
            if wanted and graph!=str(OS)+wanted: continue
            if not wanted and graph.endswith("schema"): continue
            if not hasattr(q.object,"value"): continue
            s=q.subject.value; p=q.predicate.value; o=q.object.value
            if (s in all_jobs and s not in shown_jobs) or (o in all_jobs and o not in shown_jobs): continue
            if not wanted and p not in allowed: continue
            if view=="impact" and s not in impact_ids and o not in impact_ids: continue
            if not s.startswith(str(OS)) or not o.startswith(str(OS)): continue
            if "record_" in s or "graph" in s: continue
            if p.endswith("type") and view!="schema": continue
            if len(edges)>=200: continue
            for u in [s,o]:
                short=u.removeprefix(str(OS))
                label=labels.get(u,short)
                if short.startswith("maintenance_"): label="刀具维护事件"
                if short.startswith(("snapshot_","live_")): label="冻结现场证据"
                if short.endswith("_tool"): label=short.removesuffix("_tool")+" 刀具"
                nodes.setdefault(u,{"id":u,"label":label,"group":"entity"})
            predicate=p.rsplit(":",1)[-1].rsplit("#",1)[-1]
            edges.append({"from":s,"to":o,"label":edge_labels.get(predicate,predicate)})
        return {"nodes":list(nodes.values()),"edges":edges,"meta":self.meta,"view":view,
                "note":"每订单显示 2 件代表工件；完整工件及来源保留在 RDF 与事件存档中"}

    def entity(self, id):
        u=node(id.removeprefix(str(OS)))
        rows=[{"predicate":q.predicate.value,"value":q.object.value,"graph":q.graph_name.value}
                for q in self.store if q.subject.value==str(u)]
        observations=sorted({q.subject.value for q in self.store if q.predicate.value==str(OS.observes) and q.object.value==str(u)},
                            key=lambda value:int(value.rsplit('_',1)[-1]),reverse=True)[:3]
        for record in observations:
            for q in self.store:
                if q.subject.value==record and q.predicate.value!=str(RDF.type):
                    rows.append({"predicate":q.predicate.value,"value":q.object.value,"graph":q.graph_name.value+" / "+record.removeprefix(str(OS))})
        return rows

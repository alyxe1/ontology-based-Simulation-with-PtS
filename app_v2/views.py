"""Bounded business views and evidence traversal over the same ontology records."""
from collections import deque
from .graph import Ref, PROV, SCHEMA, decode
from .specification import qualification

LABELS={"Equipment":"设备","Capability":"资格","Product":"产品","BillOfMaterial":"毛坯 BOM","OperationPlan":"工序","WorkOrder":"订单","Batch":"生产批次","MaterialLot":"原料批次","Workpiece":"工件","Execution":"执行","Inspection":"检验","FixturePool":"夹具","AssetState":"运行状态","Policy":"规则","Scenario":"方案","MaterialBalance":"物料结存","FixtureReservation":"夹具占用"}
RELATIONS={"product":"对应产品","order":"属于订单","batch":"属于批次","material_lot":"原料来源","operations":"工艺步骤","capabilities":"设备资格","resource":"加工设备","fixture":"所需夹具","preferred":"首选设备","execution":"当前执行","operation":"执行工序","job":"工件","bom":"物料清单","assigned":"当前指派","location":"当前位置"}

def focused_graph(runtime,focus="",depth=1,limit=40,view="relations",expand=False,relation=""):
    graph=runtime.graph
    if view=="schema":
        from rdflib import Graph, RDF, RDFS, OWL, URIRef
        from rdflib.collection import Collection
        from .common import NS
        import pyoxigraph as ox
        schema=Graph().parse(data=graph.store.dump(format=ox.RdfFormat.TURTLE,from_graph=SCHEMA),format="turtle")
        classes=[c for c in schema.subjects(RDF.type,OWL.Class) if isinstance(c,URIRef) and str(c).startswith(NS)]
        rows=[dict(id=str(c).removeprefix(NS),label=str(schema.value(c,RDFS.label)),kind="概念") for c in classes];edges=[]
        for prop in schema.subjects(RDF.type,OWL.ObjectProperty):
            domain=schema.value(prop,RDFS.domain);target=schema.value(prop,RDFS.range)
            domains=list(Collection(schema,schema.value(domain,OWL.unionOf))) if domain and schema.value(domain,OWL.unionOf) else [domain]
            for source in domains:
                if source in classes and target in classes:edges.append(dict(id=str(prop)+str(source),**{"from":str(source).removeprefix(NS),"to":str(target).removeprefix(NS)},label=str(schema.value(prop,RDFS.label))))
        shown={r['id'] for r in rows[:limit]}
        return dict(nodes=rows[:limit],edges=[e for e in edges if e['from'] in shown and e['to'] in shown],total=len(rows),omitted=max(0,len(rows)-limit),focus=focus,view=view)
    records={r["id"]:r for kind in LABELS for r in graph.entities(kind)}
    if not expand:
        records={k:r for k,r in records.items() if r["type"] not in ("Workpiece","Execution","FixtureReservation","Inspection") or k==focus}
    if not focus or focus not in records:
        focus=next((r["id"] for r in records.values() if r["type"]=="Batch"),next(iter(records),""))
    all_edges=[];adj={k:set() for k in records}
    for r in records.values():
        for key,value in r.items():
            if key not in RELATIONS or relation and key!=relation:continue
            for target in value if isinstance(value,list) else [value]:
                if isinstance(target,Ref) and target in records:
                    all_edges.append(dict(id=r["id"]+"|"+key+"|"+target,**{"from":r["id"],"to":str(target)},label=RELATIONS[key],predicate=key))
                    adj[r["id"]].add(str(target));adj[target].add(r["id"])
    visited={focus};front=deque([(focus,0)])
    while front:
        identity,level=front.popleft()
        if level>=depth:continue
        for neighbor in sorted(adj.get(identity,())):
            if neighbor not in visited:visited.add(neighbor);front.append((neighbor,level+1))
    ordered=[focus]+sorted(visited-{focus});shown=ordered[:limit];ids=set(shown)
    nodes=[]
    for identity in shown:
        row=records[identity];label=row.get("name",identity)
        nodes.append(dict(id=identity,label=label,kind=LABELS.get(row["type"],row["type"]),type=row["type"],focused=identity==focus,
            layer="事理" if row["type"] in ("Product","OperationPlan","BillOfMaterial","Capability","Policy") else "行动" if row["type"] in ("Scenario","Execution") else "事实"))
    return dict(nodes=nodes,edges=[e for e in all_edges if e["from"] in ids and e["to"] in ids],total=len(visited),omitted=max(0,len(visited)-limit),focus=focus,view=view,
                aggregated_workpieces=sum(j["batch"]==focus or j["product"]==focus or j["order"]==focus for j in runtime.jobs()) if not expand else 0)

def lineage(graph,identity,limit=30):
    # Scan lightweight PROV relations to locate only relevant version activities.
    from .graph import iri,AUDIT
    import pyoxigraph as ox
    sequences=set();entity=graph.get(identity)
    if '/revision/' in identity:
        base,revision=identity.rsplit('/revision/',1)
        if revision.isdigit():
            sequence=int(revision);sequences.add(sequence)
            entity=next((r for r in graph.view_at(sequence) if r['id']==base),None)
            if entity:entity={**entity,'immutable_revision':sequence}
    versions=[]
    for target in (iri(identity),iri('record/'+identity)):
        for q in graph.store.quads_for_pattern(None,ox.NamedNode(PROV+"specializationOf"),target,AUDIT):
            versions.append(q.subject);sequences.add(int(q.subject.value.rsplit("/",1)[-1]))
    for target in [iri(identity),*versions]:
        for q in graph.store.quads_for_pattern(None,ox.NamedNode(PROV+"used"),target,AUDIT):
            sequences.add(int(q.subject.value.rsplit("/",1)[-1]))
    result=[]
    for sequence in sorted(sequences,reverse=True)[:limit]:
        q=next(graph.store.quads_for_pattern(iri(f"commit/{sequence:09}"),iri("payload"),None,AUDIT),None)
        if not q:continue
        import json
        payload=json.loads(q.object.value)
        result.append({k:v for k,v in payload.items() if k not in ("changes","provenance")}|{"outputs":[c["after"]["id"] for c in payload["changes"]],"changes":[c for c in payload["changes"] if c["after"]["id"]==identity]})
    raw=[]
    for event in result:
        for source_id in event["used"]:
            row=graph.get(source_id)
            if row and row["type"]=="SourceRecord" and row["id"] not in {r["id"] for r in raw}:raw.append(row)
    return dict(entity=entity,activities=result,sources=raw,total_activities=len(sequences))

def impact(runtime):
    incidents=[]
    for asset in runtime.graph.entities("AssetState"):
        if asset["status"]=="maintenance":
            rid=asset["resource"];affected=[]
            for job in runtime.jobs():
                if job["status"] in ("done","scrapped"):continue
                if job["assigned"]==rid:affected.append(job)
                elif job["status"] in ("pending","transfer") and runtime.operation(job)["preferred"]==rid:affected.append(job)
            incidents.append(dict(id=asset["id"],kind="maintenance",title=rid+" 刀具维护",resource=rid,ends=asset["maintenance_until"],jobs=[j["id"] for j in affected],
                batches=sorted({str(j["batch"]) for j in affected}),orders=sorted({str(j["order"]) for j in affected}),evidence=[asset["id"],"rule/maintenance-v1"],
                explanation="刀具寿命达到阈值，当前工序已完成；等待工件需评估设备资格后改派。"))
    for inspection in runtime.graph.entities("Inspection"):
        if inspection["result"]=="fail":incidents.append(dict(id=inspection["id"],kind="quality",title=str(inspection["batch"])+" 尺寸检验不合格",jobs=[str(inspection["job"])],batches=[str(inspection["batch"])],orders=[],evidence=[inspection["id"],str(inspection["execution"]),str(inspection["material_lot"])],explanation="按产品工艺进入一次返工与复检；超出返工上限报废。"))
    return incidents

"""Bounded business views and evidence traversal over the same ontology records."""
from collections import deque
from pyoxigraph import RdfFormat
from rdflib import Graph, RDF, RDFS, OWL, URIRef, Namespace
from rdflib.collection import Collection
from .common import BASE, NS
from .graph import Ref, PROV, SCHEMA
from .simulation_contract import shape_summary, shapes

LABELS={
    "Equipment":"设备","Capability":"设备资格","Product":"产品版本","BillOfMaterial":"毛坯 BOM",
    "OperationPlan":"工序","WorkOrder":"生产订单","Batch":"生产批次","MaterialLot":"原料批次",
    "Workpiece":"工件","Execution":"实际工序执行","Inspection":"检验结果","FixturePool":"夹具",
    "AssetState":"设备状态","Policy":"决策规则","Scenario":"推演方案","MaterialBalance":"物料结存",
    "FixtureReservation":"夹具占用","RunResult":"运行结果","ScheduledEvent":"计划事件",
    "Checkpoint":"分析基线","Command":"原生执行意图","Clock":"运行时钟",
}
RULE_TYPES={"OperationPlan","BillOfMaterial","Capability","Policy","ProductionPlan","Mapping"}
ACTION_TYPES={"Scenario","Checkpoint","RunResult","Command","ScheduledEvent","BusinessRevision","ArchivedBaseline",
              "ArchivedRun","AdoptionDecision","FieldExecution","SimulationActivity","EvaluationActivity",
              "OutcomeEvaluation","OrderComparison","ModelImprovement","EvidenceCase"}
RELATIONS={"product":"对应产品","order":"属于订单","batch":"属于批次","material_lot":"原料来源",
           "operations":"具有工序","next_operation":"下一道工序","capabilities":"设备资格","resource":"使用设备",
           "fixture":"所需夹具","preferred":"首选设备","execution":"当前执行","operation":"执行工序",
           "job":"关联工件","bom":"物料清单","assigned":"当前指派","location":"当前位置",
           "operation_plan":"执行工序","busy":"占用工件"}

def business_layer(kind):
    # Actual executions and inspection outcomes are observations; plans and
    # unexecuted commands are not presented as things that have happened.
    return "事理" if kind in RULE_TYPES else "行动" if kind in ACTION_TYPES else "事实"

def edge(source,target,key,label=None,derived=False):
    return dict(id=f'{source}|{key}|{target}', **{'from':str(source),'to':str(target)},
                predicate=key,label=label or RELATIONS[key],derived=derived)

def product_route(graph,focus):
    row=graph.get(focus)
    product_id=(row['id'] if row['type']=='Product' else row.get('product')) if row else None
    product=graph.get(product_id) if product_id else None
    if not product:product=next(iter(graph.entities('Product')),None)
    if not product:return None
    steps=sorted((graph.get(i) for i in product['operations']),key=lambda r:r['index'])
    return dict(product=product['id'],name=product['name'],steps=[{
        'id':r['id'],'name':r['name'],'index':r['index'],'duration_s':r['duration_s'],'preferred':str(r['preferred']),
        'operation':r['operation'],'next_operation':str(r.get('next_operation') or
            (steps[i+1]['id'] if i+1<len(steps) and product.get('route_encoding')!='linked' else ''))
    } for i,r in enumerate(steps)],linked=product.get('route_encoding')=='linked',
        rework_to=steps[product['rework_index']]['id'])

def schema_definition(graph):
    schema=Graph().parse(data=graph.store.dump(format=RdfFormat.TURTLE,from_graph=SCHEMA),format="turtle")
    for name in ("simulation.ttl", "evidence.ttl"):
        schema.parse(BASE/"ontology/v2"/name,format="turtle")
    return schema


def concept_key(term):
    if str(term).startswith(PROV):return "prov:"+str(term).removeprefix(PROV)
    if str(term).startswith("http://www.w3.org/ns/sosa/"):return "sosa:"+str(term).rsplit("/",1)[1]
    return str(term).removeprefix(NS)


def concept_details(graph,identity):
    schema=schema_definition(graph)
    term=URIRef(PROV+identity[5:] if identity.startswith("prov:") else
                "http://www.w3.org/ns/sosa/"+identity[5:] if identity.startswith("sosa:") else NS+identity)
    if identity.startswith("prov:"):
        schema.parse(BASE/"ontology/v2/prov-o.ttl",format="turtle")
    elif identity.startswith("sosa:") and (None,RDFS.subClassOf,term) in schema:
        return {"概念 URI":str(term),"定义名称":"观测","标准":"W3C SOSA"}, []
    if (term,RDF.type,OWL.Class) not in schema:raise ValueError("本体概念不存在")
    matches=Namespace("http://www.w3.org/2004/02/skos/core#")
    attrs={"概念 URI":str(term),"定义名称":str(schema.value(term,RDFS.label) or identity)}
    if comment:=schema.value(term,RDFS.comment):attrs["业务定义"]=str(comment)
    if parents:=[str(p) for p in schema.objects(term,RDFS.subClassOf) if isinstance(p,URIRef)]:attrs["正式父类"]=parents
    if mapped:=[str(p) for p in schema.objects(term,matches.closeMatch)]:attrs["近似标准映射"]=" · ".join(mapped)
    return attrs, shape_summary(shapes(),term)


def schema_view(graph,focus,limit,depth):
    schema=schema_definition(graph)
    local={c for c in schema.subjects(RDF.type,OWL.Class) if isinstance(c,URIRef) and str(c).startswith(NS)}
    prov=Graph().parse(BASE/"ontology/v2/prov-o.ttl",format="turtle")
    parents={parent for c in local for parent in schema.objects(c,RDFS.subClassOf) if isinstance(parent,URIRef)}
    # Render the formal standard hierarchy reached from the workbench vocabulary.
    pending=list(parents)
    while pending:
        for parent in prov.objects(pending.pop(),RDFS.subClassOf):
            if isinstance(parent,URIRef) and parent not in parents:parents.add(parent);pending.append(parent)
    classes=local|parents
    key=concept_key
    aliases={"prov:Entity":"证据实体","prov:Activity":"活动","prov:Agent":"责任主体",
             "prov:Plan":"计划","sosa:Observation":"观测"}
    rules=shapes()
    nodes={key(c):dict(id=key(c),label=str(schema.value(c,RDFS.label) or aliases.get(key(c)) or prov.value(c,RDFS.label) or key(c)),
                       kind="制造与证据概念" if c in local else "标准概念",
                       layer=business_layer(key(c)) if c in local else "标准",
                       definition=str(schema.value(c,RDFS.comment) or ""),constraint_count=len(shape_summary(rules,c))) for c in classes}
    edges=[]
    def members(term):
        union=schema.value(term,OWL.unionOf) if term else None
        return list(Collection(schema,union)) if union else [term]
    for prop in set(schema.subjects(RDF.type,OWL.ObjectProperty)):
        for domain in schema.objects(prop,RDFS.domain):
            for target in schema.objects(prop,RDFS.range):
                for source in members(domain):
                    for destination in members(target):
                        if source in classes and destination in classes:
                            edges.append(edge(key(source),key(destination),key(prop),str(schema.value(prop,RDFS.label) or key(prop))))
    for source in classes:
        for target in set(schema.objects(source,RDFS.subClassOf))|set(prov.objects(source,RDFS.subClassOf)):
            if target in classes:edges.append(edge(key(source),key(target),"rdfs:subClassOf","是其子类"))
    core=('Product','OperationPlan','Equipment','Capability','Batch','WorkOrder','Execution','Workpiece','MaterialLot','BillOfMaterial','Inspection','AssetState','ScheduledEvent','Scenario','FixturePool')
    root=focus if focus in nodes else 'Product'
    adj={k:set() for k in nodes}
    for e in edges:adj[e['from']].add(e['to']);adj[e['to']].add(e['from'])
    seen={root};ordered=[];queue=deque([(root,0)])
    while queue and len(ordered)<min(limit,18):
        node,level=queue.popleft()
        if node not in nodes:continue
        ordered.append(node)
        if level>=depth:continue
        for item in sorted(adj[node],key=lambda k:(k not in core,k)):
            if item not in seen:seen.add(item);queue.append((item,level+1))
    shown=set(ordered)
    return dict(nodes=[{**nodes[i],'focused':i==root} for i in ordered],
        edges=sorted((e for e in edges if e['from'] in shown and e['to'] in shown),key=lambda e:e['id']),
        glossary=[nodes[i] for i in sorted(nodes)],total=len(seen),omitted=len(seen)-len(shown),focus=root,view='schema')

def focused_graph(runtime,focus="",depth=1,limit=40,view="relations",expand=False,relation=""):
    graph=runtime.graph
    if view=="schema":return schema_view(graph,focus,limit,depth)
    if view=='impact' and not focus:
        focus=next((r['id'] for r in graph.find('AssetState',status='maintenance')),'')
    route=product_route(graph,focus)
    if view=='route':return dict(route=route,view=view,nodes=[],edges=[])
    kinds=set(LABELS)-{'ScheduledEvent'}
    if not expand:kinds-={'Workpiece','Execution','FixtureReservation','Inspection'}
    records={r['id']:r for kind in sorted(kinds) for r in graph.entities(kind)}
    focused=graph.get(focus)
    if focused and focused.get('type') in LABELS:records[focus]=focused
    if focus not in records:focus=route['product'] if route else next(iter(records),'')
    if not focus:return dict(nodes=[],edges=[],total=0,omitted=0,focus='',view=view)
    all_edges=[];adj={k:set() for k in records}
    def add(source,target,key,derived=False,label=None):
        if target in records and (not relation or key==relation):
            all_edges.append(edge(source,target,key,label,derived))
            adj[source].add(str(target));adj[target].add(source)
    for r in records.values():
        for key,value in r.items():
            if key in RELATIONS:
                for target in value if isinstance(value,list) else [value]:
                    if isinstance(target,Ref):add(r['id'],target,'operation_plan' if key=='operation' and r['type']=='Execution' else key)
    if route and not route['linked']:
        for step in route['steps']:
            if step['id'] in records and step['next_operation']:
                add(step['id'],step['next_operation'],'next_operation',True)
    if view=='impact':
        # Show the decision rules tied to the selected event, with explicit view provenance.
        for r in list(records.values()):
            if r['type']=='AssetState' and r['status']=='maintenance':
                add(r['id'],'rule/maintenance-v1','rule',True,'适用维护规则')
                add(r['id'],'policy','action',True,'评估排程策略')
    if 'analysis_basis' in records:
        add('analysis_basis','policy','action',True,'应用排程策略')
        for r in records.values():
            if r['type']=='RunResult':add(r['id'],'analysis_basis','action',True,'核对方案结果')
    visited={focus};ordered=[focus];front=deque([(focus,0)])
    while front:
        identity,level=front.popleft()
        if level>=depth:continue
        for neighbor in sorted(adj[identity],key=lambda k:(records[k]['type'] not in ('Product','OperationPlan','AssetState'),records[k].get('index',0),k)):
            if neighbor not in visited:visited.add(neighbor);ordered.append(neighbor);front.append((neighbor,level+1))
    shown=ordered[:limit];ids=set(shown)
    nodes=[dict(id=i,label=records[i].get('name',i),kind=LABELS[records[i]['type']],type=records[i]['type'],focused=i==focus,
                layer='行动' if view=='impact' and i=='policy' else business_layer(records[i]['type']),
                status=records[i].get('status')) for i in shown]
    return dict(nodes=nodes,edges=sorted((e for e in all_edges if e['from'] in ids and e['to'] in ids),key=lambda e:e['id']),
                total=len(visited),omitted=max(0,len(visited)-limit),focus=focus,view=view,sequence=graph.sequence,
                aggregated_workpieces=sum(j['batch']==focus or j['product']==focus or j['order']==focus for j in runtime.jobs()) if not expand else 0)

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
        for source_id in (*event["used"], *event.get('details',{}).get('source_records',[])):
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

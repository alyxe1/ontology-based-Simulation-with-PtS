from __future__ import annotations
import re
from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator
from rdflib import Graph
from pyshacl import validate
from .graph import Ref, STATE, iri
from .common import BASE, NS, digest

class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, validate_default=True)

class Capability(Strict):
    operation: str
    materials: list[str]
    tolerance_um: float = Field(gt=0)

    @field_validator("materials")
    @classmethod
    def material_set(cls, value): return sorted(set(value))

class Resource(Strict):
    id: str
    name: str
    capabilities: list[Capability]
    speed: float = Field(default=1, gt=0)
    shift_hours: float = Field(default=8, gt=0, le=24)
    tool_limit: float = Field(default=0, ge=0)
    maintenance_s: float = Field(default=2700, gt=0)
    setup_s: float = Field(default=0, ge=0)
    mes_id: str
    opc_id: str

class Operation(Strict):
    id: str
    name: str
    operation: str
    duration_s: float = Field(gt=0)
    tolerance_um: float = Field(gt=0)
    preferred: str
    fixture: str = ""
    tool: str = ""
    wear: float = Field(default=0, ge=0)

class Product(Strict):
    id: str
    name: str
    version: int = Field(default=1, ge=1)
    material: str
    family: str
    blank_kg: float = Field(gt=0)
    finished_kg: float = Field(gt=0)
    operations: list[Operation] = Field(min_length=1)
    rework_index: int = Field(default=1, ge=0)
    reject_probability: float = Field(default=.08, ge=0, le=1)

class Order(Strict):
    id: str
    product: str
    quantity: int = Field(gt=0, le=1000)
    batch_size: int = Field(default=10, gt=0)
    due_s: float = Field(gt=0)
    release_s: float = Field(default=0, ge=0)
    material_lot: str

class Fixture(Strict):
    id: str
    name: str
    quantity: int = Field(gt=0)

class MaterialLot(Strict):
    id: str
    material: str
    quantity: int = Field(gt=0)
    supplier: str

class ProductionSpec(Strict):
    version: str = "2.0"
    name: str = "柔性机加工车间 · Cell 01"
    horizon_s: float = Field(default=129600, gt=0, le=604800)
    transfer_s: float = Field(default=30, ge=0)
    seed: int = 5202
    resources: list[Resource] = Field(min_length=1, max_length=30)
    products: list[Product] = Field(min_length=1)
    orders: list[Order] = Field(min_length=1)
    fixtures: list[Fixture] = []
    material_lots: list[MaterialLot]

    @model_validator(mode="after")
    def consistent(self):
        groups = [self.resources, self.products, self.orders, self.fixtures, self.material_lots]
        all_ids=[x.id for group in groups for x in group]
        if len(all_ids)!=len(set(all_ids)):raise ValueError("跨业务类型的对象编号不得重复")
        for group in groups:
            ids = [x.id for x in group]
            if len(ids) != len(set(ids)) or any(not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", x) for x in ids):
                raise ValueError("对象编号必须唯一且仅包含字母、数字、下划线或短横线")
        resources = {r.id for r in self.resources}; products = {p.id:p for p in self.products}
        fixtures = {f.id for f in self.fixtures}; lots = {m.id:m for m in self.material_lots}
        if sum(o.quantity for o in self.orders) > 1000: raise ValueError("单工作空间最多 1000 件")
        for p in self.products:
            if p.finished_kg > p.blank_kg or p.rework_index >= len(p.operations): raise ValueError("产品物料重量或返工工序无效")
            if len({op.id for op in p.operations}) != len(p.operations): raise ValueError("工序编号重复")
            for op in p.operations:
                if op.preferred not in resources or (op.fixture and op.fixture not in fixtures): raise ValueError("工序引用设备或夹具不存在")
                eligible = [r.id for r in self.resources if any(c.operation == op.operation and p.material in c.materials and c.tolerance_um <= op.tolerance_um for c in r.capabilities)]
                if op.preferred not in eligible: raise ValueError(f"{p.id}/{op.id} 首选设备不具备材料、工序或精度资格")
        for o in self.orders:
            if o.product not in products or o.material_lot not in lots: raise ValueError("订单引用产品或原料批次不存在")
            if lots[o.material_lot].material != products[o.product].material: raise ValueError("原料批次材料与产品不符")
        return self

def default_spec():
    def cap(op, tol=100, mats=None): return dict(operation=op, tolerance_um=tol, materials=mats or ["steel","aluminium"])
    resources=[]
    for rid,name,caps,speed,limit,setup in [
        ("SAW","下料锯床",[cap("cut")],1,0,0),
        ("CNC01","CNC 01 · 精密加工",[cap("mill",10),cap("drill",10)],1,8,120),
        ("CNC02","CNC 02 · 通用加工",[cap("mill",25),cap("drill",25)],1.15,18,180),
        ("CNC03","CNC 03 · 铝件加工",[cap("mill",10,["aluminium"]),cap("drill",10,["aluminium"])],.85,20,90),
        ("DEBURR","去毛刺工作站",[cap("deburr")],1,0,0),
        ("CMM","三坐标检测",[cap("inspect",5)],1,0,0)]:
        resources.append(dict(id=rid,name=name,capabilities=caps,speed=speed,tool_limit=limit,setup_s=setup,mes_id="MES-"+rid,opc_id="PLC/Cell01/"+rid))
    products=[]
    for pid,name,mat,tol,dur in [("P1","液压阀体","steel",10,240),("P2","传动支座","steel",25,300),("P3","铝合金壳体","aluminium",10,280)]:
        ops=[dict(id="CUT",name="毛坯下料",operation="cut",duration_s=50,tolerance_um=100,preferred="SAW")]
        if pid=="P1":
            ops.append(dict(id="ROUGH",name="粗铣",operation="mill",duration_s=190,tolerance_um=25,preferred="CNC02",fixture="FIX_STEEL",tool="ENDMILL",wear=.6))
        ops.append(dict(id="MILL",name="精铣" if pid=="P1" else "轮廓铣削",operation="mill",duration_s=dur,tolerance_um=tol,preferred="CNC01",fixture="FIX_AL" if mat=="aluminium" else "FIX_STEEL",tool="ENDMILL",wear=1))
        if pid!="P1": ops.append(dict(id="DRILL",name="孔系加工",operation="drill",duration_s=110,tolerance_um=tol,preferred="CNC03" if pid=="P3" else "CNC02",fixture="FIX_AL" if mat=="aluminium" else "FIX_STEEL",tool="DRILL",wear=.4))
        ops += [dict(id="DEBURR",name="去毛刺",operation="deburr",duration_s=75,tolerance_um=100,preferred="DEBURR"),dict(id="INSPECT",name="尺寸检验",operation="inspect",duration_s=90,tolerance_um=tol,preferred="CMM")]
        products.append(dict(id=pid,name=name,material=mat,family=pid,blank_kg=1.2 if mat=="steel" else .8,finished_kg=.8 if mat=="steel" else .5,operations=ops,rework_index=2 if pid=="P1" else 1))
    orders=[dict(id=f"WO{i+1:03}",product=f"P{i%3+1}",quantity=20,batch_size=10,due_s=[27000,22000,25000,98000,94000,100000][i],release_s=0 if i<3 else 1200,material_lot="LOT_AL" if i%3==2 else "LOT_STEEL") for i in range(6)]
    return ProductionSpec(resources=resources,products=products,orders=orders,
        fixtures=[dict(id="FIX_STEEL",name="钢件通用夹具",quantity=2),dict(id="FIX_AL",name="铝件定位夹具",quantity=2)],
        material_lots=[dict(id="LOT_STEEL",material="steel",quantity=85,supplier="SUP_STEEL"),dict(id="LOT_AL",material="aluminium",quantity=45,supplier="SUP_AL")])

def spec_records(spec):
    rows=[dict(id="configuration",type="ProductionPlan",name=spec.name,version=spec.version,horizon_s=spec.horizon_s,transfer_s=spec.transfer_s,seed=spec.seed,spec_hash=digest(spec.model_dump()))]
    for ordinal,r in enumerate(spec.resources):
        row=r.model_dump(exclude={"capabilities"}); row.update(type="Equipment",capabilities=[],ordinal=ordinal)
        for i,c in enumerate(r.capabilities):
            cid=f"{r.id}/cap/{i}"; row["capabilities"].append(Ref(cid)); rows.append(dict(id=cid,type="Capability",**c.model_dump()))
        rows.append(row)
    for ordinal,p in enumerate(spec.products):
        row=p.model_dump(exclude={"operations"});row.update(type="Product",operations=[],bom=Ref(p.id+"/bom"),ordinal=ordinal,route_encoding="linked")
        rows.append(dict(id=p.id+"/bom",type="BillOfMaterial",product=Ref(p.id),material=p.material,quantity=1,unit="piece",blank_kg=p.blank_kg,finished_kg=p.finished_kg))
        for i,op in enumerate(p.operations):
            oid=f"{p.id}/{op.id}";row["operations"].append(Ref(oid));o=op.model_dump();o["id"]=oid
            o.update(type="OperationPlan",index=i,product=Ref(p.id),material=p.material,preferred=Ref(op.preferred))
            if i+1<len(p.operations):o['next_operation']=Ref(f'{p.id}/{p.operations[i+1].id}')
            if op.fixture:o["fixture"]=Ref(op.fixture)
            rows.append(o)
        rows.append(row)
    for ordinal,o in enumerate(spec.orders):
        row=o.model_dump();row.update(type="WorkOrder",product=Ref(o.product),material_lot=Ref(o.material_lot),ordinal=ordinal);rows.append(row)
    for ordinal,f in enumerate(spec.fixtures): rows.append(dict(type="FixturePool",ordinal=ordinal,**f.model_dump()))
    for ordinal,lot in enumerate(spec.material_lots): rows.append(dict(type="MaterialLot",ordinal=ordinal,**lot.model_dump()))
    return rows

def spec_from_graph(graph):
    config=graph.get("configuration")
    def fields(record, model): return {k:v for k,v in record.items() if k in model.model_fields}
    def items(kind):return sorted(graph.entities(kind),key=lambda r:r.get("ordinal",0))
    resources=[];products=[]
    for r in items("Equipment"):
        row=fields(r,Resource);row["capabilities"]=[fields(graph.get(c),Capability) for c in sorted(r["capabilities"])];resources.append(row)
    for p in items("Product"):
        row=fields(p,Product);row["operations"]=[]
        for op in sorted([graph.get(i) for i in p["operations"]],key=lambda r:r["index"]):
            data=fields(op,Operation);data["id"]=op["id"].split("/")[-1];row["operations"].append(data)
        products.append(row)
    return ProductionSpec(**fields(config,ProductionSpec), resources=resources,products=products,
        orders=[fields(r,Order) for r in items("WorkOrder")],fixtures=[fields(r,Fixture) for r in items("FixturePool")],material_lots=[fields(r,MaterialLot) for r in items("MaterialLot")])

def qualification(graph, operation_id):
    op=graph.get(operation_id)
    if not op or op.get("type")!="OperationPlan":raise ValueError("工序不存在")
    query=f'''PREFIX os: <{NS}> SELECT DISTINCT ?machine WHERE {{ GRAPH {STATE} {{
      {iri(operation_id)} os:operation ?operation; os:material ?material; os:tolerance_um ?required .
      ?machine a os:Equipment; os:capabilities ?cap .
      ?cap os:operation ?operation; os:materials ?material; os:tolerance_um ?achievable .
      FILTER (?achievable <= ?required) }} }}'''
    from .graph import decode
    qualified={str(decode(row["machine"])) for row in graph.store.query(query)}
    result=[]
    for r in graph.entities("Equipment"):
        caps=[graph.get(c) for c in r["capabilities"]]
        checks={"工序类型":any(c["operation"]==op["operation"] for c in caps),
                "材料":any(c["operation"]==op["operation"] and op["material"] in c["materials"] for c in caps),
                "精度":any(c["operation"]==op["operation"] and c["tolerance_um"]<=op["tolerance_um"] for c in caps)}
        result.append(dict(resource=r["id"],name=r["name"],eligible=r["id"] in qualified,checks=checks,
            reason="工序、材料和精度要求均满足" if r["id"] in qualified else ("、".join(k+"不匹配" for k,v in checks.items() if not v) or "没有同一资格同时满足材料和精度组合"),
            evidence=[operation_id,*r["capabilities"]],rule="rule/qualification-v1"))
    return result

def validate_ontology(graph):
    data=Graph()
    data.parse(data=graph.store.dump(format=__import__('pyoxigraph').RdfFormat.TURTLE,from_graph=STATE),format="turtle")
    conforms,report,text=validate(data,shacl_graph=str(BASE/"ontology/v2/shapes.ttl"),inference="rdfs")
    from rdflib import Namespace, RDF
    sh=Namespace('http://www.w3.org/ns/shacl#')
    issues=[]
    for result in report.subjects(RDF.type,sh.ValidationResult):
        issues.append({key:str(report.value(result,prop) or '').removeprefix('urn:ontologysim:v2:')
                       for key,prop in [('object',sh.focusNode),('path',sh.resultPath),('message',sh.resultMessage)]})
    return {"conforms":bool(conforms),"report":text,"issues":issues,"checks":["引用及数量","工艺顺序及首选设备资格","执行时间和设备状态","物料与夹具字段"]}

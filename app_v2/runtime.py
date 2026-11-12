"""Event processing against authoritative ontology objects, no shadow state loop."""
from __future__ import annotations
import hashlib
import math
from .common import BASE, EPS, digest, uid
from .graph import OntologyStore, Ref
from .specification import spec_records, qualification, validate_ontology

class OntologyRuntime:
    def __init__(self, graph):self.graph=graph

    @property
    def config(self):return self.graph.get("configuration")
    @property
    def clock(self):return self.graph.get("clock")
    @property
    def time(self):return self.clock["time_s"]
    @property
    def policy(self):return self.graph.get("policy")
    def jobs(self):return self.graph.entities("Workpiece")
    def pending_events(self):return self.graph.find("ScheduledEvent",status="pending")
    def assert_restorable(self):
        unresolved=[c["id"] for c in self.graph.entities("Command") if c["status"]!="acknowledged"]
        if unresolved:raise ValueError("执行状态待核对：存在未确认原生命令 "+", ".join(unresolved[:5]))
    def operation(self,job):
        p=self.graph.get(job["product"])
        return sorted([self.graph.get(op) for op in p["operations"]],key=lambda op:op["index"])[job["op_index"]]

    @classmethod
    def create(cls,graph,spec,actor="service/factory-generator",seed=None,used=()):
        if graph.get("configuration"):raise ValueError("运行本体已初始化")
        graph.schema(BASE/"ontology/v2/core.ttl")
        rows=spec_records(spec)
        rows.append(dict(id='ontology_contract',type='OntologyVersion',version='2.0',schema_hash=graph.schema_digest(),execution_program='ontologysim-v02',random_algorithm='sha256-paired-v1'))
        rows += [dict(id="clock",type="Clock",time_s=0.,boundary=0,queue_sequence=0,seed=spec.seed if seed is None else seed,source="synthetic",status="ready"),
                 dict(id="policy",type="Policy",rule="FIFO",reroute=False,shift_hours=0.,name="基准 FIFO",window_start_s=0.),
                 dict(id="rule/qualification-v1",type="Policy",name="工序、材料、精度资格规则",version="1"),
                 dict(id="rule/maintenance-v1",type="Policy",name="刀具达到寿命阈值，当前工序完成后维护",version="1")]
        rows += [dict(id="mapping/MES-v1",type="Mapping",name="MES 设备编号对齐",version="1",rule="Equipment.mes_id → Equipment.id"),
                 dict(id="mapping/OPC-v1",type="Mapping",name="OPC 设备编号及时间单位对齐",version="1",rule="Equipment.opc_id → Equipment.id; event_time(ms) / 1000 → time_s(s)")]
        for r in spec.resources:rows.append(dict(id=r.id+"/state",type="AssetState",resource=Ref(r.id),status="idle",busy="",family="",tool_count=0.,maintenance_until=0.))
        for lot in spec.material_lots:rows.append(dict(id=lot.id+"/balance",type="MaterialBalance",material_lot=Ref(lot.id),available=lot.quantity,consumed=0))
        seq=0
        for order in spec.orders:
            for b in range(math.ceil(order.quantity/order.batch_size)):
                bid=f"{order.id}_B{b+1:02}";quantity=min(order.batch_size,order.quantity-b*order.batch_size)
                rows.append(dict(id=bid,type="Batch",name=f"{order.id} / 批次 {b+1}",order=Ref(order.id),product=Ref(order.product),material_lot=Ref(order.material_lot),quantity=quantity))
                for _ in range(quantity):
                    seq+=1;jid=f"J{seq:05}"
                    rows += [dict(id=jid,type="Workpiece",order=Ref(order.id),product=Ref(order.product),batch=Ref(bid),material_lot=Ref(order.material_lot),op_index=0,status="pending",location="unreleased",assigned="",queue_seq=0,attempt=0,execution="",released_at=-1.,completed_at=-1.),
                             dict(id=jid+"/release",type="ScheduledEvent",event_kind="release",due_s=order.release_s,status="pending",job=Ref(jid),resource="",priority=30)]
        graph.commit(rows,"factory_import",actor=actor,used=used,reason="来源：版本化工厂生成配置；制造对象及运行状态均实例化为 RDF")
        check=validate_ontology(graph)
        if not check["conforms"]:raise ValueError(check["report"])
        return cls(graph)

    def sample(self,job,op,purpose="duration"):
        key=f"{self.clock['seed']}:{job['id']}:{op['id']}:{job['attempt']}:{purpose}"
        return int.from_bytes(hashlib.sha256(key.encode()).digest()[:8],"big")/(2**64-1)

    def event(self,identity,kind,due,job="",resource="",priority=20):
        return dict(id=identity,type="ScheduledEvent",event_kind=kind,due_s=round(float(due),6),status="pending",job=Ref(job) if job else "",resource=Ref(resource) if resource else "",priority=priority)

    def update(self,changes,kind,used=(),reason="",details=None):
        return self.graph.commit(changes,kind,time_s=self.time,used=used,reason=reason,details=details)

    def command(self,native,kind,call,changes,*,used=(),reason=""):
        cid=uid("command")
        intent=dict(id=cid,type="Command",kind=kind,status="prepared",expected_revision=self.graph.sequence)
        self.update([intent],"command_prepared",used,reason)
        try:
            call()
        except Exception as ex:
            intent.update(status="unknown",error=str(ex))
            self.update([intent],"command_unknown",used,"原生执行结果无法确认；禁止自动重放")
            raise
        intent["status"]="acknowledged"
        self.update([*changes,intent],kind,used,reason)

    def select_resource(self,job,op):
        choices=[r["resource"] for r in qualification(self.graph,op["id"]) if r["eligible"]]
        override=self.graph.get("assignment/"+job["id"])
        if override and override["op_index"]==job["op_index"]:return override["resource"]
        if not self.policy["reroute"]:return op["preferred"]
        def score(rid):
            resource=self.graph.get(rid);asset=self.graph.get(rid+"/state")
            queue=[j for j in self.jobs() if j["assigned"]==rid and j["status"]=="queued"]
            busy=0.
            if asset["busy"]:
                j=self.graph.get(asset["busy"]);x=self.graph.get(j["execution"]);busy=max(0,x["end_s"]-self.time)
            setup=resource["setup_s"] if asset["family"]!=self.graph.get(job["product"])["family"] else 0.
            return max(0,asset["maintenance_until"]-self.time)+busy+len(queue)*op["duration_s"]*resource["speed"]+setup,rid
        return min(choices,key=score)

    def release_or_arrive(self,native,event):
        job=self.graph.get(event["job"]);op=self.operation(job);rid=self.select_resource(job,op)
        clock=self.clock;clock["queue_sequence"]+=1
        changes=[]
        if event["event_kind"]=="release":
            balance=self.graph.get(job["material_lot"]+"/balance")
            if balance["available"]<=0:
                event.update(status="cancelled");job.update(status="material_wait",location="material_wait")
                self.update([event,job],"material_shortage",[job["material_lot"]],"原料不足，禁止创建工件和消耗负库存");return
            balance["available"]-=1;balance["consumed"]+=1;changes.append(balance);job["released_at"]=self.time
        source=job["status"]
        job.update(status="queued",location=Ref(rid),assigned=Ref(rid),queue_seq=clock["queue_sequence"],queued_at=self.time,execution="")
        event["status"]="completed"
        self.command(native,"piece_released" if source=="pending" else "transfer_finished",
            lambda:native.create(job["id"],rid) if source=="pending" else native.move(job["id"],"transit",rid+":queue"),
            [job,event,clock,*changes],used=[job["batch"],op["id"],rid],reason="按工艺路线和设备资格进入等待队列")

    def start_job(self,native,job,rid,from_setup=False):
        op=self.operation(job);resource=self.graph.get(rid);asset=self.graph.get(rid+"/state");product=self.graph.get(job["product"])
        candidate=next((c for c in qualification(self.graph,op["id"]) if c["resource"]==rid),None)
        if not candidate or not candidate["eligible"]:
            reason=candidate["reason"] if candidate else "设备不在本体资格集合中"
            self.update([],"dispatch_rejected",[job["id"],op["id"],rid],reason)
            raise ValueError(reason)
        if asset["maintenance_until"]>self.time+EPS:raise ValueError("维护期间禁止开工")
        setup=not from_setup and resource["setup_s"]>0 and asset["family"]!=product["family"]
        kind="setup" if setup else "processing"
        duration=resource["setup_s"] if setup else round(op["duration_s"]*resource["speed"]*(.92+.16*self.sample(job,op)),6)
        xid=f"{job['id']}/op{job['op_index']}/try{job['attempt']}/{kind}"
        execution=dict(id=xid,type="Execution",kind=kind,job=Ref(job["id"]),resource=Ref(rid),operation=Ref(op["id"]),start_s=self.time,end_s=round(self.time+duration,6),duration_s=duration,status="active",attempt=job["attempt"],sample_algorithm="sha256-paired-v1")
        execution["queue_entered_s"]=self.time if from_setup else job.get("queued_at",self.time)
        event=self.event(xid+"/end","setup_finished" if setup else "process_finished",execution["end_s"],job["id"],rid,10)
        changes=[execution,event]
        if op["fixture"] and not from_setup:
            changes.append(dict(id=job["id"]+"/fixture",type="FixtureReservation",job=Ref(job["id"]),fixture=Ref(op["fixture"]),resource=Ref(rid),status="reserved"))
        job.update(status=kind,location=Ref(rid),assigned=Ref(rid),execution=Ref(xid));asset.update(status=kind,busy=Ref(job["id"]))
        self.command(native,kind+"_started",lambda:native.start(job["id"],rid,duration,from_machine=from_setup),[job,asset,*changes],
                     used=[op["id"],rid,"policy","rule/qualification-v1"],reason="资格、班次、刀具和夹具约束通过；采用 "+self.policy["rule"])

    def finish(self,native,event):
        job=self.graph.get(event["job"]);rid=event["resource"];asset=self.graph.get(rid+"/state");resource=self.graph.get(rid);op=self.operation(job);product=self.graph.get(job["product"])
        if not native.finished(rid):raise RuntimeError("没有原生完工证据，禁止推进工艺")
        execution=self.graph.get(job["execution"]);execution.update(status="completed",actual_end_s=self.time);event["status"]="completed"
        if event["event_kind"]=="setup_finished":
            asset["family"]=product["family"]
            self.update([execution,event,asset],"setup_finished",[execution["id"]],"原生换型时长完成")
            self.start_job(native,job,rid,from_setup=True);return
        changes=[execution,event]
        reservation=self.graph.get(job["id"]+"/fixture")
        if reservation and reservation["status"]=="reserved":reservation["status"]="released";changes.append(reservation)
        asset.update(busy="",status="idle",tool_count=round(asset["tool_count"]+op["wear"],6))
        if resource["tool_limit"] and asset["tool_count"]>=resource["tool_limit"]-EPS:
            asset.update(status="maintenance",maintenance_until=round(self.time+resource["maintenance_s"],6))
            changes.append(self.event(f"{rid}/maintenance/{self.clock['boundary']}","maintenance_finished",asset["maintenance_until"],resource=rid,priority=15))
        changes.append(asset)
        failed=False
        if op["operation"]=="inspect":
            failed=self.sample(job,op,"quality")<product["reject_probability"]
            changes.append(dict(id=f"{job['id']}/inspection/{job['attempt']}",type="Inspection",job=Ref(job["id"]),batch=job["batch"],material_lot=job["material_lot"],execution=Ref(execution["id"]),result="fail" if failed else "pass",time_s=self.time))
        if failed and job["attempt"]==0:
            job.update(attempt=1,op_index=product["rework_index"])
        elif failed:
            job.update(status="scrapped",location="scrap",assigned="",completed_at=self.time,execution="")
        elif product.get('route_encoding')=='linked':
            successor=op.get('next_operation')
            if successor:
                next_op=self.graph.get(successor)
                if not next_op or next_op['product']!=product['id'] or next_op['index']!=op['index']+1:raise ValueError('工艺后继与已核对路线不一致')
                job['op_index']=next_op['index']
            elif op['index']==len(product['operations'])-1:job['op_index']=len(product['operations'])
            else:raise ValueError('非末道工序缺少后继')
        else:job["op_index"]+=1
        if job["status"]!="scrapped" and job["op_index"]>=len(product["operations"]):
            job.update(status="done",location="finished",assigned="",completed_at=self.time,execution="")
        if job["status"] in ("done","scrapped"):
            target="finished" if job["status"]=="done" else "scrap"
        else:
            target="transit";job.update(status="transfer",location="transit",assigned="",execution="",ready_at=round(self.time+self.config["transfer_s"],6))
            changes.append(self.event(f"{job['id']}/transfer/{job['op_index']}/{job['attempt']}","transfer_finished",self.time+self.config["transfer_s"],job["id"],priority=25))
        changes.append(job)
        self.command(native,"operation_completed",lambda:native.move(job["id"],rid+":machine",target),changes,
                     used=[execution["id"],job["batch"],"rule/maintenance-v1"],reason="原生完工后更新工艺、检验、夹具及刀具；维护不抢占当前工件")

    def settle(self,native):
        for _ in range(2000):
            due=sorted([e for e in self.pending_events() if e["due_s"]<=self.time+EPS],key=lambda e:(e["priority"],e["due_s"],e["id"]))
            if not due:break
            for event in due:
                if event["event_kind"] in ("setup_finished","process_finished"):self.finish(native,event)
                elif event["event_kind"] in ("release","transfer_finished"):self.release_or_arrive(native,event)
                elif event["event_kind"]=="maintenance_finished":
                    asset=self.graph.get(event["resource"]+"/state");asset.update(status="idle",maintenance_until=0.,tool_count=0.);event["status"]="completed"
                    self.update([asset,event],"maintenance_finished",["rule/maintenance-v1"],"维护时长完成，刀具寿命复位")
        else:raise RuntimeError("语义事件未收敛")
        for resource in self.graph.entities("Equipment"):
            rid=resource["id"];asset=self.graph.get(rid+"/state")
            hours=self.policy["shift_hours"] or resource["shift_hours"]
            if asset["busy"] or asset["maintenance_until"]>self.time+EPS or self.time%86400>=hours*3600-EPS or self.time>=self.config["horizon_s"]-EPS:continue
            jobs=[j for j in self.jobs() if j["status"]=="queued" and j["assigned"]==rid]
            def key(j):return (self.graph.get(j["order"])["due_s"] if self.policy["rule"]=="EDD" else 0,j["queue_seq"],j["id"])
            for job in sorted(jobs,key=key):
                op=self.operation(job)
                if op["fixture"]:
                    occupied=self.graph.find("FixtureReservation",fixture=op["fixture"],status="reserved")
                    if len(occupied)>=self.graph.get(op["fixture"])["quantity"]:continue
                self.start_job(native,job,rid);break

    def advance(self,native):
        at=native.step(self)
        clock=self.clock;clock.update(time_s=at,boundary=clock["boundary"]+1,status="running")
        self.graph.commit([clock],"native_clock_advanced",time_s=at,reason="时钟由 Plant Simulation 原生事件推进")
        self.settle(native)

    def apply_policy(self,native,patch,actor="service/scenario"):
        policy=self.policy
        policy.update(name=patch.get("name","方案"),rule=patch.get("rule","FIFO"),reroute=patch.get("reroute",False),shift_hours=float(patch.get("shift_hours",0)),window_start_s=self.time)
        if policy["rule"] not in ("FIFO","EDD") or not 0<=policy["shift_hours"]<=24:raise ValueError("方案策略参数无效")
        assignments=[]
        for jid,rid in patch.get("assignments",{}).items():
            job=self.graph.get(jid)
            if not job or job.get("status")!="queued":raise ValueError("仅允许改派等待中的工件")
            op=self.operation(job)
            candidate=next((x for x in qualification(self.graph,op["id"]) if x["resource"]==rid),None)
            if not candidate or not candidate["eligible"]:raise ValueError(candidate["reason"] if candidate else "设备不存在")
            assignments.append(dict(id="assignment/"+jid,type="Scenario",job=Ref(jid),resource=Ref(rid),op_index=job["op_index"]))
        self.graph.commit([policy,*assignments],"scenario_applied",actor=actor,time_s=self.time,used=["configuration"],reason="从同一分析基线创建独立预测分支")
        for job in self.graph.find("Workpiece",status="queued"):
            op=self.operation(job);rid=self.select_resource(job,op);old=job["assigned"]
            if rid!=old:
                job.update(assigned=Ref(rid),location=Ref(rid))
                self.command(native,"piece_rerouted",lambda j=job,o=old,r=rid:native.move(j["id"],o+":queue",r+":queue"),[job],used=[op["id"],rid,"policy"],reason="合格设备改派；当前加工工件保持原设备")
        self.settle(native)

    def summary(self):
        jobs=self.jobs();executions=self.graph.entities("Execution");start=self.policy["window_start_s"];end=self.time;window=max(EPS,end-start)
        orders=[]
        for order in self.graph.entities("WorkOrder"):
            items=[j for j in jobs if j["order"]==order["id"]];done=[j for j in items if j["status"]=="done"]
            complete=max((j["completed_at"] for j in done),default=None) if len(done)==order["quantity"] else None
            orders.append({**order,"completed":len(done),"scrapped":sum(j["status"]=="scrapped" for j in items),"completion_s":complete,"tardiness_s":max(0,complete-order["due_s"]) if complete is not None else None})
        utilization={r["id"]:sum(max(0,min(end,x["end_s"])-max(start,x["start_s"])) for x in executions if x["resource"]==r["id"] and x["kind"]=="processing")/window for r in self.graph.entities("Equipment")}
        wip_area=sum(max(0,min(end,j["completed_at"] if j["completed_at"]>=0 else end)-max(start,j["released_at"])) for j in jobs if j["released_at"]>=0)
        queue_wait=sum(max(0,min(end,x["start_s"])-max(start,x.get("queue_entered_s",x["start_s"]))) for x in executions)
        queue_wait+=sum(max(0,end-max(start,j.get("queued_at",end))) for j in jobs if j["status"]=="queued")
        kpi=dict(completed=sum(j["status"]=="done" for j in jobs),scrapped=sum(j["status"]=="scrapped" for j in jobs),total=len(jobs),
            completed_orders=sum(o["completion_s"] is not None for o in orders),on_time_orders=sum(o["completion_s"] is not None and o["tardiness_s"]<=EPS for o in orders),
            wip=sum(j["status"] not in ("pending","done","scrapped","material_wait") for j in jobs),average_wip=wip_area/window,
            reworked=sum(j["attempt"]>0 for j in jobs),window_start_s=start,window_end_s=end,utilization=utilization,
            total_queue_wait_s=queue_wait,mean_queue_wait_per_released_piece_s=queue_wait/max(1,sum(j["released_at"]>=0 for j in jobs)))
        return dict(time_s=self.time,revision=self.graph.sequence,hash=self.graph.head,kpi=kpi,orders=orders,jobs=jobs,
            assets=self.graph.entities("AssetState"),batches=self.graph.entities("Batch"),balances=self.graph.entities("MaterialBalance"),
            reservations=self.graph.entities("FixtureReservation"),inspections=self.graph.entities("Inspection"),segments=executions,
            policy=self.policy,pending_events=self.pending_events())

"""Dedicated-worker-only COM adapter. Plant Simulation owns every event time.

Python dispatches at returned native event boundaries. No analytical fallback is
used for production results. The complete controller state is serializable.
"""
from __future__ import annotations
import copy
import hashlib
import json
import math
import time
from pathlib import Path
from .contracts import ProductionSpec, ScenarioPatch, RunResult, digest, canonical

ROOT=".Models.Model"
SYS=ROOT+".OntologySim"
EC=ROOT+".EventController"
EPS=1e-6

def samples_for(spec):
    samples={}
    seq=0
    products={p.id:p for p in spec.products}
    for o in spec.orders:
        for _ in range(o.quantity):
            seq+=1; jid=f"J{seq:04}"
            for index,op in enumerate(products[o.product].operations):
                raw=hashlib.sha256(f"{spec.seed}:{jid}:{index}".encode()).digest()
                uniform=int.from_bytes(raw[:8],"big")/(2**64-1)
                samples[f"{jid}/{index}"]=round(op.duration_s*(.92+.16*uniform),6)
    return samples

def compare_states(expected,actual):
    differences=[]
    def visit(a,b,path):
        if isinstance(a,dict) and isinstance(b,dict):
            for k in sorted(a.keys()|b.keys()):
                if k not in a or k not in b: differences.append(f"{path}.{k}: missing")
                else: visit(a[k],b[k],f"{path}.{k}")
        elif isinstance(a,list) and isinstance(b,list):
            if len(a)!=len(b): differences.append(f"{path}: list length {len(a)} != {len(b)}")
            else:
                for i,(x,y) in enumerate(zip(a,b)): visit(x,y,f"{path}[{i}]")
        elif isinstance(a,(int,float)) and isinstance(b,(int,float)) and not isinstance(a,bool):
            if abs(a-b)>EPS: differences.append(f"{path}: {a} != {b}")
        elif a!=b: differences.append(f"{path}: {a!r} != {b!r}")
    visit(expected,actual,"state")
    return differences[:50]

class NativeEngine:
    def __init__(self, emit, cancel, pause, visible, progid):
        self.emit=emit; self.cancel=cancel; self.pause=pause; self.visible=visible
        self.progid=progid; self.sim=None; self.com=None; self.owned=False; self.pid=None
        self.paths={}; self.phase="idle"; self.last_pause=False

    def checkpoint_control(self):
        if self.cancel.is_set():
            if self.sim: self.sim.StopSimulation()
            raise InterruptedError("用户已取消；已在原生事件边界停止")
        if self.sim:
            self.com.PumpWaitingMessages()
            flag=self.visible.is_set()
            if flag != getattr(self,"shown",False):
                self.sim.SetVisible(flag)
                if flag: self.sim.ExecuteSimTalk(SYS+".openDialog")
                self.shown=flag
        if self.pause.is_set():
            self.emit("status",{"status":"paused","phase":self.phase})
            while self.pause.is_set():
                if self.cancel.is_set(): raise InterruptedError("已取消暂停的任务")
                if self.com: self.com.PumpWaitingMessages()
                time.sleep(.05)
            self.emit("status",{"status":"running","phase":self.phase})

    def connect(self):
        import pythoncom, win32com.client, win32process
        self.com=pythoncom
        pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
        before=set(win32process.EnumProcesses())
        self.sim=win32com.client.DispatchEx(self.progid)
        self.pid=int(self.sim.GetCurrentProcessId())
        self.owned=self.pid not in before
        if not self.owned: raise RuntimeError("COM 指向已有进程，拒绝修改或关闭非本应用实例")
        self.emit("owner",{"pid":self.pid,"progid":self.progid,"owned":True})
        self.sim.SetNoMessageBox(1); self.sim.SetStopSimulationOnError(True)
        self.sim.SetSuppressStartOf3D(True); self.sim.SetVisible(self.visible.is_set())
        self.shown=self.visible.is_set()
        self.sim.NewModel()

    def close(self):
        try:
            if self.sim and self.owned:
                self.sim.StopSimulation(); self.sim.Quit()
                self.emit("released",{"pid":self.pid,"quit_called":True})
        finally:
            self.sim=None
            if self.com: self.com.CoUninitialize(); self.com=None

    def execute(self,code):
        if self.source:
            self.source.write(code+"\n\n")
        return self.sim.ExecuteSimTalk(code)

    def wait(self):
        deadline=time.monotonic()+15
        while self.sim.IsSimulationRunning():
            self.com.PumpWaitingMessages()
            if self.cancel.is_set(): self.sim.StopSimulation(); raise InterruptedError("已停止")
            if time.monotonic()>deadline: self.sim.StopSimulation(); raise TimeoutError("原生事件调用超过 15 秒")
            time.sleep(.002)
        if self.sim.HasSimulationError(): raise RuntimeError("Plant Simulation 报告原生仿真错误")

    def build(self,spec,output):
        self.phase="building"; self.emit("status",{"status":"running","phase":self.phase})
        self.connect()
        self.source=(output/"generated.simtalk").open("w",encoding="utf-8")
        quantity=sum(o.quantity for o in spec.orders)
        self.execute(f'''.MaterialFlow.Frame.createObject({ROOT},100,100,"OntologySim")
.MaterialFlow.Drain.createObject({SYS},880,220,"Finished")
.MaterialFlow.Buffer.createObject({SYS},440,380,"Transit")
{SYS}.Transit.Capacity := {quantity}
{SYS}.Transit.ProcTime := 0
str_to_obj(".InformationFlow.Method").createObject({SYS},880,380,"Wake")
str_to_obj("{SYS}.Wake").Program := "var dummy: integer := 0"
{EC}.RealTime := false
{EC}.End := {spec.horizon_s+1:.9f}''')
        for index,r in enumerate(spec.resources):
            self.checkpoint_control()
            frame=f"{SYS}.R{index}"
            self.paths[r.id]={"buffer":frame+".WaitingBuffer","station":frame+".Machine"}
            self.execute(f'''.MaterialFlow.Frame.createObject({SYS},{100+index%3*250},{80+index//3*160},"R{index}")
.MaterialFlow.Buffer.createObject({frame},60,100,"WaitingBuffer")
{frame}.WaitingBuffer.Capacity := {quantity}
{frame}.WaitingBuffer.ProcTime := 0
.MaterialFlow.Station.createObject({frame},220,100,"Machine")
{frame}.Machine.ProcTime := 1''')
            self.sim.SetValue(frame+".Label",r.name)
            self.sim.SetValue(frame+".Machine.Label",r.name)
        if self.shown: self.execute(SYS+".openDialog")
        self.sim.SaveModel(str((output/"model.spp").resolve()))
        self.sim.ResetSimulation(EC); self.sim.StartSimulation(EC); self.wait()
        if abs(float(self.sim.GetValue(EC+".SimTime")))>EPS: raise RuntimeError("初始化没有停在 0 秒")
        self.sim.StopSimulation()

    def wake(self,seconds):
        if seconds>EPS: self.execute(f'str_to_obj("{SYS}.Wake").executeIn({seconds:.9f})')

    def move(self,jid,source,target):
        self.execute(f'''var waiting:object := {source}
for var itemIndex := 1 to waiting.numMU
 var item:object := waiting.MU(itemIndex)
 if item.Name = "{jid}"
  item.move({target})
  return
 end
next
print "Missing controlled MU: {jid}"''')

    def native_check(self,state):
        # A single COM transaction reads the entire controlled model contract.
        fields=[f'to_str({SYS}.Finished.statNumIn)',f'to_str({SYS}.Transit.numMU)']
        for paths in self.paths.values():
            fields.extend([f'to_str({paths["buffer"]}.numMU)', f'to_str({paths["station"]}.numMU)'])
        raw=self.execute('-> string\nreturn '+ ' + ";" + '.join(fields))
        vals=[float(self.sim.GetValue(EC+".SimTime"))]+[float(x) for x in str(raw).split(";")]
        errors=[]
        if abs(vals[0]-state["time"])>EPS: errors.append("原生时钟不一致")
        if int(vals[1])!=state["completed"]: errors.append("出口完工计数不一致")
        transit=sum(j["status"]=="transfer" for j in state["jobs"].values())
        if int(vals[2])!=transit: errors.append(f"转运缓冲区不一致 {vals[2]} / {transit}")
        resources={}
        for i,(rid,paths) in enumerate(self.paths.items()):
            q,b=map(int,vals[3+2*i:5+2*i]); asset=state["assets"][rid]
            if q!=len(state["queues"][rid]) or b!=int(asset["busy"] is not None): errors.append(f"{rid} 队列或占用不一致")
            names=[]
            for n in range(1,q+1): names.append(str(self.sim.GetValue(paths["buffer"]+f".MU({n}).Name")))
            if set(names)!=set(state["queues"][rid]): errors.append(f"{rid} 物理队列工件不一致")
            busy_name=str(self.sim.GetValue(paths["station"]+".cont.Name")) if b else None
            if busy_name!=asset["busy"]: errors.append(f"{rid} 加工工件不一致")
            resources[rid]={"queued":q,"occupied":b,"busy":busy_name,"queue_members":names}
        if errors: raise RuntimeError("；".join(errors))
        return {"verified":True,"clock_s":vals[0],"completed":int(vals[1]),"transit":transit,
                "resources":resources,"clock_tolerance_s":EPS,"pid":self.pid,"engine":self.progid,
                "event_controller":EC}

    def run(self,payload,output):
        spec=ProductionSpec.model_validate(payload["spec"])
        patch=ScenarioPatch.model_validate(payload.get("patch",{"name":"基准运行"}))
        candidates=payload["candidates"]
        target=payload.get("snapshot")
        if target:
            if digest(spec.model_dump())!=target["spec_hash"]: raise ValueError("快照与工厂规格版本不匹配")
            if digest(target["state"])!=target["state_hash"]: raise ValueError("快照状态摘要不匹配")
        samples=target["samples"] if target else samples_for(spec)
        if set(samples)!=set(samples_for(spec)): raise ValueError("加工样本键不完整")
        if any(not math.isfinite(v) or v<=0 for v in samples.values()): raise ValueError("加工样本必须为有限正数")
        output.mkdir(parents=True,exist_ok=True)
        (output/"resolved-input.json").write_text(canonical(payload),encoding="utf-8")
        self.source=None
        self.build(spec,output)
        self.phase="restoring" if target else "simulating"
        self.emit("status",{"status":"running","phase":self.phase})
        products={p.id:p for p in spec.products}; orders={o.id:o for o in spec.orders}
        resources={r.id:r for r in spec.resources}
        state={"time":0.0,"boundary":-1,"jobs":{},"assets":{},"queues":{},"completed":0,"sequence":0}
        events=[]; segments=[]; trace=[]; restored=False; applied=False
        active=ScenarioPatch(name="基准 FIFO") if target else patch
        sequence=0
        for o in spec.orders:
            for n in range(o.quantity):
                sequence+=1; jid=f"J{sequence:04}"
                state["jobs"][jid]={"order":o.id,"product":o.product,"op":0,"status":"pending",
                    "location":"unreleased","assigned":products[o.product].operations[0].preferred,
                    "ready_at":o.release_s,"queued_at":None,"sequence":sequence,"started_at":None,"completed_at":None}
        for r in spec.resources:
            state["assets"][r.id]={"busy":None,"tool_count":0,"maintenance_until":0.0,"status":"idle","busy_s":0.0}
            state["queues"][r.id]=[]
        for t in sorted({o.release_s for o in spec.orders if o.release_s>0} | {spec.horizon_s}): self.wake(t)
        for day in range(1,math.ceil(spec.horizon_s/86400)+1): self.wake(day*86400)
        now=0.0; steps=0; lastclock=-1; stagnant=0; started=time.monotonic()
        def event(kind,**kwargs):
            events.append({"seq":len(events),"time":now,"kind":kind,**kwargs})
        def operation(job): return products[job["product"]].operations[job["op"]]
        def choose(jid,job):
            op=operation(job)
            if jid in active.assignments and (not target or job["op"]==target["state"]["jobs"][jid]["op"]): return active.assignments[jid]
            eligible=candidates[f"{job['product']}/{op.id}"]
            if not active.reroute: return op.preferred
            def score(rid):
                a=state["assets"][rid]
                return (max(now,a["maintenance_until"])-now + len(state["queues"][rid])*op.duration_s
                        + (op.duration_s if a["busy"] else 0),rid)
            return min(eligible,key=score)
        def enqueue(jid,job,source=None):
            rid=choose(jid,job); path=self.paths[rid]["buffer"]
            if source: self.move(jid,source,path)
            else: self.execute(f'var p:object := .MUs.Part.create({path})\np.Name := "{jid}"')
            state["sequence"]+=1
            job.update(status="queued",location=rid,assigned=rid,queued_at=now,sequence=state["sequence"])
            state["queues"][rid].append(jid)
            event("queued",job=jid,resource=rid,op=job["op"])
        trace_file=(output/"trace.jsonl").open("w",encoding="utf-8")
        try:
            while True:
                self.checkpoint_control()
                if time.monotonic()-started>900: raise TimeoutError("仿真墙钟时间超过 15 分钟")
                changed=False
                # Native completion detection is the only completion authority.
                code=['-> string','var finished:string := ""']
                for rid,a in state["assets"].items():
                    if a["busy"]:
                        code.extend([f'if {self.paths[rid]["station"]}.cont.Finished', f' finished += "{rid};"', 'end'])
                code.append('return finished')
                finished=str(self.execute("\n".join(code)) or "").split(";")
                for rid in finished:
                    if not rid: continue
                    a=state["assets"][rid]; jid=a["busy"]; job=state["jobs"][jid]; op=operation(job)
                    actual=str(self.sim.GetValue(self.paths[rid]["station"]+".cont.Name"))
                    if actual!=jid: raise RuntimeError("原生工件与占用记录不一致")
                    a["busy_s"]+=now-job["started_at"]
                    segments.append({"resource":rid,"job":jid,"order":job["order"],"op":job["op"],
                                     "operation":op.name,"start":job["started_at"],"end":now,"kind":"processing"})
                    event("completed_operation",job=jid,resource=rid,op=job["op"])
                    job["op"]+=1; a["busy"]=None
                    if job["op"]==len(products[job["product"]].operations):
                        self.execute(self.paths[rid]["station"]+f".cont.move({SYS}.Finished)")
                        job.update(status="done",location="finished",completed_at=now,assigned=None)
                        state["completed"]+=1; event("completed_piece",job=jid,order=job["order"])
                    else:
                        self.execute(self.paths[rid]["station"]+f".cont.move({SYS}.Transit)")
                        job.update(status="transfer",location="transit",ready_at=now+spec.transfer_s,
                                   assigned=operation(job).preferred)
                        self.wake(spec.transfer_s)
                        segments.append({"resource":"TRANSFER","job":jid,"order":job["order"],"op":job["op"],
                                         "operation":"固定转运","start":now,"end":now+spec.transfer_s,"kind":"transfer"})
                    r=resources[rid]
                    if r.tool_limit:
                        a["tool_count"]+=1
                        if a["tool_count"]>=r.tool_limit:
                            a["maintenance_until"]=now+r.maintenance_s
                            event("maintenance_started",resource=rid,tool_count=a["tool_count"],ends=a["maintenance_until"])
                            segments.append({"resource":rid,"job":"MAINT","order":"","op":0,"operation":"刀具维护",
                                             "start":now,"end":a["maintenance_until"],"kind":"maintenance"})
                            self.wake(r.maintenance_s)
                    changed=True
                for rid,a in state["assets"].items():
                    if a["maintenance_until"] and a["maintenance_until"]<=now+EPS:
                        a["maintenance_until"]=0.0; a["tool_count"]=0
                        event("maintenance_finished",resource=rid); changed=True
                for jid,job in state["jobs"].items():
                    if job["status"]=="pending" and job["ready_at"]<=now+EPS:
                        enqueue(jid,job); changed=True
                    elif job["status"]=="transfer" and job["ready_at"]<=now+EPS:
                        enqueue(jid,job,SYS+".Transit"); changed=True
                for rid,a in state["assets"].items():
                    hours=active.shift_hours or resources[rid].shift_hours
                    isopen=now%86400 < hours*3600-EPS
                    if a["busy"] is None and a["maintenance_until"]<=now+EPS and isopen and now<spec.horizon_s-EPS:
                        waiting=state["queues"][rid]
                        if waiting:
                            key=lambda jid: ((orders[state["jobs"][jid]["order"]].due_s if active.rule=="EDD" else 0),
                                             state["jobs"][jid]["sequence"],jid)
                            jid=min(waiting,key=key); waiting.remove(jid); job=state["jobs"][jid]
                            duration=float(samples[f"{jid}/{job['op']}"])
                            self.execute(f'{self.paths[rid]["station"]}.ProcTime := {duration:.9f}')
                            self.move(jid,self.paths[rid]["buffer"],self.paths[rid]["station"])
                            if self.sim.GetValue(self.paths[rid]["station"]+".cont.Name")!=jid: raise RuntimeError("工件移动失败")
                            a["busy"]=jid; job.update(status="processing",started_at=now)
                            event("started",job=jid,resource=rid,op=job["op"],sample_s=duration); changed=True
                    a["status"]="maintenance" if a["maintenance_until"]>now+EPS else "processing" if a["busy"] else "idle" if isopen else "off_shift"
                state["time"]=round(now,6)
                if changed or now!=lastclock:
                    state["boundary"]+=1
                    frozen=copy.deepcopy(state)
                    trace_file.write(canonical(frozen)+"\n")
                    if payload.get("trace_in_memory"): trace.append(frozen)
                    if state["boundary"]%12==0:
                        self.emit("progress",{"time":now,"completed":state["completed"],"boundary":state["boundary"],
                            "total":len(state["jobs"]),"phase":self.phase})
                    if target and not restored and state["boundary"]==target["state"]["boundary"]:
                        differences=compare_states(target["state"],state)
                        if differences: raise ValueError("恢复校验失败: "+canonical(differences))
                        evidence=self.native_check(state)
                        restored=True; self.emit("restored",{"verified":True,"differences":[],"native":evidence})
                        if payload.get("restore_only"):
                            self.sim.SaveModel(str((output/"restored.spp").resolve()))
                            return {"restored":True,"state":frozen,"native":evidence,"state_hash":digest(frozen)}
                        active=patch; self.phase="simulating"
                        self.emit("status",{"status":"running","phase":self.phase})
                        # Reassign only the waiting operation; work already started keeps its MU and sample.
                        if active.reroute or active.assignments:
                            queued=[(jid,j) for jid,j in state["jobs"].items() if j["status"]=="queued"]
                            for jid,job in queued:
                                old=job["assigned"]; new=choose(jid,job)
                                if new!=old:
                                    self.move(jid,self.paths[old]["buffer"],self.paths[new]["buffer"])
                                    state["queues"][old].remove(jid); state["queues"][new].append(jid)
                                    job.update(assigned=new,location=new)
                                    event("rerouted",job=jid,old=old,resource=new)
                        # Dispatch newly available resources at this same native time.
                        # A control-only reconsideration does not inject a fake clock tick.
                        lastclock=now
                        continue
                lastclock=now
                if now>=spec.horizon_s-EPS: break
                self.sim.StepSimulation(EC); self.wait()
                nexttime=float(self.sim.GetValue(EC+".SimTime"))
                stagnant=stagnant+1 if abs(nexttime-now)<1e-9 else 0
                if stagnant>3000: raise RuntimeError("原生事件无法推进")
                now=nexttime; steps+=1
            if target and not restored: raise ValueError("未到达要求恢复的事件边界")
            self.sim.StopSimulation()
            native=self.native_check(state)
            native.update(steps=steps,termination="horizon",all_completed=state["completed"]==len(state["jobs"]))
            self.sim.SaveModel(str((output/"final.spp").resolve()))
            start=target["state"]["time"] if target else 0.0
            result=self.result(payload["run_id"],target,patch,spec,state,segments,events,native,start,samples)
            (output/"result.json").write_text(result.model_dump_json(indent=2),encoding="utf-8")
            return {"result":result.model_dump(),"samples":samples,"trace_path":str(output/"trace.jsonl")}
        finally:
            trace_file.close()
            if self.source: self.source.close(); self.source=None

    def result(self,run_id,target,patch,spec,state,segments,events,native,start,samples):
        order_rows=[]
        for o in spec.orders:
            jobs=[j for j in state["jobs"].values() if j["order"]==o.id]
            done=[j for j in jobs if j["status"]=="done"]
            complete=max(j["completed_at"] for j in done) if len(done)==o.quantity else None
            order_rows.append({**o.model_dump(),"completed":len(done),"completion_s":complete,
                "tardiness_s":max(0,complete-o.due_s) if complete is not None else None,
                "unfinished_overdue_s":max(0,state["time"]-o.due_s) if complete is None else 0})
        end=state["time"]; window=max(EPS,end-start)
        # Event integration includes queued WIP only, not processing or transport, after freeze.
        queued={}; wait=0.0; arrivals=0
        for e in events:
            if e["kind"]=="queued": queued[e["job"]]=e["time"]
            elif e["kind"]=="started":
                at=queued.pop(e["job"],e["time"])
                if e["time"]>=start:
                    wait+=max(0,e["time"]-max(start,at)); arrivals+=1
        utilization={}
        for r in spec.resources:
            busy=sum(max(0,min(end,s["end"])-max(start,s["start"])) for s in segments if s["resource"]==r.id and s["kind"]=="processing")
            a=state["assets"][r.id]
            if a["busy"]:
                busy+=max(0,end-max(start,state["jobs"][a["busy"]]["started_at"]))
            utilization[r.id]=round(busy/window,6)
        completed_window=sum(j["status"]=="done" and j["completed_at"]>=start for j in state["jobs"].values())
        # Time-weighted WIP is reconstructed from immutable release/complete events.
        in_system=set(); last=0.0; area=0.0
        for e in events:
            area+=len(in_system)*max(0,min(end,e["time"])-max(start,last))
            last=e["time"]
            if e["kind"]=="queued" and e["op"]==0: in_system.add(e["job"])
            if e["kind"]=="completed_piece": in_system.discard(e["job"])
        area+=len(in_system)*max(0,end-max(start,last))
        kpi={"window_start_s":start,"window_end_s":end,"completed":state["completed"],
             "mean_wip":round(area/window,3),
             "makespan_s":max((j["completed_at"] for j in state["jobs"].values() if j["status"]=="done"),default=0) if state["completed"]==len(state["jobs"]) else None,
             "completed_in_window":completed_window,"wip":sum(j["status"] not in ["pending","done"] for j in state["jobs"].values()),
             "completed_orders":sum(o["completion_s"] is not None for o in order_rows),
             "on_time_orders":sum(o["completion_s"] is not None and o["tardiness_s"]==0 for o in order_rows),
             "late_completed_orders":sum(o["tardiness_s"] is not None and o["tardiness_s"]>0 for o in order_rows),
             "unfinished_orders":sum(o["completion_s"] is None for o in order_rows),
             "total_tardiness_s":sum(o["tardiness_s"] or 0 for o in order_rows),
             "mean_wait_started_s":round(wait/max(1,arrivals),3),"utilization":utilization}
        assumptions=[f"加工样本为种子 {spec.seed} 的工件—工序预测样本；未读取尚未发布的现场实际事件",
            f"固定转运 {spec.transfer_s} 秒；加工与维护均不抢占当前工件", "刀具按完工次数消耗，维护时长使用资源配置（基准为 45 分钟）",
            "设备利用率分母为冻结后经过的日历时间；等待均值仅含时间窗内开工工序",
            f"派工 {patch.rule}；兼容设备改派 {patch.reroute}；班次 {patch.shift_hours or '资源配置'} 小时；班末已开工工件继续完成"]
        return RunResult(run_id=run_id,snapshot_id=target["id"] if target else None,patch=patch,state=copy.deepcopy(state),
            kpi=kpi,orders=order_rows,segments=segments,events=events,native=native,assumptions=assumptions,
            input_hash=digest({"spec":spec.model_dump(),"snapshot":target,"samples":samples,"patch":patch.model_dump()}))

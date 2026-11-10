from __future__ import annotations
import asyncio
import copy
import hashlib
import json
import multiprocessing as mp
import os
import queue
import shutil
import uuid
import zipfile
from pathlib import Path
from .contracts import ProductionSpec, ScenarioPatch, TwinSnapshot, CheckpointManifest, canonical, digest
from .demo import demo_spec, demo_patches
from .semantic import SemanticProjection, validate_spec, validate_patch, compile_input, validate_snapshot_graph
from .storage import Repository, utcnow
from .worker import worker_main
from .publisher import publisher_main, subscribe

BASE=Path(__file__).resolve().parents[1]
RUNTIME=Path(os.environ.get("ONTOLOGYSIM_DATA",str(BASE/".runtime"))).resolve()
def uid(prefix): return prefix+"_"+uuid.uuid4().hex[:12]

class Workbench:
    def __init__(self):
        RUNTIME.mkdir(parents=True,exist_ok=True)
        self.repo=Repository(RUNTIME/"workbench.sqlite")
        self.graph=SemanticProjection(RUNTIME/"oxigraph")
        self.ctx=mp.get_context("spawn")
        self.commands=self.ctx.Queue(4); self.notifications=self.ctx.Queue(64)
        self.cancel=self.ctx.Event(); self.pause=self.ctx.Event(); self.visible=self.ctx.Event()
        self.worker=None; self.publisher=None; self.sub_task=None; self.sub_stop=None
        self.source_status={"state":"disconnected","reconnects":0}
        self.source_pause=self.ctx.Event(); self.source_paused=self.ctx.Event(); self.source_stop=self.ctx.Event()
        self.speed=self.ctx.Value("d",600); self.target=self.ctx.Value("i",-1)
        self.pending={}; self.duplicates=0; self.revision=0; self.active_task=None; self.owner=None
        self.endpoint=os.environ.get("ONTOLOGYSIM_OPC_ENDPOINT","opc.tcp://127.0.0.1:4842/ontologysim/")
        self.port=int(os.environ.get("ONTOLOGYSIM_PORT","8820"))
        self.progid=os.environ.get("ONTOLOGYSIM_PROGID","Tecnomatix.PlantSimulation.RemoteControl.26.6")
        self.closing=False; self.monitor=None; self.errors=[]
        self.management_lock=asyncio.Lock()
        for t in self.repo.list("task"):
            if t["status"] in ["queued","running","paused","cancelling"]:
                t.update(status="interrupted",message="应用上次退出时未完成；未自动重试原生执行")
                self.repo.put("task",t["id"],t,t["project_id"])
            elif t["status"]=="completed" and t["phase"]!="completed":
                t["phase"]="completed";self.repo.put("task",t["id"],t,t["project_id"])

    @property
    def project_id(self): return self.repo.meta("active_project")
    @property
    def project(self): return self.repo.get("project",self.project_id) if self.project_id else None
    @property
    def spec(self): return ProductionSpec.model_validate(self.project["spec"]) if self.project else demo_spec()

    async def start(self):
        self.worker=self.ctx.Process(target=worker_main,args=(self.commands,self.notifications,self.cancel,self.pause,self.visible,self.progid),name="OntologySim-COM-owner")
        self.worker.start(); self.monitor=asyncio.create_task(self.monitor_worker())
        if self.project:
            self.rebuild_graph()
            if self.project.get("trace_path") and Path(self.project["trace_path"]).exists(): await self.start_source(False)

    async def stop(self):
        self.closing=True
        await self.stop_source()
        self.cancel.set(); self.pause.clear()
        # Drain not-yet-executed commands rather than launching them on shutdown.
        while True:
            try:
                command=self.commands.get_nowait()
                t=self.repo.get("task",command.get("task_id",""))
                if t: t.update(status="interrupted",message="应用退出，队列中任务未执行"); self.repo.put("task",t["id"],t,t["project_id"])
            except queue.Empty: break
        self.commands.put({"kind":"shutdown"})
        if self.worker:
            await asyncio.to_thread(self.worker.join,20)
            if self.worker.is_alive():
                self.errors.append("COM 工作进程未能在 20 秒内退出，需检查 diagnostics 日志")
                # Do not kill Plant Simulation by name or unrelated sessions.
                self.worker.terminate(); await asyncio.to_thread(self.worker.join,3)
                if self.owner:
                    import psutil
                    try:
                        p=psutil.Process(self.owner["pid"])
                        if p.create_time()==self.owner.get("create_time") and p.name().lower().startswith("plantsimulation"):
                            p.terminate(); p.wait(5)
                            self.errors.append("已终止经 PID/创建时间核对归属的失去响应实例")
                    except (psutil.Error,KeyError): pass
        if self.monitor: self.monitor.cancel()
        if self.owner:
            import psutil
            for _ in range(50):
                try:
                    owned=psutil.Process(self.owner["pid"])
                    released=owned.create_time()!=self.owner.get("create_time")
                except psutil.NoSuchProcess: released=True
                except psutil.AccessDenied: released=False
                if released: self.owner["released"]=True; break
                await asyncio.sleep(.1)
            if not self.owner.get("released"):
                self.errors.append("关闭后尚未确认原生实例退出；保留 PID 与创建时间供诊断")
        (RUNTIME/"shutdown.json").write_text(canonical({"at":utcnow(),"errors":self.errors,"owner":self.owner}),encoding="utf-8")
        self.repo.close()

    def touch(self): self.revision+=1

    def tasks(self):
        return self.repo.list("task",self.project_id) if self.project_id else []

    def require_idle(self):
        if any(t["status"] in ["queued","running","paused","cancelling"] for t in self.tasks()):
            raise ValueError("当前已有原生任务，请等待完成或取消")

    def submit(self,kind,payload,project_id=None):
        if self.closing: raise ValueError("应用正在退出")
        if not self.worker or not self.worker.is_alive(): raise ValueError("原生工作进程不可用，请重启应用")
        project_id=project_id or self.project_id
        taskid=uid("task"); runid=uid("run"); payload={**payload,"run_id":runid}
        output=RUNTIME/("source-vault" if kind=="baseline" else "runs")/runid
        task={"id":taskid,"run_id":runid,"project_id":project_id,"kind":kind,"name":payload.get("patch",{}).get("name",kind),
              "status":"queued","phase":"queued","created_at":utcnow(),"output":str(output),"progress":{},
              "snapshot_id":payload.get("snapshot",{}).get("id")}
        self.repo.put("task",taskid,task,project_id)
        try: self.commands.put_nowait({"kind":"run","task_id":taskid,"payload":payload,"output":str(output)})
        except queue.Full:
            task.update(status="failed",message="原生任务队列已满"); self.repo.put("task",taskid,task,project_id)
            raise ValueError("原生任务队列已满")
        self.touch(); return task

    async def monitor_worker(self):
        while True:
            await asyncio.sleep(.05)
            while True:
                try: notice=self.notifications.get_nowait()
                except queue.Empty: break
                kind=notice["kind"]; data=notice["data"]
                t=self.repo.get("task",data.get("task_id",""))
                if kind=="owner":
                    import psutil
                    self.owner={**data,"create_time":psutil.Process(data["pid"]).create_time(),"released":False}
                elif kind=="released":
                    if self.owner and self.owner["pid"]==data["pid"]: self.owner["released"]=True
                elif kind in ["release_error","view_error"]: self.errors.append(data["message"])
                if not t: continue
                if kind=="status":
                    t.update(status=data["status"],phase=data["phase"]); self.active_task=t["id"]
                elif kind=="progress": t["progress"]=data
                elif kind=="restored": t["restore_evidence"]=data
                elif kind in ["failed","cancelled"]:
                    t.update(status=kind,message=data["message"],phase=data["phase"],hresult=data["hresult"],finished_at=utcnow())
                    self.active_task=None
                    self.repo.put("diagnostic",t["id"],data,t["project_id"])
                elif kind=="done":
                    t.update(status="completed",phase="completed",finished_at=utcnow()); self.active_task=None
                    result=data["result"]
                    if t["kind"]=="baseline":
                        p=self.repo.get("project",t["project_id"])
                        p.update(trace_path=result["trace_path"],samples=result["samples"],baseline_native=result["result"]["native"])
                        self.repo.put("project",p["id"],p,p["id"])
                        await self.start_source(True)
                    elif t["kind"]=="restore":
                        snap=self.repo.get("snapshot",t["snapshot_id"])
                        p=self.repo.get("project",t["project_id"])
                        p.update(state=result["state"],cursor=snap["source_cursor"],snapshot_id=snap["id"],restored=True)
                        self.repo.put("project",p["id"],p,p["id"]); self.repo.meta("active_project",p["id"])
                        self.rebuild_graph(); await self.start_source(False)
                    else:
                        self.repo.put("result",t["run_id"],result["result"],t["project_id"])
                        self.rebuild_graph()
                self.repo.put("task",t["id"],t,t["project_id"]); self.touch()
            if self.worker and not self.worker.is_alive() and not self.closing:
                for t in self.tasks():
                    if t["status"] in ["queued","running","paused","cancelling"]:
                        t.update(status="interrupted",message="COM 工作进程异常退出，未将任务视为完成")
                        self.repo.put("task",t["id"],t,t["project_id"]); self.touch()

    async def new_project(self,spec=None):
        self.require_idle()
        if self.project and self.project.get("state"):
            await self.save_checkpoint("重置前自动归档")
        await self.stop_source()
        spec=spec or demo_spec(); check=validate_spec(spec)
        if not check["conforms"]: raise ValueError("；".join(check["errors"]) or check["shacl"])
        project={"id":uid("project"),"name":spec.name,"created_at":utcnow(),"spec":spec.model_dump(),"cursor":-1,
                 "state":None,"snapshot_id":None,"synthetic":True,"spec_hash":digest(spec.model_dump())}
        self.repo.put("project",project["id"],project,project["id"]); self.repo.meta("active_project",project["id"])
        # Import an actual ERP file, preserving raw master data as an input artifact.
        folder=RUNTIME/"projects"/project["id"]; folder.mkdir(parents=True,exist_ok=True)
        erp={"source":"ERP_FILE","schema":"ProductionSpec/1.0","spec":spec.model_dump(),"synthetic":True}
        erp_path=folder/"erp-orders.json"; erp_path.write_text(canonical(erp),encoding="utf-8")
        imported=json.loads(erp_path.read_text(encoding="utf-8"))
        self.repo.record(project["id"],"ERP_FILE","erp:"+digest(imported),0,imported,entity="factory-plan")
        self.pending={}; self.rebuild_graph()
        return self.submit("baseline",compile_input(spec))

    async def start_source(self,initial=False):
        await self.stop_source()
        p=self.project
        if not p or not p.get("trace_path"): return
        self.source_stop=self.ctx.Event(); self.source_pause=self.ctx.Event(); self.source_paused=self.ctx.Event()
        self.source_pause.set(); self.target.value=0 if initial else -1
        self.pending={}; self.sub_stop=asyncio.Event()
        self.sub_task=asyncio.create_task(subscribe(self.endpoint,self.spec,self.accept_opc,self.source_status,self.sub_stop))
        config={"trace_path":p["trace_path"],"spec":p["spec"],"project":p["id"],"cursor":p["cursor"],
                "endpoint":self.endpoint,"api":f"http://127.0.0.1:{self.port}"}
        self.publisher=self.ctx.Process(target=publisher_main,args=(config,self.source_pause,self.source_paused,self.source_stop,self.speed,self.target),name="OntologySim-synthetic-factory")
        self.publisher.start()
        if initial: self.source_pause.clear()

    async def stop_source(self):
        if self.publisher:
            self.source_stop.set(); self.source_pause.set()
            await asyncio.to_thread(self.publisher.join,7)
            if self.publisher.is_alive(): self.publisher.terminate(); await asyncio.to_thread(self.publisher.join,2)
            self.publisher=None
        if self.sub_stop: self.sub_stop.set()
        if self.sub_task:
            self.sub_task.cancel()
            try: await self.sub_task
            except asyncio.CancelledError: pass
            self.sub_task=None
        self.source_status["state"]="disconnected"

    async def pause_source(self):
        if not self.publisher or not self.publisher.is_alive(): return
        self.source_pause.set()
        for _ in range(160):
            if self.source_paused.is_set(): self.touch(); return
            await asyncio.sleep(.05)
        raise ValueError("数据源尚未完成当前事务，未生成不一致的快照；请检查 OPC UA 连接")

    def source_play(self,mode,speed=None,target=None):
        self.require_idle()
        if not self.publisher or not self.publisher.is_alive(): raise ValueError("数据发布器未就绪")
        if speed is not None:
            if not 1<=speed<=100000: raise ValueError("倍速范围 1–100000")
            self.speed.value=speed
        self.target.value=-2 if mode=="incident" else target if target is not None else -1
        self.source_pause.clear(); self.source_paused.clear(); self.touch()

    def quarantine(self,source,payload,reason):
        self.repo.record(self.project_id or "none",source,str(payload.get("raw_id") or uid("invalid")),
            0,payload,"quarantine",str(payload.get("source_id","")),reason)
        self.touch()

    def accept_mes(self,payload):
        try:
            p=self.project
            if payload.get("project_id")!=p["id"]: raise ValueError("项目标识不匹配")
            if payload.get("quality")!="good" or payload.get("unit")!="s": raise ValueError("MES 质量或时间单位不符合契约")
            cursor=int(payload["cursor"]); t=float(payload["event_time"])
            if cursor<0 or t<0: raise ValueError("事件时间或游标无效")
            mapping={r.mes_id:r.id for r in self.spec.resources}
            state=copy.deepcopy(payload["state"])
            for j in state["jobs"].values():
                for key in ["location","assigned"]:
                    raw=j[key]
                    if raw in ["unreleased","transit","finished",None]: continue
                    if raw not in mapping: raise ValueError(f"无法映射 MES 实体 {raw}")
                    j[key]=mapping[raw]
            state["queues"]={mapping[k]:v for k,v in state["queues"].items()}
            if state["boundary"]!=cursor or abs(state["time"]-t)>1e-6: raise ValueError("MES 切片时钟不一致")
            fresh=self.repo.record(p["id"],"MES",payload["raw_id"],t,payload)
            if not fresh: self.duplicates+=1
            if cursor>p["cursor"]:
                if cursor!=p["cursor"]+1: raise ValueError("MES 游标不连续")
                self.pending.setdefault(cursor,{})["mes"]=state
                self.commit_frame(cursor)
            return {"accepted":True,"duplicate":not fresh,"cursor":self.project["cursor"]}
        except (ValueError,KeyError,TypeError,AttributeError) as ex:
            self.quarantine("MES",{**payload,"raw_id":str(payload.get("raw_id",""))+":rejected"},str(ex))
            raise ValueError(str(ex))

    def accept_opc(self,payload):
        try:
            p=self.project
            if payload.get("project_id")!=p["id"]: raise ValueError("OPC 项目标识不匹配")
            mapping={r.opc_id:r.id for r in self.spec.resources}
            if payload["source_id"] not in mapping: raise ValueError("OPC 设备未映射")
            if payload["quality"]!="good" or payload["unit"] not in ["ms","s"]: raise ValueError("OPC 质量或单位无效")
            rid=mapping[payload["source_id"]]; cursor=int(payload["cursor"])
            t=float(payload["event_time"])/(1000 if payload["unit"]=="ms" else 1)
            fresh=self.repo.record(p["id"],"OPC_UA",payload["raw_id"],t,payload,entity=rid)
            if not fresh: self.duplicates+=1
            if cursor<=p["cursor"]: return
            if cursor!=p["cursor"]+1: raise ValueError("OPC 游标不连续")
            self.pending.setdefault(cursor,{}).setdefault("assets",{})[rid]={"value":payload["asset"],"time":t}
            self.commit_frame(cursor)
        except (ValueError,KeyError,TypeError,AttributeError) as ex:
            self.quarantine("OPC_UA",{**payload,"raw_id":str(payload.get("raw_id",""))+":rejected"},str(ex))

    def commit_frame(self,cursor):
        frame=self.pending[cursor]
        if "mes" not in frame or len(frame.get("assets",{}))!=len(self.spec.resources): return
        state=frame["mes"]
        if any(abs(x["time"]-state["time"])>1e-6 for x in frame["assets"].values()): raise ValueError("跨协议时间切片不一致")
        state["assets"]={r.id:frame["assets"][r.id]["value"] for r in self.spec.resources}
        self.validate_state(state)
        p=self.project; p.update(state=state,cursor=cursor,snapshot_id=None,restored=False)
        self.repo.frame(p["id"],cursor,state,digest(state)); self.repo.put("project",p["id"],p,p["id"])
        self.pending.pop(cursor,None); self.touch()

    def validate_state(self,state):
        spec=self.spec
        if len(state["jobs"])!=sum(o.quantity for o in spec.orders): raise ValueError("工件数量不守恒")
        present=[]
        for rid,a in state["assets"].items():
            present+=state["queues"][rid]
            if a["busy"]: present.append(a["busy"])
            if a["status"]=="maintenance" and a["busy"]: raise ValueError("维护期间存在加工占用")
        if len(present)!=len(set(present)): raise ValueError("同一工件存在多个位置")
        for jid,j in state["jobs"].items():
            if j["status"]=="queued" and jid not in state["queues"].get(j["location"],[]): raise ValueError("队列与工件位置矛盾")
            if j["status"]=="processing" and state["assets"][j["location"]]["busy"]!=jid: raise ValueError("占用与工件位置矛盾")
        if sum(j["status"]=="done" for j in state["jobs"].values())!=state["completed"]: raise ValueError("完工数不守恒")

    def rebuild_graph(self):
        p=self.project
        if not p: return
        records=[r for r in self.repo.records(p["id"],10000) if r["quality"]=="good" and r["payload"].get("cursor",-1)<=p["cursor"]]
        self.graph.rebuild(p["id"],self.spec,p["state"],records,self.repo.list("result",p["id"]),p["cursor"],p.get("snapshot_id"))

    async def freeze(self):
        self.require_idle()
        await self.pause_source()
        p=self.project
        if not p or not p["state"]: raise ValueError("尚无完整现场切片")
        validate_snapshot_graph(self.spec,p["state"])
        existing=self.repo.get("snapshot",p["snapshot_id"]) if p.get("snapshot_id") else None
        if existing and existing["state_hash"]==digest(p["state"]) and existing["spec_hash"]==p["spec_hash"] and existing["source_cursor"]==p["cursor"]:
            self.rebuild_graph();return existing
        snapshot=TwinSnapshot(id=uid("snapshot"),project_id=p["id"],spec_hash=p["spec_hash"],source_cursor=p["cursor"],
            state=p["state"],state_hash=digest(p["state"]),samples=p["samples"],
            assumptions=["ERP 订单和工艺为已知计划", "工件—工序固定样本是预测假设；不读尚未发布的参考轨迹"])
        self.repo.put("snapshot",snapshot.id,snapshot.model_dump(),p["id"])
        p["snapshot_id"]=snapshot.id; self.repo.put("project",p["id"],p,p["id"])
        self.rebuild_graph(); self.touch(); return snapshot.model_dump()

    def compare(self,patches=None):
        self.require_idle(); p=self.project
        if not p or not p.get("snapshot_id"): raise ValueError("请先冻结现场")
        snap=self.repo.get("snapshot",p["snapshot_id"])
        patches=patches or demo_patches()
        candidates=[]
        for patch in patches: candidates.append(validate_patch(self.spec,snap["state"],patch))
        compiled=compile_input(self.spec)
        return [self.submit("scenario",{**compiled,"snapshot":snap,"patch":patch.model_dump(),"candidates":candidate})
                for patch,candidate in zip(patches,candidates)]

    async def save_checkpoint(self,name="保存现场"):
        snap=await self.freeze(); p=self.project; checkpoint_id=uid("checkpoint")
        folder=RUNTIME/"checkpoints"/checkpoint_id; folder.mkdir(parents=True)
        inputs={"spec.json":p["spec"],"snapshot.json":snap,"frames.json":self.repo.frames(p["id"],p["cursor"]),
            "records.json":[r for r in self.repo.records(p["id"],100000) if r["payload"].get("cursor",-1)<=p["cursor"]],
            "results.json":self.repo.list("result",p["id"])}
        for filename,data in inputs.items(): (folder/filename).write_text(canonical(data),encoding="utf-8")
        (folder/"semantics.trig").write_text(self.graph.export(),encoding="utf-8")
        baseline=next((t for t in self.tasks() if t["kind"]=="baseline" and t["status"]=="completed"),None)
        if baseline and (Path(baseline["output"])/"model.spp").exists():
            shutil.copy2(Path(baseline["output"])/"model.spp",folder/"baseline-template.spp")
        for t in self.tasks():
            if t["status"]=="completed" and t["kind"] in ["scenario","restore"]:
                path=Path(t["output"])
                for filename in ["model.spp","final.spp","restored.spp","generated.simtalk","resolved-input.json"]:
                    if (path/filename).exists(): shutil.copy2(path/filename,folder/(t["run_id"]+"_"+filename))
        files={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in folder.iterdir() if f.is_file()}
        manifest=CheckpointManifest(id=checkpoint_id,project_id=p["id"],snapshot=TwinSnapshot.model_validate(snap),
            spec=self.spec,created_at=utcnow(),event_cursor=max((r["seq"] for r in inputs["records.json"]),default=0),files=files)
        (folder/"manifest.json").write_text(manifest.model_dump_json(indent=2),encoding="utf-8")
        with zipfile.ZipFile(folder.with_suffix(".zip"),"w",zipfile.ZIP_DEFLATED) as z:
            for f in folder.iterdir(): z.write(f,f.name)
        row={"id":checkpoint_id,"project_id":p["id"],"name":name,"created_at":manifest.created_at,
             "time":snap["state"]["time"],"completed":snap["state"]["completed"],"hash":snap["state_hash"],
             "folder":str(folder),"snapshot_id":snap["id"],"results":len(inputs["results.json"])}
        self.repo.put("checkpoint",checkpoint_id,row,p["id"]); self.touch(); return row

    async def restore(self,id):
        self.require_idle(); row=self.repo.get("checkpoint",id)
        if not row: raise ValueError("存档不存在")
        folder=Path(row["folder"]); manifest=CheckpointManifest.model_validate_json((folder/"manifest.json").read_text(encoding="utf-8"))
        for filename,hash in manifest.files.items():
            path=(folder/filename).resolve()
            if path.parent!=folder.resolve() or not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest()!=hash:
                raise ValueError(f"存档文件哈希不匹配：{filename}")
        if digest(manifest.snapshot.state)!=manifest.snapshot.state_hash: raise ValueError("存档状态摘要不匹配")
        await self.stop_source()
        self.repo.meta("active_project",row["project_id"])
        check=validate_spec(manifest.spec)
        if not check["conforms"]: raise ValueError("存档语义校验失败")
        return self.submit("restore",{**compile_input(manifest.spec),"snapshot":manifest.snapshot.model_dump(),
                    "restore_only":True},row["project_id"])

    def summary(self):
        p=self.project
        return {"revision":self.revision,"project":{k:v for k,v in p.items() if k not in ["samples","trace_path","baseline_native"]} if p else None,
            "tasks":self.tasks()[:20],"source":{**self.source_status,"paused":self.source_pause.is_set(),
                "publisher_alive":bool(self.publisher and self.publisher.is_alive()),"speed":self.speed.value,"endpoint":self.endpoint,
                "cursor":p["cursor"] if p else -1,"duplicates":self.duplicates},
            "results":[{k:v for k,v in r.items() if k not in ["state","events","segments"]} for r in self.repo.list("result",self.project_id)] if p else [],
            "checkpoints":self.repo.list("checkpoint"),"owner":self.owner,"errors":self.errors[-5:],"progid":self.progid,
            "graph":self.graph.meta,"synthetic":True}

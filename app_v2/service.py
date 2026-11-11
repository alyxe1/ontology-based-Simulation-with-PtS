from __future__ import annotations
import asyncio
import copy
import hashlib
import json
import multiprocessing as mp
import queue
import secrets
import shutil
import time
import zipfile
import os
from datetime import datetime, timezone
from pathlib import Path
from asyncua import Client
from .common import BASE, DATA, EPS, NS, atomic_write, canonical, digest, now, uid
from .auth import Accounts
from .reference import REFERENCE_DIR, model_contract, verified_reference
from .contracts import SourceRecordEnvelope
from .evidence import Signer
from .graph import OntologyStore, Ref, encode_record, decode_record
from .runtime import OntologyRuntime
from .specification import ProductionSpec, default_spec, spec_from_graph, spec_records, qualification, validate_ontology
from .worker import worker_main
from .publisher import publisher_main
from .invariants import validate_boundary

class Workbench:
    def __init__(self,root=DATA,port=8820):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True);self.port=port
        self.key_path=self.root/"signing-key.dpapi";self.signer=Signer(self.key_path)
        self.catalog=OntologyStore(self.root/"catalog",self.signer);self.accounts=Accounts(self.root/"accounts.sqlite")
        self.graphs={};self.views={};self.progress={};self.pending={};self.revision=0;self.active_task=None;self.shutdown=False
        self.source_token=secrets.token_urlsafe(32);self.source_pause=mp.Event();self.source_pause.set();self.source_stop=mp.Event()
        self.speed=mp.Value('d',600.);self.target=mp.Value('i',-1);self.publisher=None;self.opc_task=None
        self.source_status={"connected":False,"paused":True,"cursor":-1,"duplicates":0,"quality":"waiting","endpoint":"opc.tcp://127.0.0.1:4843/ontologysim-v2/","synthetic":True}
        self.source_status["endpoint"]=os.environ.get("ONTOLOGYSIM_OPC_ENDPOINT",self.source_status["endpoint"])
        self.commands=mp.Queue(2);self.notifications=mp.Queue(128);self.cancel=mp.Event();self.pause=mp.Event();self.visible=mp.Event();self.capture=mp.Event()
        self.process=None;self.pump_task=None

    def audit(self,kind,actor="service/workbench",used=(),reason="",changes=(),details=None):
        result=self.catalog.commit(changes,kind,actor=actor,used=used,reason=reason,details=details);self.revision+=1;return result

    def active(self):
        pointer=self.catalog.get("active_workspace")
        return self.catalog.get(pointer["workspace"]) if pointer else None

    def graph(self,workspace=None):
        workspace=workspace or self.active()["id"]
        if workspace not in self.graphs:self.graphs[workspace]=OntologyStore(self.root/"workspaces"/workspace/"ontology",self.signer)
        return self.graphs[workspace]

    def context(self,context="live"):
        if context=="live":
            active=self.active()
            if not active:return None
            return OntologyRuntime(self.graph(active["id"]))
        record=self.catalog.get(context)
        if not record:raise ValueError("对象不存在")
        if record.get("kind")=="baseline":raise ValueError("现场未来轨迹由数据源隔离保存，不可用于预测查询")
        if record["type"]=="Workspace":return OntologyRuntime(self.graph(record["id"]))
        path=record.get("path") if record["type"]=="Checkpoint" else str(Path(record["output"])/"runtime.trig")
        if not path or not Path(path).exists():raise ValueError("该运行尚无可读取的本体存档")
        if context not in self.views:self.views[context]=OntologyRuntime(OntologyStore.restore(path,signer=self.signer))
        return self.views[context]

    async def start(self):
        for task in self.catalog.entities("Task"):
            if task["status"] in ("running","pausing","paused","cancelling"):
                task.update(status="interrupted",error="应用上次退出时任务未完成；可从已保存的本体边界恢复")
                self.audit("task_interrupted",changes=[task])
        self.process=mp.Process(target=worker_main,args=(self.commands,self.notifications,self.cancel,self.pause,self.visible,self.capture,str(self.key_path)),daemon=False)
        self.process.start();self.pump_task=asyncio.create_task(self.pump())
        active=self.active()
        if active and active.get("trace_path") and Path(active["trace_path"]).exists():await self.start_source(active)

    async def close(self):
        self.shutdown=True;await self.stop_source();self.cancel.set();self.pause.clear()
        if self.process:
            self.commands.put({"kind":"shutdown"})
            for _ in range(200):
                if not self.process.is_alive():break
                await asyncio.sleep(.05)
            if self.process.is_alive():
                self.audit("shutdown_incomplete",reason="工作进程仍在返回原生控制点，未强杀任何 Plant Simulation 会话")
            else:self.audit("application_stopped",reason="本应用工作进程及持有的原生实例已退出")
        if self.pump_task:self.pump_task.cancel()
        self.accounts.close()

    def tasks(self):return sorted(self.catalog.entities("Task"),key=lambda r:r["created_at"],reverse=True)

    def submit(self,kind,actor,workspace=None,**payload):
        if sum(t["status"] in ("queued","running","paused") for t in self.tasks())>=24:raise ValueError("待执行任务已达上限")
        tid=uid("task");folder=self.root/("source-vault" if kind=="baseline" else "runs")/tid
        task=dict(id=tid,type="Task",kind=kind,status="queued",actor=actor,workspace=Ref(workspace or self.active()["id"]),created_at=now(),output=str(folder),payload=canonical(payload),time_s=0.,completed=0)
        self.audit("task_queued",actor,used=[str(task["workspace"])],changes=[task]);return task

    async def pump(self):
        while not self.shutdown:
            while True:
                try:message=self.notifications.get_nowait()
                except queue.Empty:break
                kind=message["kind"];data=message["data"];tid=data.get("task_id");task=self.catalog.get(tid) if tid else None
                if not task:continue
                if kind=="progress":
                    self.progress[tid]=data["summary"];task.update(time_s=data["summary"]["time_s"],completed=data["summary"]["kpi"]["completed"])
                    task['updated_at']=now()
                    self.audit("task_progress",changes=[task])
                elif kind=="checkpoint":
                    self.progress[tid]=data["summary"]
                    cp=self.register_checkpoint(Path(data["path"]),str(task["workspace"]),task["actor"],"运行中保存",task_id=tid)
                    task["checkpoint_id"]=cp["id"]
                    if self.pause.is_set():task['status']='paused'
                    self.audit("task_checkpoint_saved",changes=[task],used=[cp["id"]])
                elif kind=="done":
                    task.update(status="completed",time_s=data["summary"]["time_s"],completed=data["summary"]["kpi"]["completed"],native=canonical(data["native"]))
                    if task['kind']=='baseline':task['trace_hash']=hashlib.sha256((Path(task['output'])/'source.jsonl').read_bytes()).hexdigest()
                    self.progress[tid]=data["summary"];self.audit("task_completed",changes=[task],used=[tid]);self.active_task=None
                    if task["kind"]=="baseline":
                        ws=self.catalog.get(task["workspace"]);ws.update(trace_path=str(Path(task["output"])/"source.jsonl"),baseline_task=Ref(tid))
                        generation=dict(id='generation/'+tid,type='SourceGeneration',native_task=tid,algorithm='sha256-paired-v1',seed=2606,model_contract=json.loads(task['payload']).get('model_contract','development-build'),trace_hash=task['trace_hash'],started_at=task['created_at'],finished_at=now(),native_verified=True,source_kind='synthetic')
                        self.graph(str(ws['id'])).commit([generation],'source_generation_confirmed',actor='service/factory-generator',used=['configuration'],reason='参考轨迹由真实原生运行生成，保留参数、种子和生成证明；未来观测仍由发布器隔离保存')
                        self.audit("factory_source_ready",changes=[ws],used=[tid]);await self.start_source(ws);self.source_pause.clear()
                elif kind in ("failed","cancelled"):
                    task.update(status=data.get("status",kind),error=data["message"],hresult=str(data.get("hresult","")),updated_at=now())
                    self.audit("task_"+kind,changes=[task],reason=data["message"]);self.active_task=None
                elif kind in ("owner","released","release_error"):
                    self.audit("native_"+kind,used=[tid],details=data)
                self.revision+=1
            if not self.active_task:
                waiting=sorted([t for t in self.tasks() if t["status"]=="queued"],key=lambda t:t["created_at"])
                if waiting:
                    self.cancel.clear();self.pause.clear();self.capture.clear()
                    task=waiting[0];task["status"]="running";self.active_task=task["id"]
                    self.audit("task_started",changes=[task]);self.commands.put({**task,**json.loads(task["payload"])})
            await asyncio.sleep(.08)

    async def create_workspace(self,spec,actor):
        if self.active_task or any(t["status"]=="queued" for t in self.tasks()):raise ValueError("请先完成或取消当前任务")
        await self.stop_source();old=self.active()
        if old:
            old["status"]="archived";self.audit("workspace_archived",actor,changes=[old])
        wid=uid("workspace");folder=self.root/"workspaces"/wid;folder.mkdir(parents=True)
        raw_path=folder/"erp.json";atomic_write(raw_path,canonical(spec.model_dump()))
        # The ERP adapter reads the actual file, rather than bypassing the ingestion boundary.
        loaded=ProductionSpec.model_validate_json(raw_path.read_text(encoding="utf-8"))
        g=self.graph(wid)
        source=dict(id="source/erp/initial",type="SourceRecord",source="ERP",raw_id=raw_path.name,payload=raw_path.read_text(encoding="utf-8"),hash=hashlib.sha256(raw_path.read_bytes()).hexdigest(),received_at=now(),event_time=0.,quality="good",synthetic=True)
        g.commit([source],"erp_file_received",actor=actor,reason="读取 ERP 原始文件并保留摘要")
        OntologyRuntime.create(g,loaded,actor=actor,used=[source["id"]])
        ws=dict(id=wid,type="Workspace",name=spec.name,status="active",created_at=now(),cursor=-1,spec_revision=1,synthetic=True)
        self.audit("workspace_created",actor,changes=[ws,dict(id="active_workspace",type="Pointer",workspace=Ref(wid))])
        self.pending={};self.source_status.update(cursor=-1,connected=False,quality="preparing")
        contract=model_contract()
        for previous in self.tasks():
            payload=json.loads(previous['payload'])
            if previous['kind']!='baseline' or previous['status']!='completed' or payload.get('model_contract')!=contract or payload.get('spec')!=loaded.model_dump():continue
            trace=Path(previous['output'])/'source.jsonl'
            if trace.exists() and previous.get('trace_hash')==hashlib.sha256(trace.read_bytes()).hexdigest() and json.loads(previous.get('native','{}')).get('verified'):
                ws.update(trace_path=str(trace),baseline_task=Ref(previous['id']))
                self.audit('factory_source_reused',actor,changes=[ws],used=[previous['id']],reason='配置、执行程序摘要与已核对的原生轨迹一致；从游标零重新发布')
                previous_generation=self.graph(str(previous['workspace'])).get('generation/'+previous['id'])
                if previous_generation:g.commit([previous_generation],'source_generation_referenced',actor=actor,used=['configuration'],reason='复用同一已验证参考轨迹的生成记录')
                await self.start_source(ws);self.source_pause.clear()
                return dict(id=wid,workspace=wid,kind='source_reused',status='completed')
        try:
            reference=verified_reference(loaded.model_dump(),REFERENCE_DIR)
        except Exception as ex:
            self.audit('factory_reference_rejected',actor,reason=str(ex))
            reference=None
        if reference:
            trace,metadata,envelope=reference
            # Future frames stay in the private publisher vault, outside all API contexts.
            target=self.root/'source-vault'/wid/'source.jsonl';target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(trace,target)
            identity='reference_'+metadata['trace_hash'][:16]
            manifest_record=dict(id='source/reference/manifest',type='SourceRecord',source='REFERENCE',
                raw_id='factory-reference/manifest.json',payload=canonical(envelope),hash=digest(envelope),
                received_at=now(),event_time=0.,quality='good',synthetic=True)
            g.commit([manifest_record],'reference_manifest_verified',actor='service/artifact-validation',
                reason='保留随附轨迹的签名发布清单，记录生成任务及原生核对产物摘要')
            generation=dict(id='generation/'+identity,type='SourceGeneration',native_task=metadata['native_task'],
                algorithm=metadata['algorithm'],seed=metadata['seed'],model_contract=metadata['model_contract'],
                trace_hash=metadata['trace_hash'],started_at=metadata['generated_at'],native_verified=True,
                source_kind='synthetic',origin='packaged-native-reference',manifest_hash=digest(envelope),
                native_result_hash=metadata['native_result_hash'],runtime_hash=metadata['runtime_hash'],source=Ref(manifest_record['id']))
            g.commit([generation],'source_generation_referenced',actor=actor,used=['configuration',manifest_record['id']],
                reason='使用随版本交付且签名、配置、模型与内容摘要均已核对的原生参考轨迹；方案仍执行真实原生仿真')
            ws.update(trace_path=str(target),baseline_task=Ref(identity))
            self.audit('factory_reference_loaded',actor,changes=[ws],used=[wid],details={'generation':generation['id'],'trace_hash':metadata['trace_hash']})
            await self.start_source(ws);self.source_pause.clear()
            return dict(id=wid,workspace=wid,kind='source_reused',status='completed')
        return self.submit("baseline",actor,wid,spec=loaded.model_dump(),model_contract=contract)

    async def retry_source(self,actor):
        ws=self.active()
        if not ws:raise ValueError('请先载入工作空间')
        if self.active_task or any(t['status']=='queued' for t in self.tasks()):raise ValueError('请先完成或取消当前原生任务')
        if ws.get('trace_path') and Path(ws['trace_path']).exists():
            await self.start_source(ws);self.source_pause.clear()
            self.audit('source_reconnected',actor,used=[ws['id']]);return {'status':'connecting'}
        spec=spec_from_graph(self.graph(ws['id']))
        return self.submit('baseline',actor,ws['id'],spec=spec.model_dump(),model_contract=model_contract())

    async def start_source(self,workspace):
        await self.stop_source();self.source_stop.clear();self.source_pause.set()
        if 'trace_end_cursor' not in workspace:
            with Path(workspace['trace_path']).open(encoding='utf-8') as stream:
                workspace['trace_end_cursor']=sum(1 for line in stream if line.strip())-1
            self.audit('source_extent_recorded',changes=[workspace],reason='记录参考轨迹发布边界，区分正常结束与断连')
        if workspace['cursor']>=workspace['trace_end_cursor']:
            self.source_status.update(quality='completed',connected=False,cursor=workspace['cursor']);return
        spec=spec_from_graph(self.graph(workspace["id"]))
        config=dict(workspace=workspace["id"],spec=spec.model_dump(),trace_path=workspace["trace_path"],cursor=workspace.get("cursor",-1),url=f"http://127.0.0.1:{self.port}",endpoint=self.source_status["endpoint"],token=self.source_token)
        self.publisher=mp.Process(target=publisher_main,args=(config,self.source_pause,self.source_stop,self.speed,self.target),daemon=False)
        self.publisher.start();self.opc_task=asyncio.create_task(self.opc_loop(spec))

    async def stop_source(self):
        self.source_stop.set()
        if self.opc_task:
            self.opc_task.cancel()
            try:await self.opc_task
            except asyncio.CancelledError:pass
            self.opc_task=None
        if self.publisher:
            for _ in range(100):
                if not self.publisher.is_alive():break
                await asyncio.sleep(.05)
            if self.publisher.is_alive():self.publisher.terminate();self.publisher.join(2)
            self.publisher=None
        self.source_status["connected"]=False

    async def opc_loop(self,spec):
        service=self
        class Handler:
            def status_change_notification(self,status):service.source_status.update(quality="reconnecting",connected=False)
            def datachange_notification(self,node,value,data):
                if value:
                    try:service.ingest(SourceRecordEnvelope.model_validate_json(value))
                    except Exception as ex:service.audit("opc_rejected",reason=str(ex))
        while not self.source_stop.is_set():
            if self.source_exhausted():
                self.source_status.update(quality='completed',connected=False);self.source_pause.set();return
            try:
                async with Client(self.source_status["endpoint"],timeout=3) as client:
                    idx=await client.get_namespace_index("urn:ontologysim:v2:factory")
                    folder=await client.nodes.objects.get_child([f"{idx}:Cell01"])
                    nodes=[await folder.get_child([f"{idx}:{r.id}",f"{idx}:Observation"]) for r in spec.resources]
                    subscription=await client.create_subscription(10,Handler());await subscription.subscribe_data_change(nodes)
                    self.source_status.update(connected=True,quality="good")
                    while not self.source_stop.is_set():
                        if self.source_exhausted():
                            self.source_status.update(quality='completed',connected=False);self.source_pause.set();return
                        await asyncio.sleep(.5);await client.check_connection()
            except asyncio.CancelledError:raise
            except Exception:
                self.source_status.update(connected=False,quality="reconnecting");await asyncio.sleep(.5)

    def ingest(self,envelope):
        data=envelope.model_dump();ws=self.active()
        if not ws or envelope.workspace!=ws["id"]:raise ValueError("数据不属于当前工作空间")
        graph=self.graph();record_id="source/"+envelope.raw_id
        previous=graph.get(record_id);checksum=digest(data)
        if previous:
            self.source_status["duplicates"]+=1
            if previous["hash"]!=checksum:
                graph.commit([dict(id=uid("quarantine"),type="SourceRecord",source=envelope.source,raw_id=envelope.raw_id,payload=canonical(data),hash=checksum,quality="conflict",received_at=now())],"source_conflict",reason="相同来源编号收到不同内容，拒绝覆盖")
                raise ValueError("重复消息内容冲突")
            if envelope.cursor<=ws["cursor"]:return {"duplicate":True}
        occurred=envelope.event_time/(1000 if envelope.unit=="ms" else 1)
        if envelope.cursor>ws["cursor"] and occurred<OntologyRuntime(graph).time-EPS:raise ValueError("新边界的业务时间不能倒退；请作为更正版本接收")
        source=dict(id=record_id,type="SourceRecord",source=envelope.source,raw_id=envelope.raw_id,payload=canonical(data),hash=checksum,quality=envelope.quality,received_at=now(),event_time=occurred,original_unit=envelope.unit,cursor=envelope.cursor,synthetic=envelope.synthetic)
        generation='generation/'+str(ws.get('baseline_task',''))
        if graph.get(generation):source['generation']=Ref(generation)
        if not previous:graph.commit([source],"source_received",actor="service/"+envelope.source.lower(),time_s=occurred,used=[generation] if graph.get(generation) else [],reason="原始数据保留；来源、单位和接收时间已记录")
        if envelope.quality!="good":return {"quarantined":True}
        if envelope.cursor<=ws["cursor"]:
            graph.commit([],"late_record_retained",used=[record_id],reason="迟到记录保留，不覆盖已提交的现场边界");return {"late":True}
        pending=self.pending.setdefault(envelope.cursor,{"sources":[],"assets":{},"time":occurred})
        if abs(pending["time"]-occurred)>EPS:raise ValueError("来源时间切片不一致")
        if record_id not in pending["sources"]:pending["sources"].append(record_id)
        if envelope.source=="MES":pending["mes"]=envelope.records
        else:
            rid=next((r["id"] for r in graph.entities("Equipment") if r["opc_id"]==envelope.source_id),None)
            if not rid:raise ValueError("无法映射 OPC 设备编号")
            observation=decode_record(envelope.record)
            if observation.get("type")!="AssetState" or observation.get("resource")!=rid or observation.get("id")!=rid+"/state":raise ValueError("OPC 来源与设备状态实体不一致")
            pending["assets"][rid]=envelope.record
        if "mes" in pending and len(pending["assets"])==len(graph.entities("Equipment")):
            if envelope.cursor!=ws["cursor"]+1:raise ValueError("数据边界不连续，等待缺失记录重传")
            aliases={r["mes_id"]:r["id"] for r in graph.entities("Equipment")}
            def resolve(value):
                if isinstance(value,Ref):return Ref(aliases.get(str(value),str(value)))
                if isinstance(value,list):return [resolve(v) for v in value]
                return value
            changes=[]
            for encoded in [*pending["mes"],*pending["assets"].values()]:
                row={k:resolve(v) for k,v in decode_record(encoded).items()}
                if row["type"] not in ("Workpiece","AssetState","MaterialBalance","FixtureReservation","Execution","Inspection"):raise ValueError("接入实体类型未获允许")
                if row["type"]=="Inspection" and graph.get(row["id"])==row:continue
                if row["type"]=="Execution" and row["status"]=="active":
                    op=graph.get(row["operation"]);resource=graph.get(row["resource"])
                    duration=resource["setup_s"] if row["kind"]=="setup" else op["duration_s"]*resource["speed"]
                    row.update(duration_s=duration,end_s=max(occurred+1,row["start_s"]+duration),time_basis="standard_estimate")
                changes.append(row)
            for rid,row in pending["assets"].items():
                changes.append(dict(id=f"observation/{envelope.cursor}/{rid}",type="Observation",resource=Ref(rid),time_s=occurred,status=decode_record(row)["status"],source=Ref(f"source/OPC:{envelope.cursor}:{rid}"),synthetic=envelope.synthetic))
            clock=graph.get("clock");clock.update(time_s=occurred,boundary=envelope.cursor,status="observed",source_cursor=envelope.cursor)
            changes.append(clock)
            try:validate_boundary(graph,changes)
            except ValueError as ex:
                graph.commit([dict(id=uid('validation'),type='SourceValidation',quality='invalid',reason=str(ex),sources=[Ref(i) for i in pending['sources']])],'source_validation_failed',used=pending['sources'],reason=str(ex))
                self.source_status['quality']='invalid';raise
            graph.commit(changes,"observations_aligned",actor="service/semantic-ingestion",time_s=occurred,used=[*pending["sources"],"mapping/MES-v1","mapping/OPC-v1"],reason="MES 编号映射与 OPC 毫秒转秒；同一接收边界完整提交")
            ws["cursor"]=envelope.cursor;self.audit("source_boundary_committed",changes=[ws],details={"cursor":envelope.cursor,"time_s":occurred})
            self.pending.pop(envelope.cursor,None);self.source_status.update(cursor=envelope.cursor,quality="good");self.revision+=1
        return {"accepted":True}

    def prepare_prediction(self,graph,actor):
        runtime=OntologyRuntime(graph);changes=[];clock=runtime.clock
        clock.update(seed=5202,status="ready");changes.append(clock)
        max_queue=max((j.get("queue_seq",0) for j in runtime.jobs()),default=0);clock["queue_sequence"]=max_queue
        for event in runtime.pending_events():event["status"]="cancelled";changes.append(event)
        # Reconstruct only known releases, transfers, maintenance and explicitly estimated active work.
        prefix=f"forecast/{graph.sequence+1}"
        for job in runtime.jobs():
            if job["status"]=="pending":
                order=graph.get(job["order"]);changes.append(runtime.event(prefix+"/"+job["id"],"release",max(runtime.time,order["release_s"]),job["id"],priority=30))
            elif job["status"]=="transfer":changes.append(runtime.event(prefix+"/"+job["id"],"transfer_finished",max(runtime.time,job["ready_at"]),job["id"],priority=25))
            elif job["status"] in ("processing","setup"):
                execution=graph.get(job["execution"]);execution["end_s"]=max(runtime.time+1,execution["end_s"]);execution["time_basis"]="remaining_time_assumption"
                changes.append(execution);changes.append(runtime.event(prefix+"/"+job["id"],"process_finished" if job["status"]=="processing" else "setup_finished",execution["end_s"],job["id"],job["assigned"],10))
        for asset in graph.entities("AssetState"):
            if asset["maintenance_until"]>runtime.time+EPS:changes.append(runtime.event(prefix+"/"+asset["resource"],"maintenance_finished",asset["maintenance_until"],resource=asset["resource"],priority=15))
        policy=runtime.policy;policy["window_start_s"]=runtime.time;changes.append(policy)
        graph.commit(changes,"prediction_initialized",actor=actor,time_s=runtime.time,used=["configuration","clock"],reason="只使用已接收观测与已知计划；在加工剩余时长采用标准工时估计，未来样本独立于现场生成器")
        report=validate_ontology(graph)
        if not report["conforms"]:raise ValueError(report["report"])

    def checkpoint(self,actor):
        if not self.active() or self.active()["cursor"]<0:raise ValueError("尚未收到完整现场边界")
        graph=self.graph();time_s=OntologyRuntime(graph).time
        validate_boundary(graph)
        graph.commit([],"analysis_baseline_requested",actor=actor,time_s=time_s,used=["clock"],reason="固定已接收语义版本；现场接入继续")
        temporary=self.root/"checkpoints"/(uid("prepare")+".trig");graph.export(temporary)
        branch=OntologyStore.restore(temporary,signer=self.signer);self.prepare_prediction(branch,actor);branch.export(temporary)
        return self.register_checkpoint(temporary,self.active()["id"],actor,"分析基线")

    def register_checkpoint(self,path,workspace,actor,name,task_id=""):
        cid=uid("checkpoint");folder=self.root/"checkpoints"/cid;folder.mkdir(parents=True)
        target=folder/"runtime.trig";shutil.copy2(path,target)
        graph=OntologyStore.restore(target,signer=self.signer);runtime=OntologyRuntime(graph)
        manifest={"version":"2.0","id":cid,"workspace":workspace,"created_at":now(),"graph_sequence":graph.sequence,"graph_head":graph.head,"public_key":self.signer.public,"fingerprint":self.signer.fingerprint,"files":{"runtime.trig":hashlib.sha256(target.read_bytes()).hexdigest()}}
        atomic_write(folder/"manifest.json",canonical({"manifest":manifest,"signature":self.signer.sign(manifest)}))
        row=dict(id=cid,type="Checkpoint",workspace=Ref(workspace),name=name,path=str(target),created_at=manifest["created_at"],time_s=runtime.time,graph_sequence=graph.sequence,graph_head=graph.head,source_task=task_id)
        self.audit("checkpoint_created",actor,used=[workspace],changes=[row],reason="不可变本体快照已保存并签名")
        return row

    def compare(self,checkpoint_id,actor,repetitions=1,patch=None):
        cp=self.catalog.get(checkpoint_id)
        if not cp or cp["type"]!="Checkpoint":raise ValueError("分析基线不存在")
        patches=[patch] if patch else [dict(name="维持 FIFO",rule="FIFO",reroute=False),dict(name="合格设备改派 + EDD",rule="EDD",reroute=True),dict(name="改派 + 延长班次",rule="EDD",reroute=True,shift_hours=12)]
        result=[]
        for sample in range(repetitions):
            for p in patches:
                result.append(self.submit("scenario",actor,str(cp["workspace"]),checkpoint=cp["path"],checkpoint_id=checkpoint_id,patch=p,seed=5202+sample,sample_index=sample))
        return result

    def task_control(self,tid,action,actor):
        task=self.catalog.get(tid)
        if not task or task["type"]!="Task":raise ValueError("任务不存在")
        if action=="cancel" and task["status"]=="queued":task["status"]="cancelled"
        elif tid!=self.active_task:raise ValueError("只能控制当前执行任务")
        elif action=="pause":self.pause.set();task["status"]="pausing"
        elif action=="resume":self.pause.clear();task["status"]="running"
        elif action=="cancel":self.cancel.set();self.pause.clear();task["status"]="cancelling"
        elif action=="capture":self.capture.set()
        else:raise ValueError("未知任务操作")
        self.audit("task_"+action,actor,used=[tid],changes=[task]);return task

    def source_control(self,action,actor,speed=None):
        if action in ('play','incident') and self.source_exhausted():raise ValueError('现场参考轨迹已发布完毕；请归档并重新载入，或查看历史与分析基线')
        if action=="pause":self.source_pause.set()
        elif action=="play":self.target.value=-1;self.source_pause.clear()
        elif action=="incident":self.target.value=-2;self.source_pause.clear()
        elif action=="speed" and speed and 1<=speed<=3600:self.speed.value=speed
        else:raise ValueError("数据源操作无效")
        self.audit("source_"+action,actor,reason="控制本机合成数据发布器，不控制真实设备")

    def state(self,context="live"):
        runtime=self.context(context);active=self.active()
        return dict(version="0.2.0",revision=self.revision,workspace=active,context=context,
            spec=spec_from_graph(runtime.graph).model_dump() if runtime else default_spec().model_dump(),
            runtime=runtime.summary() if runtime else None,tasks=self.tasks()[:30],
            checkpoints=sorted(self.catalog.entities("Checkpoint"),key=lambda r:r["created_at"],reverse=True),
            workspaces=self.catalog.entities("Workspace"),source={**self.source_status,"paused":self.source_pause.is_set(),"speed":self.speed.value,"exhausted":self.source_exhausted()},preparation=self.preparation(),fingerprint=self.signer.fingerprint)

    def source_exhausted(self):
        ws=self.active()
        return bool(ws and 'trace_end_cursor' in ws and ws['cursor']>=ws['trace_end_cursor'])

    def preparation(self):
        ws=self.active()
        if not ws:return None
        if self.source_exhausted():
            return {'status':'complete','source_cursor':ws['cursor'],'message':'本轮参考轨迹已全部发布。可以查看历史、创建分析基线，或归档后从头载入。'}
        if ws.get('trace_path'):
            return {'status':'ready' if self.source_status['connected'] and ws['cursor']>=0 else 'connecting',
                    'task_id':None,'source_cursor':ws['cursor'],'message':'现场数据已接入' if ws['cursor']>=0 else '正在建立 MES 与 OPC UA 接入'}
        task=next((t for t in self.tasks() if t['workspace']==ws['id'] and t['kind']=='baseline'),None)
        if not task:return {'status':'interrupted','message':'没有可用的数据源轨迹，请重试'}
        payload=json.loads(task['payload']);horizon=payload['spec']['horizon_s']
        return dict(status=task['status'],task_id=task['id'],time_s=task['time_s'],horizon_s=horizon,
                    percent=min(99.,100*task['time_s']/horizon),updated_at=task.get('updated_at',task['created_at']),
                    elapsed_s=max(0.,(datetime.now(timezone.utc)-datetime.fromisoformat(task['created_at'])).total_seconds()),
                    message=task.get('error','正在计算并核对参考轨迹，完成后自动接入现场数据'))

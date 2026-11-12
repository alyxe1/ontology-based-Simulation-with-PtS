"""One spawned process and one owned STA session. Runtime truth stays in RDF."""
from __future__ import annotations
import json
import os
import queue
import time
import traceback
from pathlib import Path
from .common import EPS, atomic_write, canonical
from .evidence import Signer
from .graph import OntologyStore, encode_record
from .specification import ProductionSpec
from .runtime import OntologyRuntime
from .native import PlantAdapter

def public_frame(runtime,previous,cursor):
    """Only observations already made. Never publishes generator RNG or future processing outcomes."""
    records=[]
    for kind in ("Workpiece","AssetState","MaterialBalance","FixtureReservation","Execution","Inspection"):
        for original in runtime.graph.entities(kind):
            row=dict(original)
            if kind=="Execution" and row["status"]=="active":
                for field in ("end_s","duration_s","sample_algorithm"):row.pop(field,None)
            encoded=encode_record(row)
            if previous.get(row["id"])!=encoded:records.append(encoded)
            previous[row["id"]]=encoded
    return {"cursor":cursor,"time_s":runtime.time,"records":records,
            "assets":[encode_record(r) for r in runtime.graph.entities("AssetState")],
            "incident":any(r["status"]=="maintenance" for r in runtime.graph.entities("AssetState"))}

def worker_main(commands,notifications,cancel,pause,visible,capture,key_path):
    retained=None
    def emit(kind,data):notifications.put({"kind":kind,"data":data})
    while True:
        try:task=commands.get(timeout=.2)
        except queue.Empty:
            if retained:
                try:retained.engine.com.PumpWaitingMessages()
                except Exception:pass
            continue
        if retained:retained.close();retained=None
        if task["kind"]=="shutdown":emit("shutdown",{});return
        task_id=task["id"];out=Path(task["output"]);out.mkdir(parents=True,exist_ok=True)
        native=None;graph=None;runtime=None;trace=None
        try:
            signer=Signer(key_path)
            if task['kind'] in ('view','scenario','continuation'):visible.set()
            else:visible.clear()
            if task["kind"]=="baseline":
                graph=OntologyStore(out/"ontology",signer)
                runtime=OntologyRuntime.create(graph,ProductionSpec.model_validate(task["spec"]),seed=2606)
                trace=(out/"source.jsonl").open("w",encoding="utf-8")
            else:
                graph=OntologyStore.restore(task["checkpoint"],signer=signer,destination=out/"ontology")
                runtime=OntologyRuntime(graph)
                graph.commit([dict(id="analysis_basis",type="Scenario",checkpoint=task.get("checkpoint_id",""),branch=task_id,name=task.get("patch",{}).get("name","直接恢复"))],"branch_created",actor=task.get("actor","service/scenario"),time_s=runtime.time,used=["configuration","clock","policy"],reason="固定本体版本创建独立运行图")
                if "seed" in task:
                    clock=runtime.clock;clock["seed"]=task["seed"]
                    graph.commit([clock],"prediction_sample_selected",time_s=runtime.time,used=["policy"],reason="同一重复组采用配对样本，现场生成器使用独立种子")
            graph.defer_non_intent_flush=True
            def native_emit(kind,data):emit(kind,{"task_id":task_id,**data})
            native=PlantAdapter(native_emit,cancel=cancel,visible=visible)
            emit('phase',{'task_id':task_id,'phase':'opening_native'})
            initial=native.initialize(runtime)
            graph.commit([],"native_initialized",actor=task.get("actor","service/native"),time_s=runtime.time,used=["clock"],details=initial,
                reason="由本体直接装载原生对象；原生本地时钟为零，无历史快进")
            if task["kind"] in ("restore","view"):
                native.verify(runtime);graph.export(out/"runtime.trig");native.save(out/"restored.spp")
                emit("done",{"task_id":task_id,"summary":runtime.summary(),"native":initial,"output":str(out)})
                if task["kind"]=="view":retained=native;native=None
                continue
            if task.get("patch"):runtime.apply_policy(native,task["patch"],task.get("actor","service/scenario"))
            else:runtime.settle(native)
            previous={};cursor=0;steps=0;started=time.monotonic();paused=False
            if trace:trace.write(canonical(public_frame(runtime,previous,cursor))+"\n");cursor+=1
            emit("progress",{"task_id":task_id,"summary":runtime.summary(),"phase":"running"})
            while runtime.time<runtime.config["horizon_s"]-EPS:
                if cancel.is_set():raise InterruptedError("已在完整事件边界停止")
                if pause.is_set() or capture.is_set():
                    if not paused:paused_started=time.monotonic()
                    if not paused or capture.is_set():
                        native.verify(runtime);graph.export(out/"checkpoint.trig")
                        emit("checkpoint",{"task_id":task_id,"path":str(out/"checkpoint.trig"),"summary":runtime.summary()});capture.clear()
                    paused=True
                    if pause.is_set():
                        native.engine.com.PumpWaitingMessages();time.sleep(.08);continue
                if paused:started+=time.monotonic()-paused_started
                paused=False
                runtime.advance(native);steps+=1
                if trace:
                    trace.write(canonical(public_frame(runtime,previous,cursor))+"\n");cursor+=1
                if steps%25==0:emit("progress",{"task_id":task_id,"summary":runtime.summary(),"phase":"running","elapsed_s":round(time.monotonic()-started,1)})
                if steps>20000 or time.monotonic()-started>1800:raise TimeoutError("原生运行超过受控预算")
            if trace:trace.flush();os.fsync(trace.fileno())
            evidence=native.verify(runtime)
            result_record=dict(id="result/"+task_id,type="RunResult",task=task_id,completed=runtime.summary()["kpi"]["completed"],time_s=runtime.time,policy="policy",native_evidence=canonical(evidence))
            graph.commit([result_record],"run_completed",actor="service/native",time_s=runtime.time,used=["policy","configuration","analysis_basis"],details=evidence)
            graph.export(out/"runtime.trig");native.save(out/"final.spp")
            summary=runtime.summary();atomic_write(out/"result.json",canonical({"summary":summary,"native":evidence}))
            emit("done",{"task_id":task_id,"summary":summary,"native":evidence,"output":str(out),"elapsed_s":round(time.monotonic()-started,1)})
        except BaseException as ex:
            status="cancelled" if isinstance(ex,InterruptedError) else "failed"
            uncertain=bool(graph and any(c['status']!='acknowledged' for c in graph.entities('Command')))
            if graph:
                try:
                    graph.commit([],"run_"+status,time_s=runtime.time if runtime else 0,reason=str(ex),details={"hresult":getattr(ex,"hresult",None)})
                    graph.export(out/"interrupted.trig")
                except Exception:pass
            emit(status,{"task_id":task_id,"status":"reconciliation_required" if uncertain else status,"message":str(ex),"hresult":getattr(ex,"hresult",None),"traceback":traceback.format_exc()})
        finally:
            if trace:trace.close()
            if native:
                try:native.close()
                except Exception as ex:emit("release_error",{"task_id":task_id,"message":str(ex)})
            runtime=None;graph=None

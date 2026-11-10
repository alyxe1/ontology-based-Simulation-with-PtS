from __future__ import annotations
import asyncio
import json
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from . import __version__
from .service import Workbench, BASE
from .contracts import ProductionSpec, ScenarioPatch
from .semantic import validate_spec, validate_patch, compile_input

@asynccontextmanager
async def lifespan(app):
    wb=Workbench(); app.state.wb=wb
    await wb.start()
    yield
    await wb.stop()

app=FastAPI(title="OntologySim 工作台",version=__version__,lifespan=lifespan)

@app.exception_handler(ValueError)
async def invalid(request,exc): return JSONResponse(status_code=409,content={"detail":str(exc)})

@app.middleware("http")
async def local_only(request,call_next):
    origin=request.headers.get("origin")
    if origin and origin not in [f"http://127.0.0.1:{os.environ.get('ONTOLOGYSIM_PORT','8820')}","http://localhost:8820","http://localhost:5173","http://127.0.0.1:5173"]:
        return JSONResponse(status_code=403,content={"detail":"仅允许本机工作台来源"})
    if int(request.headers.get("content-length","0"))>8_000_000: return JSONResponse(status_code=413,content={"detail":"输入超过 8 MB"})
    return await call_next(request)

def wb(request): return request.app.state.wb

@app.get("/api/health")
async def health(request:Request): return {"status":"ok","version":__version__,"worker_alive":wb(request).worker.is_alive(),"workspace":str(BASE)}

@app.post("/api/admin/shutdown")
async def shutdown(request:Request):
    token=getattr(request.app.state,"shutdown_token",None)
    if not token or not secrets.compare_digest(request.headers.get("x-local-control",""),token):
        raise HTTPException(403,"需要本机启动器的控制令牌")
    request.app.state.server.should_exit=True
    return {"status":"shutting_down"}

@app.get("/api/state")
async def state(request:Request): return wb(request).summary()

@app.get("/api/stream")
async def stream(request:Request):
    async def generate():
        revision=-1; ticks=0
        while not await request.is_disconnected():
            service=wb(request)
            if revision!=service.revision or ticks%20==0:
                revision=service.revision
                yield "data: "+json.dumps(service.summary(),ensure_ascii=False)+"\n\n"
            else: yield ": keepalive\n\n"
            ticks+=1; await asyncio.sleep(.5)
    return StreamingResponse(generate(),media_type="text/event-stream",headers={"Cache-Control":"no-cache"})

@app.post("/api/demo/load")
@app.post("/api/demo/reset")
async def load(request:Request):
    s=wb(request)
    async with s.management_lock: return await s.new_project()

@app.get("/api/spec")
async def spec(request:Request): return wb(request).spec.model_dump()

@app.post("/api/spec/validate")
async def spec_validate(spec:ProductionSpec): return validate_spec(spec)

@app.post("/api/ingest/erp")
async def erp_import(spec:ProductionSpec,request:Request):
    s=wb(request)
    async with s.management_lock: return await s.new_project(spec)

@app.post("/api/ingest/mes")
async def mes(payload:dict,request:Request): return wb(request).accept_mes(payload)

@app.get("/api/internal/source-cursor")
async def cursor(project_id:str,request:Request):
    p=wb(request).project
    return {"cursor":p["cursor"] if p and p["id"]==project_id else -1}

@app.get("/api/ingest")
async def ingestion(request:Request):
    s=wb(request)
    return {"records":s.repo.records(s.project_id,80),"counts":s.repo.counts(s.project_id),"duplicates":s.duplicates,
        "mappings":[{"canonical":r.id,"mes":r.mes_id,"opc":r.opc_id,"unit":"ms → s","name":r.name} for r in s.spec.resources]}

@app.post("/api/source/control")
async def source_control(payload:dict,request:Request):
    s=wb(request); action=payload.get("action")
    async with s.management_lock:
        if action=="pause": await s.pause_source()
        elif action in ["play","incident"]: s.source_play(action,payload.get("speed"),payload.get("cursor"))
        elif action=="speed":
            speed=float(payload.get("speed",600))
            if not 1<=speed<=100000: raise ValueError("倍速范围 1–100000")
            s.speed.value=speed;s.touch()
        elif action=="reconnect": await s.pause_source(); await s.start_source(False)
        else: raise ValueError("未知数据源操作")
    return s.summary()["source"]

@app.post("/api/snapshots")
async def freeze(request:Request):
    s=wb(request)
    async with s.management_lock: return await s.freeze()

@app.get("/api/semantic")
async def semantic(request:Request,view:str="relations"):
    s=wb(request); s.rebuild_graph(); return s.graph.graph(view)

@app.get("/api/semantic/entity")
async def entity(request:Request,id:str): return wb(request).graph.entity(id)

@app.get("/api/semantic/validate")
async def validate(request:Request): return validate_spec(wb(request).spec)

@app.get("/api/semantic/export")
async def graph_export(request:Request):
    s=wb(request); s.rebuild_graph()
    return Response(s.graph.export(),media_type="application/trig",headers={"Content-Disposition":"attachment; filename=ontology.trig"})

@app.get("/api/impact")
async def impact(request:Request):
    s=wb(request); s.rebuild_graph(); p=s.project
    if not p or not p["state"]: return {"events":[],"orders":[],"paths":[]}
    rows=list(s.graph.store.query('''PREFIX os: <urn:ontologysim:>
      SELECT DISTINCT ?event ?target WHERE {GRAPH <urn:ontologysim:graph/enrichment> {?event a os:MaintenanceEvent; os:affects ?target}}'''))
    ids={row["target"].value.removeprefix("urn:ontologysim:") for row in rows}
    return {"events":[{"asset":rid,**a} for rid,a in p["state"]["assets"].items() if a["status"]=="maintenance"],
            "orders":[o.model_dump() for o in s.spec.orders if o.id in ids],
            "paths":[{"event":row["event"].value,"affected":row["target"].value} for row in rows],
            "candidates":validate_spec(s.spec)["candidates"],"snapshot":p.get("snapshot_id")}

@app.post("/api/scenarios/validate")
async def patch_validate(patch:ScenarioPatch,request:Request):
    s=wb(request)
    if not s.project or not s.project["state"]: raise ValueError("尚无现场")
    return {"accepted":True,"candidates":validate_patch(s.spec,s.project["state"],patch)}

@app.post("/api/runs/compare")
async def compare(request:Request):
    s=wb(request)
    async with s.management_lock: return s.compare()

@app.post("/api/runs")
async def run(patch:ScenarioPatch,request:Request):
    s=wb(request)
    async with s.management_lock: return s.compare([patch])

@app.get("/api/results/{id}")
async def result(id:str,request:Request):
    value=wb(request).repo.get("result",id)
    if not value: raise HTTPException(404,"结果不存在")
    return value

@app.post("/api/tasks/control")
async def task_control(payload:dict,request:Request):
    s=wb(request); action=payload.get("action")
    if action=="show": s.visible.set()
    elif action=="hide": s.visible.clear()
    elif action=="pause": s.pause.set()
    elif action=="resume": s.pause.clear()
    elif action=="cancel":
        s.cancel.set(); s.pause.clear()
        if s.active_task:
            t=s.repo.get("task",s.active_task); t["status"]="cancelling"; s.repo.put("task",t["id"],t,t["project_id"])
    else: raise ValueError("未知任务操作")
    s.touch(); return {"requested":action}

@app.post("/api/native/view")
async def native_view(request:Request):
    s=wb(request)
    if s.active_task:
        s.visible.set(); return {"requested":"show_current_run"}
    async with s.management_lock:
        s.require_idle()
        snap=await s.freeze()
        s.visible.set()
        return s.submit("restore",{**compile_input(s.spec),"snapshot":snap,"restore_only":True})

@app.get("/api/replay")
async def replay(request:Request):
    s=wb(request); p=s.project
    return s.repo.frames(p["id"],p["cursor"]) if p else []

@app.post("/api/checkpoints")
async def save(request:Request):
    s=wb(request)
    async with s.management_lock: return await s.save_checkpoint()

@app.post("/api/checkpoints/{id}/restore")
async def restore(id:str,request:Request):
    s=wb(request)
    async with s.management_lock: return await s.restore(id)

@app.get("/api/checkpoints/{id}/export")
async def export(id:str,request:Request):
    row=wb(request).repo.get("checkpoint",id)
    if not row: raise HTTPException(404,"存档不存在")
    return FileResponse(Path(row["folder"]).with_suffix(".zip"),filename=id+".zip")

@app.get("/api/projects")
async def projects(request:Request):
    return [{"id":p["id"],"name":p["name"],"created_at":p["created_at"]} for p in wb(request).repo.list("project")]

@app.get("/api/diagnostics/{id}")
async def diagnostic(id:str,request:Request): return wb(request).repo.get("diagnostic",id)

dist=BASE/"frontend/dist"
if (dist/"index.html").exists() and (dist/"assets").is_dir():
    app.mount("/assets",StaticFiles(directory=dist/"assets"),name="assets")
    @app.get("/{path:path}")
    async def frontend(path:str):
        if path.startswith("api/"): raise HTTPException(404)
        return FileResponse(dist/"index.html")

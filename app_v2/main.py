"""Authenticated local production workbench. All production views originate in RDF."""
from __future__ import annotations
import asyncio
import json
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException, Depends, Response, Query
from fastapi.responses import JSONResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from .common import BASE, DATA, canonical, uid, now, atomic_write
from .contracts import Login, NewAccount, BusinessRevision, CompareRequest, RunRequest, SourceRecordEnvelope
from .graph import OntologyStore, Ref, decode_record
from .runtime import OntologyRuntime
from .specification import ProductionSpec, default_spec, spec_from_graph, qualification, validate_ontology
from .service import Workbench
from .views import focused_graph, lineage, impact
from .bundles import make_bundle, verify_bundle


def create_app(root=DATA, workers=True):
    @asynccontextmanager
    async def lifespan(app):
        app.state.workbench=Workbench(root,port=int(os.getenv("ONTOLOGYSIM_PORT","8820")))
        if workers:await app.state.workbench.start()
        yield
        await app.state.workbench.close()
    app=FastAPI(title="OntologySim 生产推演工作台",version="0.2.0",lifespan=lifespan)
    def workbench(request:Request):return request.app.state.workbench
    def user(request:Request,w=Depends(workbench)):
        account=w.accounts.session(request.cookies.get("ontologysim_session"))
        if not account:raise HTTPException(401,"请登录")
        return account
    def writer(request:Request,account=Depends(user),w=Depends(workbench)):
        if account["role"] not in ("admin","analyst"):
            w.audit("access_denied","user/"+account["id"],reason=request.url.path)
            raise HTTPException(403,"审计员仅可查询、回放和导出证据")
        return account
    def administrator(account=Depends(user)):
        if account["role"]!="admin":raise HTTPException(403,"需要管理员角色")
        return account
    def actor(account):return "user/"+account["id"]
    def runtime(w,context):
        r=w.context(context)
        if not r:raise ValueError("请先导入生产工作空间")
        return r
    def internal(request,w):
        if not secrets.compare_digest(request.headers.get("x-source-token",""),w.source_token):raise HTTPException(403,"来源身份无效")

    @app.middleware("http")
    async def boundary(request,call_next):
        if request.method not in ("GET","HEAD","OPTIONS") and not request.url.path.startswith("/api/v2/internal/"):
            origin=request.headers.get("origin")
            if origin and origin!=str(request.base_url).rstrip("/"):return JSONResponse({"detail":"来源地址不匹配"},403)
            if request.headers.get("x-ontologysim")!="v2":return JSONResponse({"detail":"缺少应用请求标识"},403)
        if int(request.headers.get("content-length","0"))>80_000_000:return JSONResponse({"detail":"请求超过 80 MB"},413)
        response=await call_next(request)
        response.headers["X-Content-Type-Options"]="nosniff"
        if request.url.path.startswith("/api/"):response.headers["Cache-Control"]="no-store"
        return response

    @app.exception_handler(ValueError)
    async def invalid(request,ex):
        w=request.app.state.workbench;account=w.accounts.session(request.cookies.get("ontologysim_session"))
        w.audit("request_rejected",actor(account) if account else "service/api",reason=str(ex),details={"path":request.url.path})
        return JSONResponse({"detail":str(ex)},409 if "版本" in str(ex) else 400)

    @app.get("/api/v2/session")
    async def session(request:Request,w=Depends(workbench)):
        import hashlib
        return {"initialized":w.accounts.initialized(),"user":w.accounts.session(request.cookies.get("ontologysim_session")),"version":"0.2.0","data_directory_id":hashlib.sha256(str(w.root.resolve()).casefold().encode()).hexdigest()}
    @app.post("/api/v2/setup")
    async def setup(body:NewAccount,w=Depends(workbench)):
        if w.accounts.initialized():raise HTTPException(409,"已完成初始化")
        account=w.accounts.create(body.username,body.name,"admin",body.password)
        w.audit("administrator_created",actor(account),details={"user":account});return account
    @app.post("/api/v2/login")
    async def login(body:Login,response:Response,w=Depends(workbench)):
        token,account=w.accounts.login(body.username,body.password)
        response.set_cookie("ontologysim_session",token,httponly=True,samesite="strict",max_age=28800)
        w.audit("session_started",actor(account));return account
    @app.post("/api/v2/logout")
    async def logout(request:Request,response:Response,account=Depends(user),w=Depends(workbench)):
        w.accounts.logout(request.cookies["ontologysim_session"]);response.delete_cookie("ontologysim_session");w.audit("session_ended",actor(account));return {"ok":True}
    @app.get("/api/v2/users")
    async def users(account=Depends(administrator),w=Depends(workbench)):return w.accounts.users()
    @app.post("/api/v2/users")
    async def add_user(body:NewAccount,account=Depends(administrator),w=Depends(workbench)):
        if any(u["id"]==body.username for u in w.accounts.users()):raise ValueError("账号已存在")
        created=w.accounts.create(body.username,body.name,body.role,body.password);w.audit("account_created",actor(account),details=created);return created

    @app.get("/api/v2/state")
    async def state(context="live",account=Depends(user),w=Depends(workbench)):return w.state(context)
    @app.get("/api/v2/events")
    async def events(request:Request,account=Depends(user),w=Depends(workbench)):
        async def stream():
            last=int(request.headers.get("last-event-id","-1"))
            while not await request.is_disconnected():
                if last!=w.revision:
                    last=w.revision;yield f"id: {last}\nevent: revision\ndata: {canonical({'revision':last,'active_task':w.active_task})}\n\n"
                else:yield ": heartbeat\n\n"
                await asyncio.sleep(1)
        return StreamingResponse(stream(),media_type="text/event-stream",headers={"X-Accel-Buffering":"no"})
    @app.post("/api/v2/workspaces")
    async def workspace(body:dict,account=Depends(writer),w=Depends(workbench)):
        spec=ProductionSpec.model_validate(body["specification"]) if body.get("specification") else default_spec()
        return await w.create_workspace(spec,actor(account))
    @app.post("/api/v2/source/{action}")
    async def source(action:str,body:dict,account=Depends(writer),w=Depends(workbench)):
        if action=='retry':return await w.retry_source(actor(account))
        if action=='restart':return await w.create_workspace(spec_from_graph(runtime(w,'live').graph),actor(account))
        w.source_control(action,actor(account),body.get("speed"));return {"ok":True}
    @app.post("/api/v2/internal/mes")
    async def mes(body:SourceRecordEnvelope,request:Request,w=Depends(workbench)):
        internal(request,w)
        if body.source!="MES":raise ValueError("HTTP 接收端仅接收 MES")
        return w.ingest(body)
    @app.get("/api/v2/internal/cursor")
    async def cursor(request:Request,workspace:str,w=Depends(workbench)):
        internal(request,w);active=w.active()
        return {"cursor":active["cursor"] if active and active["id"]==workspace else -1}
    @app.post("/api/v2/checkpoints")
    async def checkpoint(account=Depends(writer),w=Depends(workbench)):return w.checkpoint(actor(account))
    @app.post("/api/v2/compare")
    async def compare(body:CompareRequest,account=Depends(writer),w=Depends(workbench)):return w.compare(body.checkpoint_id,actor(account),body.repetitions)
    @app.post("/api/v2/scenarios")
    async def scenario(body:RunRequest,account=Depends(writer),w=Depends(workbench)):
        r=runtime(w,body.checkpoint_id)
        for jid,rid in body.patch.assignments.items():
            job=r.graph.get(jid)
            if not job or job["status"]!="queued":raise ValueError("仅可改派等待工件")
            candidate=next((c for c in qualification(r.graph,r.operation(job)["id"]) if c["resource"]==rid),None)
            if not candidate or not candidate["eligible"]:raise ValueError(candidate["reason"] if candidate else "设备不存在")
        return w.compare(body.checkpoint_id,actor(account),patch=body.patch.model_dump())
    @app.post("/api/v2/tasks/{task_id}/{action}")
    async def control(task_id:str,action:str,account=Depends(writer),w=Depends(workbench)):return w.task_control(task_id,action,actor(account))
    @app.get("/api/v2/tasks/{task_id}/result")
    async def result(task_id:str,account=Depends(user),w=Depends(workbench)):
        task=w.catalog.get(task_id)
        if not task or task["type"]!="Task":raise ValueError("任务不存在")
        if task["kind"]=="baseline":raise HTTPException(403,"现场未来轨迹由数据源隔离保存")
        if task_id in w.progress:return {"task":task,"summary":w.progress[task_id],"native":json.loads(task.get("native","{}"))}
        return {"task":task,"summary":runtime(w,task_id).summary(),"native":json.loads(task.get("native","{}"))}
    @app.post("/api/v2/checkpoints/{checkpoint_id}/{action}")
    async def restore(checkpoint_id:str,action:str,account=Depends(writer),w=Depends(workbench)):
        if action not in ("restore","continuation","view"):raise ValueError("无效恢复操作")
        cp=w.catalog.get(checkpoint_id)
        if not cp or cp["type"]!="Checkpoint":raise ValueError("本体存档不存在")
        return w.submit(action,actor(account),str(cp["workspace"]),checkpoint=cp["path"],checkpoint_id=checkpoint_id)
    @app.get("/api/v2/qualification")
    async def candidates(operation:str,context="live",account=Depends(user),w=Depends(workbench)):return qualification(runtime(w,context).graph,operation)
    @app.get("/api/v2/graph")
    async def graph(focus="",depth:int=Query(1,ge=1,le=3),limit:int=Query(40,ge=1,le=40),view="relations",expand:bool=False,relation="",context="live",account=Depends(user),w=Depends(workbench)):
        return focused_graph(runtime(w,context),focus,depth,limit,view,expand,relation)
    @app.get("/api/v2/impact")
    async def impacts(context="live",account=Depends(user),w=Depends(workbench)):return impact(runtime(w,context))
    @app.get("/api/v2/evidence")
    async def evidence(identity:str,context="live",account=Depends(user),w=Depends(workbench)):
        graph=w.catalog if context=="catalog" else runtime(w,context).graph
        if identity.startswith('schema/'):
            from .graph import SCHEMA,iri
            rows=list(graph.store.quads_for_pattern(iri(identity.removeprefix('schema/')),None,None,SCHEMA))
            if not rows:raise ValueError('本体概念不存在')
            attributes={'概念 URI':iri(identity.removeprefix('schema/')).value,'本体版本':'2.0'}
            for row in rows:
                name=row.predicate.value.rsplit('#',1)[-1].rsplit('/',1)[-1]
                if name in ('label','subClassOf','closeMatch','type'):
                    label={'label':'定义名称','subClassOf':'正式父类','closeMatch':'近似标准映射','type':'定义类型'}[name]
                    attributes[label]=row.object.value
            proof=lineage(graph,'ontology_contract');return {**proof,'entity':attributes}
        if not graph.get(identity) and w.catalog.get(identity):graph=w.catalog
        return lineage(graph,identity)
    @app.get("/api/v2/history")
    async def history(context="live",limit:int=Query(60,ge=1,le=200),account=Depends(user),w=Depends(workbench)):
        graph=w.catalog if context=="catalog" else runtime(w,context).graph
        return {"head":graph.sequence,"hash":graph.head,"records":[{**r,"payload":{k:v for k,v in r["payload"].items() if k not in ("changes","provenance")}} for r in graph.records(limit=limit)]}
    @app.get("/api/v2/replay")
    async def replay(sequence:int=Query(...,ge=1),context="live",account=Depends(user),w=Depends(workbench)):
        graph=runtime(w,context).graph
        if sequence>graph.sequence:raise ValueError("历史版本不存在")
        rows=graph.view_at(sequence);return {"sequence":sequence,"rows":[r for r in rows if r['type'] in ('Workpiece','AssetState','Batch','WorkOrder','Execution','Clock')],"mode":"historical_state"}
    @app.get("/api/v2/sources")
    async def sources(context="live",account=Depends(user),w=Depends(workbench)):
        graph=runtime(w,context).graph
        from .graph import STATE,iri,decode,RDF
        query=f'SELECT ?s WHERE {{GRAPH {STATE} {{?s <{RDF}type> {iri("SourceRecord")}; {iri("received_at")} ?at}}}} ORDER BY DESC(?at) LIMIT 80'
        rows=[graph.get(str(decode(row['s']))) for row in graph.store.query(query)]
        count=next(iter(graph.store.query(f'SELECT (COUNT(?s) AS ?count) WHERE {{ GRAPH {STATE} {{ ?s <{RDF}type> {iri("SourceRecord")} }} }}')))
        return {"total":int(count['count'].value),"records":[{k:v for k,v in r.items() if k!="payload"} for r in rows],"issues":graph.entities('SourceValidation'),"mapping":[{"equipment":r["id"],"mes":r["mes_id"],"opc":r["opc_id"],"conversion":"OPC ms ÷ 1000 → s"} for r in graph.entities("Equipment")]}
    @app.get("/api/v2/validate")
    async def validate(context="live",account=Depends(user),w=Depends(workbench)):return validate_ontology(runtime(w,context).graph)

    @app.post("/api/v2/revisions/validate")
    async def revision(body:BusinessRevision,account=Depends(writer),w=Depends(workbench)):
        ws=w.active()
        if not ws or body.workspace!=ws["id"] or body.base_revision!=ws["spec_revision"]:raise ValueError("主数据版本已变化")
        spec=ProductionSpec.model_validate(body.specification);original=spec_from_graph(w.graph()).model_dump();updated=spec.model_dump()
        differences=[]
        def diff(a,b,path=""):
            if type(a)!=type(b):differences.append({"path":path,"before":a,"after":b})
            elif isinstance(a,dict):
                for key in sorted(set(a)|set(b)):diff(a.get(key),b.get(key),path+"/"+key)
            elif isinstance(a,list):
                for i in range(max(len(a),len(b))):diff(a[i] if i<len(a) else None,b[i] if i<len(b) else None,path+"/"+str(i))
            elif a!=b:differences.append({"path":path,"before":a,"after":b})
        diff(original,updated)
        trial=OntologyStore();OntologyRuntime.create(trial,spec);report=validate_ontology(trial)
        row=dict(id=uid("revision"),type="BusinessRevision",workspace=Ref(ws["id"]),base_revision=body.base_revision,specification=canonical(updated),reason=body.reason,created_at=now(),differences=canonical(differences),conforms=report["conforms"])
        w.audit("business_revision_validated",actor(account),used=[ws["id"]],changes=[row],details={"report":report})
        return {"id":row["id"],"differences":differences,"validation":report,"impact":{"orders":[o.id for o in spec.orders],"bound_existing_runs":"原有批次与方案仍使用原版本","publication":"发布为新的生产工作空间，当前空间自动归档"}}
    @app.post("/api/v2/revisions/{revision_id}/publish")
    async def publish(revision_id:str,account=Depends(writer),w=Depends(workbench)):
        row=w.catalog.get(revision_id);ws=w.active()
        if not row or row["type"]!="BusinessRevision" or not row["conforms"]:raise ValueError("修订不存在或未通过校验")
        if row["workspace"]!=ws["id"] or row["base_revision"]!=ws["spec_revision"]:raise ValueError("主数据版本冲突，请重新校验")
        task=await w.create_workspace(ProductionSpec.model_validate_json(row["specification"]),actor(account))
        w.audit("business_revision_published",actor(account),used=[revision_id,task["workspace"]]);return task

    @app.get("/api/v2/export")
    async def export(context="live",account=Depends(user),w=Depends(workbench)):
        graph=runtime(w,context).graph
        w.audit("evidence_exported",actor(account),used=[context],details={"sequence":graph.sequence,"hash":graph.head})
        artifacts={};record=w.catalog.get(context)
        if record and record['type']=='Task':
            for name in ('final.spp','restored.spp'):
                path=Path(record['output'])/name
                if path.exists():artifacts['native.spp']=path.read_bytes();break
        data=make_bundle(graph,w.signer,{"context":context,"created_at":now(),"software":"0.2.0","native":"2606.0006","restorable":context!="live"},artifacts)
        return Response(data,media_type="application/zip",headers={"Content-Disposition":'attachment; filename="ontologysim-v02-evidence.zip"'})
    @app.post("/api/v2/import")
    async def import_bundle(request:Request,account=Depends(writer),w=Depends(workbench)):
        report,trig,manifest=verify_bundle(await request.body(),request.query_params.get("fingerprint"))
        if not report["valid"]:raise ValueError("证据包校验失败")
        if not manifest["metadata"].get("restorable"):raise ValueError("现场证据包仅供核查；请导入分析基线或运行存档")
        if not w.active():raise ValueError("请先创建工作空间以关联外部存档")
        path=w.root/"imports"/(uid("import")+".trig");atomic_write(path,trig)
        cp=w.register_checkpoint(path,w.active()["id"],actor(account),"外部本体存档")
        return {"checkpoint":cp,"verification":report}
    @app.get("/api/v2/verify")
    async def verify(context="live",account=Depends(user),w=Depends(workbench)):
        graph=w.catalog if context=="catalog" else runtime(w,context).graph
        report=graph.verify(w.signer.fingerprint);report.update(fingerprint=w.signer.fingerprint,sequence=graph.sequence,head=graph.head)
        w.audit("evidence_verified",actor(account),details=report);return report
    @app.get("/api/v2/health")
    async def health(account=Depends(user),w=Depends(workbench)):
        return {"version":"0.2.0","worker_alive":bool(w.process and w.process.is_alive()),"native_owner":w.active_task,"source":w.source_status,"fingerprint":w.signer.fingerprint,"environment":"ontologysim-v02","storage":"Oxigraph / RDF authoritative state","program_id":"Tecnomatix.PlantSimulation.RemoteControl.26.6"}
    @app.get('/api/v2/legacy')
    async def legacy(account=Depends(user)):
        import sqlite3
        path=BASE/'.runtime/workbench.sqlite'
        if not path.exists():return {'objects':[],'read_only':True,'notice':'未检测到 V0.1 历史库'}
        db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
        try:rows=db.execute("SELECT kind,id,project,data,updated FROM objects WHERE kind IN ('project','result','checkpoint') ORDER BY updated DESC LIMIT 60").fetchall()
        finally:db.close()
        return {'objects':[{'kind':r[0],'id':r[1],'project':r[2],'data':json.loads(r[3]),'updated':r[4]} for r in rows], 'read_only':True,'notice':'V0.1 只读历史：原记录缺少完整责任主体与签名，不补造证据；旧 JSON 快照不能直接恢复为 V0.2'}
    @app.post("/api/v2/internal/shutdown")
    async def shutdown_server(request:Request):
        if not secrets.compare_digest(request.headers.get("x-shutdown-token",""),getattr(app.state,"shutdown_token","!")):raise HTTPException(403,"退出令牌无效")
        if hasattr(app.state,"server"):app.state.server.should_exit=True
        return {"stopping":True}
    static=BASE/"frontend/dist"
    if static.exists():
        app.mount("/assets",StaticFiles(directory=static/"assets"),name="assets")
        @app.get("/")
        async def index():return FileResponse(static/"index.html")
    return app

app=create_app()

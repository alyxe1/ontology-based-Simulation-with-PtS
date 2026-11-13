import json
import pytest
from fastapi.testclient import TestClient
from app_v2.main import create_app
from app_v2.specification import default_spec
from app_v2.graph import Ref, encode_record, OntologyStore
from app_v2.worker import public_frame
from app_v2.runtime import OntologyRuntime
from app_v2.bundles import verify_bundle
from app_v2.contracts import SourceRecordEnvelope

@pytest.fixture
def web(tmp_path,monkeypatch):
    monkeypatch.setattr("app_v2.service.REFERENCE_DIR",tmp_path/"no-packaged-reference")
    with TestClient(create_app(tmp_path,workers=False),headers={"x-ontologysim":"v2"}) as client:
        assert client.post('/api/v2/setup',json=dict(username='owner',name='Test owner',password='test-only-long-password',role='admin')).status_code==200
        assert client.post('/api/v2/login',json=dict(username='owner',password='test-only-long-password')).status_code==200
        yield client

def initialize(client):
    r=client.post('/api/v2/workspaces',json={});assert r.status_code==200,r.text
    return client.app.state.workbench

def feed(w,cursor=0,at=0):
    runtime=OntologyRuntime(w.graph());frame=public_frame(runtime,{},cursor)
    ws=w.active()['id'];spec=default_spec()
    from app_v2.contracts import SourceRecordEnvelope
    mes=dict(workspace=ws,source='MES',raw_id=f'MES:{cursor}',cursor=cursor,event_time=at,unit='s',quality='good',synthetic=True,records=[r for r in frame['records'] if r['type']!='AssetState'])
    result=w.ingest(SourceRecordEnvelope(**mes))
    for resource,row in zip(sorted(spec.resources,key=lambda r:r.id),frame['assets']):
        w.ingest(SourceRecordEnvelope(workspace=ws,source='OPC',source_id=resource.opc_id,raw_id=f'OPC:{cursor}:{resource.id}',cursor=cursor,event_time=at*1000,unit='ms',quality='good',synthetic=True,record=row))
    return mes

def test_auth_roles_and_csrf(web):
    assert web.post('/api/v2/setup',json=dict(username='again',password='long-password',name='other',role='admin')).status_code==409
    assert web.post('/api/v2/users',json=dict(username='audit',password='audit-only-password',name='Auditor',role='auditor')).status_code==200
    web.post('/api/v2/logout');web.post('/api/v2/login',json=dict(username='audit',password='audit-only-password'))
    assert web.post('/api/v2/workspaces',json={}).status_code==403
    assert web.post('/api/v2/reset',json={'confirm':'清空工作台'}).status_code==403
    assert web.get('/api/v2/state').status_code==200
    assert web.post('/api/v2/logout',headers={'x-ontologysim':'wrong'}).status_code==403
    assert web.post('/api/v2/logout',headers={'Origin':'https://unexpected.test'}).status_code==403

def test_live_baseline_isolation_and_lineage(web):
    w=initialize(web);mes=feed(w);assert w.active()['cursor']==0
    from app_v2.contracts import SourceRecordEnvelope
    before=w.graph().sequence
    assert w.ingest(SourceRecordEnvelope(**mes))['duplicate']
    assert w.graph().sequence==before
    w.source_pause.clear()
    cp=web.post('/api/v2/checkpoints');assert cp.status_code==200,cp.text
    assert not w.source_pause.is_set(),'creating baseline stopped live acquisition'
    cid=cp.json()['id'];fixed=w.context(cid).graph.head
    feed(w,1,10)
    assert w.context(cid).graph.head==fixed and w.context(cid).time==0 and OntologyRuntime(w.graph()).time==10
    sources=web.get('/api/v2/sources');assert sources.status_code==200,sources.text
    assert sources.json()['total']==15 and len(sources.json()['mapping'])==6
    schema=web.get('/api/v2/graph',params={'view':'schema'}).json();assert schema['edges'] and len(schema['nodes'])<=40
    definition=web.get('/api/v2/evidence',params={'identity':'schema/Equipment'}).json()
    assert 'PieceOfEquipment' in definition['entity']['近似标准映射']
    source=web.get('/api/v2/evidence',params={'identity':'source/erp/initial'}).json()
    assert source['entity']['source']=='ERP' and any(a['kind']=='factory_import' for a in source['activities'])
    assert web.get('/api/v2/replay',params={'sequence':3}).status_code==200
    task=w.tasks()[0]
    assert web.get('/api/v2/state',params={'context':task['id']}).status_code==400


def test_replay_freezes_real_observation_boundaries(web):
    w=initialize(web);feed(w);feed(w,1,10)
    timeline=web.get('/api/v2/replay/timeline').json()
    assert [f['time_s'] for f in timeline['frames']]==[0,10]
    feed(w,2,20)
    frame=web.get('/api/v2/replay/frame',params={'index':1,'head':timeline['head']}).json()
    assert frame['time_s']==10 and frame['total']==2 and len(frame['equipment'])==6
    assert len(frame['jobs'])==120 and frame['jobs'][0]['operation_name']=='毛坯下料'
    assert web.get('/api/v2/replay/frame',params={'index':2,'head':timeline['head']}).status_code==400
    assert w.graph().get('clock')['time_s']==20


def test_remove_baseline_and_reset_leave_empty_workbench_with_signed_history(web):
    w=initialize(web);feed(w)
    assert web.post('/api/v2/reset',json={'confirm':'清空工作台'}).status_code==400 # queued native work
    for t in w.tasks():web.post(f"/api/v2/tasks/{t['id']}/cancel",json={})
    checkpoint=web.post('/api/v2/checkpoints').json()
    assert web.delete('/api/v2/contexts/'+checkpoint['id']).status_code==200
    assert web.post('/api/v2/compare',json={'checkpoint_id':checkpoint['id']}).status_code==400
    assert web.get('/api/v2/state').json()['checkpoints']==[]
    assert web.post('/api/v2/reset',json={'confirm':'清空工作台'}).status_code==200
    state=web.get('/api/v2/state').json()
    assert state['workspace'] is None and state['runtime'] is None
    assert not (state['workspaces'] or state['checkpoints'] or state['tasks'])
    assert web.get('/api/v2/session').json()['user']['role']=='admin'
    assert w.catalog.verify()['valid']
    assert checkpoint['id'] in {r['id'] for r in w.catalog.entities('Checkpoint')}
    assert all(r['payload']['sequence']>=state['reset']['sequence_after'] for r in web.get('/api/v2/history?context=catalog').json()['records'])


def test_remove_current_workspace_keeps_unrelated_archive(web):
    w=initialize(web)
    old=w.active()['id']
    for t in w.tasks():web.post(f"/api/v2/tasks/{t['id']}/cancel",json={})
    initialize(web);current=w.active()['id']
    assert web.delete('/api/v2/contexts/'+current).status_code==400
    for t in w.tasks():
        if t['workspace']==current:web.post(f"/api/v2/tasks/{t['id']}/cancel",json={})
    assert web.delete('/api/v2/contexts/'+current).status_code==200
    state=web.get('/api/v2/state').json()
    assert state['workspace'] is None and [r['id'] for r in state['workspaces']]==[old]
    assert web.get('/api/v2/state',params={'context':old}).json()['runtime']

def test_revision_export_and_immutable_entities(web):
    w=initialize(web);feed(w)
    spec=default_spec().model_dump();spec['orders'][0]['due_s']+=900
    response=web.post('/api/v2/revisions/validate',json=dict(workspace=w.active()['id'],base_revision=1,specification=spec,reason='change due date'))
    assert response.status_code==200,response.text
    assert response.json()['differences'][0]['path']=='/orders/0/due_s'
    row=w.catalog.get(response.json()['id'])
    with pytest.raises(ValueError,match='不可覆盖'):w.catalog.commit([row],'overwrite')
    cid=web.post('/api/v2/checkpoints').json()['id']
    exported=web.get('/api/v2/export',params={'context':cid});assert exported.status_code==200
    report,_,_=verify_bundle(exported.content,w.signer.fingerprint)
    assert report['valid'] and report['ontology']['state_matches_journal']
    assert web.get('/api/v2/graph',params={'focus':'P1','depth':3,'expand':True}).json()['nodes'].__len__()<=40

def test_unknown_native_command_blocks_restore():
    graph=OntologyStore();r=OntologyRuntime.create(graph,default_spec())
    def lost():raise TimeoutError('response lost after native operation')
    with pytest.raises(TimeoutError):r.command(None,'test',lost,[])
    assert graph.entities('Command')[0]['status']=='unknown'
    with pytest.raises(ValueError,match='待核对'):r.assert_restorable()
    assert graph.verify()['valid']

def test_five_paired_groups_and_rejected_assignment(web):
    w=initialize(web);feed(w);cp=web.post('/api/v2/checkpoints').json()['id']
    response=web.post('/api/v2/compare',json={'checkpoint_id':cp,'repetitions':5})
    assert response.status_code==200
    tasks=response.json();assert len(tasks)==15
    payloads=[json.loads(t['payload']) for t in tasks]
    for group in range(5):
        paired=[p for p in payloads if p['sample_index']==group]
        assert len(paired)==3 and {p['seed'] for p in paired}=={5202+group}
    before=len(w.tasks())
    rejected=web.post('/api/v2/scenarios',json={'checkpoint_id':cp,'patch':{'name':'invalid','assignments':{'J00001':'CNC03'}}})
    assert rejected.status_code==400 and len(w.tasks())==before


def test_comparison_mapping_uses_sealed_abox_and_rejects_invalid_route(web):
    w=initialize(web)
    feed(w)
    cp=web.post('/api/v2/checkpoints').json()
    request={'checkpoint_id':cp['id'],'scenarios':[{'name':'交期优先','rule':'EDD'}]}
    response=web.post('/api/v2/compare/validate',json=request)
    assert response.status_code==200,response.text
    plan=response.json()
    mapping=plan['mapping']
    assert mapping['graph_head']==cp['graph_head'] and mapping['source_cursor']==0
    assert mapping['shacl']['conforms'] and mapping['seeds']==[5202]
    assert len(mapping['workpieces'])==120 and len(mapping['resources'])==6
    assert mapping['resources'][0]['station']=='.Models.Model.OntologySim.R0.Machine'
    assert mapping['workpieces'][0]['native_path'] is None
    assert any(op['id']=='P1/CUT' and op['next_operation']=='P1/ROUGH' for op in mapping['operations'])
    graph=w.context(cp['id']).graph
    job=graph.get('J00001')
    job['op_index']=999
    graph.commit([job],'test_invalid_comparison_input')
    count=len(w.tasks())
    for route in ('/compare/validate','/compare'):
        rejected=web.post('/api/v2'+route,json=request)
        assert rejected.status_code==400 and 'J00001' in rejected.json()['detail']
    assert len(w.tasks())==count

def test_reset_reuses_only_verified_unchanged_trace(web,monkeypatch):
    from pathlib import Path
    import hashlib
    w=initialize(web);task=w.tasks()[0];trace=Path(task['output'])/'source.jsonl';trace.parent.mkdir(parents=True,exist_ok=True);trace.write_text('test-only controlled reference',encoding='utf-8')
    task.update(status='completed',native=json.dumps({'verified':True}),trace_hash=hashlib.sha256(trace.read_bytes()).hexdigest());w.audit('test_fixture',changes=[task])
    started=[]
    async def start(workspace):started.append(workspace['id'])
    monkeypatch.setattr(w,'start_source',start)
    response=web.post('/api/v2/workspaces',json={});assert response.status_code==200,response.text
    assert response.json()['kind']=='source_reused' and len(w.tasks())==1 and started
    assert w.active()['cursor']==-1
    trace.write_text('changed reference',encoding='utf-8')
    response=web.post('/api/v2/workspaces',json={});assert response.json()['kind']=='baseline' and len(w.tasks())==2


def test_source_frame_commits_once_with_complete_lineage(web,monkeypatch):
    w=initialize(web);g=w.graph();initial=g.sequence
    audit=w.audit
    def lose_ack(kind,*args,**kwargs):
        if kind=='source_boundary_committed':raise OSError('simulated catalog write failure')
        return audit(kind,*args,**kwargs)
    monkeypatch.setattr(w,'audit',lose_ack)
    with pytest.raises(OSError,match='catalog write'):
        feed(w)
    assert w.active()['cursor']==-1 and g.get('clock')['source_cursor']==0
    assert g.sequence==initial+1 and len(g.entities('SourceRecord'))==8
    monkeypatch.setattr(w,'audit',audit)
    feed(w)
    assert w.active()['cursor']==0 and g.sequence==initial+1 and not w.pending
    trace=web.get('/api/v2/evidence',params={'identity':'CNC01/state'}).json()
    assert any(a['kind']=='observations_aligned' for a in trace['activities'])
    assert {'source/MES:0','source/OPC:0:CNC01'}<={source['id'] for source in trace['sources']}
    assert g.verify()['valid']


def test_incident_options_and_comparison_submission_are_bound_to_baseline(web,monkeypatch):
    w=initialize(web);g=w.graph()
    job=g.get('J00021');assert job['product']=='P2'
    job.update(status='queued',op_index=1,assigned=Ref('CNC01'),location=Ref('CNC01'),
               queue_seq=1,released_at=0.,queued_at=0.)
    asset=g.get('CNC01/state');asset.update(status='maintenance',maintenance_until=2700.)
    balance=g.get(str(job['material_lot'])+'/balance');balance['available']-=1;balance['consumed']+=1
    g.commit([job,asset,balance],'test_maintenance')
    feed(w)
    assert web.post('/api/v2/checkpoints',json={'incident_id':asset['id'],'source_cursor':1}).status_code==409
    cp=web.post('/api/v2/checkpoints',json={'incident_id':asset['id'],'source_cursor':0}).json()
    options=web.get('/api/v2/checkpoints/'+cp['id']+'/options').json()
    option=next(j for j in options['jobs'] if j['id']==job['id'])
    assert option['affected'] and option['assigned']=='CNC01' and options['incident_id']==asset['id']
    assert {r['resource'] for r in option['candidates'] if r['eligible']}=={'CNC01','CNC02'}
    assert next(r for r in option['candidates'] if r['resource']=='CNC01')['available_at_s']==2700
    body={'checkpoint_id':cp['id'],'scenarios':[{'name':'合格改派','assignments':{job['id']:'CNC02'}}]}
    preview=web.post('/api/v2/compare/validate',json=body).json()
    assert preview['task_count']==2 and preview['scenarios'][0]['name']=='保持现状'
    assert preview['scenarios'][1]['assignments'][0]['target']=='CNC02'
    invalid={**body,'scenarios':[*body['scenarios'],{'name':'资格不符','assignments':{job['id']:'CNC03'}}]}
    before=len(w.tasks())
    rejected=web.post('/api/v2/compare',json=invalid)
    assert rejected.status_code==400 and '材料' in rejected.json()['detail'] and len(w.tasks())==before
    tasks=web.post('/api/v2/compare',json=body).json()
    patches=[json.loads(t['payload']) for t in tasks]
    assert len(tasks)==2 and len({p['comparison_id'] for p in patches})==1 and {p['seed'] for p in patches}=={5202}
    assert patches[0]['patch']=={'name':'保持现状'}
    assert len(web.get('/api/v2/compare',params={'checkpoint_id':cp['id']}).json())==2
    w.submit_batch('scenario','service/test',w.active()['id'],[{} for _ in range(20)])
    assert web.post('/api/v2/compare',json=body).status_code==400
    assert len(w.tasks())==before+22


def test_unchanged_policy_keeps_an_existing_nonpreferred_assignment(monkeypatch):
    graph=OntologyStore();runtime=OntologyRuntime.create(graph,default_spec())
    job=graph.get('J00021');assert job['product']=='P2'
    job.update(status='queued',op_index=1,assigned=Ref('CNC02'),location=Ref('CNC02'))
    policy=runtime.policy;policy.update(rule='EDD',shift_hours=11.)
    graph.commit([job,policy],'test_nonpreferred_assignment')
    monkeypatch.setattr(runtime,'settle',lambda native:None)
    runtime.apply_policy(None,{'name':'保持现状'})
    assert runtime.policy['rule']=='EDD' and runtime.policy['shift_hours']==11
    assert graph.get(job['id'])['assigned']=='CNC02'
    assert not graph.entities('Command')

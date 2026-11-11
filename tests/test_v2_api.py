import json
import pytest
from fastapi.testclient import TestClient
from app_v2.main import create_app
from app_v2.specification import default_spec
from app_v2.graph import Ref, encode_record, OntologyStore
from app_v2.worker import public_frame
from app_v2.runtime import OntologyRuntime
from app_v2.bundles import verify_bundle

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

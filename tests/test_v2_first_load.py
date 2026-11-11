import json
import shutil
import pytest
from fastapi.testclient import TestClient
from app_v2.main import create_app
from app_v2.reference import REFERENCE_DIR, verified_reference
from app_v2.specification import default_spec


@pytest.fixture
def web(tmp_path,monkeypatch):
    with TestClient(create_app(tmp_path,workers=False),headers={'x-ontologysim':'v2'}) as client:
        client.post('/api/v2/setup',json=dict(username='loadtest',name='Load test',password='load-test-only-password',role='admin'))
        client.post('/api/v2/login',json=dict(username='loadtest',password='load-test-only-password'))
        starts=[]
        async def start(workspace):starts.append(workspace['id'])
        monkeypatch.setattr(client.app.state.workbench,'start_source',start)
        yield client,starts


def test_clean_first_load_uses_verified_source_not_a_native_job(web):
    client,starts=web
    loaded=client.post('/api/v2/workspaces',json={})
    assert loaded.status_code==200,loaded.text
    assert loaded.json()['kind']=='source_reused' and starts
    state=client.get('/api/v2/state').json()
    assert state['tasks']==[] and state['runtime']['time_s']==0
    assert state['preparation']['status']=='connecting'
    assert state['workspace']['cursor']==-1
    w=client.app.state.workbench
    generation=w.graph().entities('SourceGeneration')[0]
    assert generation['native_verified'] and generation['origin']=='packaged-native-reference'
    manifest=w.graph().get(generation['source'])
    assert manifest['source']=='REFERENCE' and json.loads(manifest['payload'])['payload']['trace_hash']==generation['trace_hash']
    assert str(w.root/'source-vault') in w.active()['trace_path']
    # Frozen prediction state cannot contain unpublished reference observations.
    assert w.graph().entities('Observation')==[]
    assert client.post('/api/v2/checkpoints').status_code==400


def test_changed_configuration_recalculates_and_shows_progress(web):
    client,_=web;spec=default_spec().model_dump();spec['products'][0]['operations'][0]['duration_s']+=1
    loaded=client.post('/api/v2/workspaces',json={'specification':spec}).json()
    assert loaded['kind']=='baseline'
    w=client.app.state.workbench;task=w.catalog.get(loaded['id'])
    task.update(status='running',time_s=spec['horizon_s']/2);w.audit('test_progress',changes=[task])
    preparation=client.get('/api/v2/state').json()['preparation']
    assert preparation['percent']==50 and preparation['status']=='running'
    task.update(status='failed',error='测试许可失败');w.audit('test_failure',changes=[task])
    preparation=client.get('/api/v2/state').json()['preparation']
    assert preparation['status']=='failed' and preparation['message']=='测试许可失败'
    retry=client.post('/api/v2/source/retry',json={})
    assert retry.status_code==200 and retry.json()['workspace']==w.active()['id']


def test_packaged_reference_corruption_is_rejected(tmp_path):
    assert verified_reference(default_spec().model_dump()) is not None
    for name in ('manifest.json','manifest.sha256','source.jsonl'):shutil.copyfile(REFERENCE_DIR/name,tmp_path/name)
    with (tmp_path/'source.jsonl').open('a',encoding='utf-8') as stream:stream.write('tampered')
    with pytest.raises(ValueError,match='内容校验失败'):verified_reference(default_spec().model_dump(),tmp_path)


def test_source_end_is_not_reported_as_a_connection_failure(web):
    client,_=web
    client.post('/api/v2/workspaces',json={})
    w=client.app.state.workbench;workspace=w.active()
    workspace.update(cursor=957,trace_end_cursor=957)
    w.audit('test_end',changes=[workspace])
    state=client.get('/api/v2/state').json()
    assert state['preparation']['status']=='complete' and state['source']['exhausted']
    assert client.post('/api/v2/source/incident',json={}).status_code==400
    restarted=client.post('/api/v2/source/restart',json={})
    assert restarted.status_code==200,restarted.text
    assert w.catalog.get(workspace['id'])['status']=='archived'
    assert w.active()['cursor']==-1 and w.active()['id']!=workspace['id']

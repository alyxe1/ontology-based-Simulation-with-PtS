"""Real HTTP + OPC UA + native COM end-to-end acceptance; no fake engine."""
import copy
import json
import os
import subprocess
import sys
import time
from pathlib import Path
import httpx
import psutil
from app.contracts import digest

ROOT=Path(__file__).resolve().parents[1]
client=httpx.Client(base_url="http://127.0.0.1:8820",timeout=120,trust_env=False)
proof={}

def get(path):
    r=client.get(path); r.raise_for_status(); return r.json()
def post(path,payload=None):
    r=client.post(path,json=payload or {}); r.raise_for_status(); return r.json()
def until(predicate,timeout=180):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        value=predicate()
        if value:return value
        time.sleep(.2)
    raise AssertionError("Timed out waiting for condition")
def task_done(id):
    def check():
        task=next((t for t in get('/api/state')['tasks'] if t['id']==id),None)
        if task and task['status'] in ['failed','interrupted','cancelled']: raise AssertionError(task)
        return task if task and task['status']=='completed' else False
    return until(check)
def show(step): print(step,flush=True)
def incident_ready():
    s=get('/api/state')
    return s if s['source']['paused'] and any(a['status']=='maintenance' for a in (s['project'].get('state') or {}).get('assets',{}).values()) else False

def main():
    show('load native reference factory')
    task=post('/api/demo/load');task_done(task['id'])
    until(lambda:get('/api/state')['project']['cursor']>=0)
    before=get('/api/state'); project=before['project']['id']
    records=get('/api/ingest')
    assert {c['source'] for c in records['counts']}=={'ERP_FILE','MES','OPC_UA'}
    mes=next(r['payload'] for r in records['records'] if r['source']=='MES')
    duplicate=post('/api/ingest/mes',mes); assert duplicate['duplicate']
    bad=copy.deepcopy(mes);bad['raw_id']='invalid-mapping-proof';next(iter(bad['state']['jobs'].values()))['location']='UNKNOWN-MES-MACHINE'
    response=client.post('/api/ingest/mes',json=bad); assert response.status_code==409,response.text
    proof['three_real_ingestion_channels']=True;proof['deduplication']=True;proof['unmapped_record_quarantined']=True
    show('advance through real protocol subscriptions to maintenance')
    post('/api/source/control',{'action':'incident'})
    incident=until(incident_ready)
    snapshot=post('/api/snapshots');initial_hash=snapshot['state_hash']; initial_cursor=snapshot['source_cursor']
    kernel=json.loads((ROOT/'.runtime/kernel-acceptance/snapshot.json').read_text(encoding='utf-8'))
    assert digest(snapshot['state'])==digest(kernel['state']), 'Protocol state differs from native generated reference'
    assert get('/api/impact')['orders']
    proof['protocol_projection_equals_native']=True;proof['freeze_time_s']=snapshot['state']['time'];proof['source_cursor']=initial_cursor
    jid=next(jid for jid,j in snapshot['state']['jobs'].items() if j['product']=='P1' and j['status']=='queued' and j['op']==1)
    rejected=client.post('/api/scenarios/validate',json={'name':'Invalid assignment','assignments':{jid:'CNC03'}})
    assert rejected.status_code==409;proof['ineligible_assignment_rejected']=True
    show('disconnect and reconnect OPC server and subscriber')
    post('/api/source/control',{'action':'reconnect'})
    until(lambda:get('/api/state')['source']['state']=='connected')
    assert digest(get('/api/state')['project']['state'])==initial_hash
    proof['source_reconnect_preserves_cursor']=True
    show('compare three native scenarios; exercise pause/resume')
    tasks=post('/api/runs/compare')
    until(lambda:any(t['id']==tasks[0]['id'] and t['status']=='running' for t in get('/api/state')['tasks']))
    post('/api/tasks/control',{'action':'pause'})
    until(lambda:any(t['id']==tasks[0]['id'] and t['status']=='paused' for t in get('/api/state')['tasks']))
    time.sleep(.3);post('/api/tasks/control',{'action':'resume'})
    for task in tasks:task_done(task['id'])
    results=[get('/api/results/'+task['run_id']) for task in tasks]
    assert len({r['snapshot_id'] for r in results})==1
    assert len({r['kpi']['window_end_s'] for r in results})==1
    assert all(r['native']['verified'] for r in results)
    proof['pause_resume']=True;proof['native_scenarios']=[{'name':r['patch']['name'],'kpi':r['kpi']} for r in results]
    frames=get('/api/replay');assert frames[-1]['hash']==initial_hash
    assert all(f['cursor']<=initial_cursor for f in frames)
    proof['no_future_observations_in_replay']=True
    show('save, graceful exit, restart, rebuild and verify')
    saved=post('/api/checkpoints');assert saved['hash']==initial_hash
    export=client.get('/api/checkpoints/'+saved['id']+'/export');assert export.status_code==200 and export.content[:2]==b'PK'
    old=get('/api/state');nativepid=old['owner']['pid']
    server_info=json.loads((ROOT/'.runtime/server.json').read_text())
    response=client.post('/api/admin/shutdown',headers={'x-local-control':server_info['token']});response.raise_for_status()
    until(lambda:not psutil.pid_exists(server_info['pid']),timeout=40)
    until(lambda:not psutil.pid_exists(nativepid),timeout=15)
    proof['owned_native_session_released']=True
    log=(ROOT/'.runtime/restarted-server.log').open('w')
    process=subprocess.Popen([sys.executable,'-m','scripts.serve'],cwd=ROOT,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW)
    def healthy():
        try:return get('/api/health')['status']=='ok'
        except httpx.HTTPError:return False
    until(healthy,40)
    restored=post('/api/checkpoints/'+saved['id']+'/restore');task_done(restored['id'])
    current=get('/api/state');assert digest(current['project']['state'])==initial_hash
    assert current['project']['restored']
    continued=post('/api/runs',{'name':'Restored FIFO continuation'})[0];task_done(continued['id'])
    continued_result=get('/api/results/'+continued['run_id'])
    assert continued_result['events']==results[0]['events']
    assert continued_result['state']==results[0]['state']
    proof['save_exit_reload_continue_identical']=True
    show('cancel one native task without treating it as success')
    cancelled=post('/api/runs',{'name':'Cancellation acceptance'})[0]
    until(lambda:any(t['id']==cancelled['id'] and t['status']=='running' for t in get('/api/state')['tasks']))
    post('/api/tasks/control',{'action':'cancel'})
    until(lambda:any(t['id']==cancelled['id'] and t['status']=='cancelled' for t in get('/api/state')['tasks']))
    assert client.get('/api/results/'+cancelled['run_id']).status_code==404
    proof['cancellation_is_not_completion']=True
    show('reset, auto archive, and reproduce same maintenance snapshot')
    reset=post('/api/demo/reset');task_done(reset['id'])
    until(lambda:get('/api/state')['project']['cursor']>=0)
    assert get('/api/state')['project']['id']!=project
    post('/api/source/control',{'action':'incident'})
    until(incident_ready)
    repeated=post('/api/snapshots');assert repeated['state_hash']==initial_hash
    proof['reset_reproducible_and_project_isolated']=True
    proof['passed']=True
    (ROOT/'artifacts/acceptance/integration.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2),encoding='utf-8')
    show('ALL INTEGRATION CHECKS PASSED')

if __name__=='__main__': main()

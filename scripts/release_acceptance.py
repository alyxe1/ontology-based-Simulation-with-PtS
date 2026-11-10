"""Focused verification of the final UI/API additions without resetting the demo."""
import copy
import json
import time
from pathlib import Path
import httpx
from app.contracts import digest

def main():
    client=httpx.Client(base_url='http://127.0.0.1:8820',trust_env=False,timeout=90)
    def get(path):
        r=client.get('/api'+path);r.raise_for_status();return r.json()
    def post(path,payload=None):
        r=client.post('/api'+path,json=payload or {});r.raise_for_status();return r.json()
    start=get('/state');expected=digest(start['project']['state']);proof={}
    for view in ['relations','impact','schema']:
        graph=get('/semantic?view='+view);ids={n['id'] for n in graph['nodes']}
        assert graph['nodes'] and graph['edges']
        assert all(e['from'] in ids and e['to'] in ids for e in graph['edges'])
        proof[view]={'nodes':len(ids),'edges':len(graph['edges'])}
    first=post('/snapshots');second=post('/snapshots')
    assert first['id']==second['id'];proof['unchanged_snapshot_reused']=True
    for speed in [60,600]:
        reply=post('/source/control',{'action':'speed','speed':speed})
        assert reply['speed']==speed and reply['paused']
    assert digest(get('/state')['project']['state'])==expected;proof['speed_does_not_resume_paused_source']=True
    record=next(r for r in get('/ingest')['records'] if r['source']=='MES' and r['quality']=='good')
    assert post('/ingest/mes',record['payload'])['duplicate']
    conflict=copy.deepcopy(record['payload']);conflict['state']['completed']+=1
    rejected=client.post('/api/ingest/mes',json=conflict)
    assert rejected.status_code==409;proof['conflicting_duplicate_rejected']=True
    assert digest(get('/state')['project']['state'])==expected
    task=post('/native/view')
    for _ in range(300):
        state=get('/state');row=next(t for t in state['tasks'] if t['id']==task['id'])
        if row['status']=='failed':raise AssertionError(row)
        if row['status']=='completed':break
        time.sleep(.2)
    else:raise TimeoutError('Native view did not finish reconstruction')
    assert row['restore_evidence']['verified'];assert digest(state['project']['state'])==expected
    post('/tasks/control',{'action':'hide'})
    proof['native_view_reconstructs_current_scene']=True
    checkpoint=post('/checkpoints')
    archive=client.get('/api/checkpoints/'+checkpoint['id']+'/export');archive.raise_for_status()
    Path('artifacts/demo/customer-demo.zip').write_bytes(archive.content)
    Path('artifacts/demo/ready.json').write_text(json.dumps({'snapshot':checkpoint['snapshot_id'],'checkpoint':checkpoint['id'],
        'time_s':state['project']['state']['time'],'state_hash':expected,'run_ids':[r['run_id'] for r in state['results']]},indent=2),encoding='utf-8')
    proof['passed']=True
    Path('artifacts/acceptance/release.json').write_text(json.dumps(proof,indent=2),encoding='utf-8')
    print(json.dumps(proof),flush=True)

if __name__=='__main__':main()

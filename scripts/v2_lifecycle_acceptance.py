"""Checkpoint, pause/cancel, restart visibility and live-source reconnection checks."""
import json,time
import httpx
from app_v2.common import DATA,canonical
def main():
    c=httpx.Client(base_url='http://127.0.0.1:8842/api/v2',headers={'x-ontologysim':'v2'},timeout=90,trust_env=False)
    def post(path,body={}):
        r=c.post(path,json=body);r.raise_for_status();return r.json()
    def state():
        r=c.get('/state');r.raise_for_status();return r.json()
    def wait(test,timeout=120):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            s=state()
            if test(s):return s
            time.sleep(.5)
        raise TimeoutError('lifecycle condition not reached')
    post('/login',{'username':'acceptance','password':'acceptance-only-2026'})
    before=state();cursor=before['workspace']['cursor'];post('/source/play')
    resumed=wait(lambda s:s['source']['connected'] and s['workspace']['cursor']>cursor)
    post('/source/pause');cp=state()['checkpoints'][0]
    task=post('/checkpoints/'+cp['id']+'/continuation');tid=task['id']
    wait(lambda s:any(t['id']==tid and t['status']=='running' for t in s['tasks']))
    original=len(state()['tasks'])
    for _ in range(3):state()
    assert len(state()['tasks'])==original,'refresh created a new task'
    post('/tasks/'+tid+'/pause')
    paused=wait(lambda s:any(t['id']==tid and t.get('checkpoint_id') for t in s['tasks']),240)
    captured=next(t['checkpoint_id'] for t in paused['tasks'] if t['id']==tid)
    post('/tasks/'+tid+'/cancel')
    cancelled=wait(lambda s:any(t['id']==tid and t['status']=='cancelled' for t in s['tasks']))
    with c.stream('GET','/events',headers={'last-event-id':'-1'}) as stream:
        lines=[]
        for line in stream.iter_lines():
            lines.append(line)
            if line.startswith('data:'):break
    assert any(line.startswith('id:') for line in lines)
    verified=post('/checkpoints/'+captured+'/restore')
    final=wait(lambda s:any(t['id']==verified['id'] and t['status'] in ('completed','failed') for t in s['tasks']),240)
    done=next(t for t in final['tasks'] if t['id']==verified['id']);assert done['status']=='completed',done
    result=c.get('/tasks/'+verified['id']+'/result').json()
    assert result['native']['native_clock_s']==0
    report=dict(passed=True,source_reconnected=True,cursor_before=cursor,cursor_after=resumed['workspace']['cursor'],refresh_did_not_rerun=True,sse_reconnected=True,cancelled=tid,captured=captured,restored=verified['id'],native=result['native'])
    (DATA/'acceptance/lifecycle.json').write_text(canonical(report),encoding='utf-8');print(canonical(report),flush=True)
if __name__=='__main__':main()

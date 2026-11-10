"""Populate the delivered workbench using the same public API as the browser."""
import json
import time
from pathlib import Path
import httpx

def main():
    client=httpx.Client(base_url='http://127.0.0.1:8820',timeout=90,trust_env=False)
    def get(path):
        r=client.get('/api'+path);r.raise_for_status();return r.json()
    def post(path):
        r=client.post('/api'+path,json={});r.raise_for_status();return r.json()
    def wait(condition):
        for _ in range(1200):
            state=get('/state')
            failed=[t for t in state['tasks'] if t['status']=='failed']
            if failed:raise RuntimeError(failed[0]['message'])
            if condition(state):return state
            time.sleep(.2)
        raise TimeoutError('Demo preparation did not complete')
    post('/demo/load');print('Generating native reference',flush=True)
    wait(lambda s:s['project']['cursor']>=0)
    r=client.post('/api/source/control',json={'action':'incident'});r.raise_for_status()
    wait(lambda s:s['source']['paused'] and any(a['status']=='maintenance' for a in s['project']['state']['assets'].values()))
    snapshot=post('/snapshots');print('Frozen at maintenance',flush=True)
    jobs=post('/runs/compare')
    wait(lambda s:all(any(t['id']==job['id'] and t['status']=='completed' for t in s['tasks']) for job in jobs))
    checkpoint=post('/checkpoints');print('Ready with three native results and checkpoint',flush=True)
    Path('artifacts/demo').mkdir(parents=True,exist_ok=True)
    Path('artifacts/demo/ready.json').write_text(json.dumps({'snapshot':snapshot['id'],'checkpoint':checkpoint['id'],
        'time_s':snapshot['state']['time'],'state_hash':snapshot['state_hash'],'run_ids':[j['run_id'] for j in jobs]},indent=2),encoding='utf-8')
    exported=client.get('/api/checkpoints/'+checkpoint['id']+'/export');exported.raise_for_status()
    Path('artifacts/demo/customer-demo.zip').write_bytes(exported.content)

if __name__=='__main__':main()

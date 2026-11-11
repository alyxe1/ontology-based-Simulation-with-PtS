"""First-use protocol regression. Requires a separate local acceptance service."""
import argparse
import json
import time
from urllib.parse import urlparse
import httpx
from app_v2.common import BASE, atomic_write, canonical


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--url',default='http://127.0.0.1:8846');args=parser.parse_args()
    address=urlparse(args.url)
    if address.hostname!='127.0.0.1' or address.port in (None,8820):raise ValueError('必须指定隔离本机验收服务，禁止使用正式端口 8820')
    with httpx.Client(base_url=args.url,trust_env=False,headers={'x-ontologysim':'v2'},timeout=45) as client:
        def get(path):
            r=client.get('/api/v2'+path);r.raise_for_status();return r.json()
        def post(path,body=None):
            r=client.post('/api/v2'+path,json=body or {});r.raise_for_status();return r.json()
        if not get('/session')['initialized']:
            post('/setup',dict(username='firstload',name='首次载入验收',password='isolated-first-load-test',role='admin'))
        post('/login',dict(username='firstload',password='isolated-first-load-test'))
        def until(predicate,timeout=60):
            end=time.monotonic()+timeout
            while time.monotonic()<end:
                state=get('/state')
                failed=[t for t in state['tasks'] if t['workspace']==state['workspace']['id'] and t['status'] in ('failed','interrupted','reconciliation_required')]
                if failed:raise RuntimeError(str(failed))
                if predicate(state):return state
                time.sleep(.25)
            raise TimeoutError('验收等待超时')
        started=time.monotonic();loaded=post('/workspaces')
        assert loaded['kind']=='source_reused',loaded
        ready=until(lambda s:s['preparation']['status']=='ready',30)
        latency=time.monotonic()-started
        assert not [t for t in ready['tasks'] if t['workspace']==ready['workspace']['id'] and t['kind']=='baseline']
        print(canonical({'stage':'first-load-ready','seconds':round(latency,3)}),flush=True)
        post('/source/incident')
        incident=until(lambda s:s['source']['paused'] and any(a['status']=='maintenance' for a in s['runtime']['assets']),180)
        checkpoint=post('/checkpoints');task=post('/checkpoints/'+checkpoint['id']+'/restore')
        done=until(lambda s:any(t['id']==task['id'] and t['status']=='completed' for t in s['tasks']),90)
        result=get('/tasks/'+task['id']+'/result')
        assert result['native']['verified'] and result['native']['native_clock_s']==0
        assert abs(result['native']['clock_s']-checkpoint['time_s'])<=1e-6
        proof=get('/verify?context='+checkpoint['id']);assert proof['valid']
        sources=get('/sources');assert {'OPC','MES'}<={r['source'] for r in sources['records']}
        report=dict(passed=True,first_load_seconds=round(latency,3),native_bootstrap_required=False,
            source_cursor=incident['workspace']['cursor'],maintenance_time_s=incident['runtime']['time_s'],
            checkpoint=checkpoint['id'],native_restore=dict(verified=True,clock_s=result['native']['clock_s'],native_clock_s=0,completed=result['native']['completed']),
            signature_valid=proof['valid'],provenance_sealed_commits=proof['provenance_sealed_commits'],
            http_and_opc=True,retained_workspace_count=len(done['workspaces']))
        atomic_write(BASE/'artifacts/v02/first-load.json',canonical(report));print(canonical(report),flush=True)


if __name__=='__main__':main()

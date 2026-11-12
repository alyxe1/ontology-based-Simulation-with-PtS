"""Verify the actual data links and configurable native comparisons on an isolated server."""
import argparse
import json
import time
from urllib.parse import urlparse
import httpx
from app_v2.common import BASE, atomic_write, canonical
from app_v2.publisher import maintenance_assets
from app_v2.specification import default_spec


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--url',default='http://127.0.0.1:8848')
    args=parser.parse_args()
    url=urlparse(args.url)
    if url.hostname!='127.0.0.1' or url.port in (None,8820):
        raise ValueError('Use a fresh, isolated acceptance workspace on a separate port')
    starts=[];previous=set()
    with (BASE/'factory-reference/source.jsonl').open(encoding='utf-8') as trace:
        for line in trace:
            frame=json.loads(line);current=maintenance_assets(frame)
            if current-previous:starts.append((frame['cursor'],frame['time_s'],sorted(current-previous)))
            previous=current
            if len(starts)==2:break

    with httpx.Client(base_url=args.url,trust_env=False,timeout=45,headers={'x-ontologysim':'v2'}) as client:
        def request(method,path,body=None,expected=200):
            response=client.request(method,'/api/v2'+path,json=body)
            assert response.status_code==expected,(path,response.status_code,response.text)
            return response.json()
        def get(path):return request('GET',path)
        def post(path,body=None,expected=200):return request('POST',path,body or {},expected)
        def until(predicate,timeout=120):
            start=time.monotonic()
            while time.monotonic()-start<timeout:
                state=get('/state')
                if predicate(state):return state
                failed=[t.get('error') for t in state['tasks'] if t['status'] in ('failed','reconciliation_required')]
                if failed:raise AssertionError(failed)
                time.sleep(.5)
            raise TimeoutError('Acceptance stage exceeded timeout')

        account=dict(username='comparison-test',name='方案验收',password='isolated-comparison-test-only')
        if not get('/session')['initialized']:post('/setup',{**account,'role':'admin'})
        post('/login',{k:account[k] for k in ('username','password')})
        assert get('/state')['workspace'] is None,'Start with a fresh isolated directory'
        assert post('/workspaces')['kind']=='source_reused'
        until(lambda s:s['source']['connected'] and s['workspace']['cursor']>=0)
        fast_forward=[];first_checkpoint=None
        for expected_cursor,expected_time,devices in starts:
            started=time.monotonic()
            post('/source/incident')
            state=until(lambda s:s['source']['paused'] and s['source']['fast_forward'] is not None)
            assert state['workspace']['cursor']==expected_cursor,(state['workspace']['cursor'],expected_cursor)
            assert abs(state['runtime']['time_s']-expected_time)<=1e-6
            row=dict(seconds=round(time.monotonic()-started,3),cursor=expected_cursor,time_s=expected_time,
                     devices=devices,confirmed_frames=state['source']['fast_forward']['confirmed_frames'])
            fast_forward.append(row);print(canonical(row),flush=True)
            if first_checkpoint is None:
                incident=next(x for x in get('/impact') if x.get('resource') in devices)
                first_checkpoint=post('/checkpoints',dict(incident_id=incident['id'],source_cursor=incident['source_cursor']))
                options=get('/checkpoints/'+first_checkpoint['id']+'/options')
                assert options['incident_id']==incident['id'] and any(j['affected'] for j in options['jobs'])
                assert first_checkpoint['source_cursor']==expected_cursor
        assert get('/state?context='+first_checkpoint['id'])['runtime']['time_s']==starts[0][1]
        assert {'MES','OPC'}<={r['source'] for r in get('/sources')['records']}

        spec=default_spec().model_dump();spec.update(name='机加工方案验收',horizon_s=5000)
        spec['orders']=[{**spec['orders'][1],'quantity':6,'batch_size':3}]
        post('/workspaces',{'specification':spec})
        until(lambda s:s['source']['connected'] and s['workspace']['cursor']>=0)
        post('/source/pause')
        cp=post('/checkpoints');policy=get('/checkpoints/'+cp['id']+'/options')['policy']
        body=dict(checkpoint_id=cp['id'],scenarios=[dict(name='自定义交期优先',rule='EDD',reroute=True,shift_hours=10)])
        plan=post('/compare/validate',body);assert plan['task_count']==2
        tasks=post('/compare',body);ids={t['id'] for t in tasks}
        until(lambda s:{t['id'] for t in s['tasks'] if t['status']=='completed'}>=ids,180)
        rows=[get('/tasks/'+t['id']+'/result') for t in tasks]
        assert {json.loads(r['task']['payload'])['seed'] for r in rows}=={5202}
        assert all(r['native']['verified'] and r['native']['window_visible'] for r in rows)
        assert all(abs(r['native']['origin_s']-cp['time_s'])<=1e-6 for r in rows)
        assert rows[0]['summary']['policy']['rule']==policy['rule']
        assert rows[1]['summary']['policy']['rule']=='EDD'
        report=dict(passed=True,fast_forward=fast_forward,incident_baseline_isolated=True,native=[
            dict(name=r['summary']['policy']['name'],rule=r['summary']['policy']['rule'],
                 verified=r['native']['verified'],window_visible=r['native']['window_visible'],
                 clock_s=r['native']['clock_s'],origin_s=r['native']['origin_s'],completed=r['native']['completed'],
                 engine=r['native']['engine']) for r in rows])
        atomic_write(BASE/'artifacts/v02/configurable-comparison.json',canonical(report))
        print(json.dumps(report,ensure_ascii=True),flush=True)


if __name__=='__main__':main()

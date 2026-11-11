"""End-to-end acceptance over actual HTTP, OPC UA subscription and native COM jobs."""
import argparse,json,time
from pathlib import Path
import httpx
from app_v2.common import DATA,canonical

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--url',default='http://127.0.0.1:8842');parser.add_argument('--small',action='store_true');args=parser.parse_args()
    client=httpx.Client(base_url=args.url,headers={'x-ontologysim':'v2'},timeout=120,trust_env=False)
    def get(path):
        r=client.get('/api/v2'+path);r.raise_for_status();return r.json()
    def post(path,data={}):
        r=client.post('/api/v2'+path,json=data);r.raise_for_status();return r.json()
    account=dict(username='acceptance',name='验收分析员',password='acceptance-only-2026',role='admin')
    if not get('/session')['initialized']:post('/setup',account)
    post('/login',{k:account[k] for k in ('username','password')})
    if not get('/state')['workspace']:
        if args.small:
            from app_v2.specification import default_spec
            spec=default_spec().model_dump();spec['horizon_s']=20000
            for o in spec['orders']:o.update(quantity=3,batch_size=3)
            for resource in spec['resources']:
                if resource['id']=='CNC01':resource['tool_limit']=2
            post('/workspaces',{'specification':spec})
        else:post('/workspaces')
    def until(predicate,label,timeout=1800):
        started=time.monotonic();last=None
        while time.monotonic()-started<timeout:
            state=get('/state')
            failures=[(t['id'],t.get('error')) for t in state['tasks'] if t['status']=='failed']
            if failures:raise RuntimeError(failures)
            status=[(t['kind'],t['status'],t['completed'],round(t['time_s'])) for t in state['tasks'][:4]]
            if status!=last:print(canonical({'phase':label,'tasks':status,'cursor':state['workspace']['cursor']}),flush=True);last=status
            if predicate(state):return state
            time.sleep(2)
        raise TimeoutError(label)
    until(lambda s:s['source']['connected'] and s['workspace']['cursor']>=0,'factory source')
    post('/source/incident')
    state=until(lambda s:s['source']['paused'] and any(a['status']=='maintenance' for a in s['runtime']['assets']),'maintenance observation')
    sources=get('/sources');assert {r['source'] for r in sources['records']} >= {'MES','OPC'}
    impact=get('/impact');assert any(i['kind']=='maintenance' for i in impact)
    before=state['workspace']['cursor'];post('/source/play');cp=post('/checkpoints')
    until(lambda s:s['workspace']['cursor']>before,'live continues after baseline',120)
    post('/source/pause')
    branch=get('/state?context='+cp['id']);fixed=branch['runtime']['hash']
    assert get('/state?context='+cp['id'])['runtime']['hash']==fixed
    tasks=post('/compare',{'checkpoint_id':cp['id'],'repetitions':1});ids=[t['id'] for t in tasks]
    finished=until(lambda s:all(next(t for t in s['tasks'] if t['id']==i)['status']=='completed' for i in ids),'scenario comparison')
    results=[get('/tasks/'+i+'/result') for i in ids]
    verification=get('/verify?context='+cp['id']);assert verification['valid']
    assert get('/validate?context='+cp['id'])['conforms']
    directory=DATA/'acceptance';directory.mkdir(parents=True,exist_ok=True)
    report={'passed':True,'checkpoint':cp,'live_continued':True,'sources':{k:sources[k] for k in ('total','mapping')},'impact':impact,'results':[{'task':r['task']['id'],'policy':r['summary']['policy'],'kpi':r['summary']['kpi'],'native':r['native']} for r in results],'verification':verification}
    (directory/('http-native-small.json' if args.small else 'http-native.json')).write_text(canonical(report),encoding='utf-8')
    response=client.get('/api/v2/export',params={'context':cp['id']});response.raise_for_status();(directory/'verified-baseline.zip').write_bytes(response.content)
    print(canonical({'passed':True,'completed':[r['summary']['kpi']['completed'] for r in results],'checkpoint':cp['id']}),flush=True)

if __name__=='__main__':main()

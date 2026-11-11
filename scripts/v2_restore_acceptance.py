"""Direct native restoration at setup, machining, maintenance, transit and rework boundaries."""
import json
import psutil
from app_v2.common import DATA,EPS,canonical
from app_v2.graph import OntologyStore
from app_v2.runtime import OntologyRuntime
from app_v2.native import PlantAdapter
from app_v2.specification import default_spec,ProductionSpec
from scripts.v2_kernel_acceptance import production_state

def events(graph,after=0):
    return [(r['payload']['kind'],r['payload']['simulation_time_s'],[(c['after']['id'],c['after'].get('status')) for c in r['payload']['changes'] if c['after']['type'] in ('Workpiece','Execution','ScheduledEvent','FixtureReservation')])
            for r in graph.records(after=after) if r['payload']['kind'] not in ('command_prepared',)]

def main():
    output=DATA/'acceptance'/'complex';output.mkdir(parents=True,exist_ok=True)
    source=default_spec().model_dump();source['horizon_s']=5500.;source['transfer_s']=15.
    source['resources']=[r for r in source['resources'] if r['id'] in ('SAW','CNC01','CMM')]
    for r in source['resources']:
        if r['id']=='CNC01':r.update(setup_s=40,tool_limit=2,maintenance_s=90)
    source['products']=source['products'][:2]
    for p in source['products']:
        p['operations']=[o for o in p['operations'] if o['id'] in ('CUT','MILL','INSPECT')]
        for op in p['operations']:op['duration_s']=dict(CUT=20,MILL=75,INSPECT=25)[op['id']];op['preferred']={'CUT':'SAW','MILL':'CNC01','INSPECT':'CMM'}[op['id']]
        p.update(rework_index=1,reject_probability=1.)
    source['orders']=source['orders'][:2]
    for order in source['orders']:order.update(quantity=3,batch_size=3,due_s=5000)
    source['fixtures']=source['fixtures'][:1];source['fixtures'][0]['quantity']=1
    spec=ProductionSpec.model_validate(source);graph=OntologyStore();runtime=OntologyRuntime.create(graph,spec)
    native=PlantAdapter();saved={};pids=[]
    try:
        native.initialize(runtime);pids.append(native.engine.pid);runtime.settle(native)
        while runtime.time<spec.horizon_s-EPS:
            runtime.advance(native)
            jobs=runtime.jobs();assets=graph.entities('AssetState')
            predicates={kind:any(j['status']==kind for j in jobs) for kind in ('processing','setup','transfer')}
            predicates.update(maintenance=any(a['status']=='maintenance' for a in assets),fixture=bool(graph.find('FixtureReservation',status='reserved')),rework=any(j['attempt']==1 and j['status']=='processing' for j in jobs))
            for kind,match in predicates.items():
                if match and kind not in saved:
                    path=output/(kind+'.trig');graph.export(path);saved[kind]={'path':path,'sequence':graph.sequence,'time_s':runtime.time,'native':native.verify(runtime)}
        expected=production_state(runtime);kpi=runtime.summary()['kpi'];native.verify(runtime)
    finally:native.close()
    assert len(saved)==6,saved.keys()
    report=[]
    for kind,cp in saved.items():
        r=OntologyRuntime(OntologyStore.restore(cp['path']));adapter=PlantAdapter()
        try:
            initial=adapter.initialize(r);pids.append(adapter.engine.pid)
            assert initial['native_clock_s']==0 and abs(r.time-cp['time_s'])<=EPS
            while r.time<spec.horizon_s-EPS:r.advance(adapter)
            checked=adapter.verify(r)
            assert production_state(r)==expected,kind+' production state differs'
            assert r.summary()['kpi']==kpi,kind+' KPI differs'
            assert events(r.graph,cp['sequence'])==events(graph,cp['sequence']),kind+' event sequence differs'
            report.append({'kind':kind,'at':cp['time_s'],'native':checked,'events_identical':True})
            print(canonical({'restored':kind,'time':cp['time_s'],'passed':True}),flush=True)
        finally:adapter.close()
    import time
    for _ in range(100):
        if not any(psutil.pid_exists(p) for p in pids):break
        time.sleep(.1)
    assert not any(psutil.pid_exists(p) for p in pids),'owned COM process not released'
    (output/'report.json').write_text(canonical({'passed':True,'cases':report,'kpi':kpi,'owned_processes_released':pids,'restored_from_trig_only':True,'replayed_history':False}),encoding='utf-8')

if __name__=='__main__':main()

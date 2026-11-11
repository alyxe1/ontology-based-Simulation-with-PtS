"""Real COM acceptance. Never substitutes a Python simulator for native results."""
import argparse
import json
from pathlib import Path
from app_v2.common import DATA, EPS, canonical
from app_v2.graph import OntologyStore
from app_v2.evidence import Signer
from app_v2.specification import default_spec
from app_v2.runtime import OntologyRuntime
from app_v2.native import PlantAdapter

def production_state(runtime):
    kinds=("Workpiece","AssetState","MaterialBalance","FixtureReservation","Execution","ScheduledEvent")
    return {kind:runtime.graph.entities(kind) for kind in kinds}

def main():
    parser=argparse.ArgumentParser();parser.add_argument("--full",action="store_true");args=parser.parse_args()
    out=DATA/"acceptance";out.mkdir(parents=True,exist_ok=True)
    spec=default_spec()
    if not args.full:
        spec.resources=[r for r in spec.resources if r.id=="SAW"]
        product=spec.products[0];product.operations=product.operations[:1];product.operations[0].duration_s=100.;product.rework_index=0;product.reject_probability=0.
        spec.products=[product];order=spec.orders[0];order.quantity=2;order.batch_size=2;spec.orders=[order];spec.fixtures=[];spec.horizon_s=300.
    signer=Signer();graph=OntologyStore(signer=signer);runtime=OntologyRuntime.create(graph,spec)
    checkpoint=out/("full-checkpoint.trig" if args.full else "small-checkpoint.trig")
    adapter=PlantAdapter()
    snapshots={};steps=0
    try:
        initial=adapter.initialize(runtime);runtime.settle(adapter)
        if not args.full:adapter.execute('str_to_obj(".Models.Model.OntologySim.Wake").executeIn(40)')
        while runtime.time<spec.horizon_s-EPS:
            runtime.advance(adapter);steps+=1
            if steps%100==0:print(json.dumps({"phase":"continuous","step":steps,"time":runtime.time}),flush=True)
            if not checkpoint.exists() or "saved" not in snapshots:
                eligible=any(a["status"]=="maintenance" for a in graph.entities("AssetState")) if args.full else runtime.time>=40-EPS
                if eligible:
                    graph.export(checkpoint);snapshots["saved"]={"time":runtime.time,"native":adapter.verify(runtime),"sequence":graph.sequence}
            if args.full:
                for kind in ("processing","setup","transfer"):
                    if kind not in snapshots and any(j["status"]==kind for j in runtime.jobs()):
                        graph.export(out/("state-"+kind+".trig"));snapshots[kind]=runtime.time
            if steps>10000:raise RuntimeError("Event limit")
        evidence=adapter.verify(runtime);expected=production_state(runtime);kpi=runtime.summary()["kpi"]
        if not args.full:
            jobs=runtime.jobs();operation=graph.get('P1/CUT')
            first=round(100*(.92+.16*runtime.sample(jobs[0],operation)),6)
            second=round(100*(.92+.16*runtime.sample(jobs[1],operation)),6)
            assert abs(jobs[0]['completed_at']-first)<=EPS and abs(jobs[1]['completed_at']-(first+second))<=EPS
            assert kpi['completed']==2 and not graph.find('Workpiece',status='queued')
    finally:adapter.close()
    assert "saved" in snapshots,"No checkpoint reached"
    restored=OntologyRuntime(OntologyStore.restore(checkpoint,signer=signer))
    adapter=PlantAdapter();restored_steps=0
    try:
        restored_native=adapter.initialize(restored)
        assert restored_native["native_clock_s"]==0.
        assert abs(restored.time-snapshots["saved"]["time"])<EPS
        while restored.time<spec.horizon_s-EPS:
            restored.advance(adapter);restored_steps+=1
            if restored_steps%100==0:print(json.dumps({"phase":"restored","step":restored_steps,"time":restored.time}),flush=True)
        final=adapter.verify(restored)
        assert production_state(restored)==expected,"Production state after direct restore differs"
        assert restored.summary()["kpi"]==kpi,"KPI differs"
        assert graph.verify()["valid"] and restored.graph.verify()["valid"]
        adapter.save(out/("full-final.spp" if args.full else "small-final.spp"))
    finally:adapter.close()
    report={"passed":True,"native":evidence,"restored_native":restored_native,"final_native":final,"snapshots":snapshots,"kpi":kpi,
            "only_trig_used":True,"replayed_from_zero":False,"continuous_steps":steps,"restored_steps":restored_steps}
    (out/("kernel-full.json" if args.full else "kernel-small.json")).write_text(canonical(report),encoding="utf-8")
    print(json.dumps({"passed":True,"checkpoint_time":snapshots["saved"]["time"],"completed":kpi["completed"],"steps":steps,"restored_steps":restored_steps}),flush=True)

if __name__=="__main__":main()

import json
import threading
from pathlib import Path
from app.demo import demo_spec, demo_patches
from app.native import NativeEngine, compare_states
from app.contracts import TwinSnapshot, digest
from app.semantic import validate_spec

def main():
    spec=demo_spec(); check=validate_spec(spec)
    assert check["conforms"], check
    out=Path(".runtime/kernel-acceptance"); out.mkdir(parents=True,exist_ok=True)
    def run(payload,folder):
        engine=NativeEngine(lambda k,d: print(k,json.dumps(d),flush=True) if k!="progress" or d["boundary"]%120==0 else None,
                            threading.Event(),threading.Event(),threading.Event(),"Tecnomatix.PlantSimulation.RemoteControl.26.6")
        try: return engine.run(payload,folder)
        finally: engine.close()
    base={"spec":spec.model_dump(),"candidates":check["candidates"],"run_id":"kernel-baseline"}
    result=run(base,out/"baseline")
    states=[json.loads(x) for x in Path(result["trace_path"]).read_text(encoding="utf-8").splitlines()]
    frozen=next(s for s in states if any(a["status"]=="maintenance" for a in s["assets"].values()))
    snapshot=TwinSnapshot(id="kernel-snapshot",project_id="kernel",spec_hash=digest(spec.model_dump()),source_cursor=frozen["boundary"],
        state=frozen,state_hash=digest(frozen),samples=result["samples"],assumptions=["Synthetic fixed samples"]).model_dump()
    (out/"snapshot.json").write_text(json.dumps(snapshot),encoding="utf-8")
    restored=run({**base,"snapshot":snapshot,"restore_only":True},out/"restore")
    assert restored["restored"]
    comparison=[]
    for i,patch in enumerate(demo_patches()):
        branch=run({**base,"run_id":f"branch{i}","snapshot":snapshot,"patch":patch.model_dump()},out/f"branch{i}")["result"]
        comparison.append({"name":patch.name,"kpi":branch["kpi"],"native":branch["native"]})
        if i==0:
            diffs=compare_states(result["result"]["state"],branch["state"])
            assert not diffs,diffs
            assert result["result"]["events"]==branch["events"], "Production event order differs after restore"
    proof={"passed":True,"freeze_time":frozen["time"],"boundary":frozen["boundary"],"state_hash":digest(frozen),
           "restore_verified":True,"continued_event_order_identical":True,"scenarios":comparison}
    Path("artifacts/acceptance").mkdir(parents=True,exist_ok=True)
    Path("artifacts/acceptance/kernel.json").write_text(json.dumps(proof,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(proof,ensure_ascii=True),flush=True)

if __name__=="__main__": main()

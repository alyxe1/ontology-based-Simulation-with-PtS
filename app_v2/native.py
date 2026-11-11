"""Stateless business adapter: native objects are reconstructed from RDF records."""
from __future__ import annotations
import threading
from pathlib import Path
from app.native import NativeEngine
from .common import EPS, PROGID

ROOT = ".Models.Model"
SYS = ROOT + ".OntologySim"
EC = ROOT + ".EventController"

class PlantAdapter:
    def __init__(self, emit=lambda *a:None, cancel=None, pause=None, visible=None):
        self.engine=NativeEngine(emit,cancel or threading.Event(),pause or threading.Event(),visible or threading.Event(),PROGID)
        self.engine.source=None
        self.paths={};self.origin=0.;self.wakes=set();self.base_finished=0;self.base_scrap=0

    def execute(self, text): return self.engine.execute(text)

    def initialize(self, runtime):
        runtime.assert_restorable()
        from .invariants import validate_boundary
        validate_boundary(runtime.graph)
        self.engine.connect(); self.origin=runtime.time
        self.execute(f'''.MaterialFlow.Frame.createObject({ROOT},100,100,"OntologySim")
.MaterialFlow.Buffer.createObject({SYS},420,420,"Transit")
{SYS}.Transit.Capacity := 2000
{SYS}.Transit.ProcTime := 0
.MaterialFlow.Drain.createObject({SYS},850,220,"Finished")
.MaterialFlow.Drain.createObject({SYS},850,330,"Scrap")
str_to_obj(".InformationFlow.Method").createObject({SYS},850,420,"Wake")
str_to_obj("{SYS}.Wake").Program := "var dummy: integer := 0"
{EC}.RealTime := false
{EC}.End := {max(1,runtime.config['horizon_s']-self.origin)+1:.9f}''')
        for index,r in enumerate(runtime.graph.entities("Equipment")):
            frame=f"{SYS}.R{index}";self.paths[r["id"]]={"queue":frame+".Waiting","machine":frame+".Machine"}
            self.execute(f'''.MaterialFlow.Frame.createObject({SYS},{80+index%3*260},{70+index//3*170},"R{index}")
.MaterialFlow.Buffer.createObject({frame},40,80,"Waiting")
{frame}.Waiting.Capacity := 2000
{frame}.Waiting.ProcTime := 0
.MaterialFlow.Station.createObject({frame},200,80,"Machine")
{frame}.Machine.ProcTime := 1''')
            self.engine.sim.SetValue(frame+".Label",r["name"])
        self.engine.sim.ResetSimulation(EC);self.engine.sim.StartSimulation(EC);self.engine.wait();self.engine.sim.StopSimulation()
        for job in sorted(runtime.jobs(),key=lambda j:(j.get("queue_seq",0),j["id"])):
            status=job["status"]
            if status in ("queued","setup","processing"):
                rid=job["assigned"];self.create(job["id"],rid)
                if status in ("setup","processing"):
                    execution=runtime.graph.get(job["execution"])
                    self.start(job["id"],rid,max(EPS,execution["end_s"]-self.origin))
            elif status=="transfer":self.create(job["id"],"transit")
        self.base_finished=sum(j["status"]=="done" for j in runtime.jobs())
        self.base_scrap=sum(j["status"]=="scrapped" for j in runtime.jobs())
        self.schedule(runtime)
        if self.engine.visible.is_set():self.execute(SYS+'.openDialog')
        return self.verify(runtime)

    def create(self,jid,rid):
        target=SYS+".Transit" if rid=="transit" else self.paths[rid]["queue"]
        actual=self.execute(f'-> string\nvar p:object := .MUs.Part.create({target})\np.Name := "{jid}"\nreturn p.Name')
        if actual!=jid:raise RuntimeError("原生工件创建未确认")

    def move(self,jid,source,target):
        def path(loc):
            if loc in ("transit","finished","scrap"):
                return SYS+{"transit":".Transit","finished":".Finished","scrap":".Scrap"}[loc]
            rid,place=loc.split(":");return self.paths[rid][place]
        src=path(source);dst=path(target)
        actual=self.execute(f'''-> string
for var i := 1 to {src}.numMU
 var p:object := {src}.MU(i)
 if p.Name = "{jid}"
  p.move({dst})
  return "{jid}"
 end
next
return ""''')
        if actual!=jid:raise RuntimeError(f"原生移动未确认: {jid} {source} -> {target}")

    def start(self,jid,rid,duration,from_machine=False):
        if from_machine:self.move(jid,rid+":machine",rid+":queue")
        self.execute(f"{self.paths[rid]['machine']}.ProcTime := {duration:.9f}")
        self.move(jid,rid+":queue",rid+":machine")
        if self.engine.sim.GetValue(self.paths[rid]["machine"]+".cont.Name")!=jid:raise RuntimeError("原生开工未确认")

    def finished(self,rid):
        return bool(self.engine.sim.GetValue(self.paths[rid]["machine"]+".cont.Finished"))

    def schedule(self,runtime):
        times={e["due_s"] for e in runtime.pending_events() if e["event_kind"] not in ("process_finished","setup_finished")}
        times.add(runtime.config["horizon_s"])
        for day in range(int(runtime.config["horizon_s"]//86400)+2):times.add(float(day*86400))
        for absolute in sorted(times):
            if absolute>runtime.time+EPS and absolute not in self.wakes:
                self.execute(f'str_to_obj("{SYS}.Wake").executeIn({absolute-runtime.time:.9f})');self.wakes.add(absolute)

    def step(self,runtime):
        self.engine.checkpoint_control();self.schedule(runtime)
        for _ in range(2000):
            self.engine.sim.StepSimulation(EC);self.engine.wait()
            at=self.origin+float(self.engine.sim.GetValue(EC+".SimTime"))
            if at>runtime.time+EPS:break
        else:raise RuntimeError("原生事件时钟无法推进")
        # All due station completions at this timestamp must be observed before dispatch.
        for _ in range(100):
            due=[e for e in runtime.pending_events() if e["due_s"]<=at+EPS and e["event_kind"] in ("process_finished","setup_finished")]
            if all(self.finished(e["resource"]) for e in due):return round(at,6)
            self.engine.sim.StepSimulation(EC);self.engine.wait()
            observed=self.origin+float(self.engine.sim.GetValue(EC+".SimTime"))
            if abs(observed-at)>EPS:raise RuntimeError("原生时钟越过尚未完成的语义事件")
        raise RuntimeError("同一时刻的原生事件未收敛")

    def verify(self,runtime):
        code=['-> string','var reportText:string := ""'];loop_index=0
        for rid,paths in self.paths.items():
            for place,path in paths.items():
                loop_index+=1;k=f"itemIndex{loop_index}"
                code += [f'reportText += "{rid}:{place}|"',f'for var {k} := 1 to {path}.numMU',f' reportText += {path}.MU({k}).Name + ","','next','reportText += ";"']
        code += [f'reportText += "transit|"',f'for var transitIndex := 1 to {SYS}.Transit.numMU',f' reportText += {SYS}.Transit.MU(transitIndex).Name + ","','next','return reportText']
        raw=str(self.execute("\n".join(code)));actual={}
        for section in raw.split(";"):
            if "|" in section:
                key,names=section.split("|",1);actual[key]=[n for n in names.split(",") if n]
        expected={k:[] for k in actual}
        for j in sorted(runtime.jobs(),key=lambda j:(j.get("queue_seq",0),j["id"])):
            if j["status"]=="queued":expected[j["assigned"]+":queue"].append(j["id"])
            elif j["status"] in ("processing","setup"):expected[j["assigned"]+":machine"].append(j["id"])
            elif j["status"]=="transfer":expected["transit"].append(j["id"])
        diffs=[]
        for key in expected:
            if sorted(expected[key])!=sorted(actual[key]):diffs.append({"position":key,"expected":expected[key],"actual":actual[key]})
        clock=self.origin+float(self.engine.sim.GetValue(EC+".SimTime"))
        if abs(clock-runtime.time)>EPS:diffs.append({"clock":clock,"expected":runtime.time})
        finished=self.base_finished+int(self.engine.sim.GetValue(SYS+".Finished.statNumIn"))
        scrap=self.base_scrap+int(self.engine.sim.GetValue(SYS+".Scrap.statNumIn"))
        if finished!=sum(j["status"]=="done" for j in runtime.jobs()):diffs.append({"finished":finished})
        if scrap!=sum(j["status"]=="scrapped" for j in runtime.jobs()):diffs.append({"scrapped":scrap})
        if diffs:raise RuntimeError("原生与本体状态不一致: "+str(diffs))
        return dict(verified=True,clock_s=clock,origin_s=self.origin,native_clock_s=clock-self.origin,completed=finished,scrapped=scrap,
                    locations=actual,engine=PROGID,owned_pid=self.engine.pid,restore_mode="direct-ontology-initialization",clock_tolerance_s=EPS)

    def save(self,path):self.engine.sim.SaveModel(str(Path(path).resolve()))
    def close(self):self.engine.close()

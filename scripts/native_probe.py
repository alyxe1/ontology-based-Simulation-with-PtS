"""Hand-calculable native test. Only owns the server PID created here."""
import json
import time
from pathlib import Path
import pythoncom
import win32com.client
import win32process

def main():
    before = set(win32process.EnumProcesses())
    pythoncom.CoInitialize()
    sim = None
    own = False
    report = {}
    try:
        sim = win32com.client.DispatchEx("Tecnomatix.PlantSimulation.RemoteControl.26.6")
        pid = sim.GetCurrentProcessId()
        own = pid not in before
        if not own:
            raise RuntimeError("COM returned a pre-existing process; refusing mutation")
        sim.SetVisible(False)
        sim.SetNoMessageBox(1)
        sim.SetStopSimulationOnError(True)
        sim.NewModel()
        root = ".Models.Model"
        ec = root + ".EventController"
        sim.ExecuteSimTalk(f'''.MaterialFlow.Buffer.createObject({root},50,100,"Waiting")
{root}.Waiting.Capacity := 10
{root}.Waiting.ProcTime := 0
.MaterialFlow.Station.createObject({root},200,100,"Machine")
{root}.Machine.ProcTime := 10
.MaterialFlow.Drain.createObject({root},350,100,"Done")
{ec}.RealTime := false
{ec}.End := 100''')
        sim.ResetSimulation(ec)
        sim.StartSimulation(ec)
        def wait():
            deadline=time.monotonic()+10
            while sim.IsSimulationRunning():
                pythoncom.PumpWaitingMessages()
                if time.monotonic()>deadline: raise TimeoutError("Native step blocked")
                time.sleep(.005)
            if sim.HasSimulationError(): raise RuntimeError("Native simulation error")
        wait()
        assert float(sim.GetValue(ec+".SimTime")) == 0
        sim.ExecuteSimTalk(f'''var a:object := .MUs.Part.create({root}.Waiting)
a.Name := "J1"
var b:object := .MUs.Part.create({root}.Waiting)
b.Name := "J2"
{root}.Waiting.MU(1).move({root}.Machine)''')
        completions=[]
        for j in range(2):
            for step in range(100):
                sim.StepSimulation(ec)
                wait()
                if sim.GetValue(root+".Machine.cont.Finished"):
                    break
            t=float(sim.GetValue(ec+".SimTime"))
            completions.append(t)
            sim.ExecuteSimTalk(f"{root}.Machine.cont.move({root}.Done)")
            if j==0:
                sim.ExecuteSimTalk(f"{root}.Waiting.MU(1).move({root}.Machine)")
        assert completions == [10.0,20.0], completions
        count=int(sim.GetValue(root+".Done.statNumIn"))
        assert count == 2, count
        assert int(sim.GetValue(root+".Waiting.numMU")) == 0
        assert int(sim.GetValue(root+".Machine.numMU")) == 0
        out=Path(__file__).resolve().parents[1]/"artifacts"/"acceptance"
        out.mkdir(parents=True,exist_ok=True)
        sim.SaveModel(str(out/"analytical-model.spp"))
        report={"passed":True,"progid":"Tecnomatix.PlantSimulation.RemoteControl.26.6",
                "event_controller":ec,"api_paths_verified":["ResetSimulation","StartSimulation","StepSimulation"],
                "completions_s":completions,"native_completed":count,"queue":0,"occupied":0,"owned_pid":pid}
    finally:
        if sim is not None and own:
            sim.Quit()
            report["quit_called"]=True
        sim=None
        pythoncom.CoUninitialize()
    (out/"native-probe.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps(report))

if __name__ == "__main__": main()

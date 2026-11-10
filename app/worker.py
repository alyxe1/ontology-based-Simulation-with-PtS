"""One spawned process, one STA owner, bounded plain-data command queue."""
from __future__ import annotations
import queue
import traceback
from pathlib import Path
from .native import NativeEngine

def worker_main(commands, notifications, cancel, pause, visible, progid):
    engine=None
    def emit(kind,data): notifications.put({"kind":kind,"data":data})
    while True:
        try: command=commands.get(timeout=.15)
        except queue.Empty:
            if engine and engine.sim:
                try:
                    engine.com.PumpWaitingMessages()
                    show=visible.is_set()
                    if show!=engine.shown:
                        engine.sim.SetVisible(show)
                        if show: engine.execute(".Models.Model.OntologySim.openDialog")
                        engine.shown=show
                except Exception as ex: emit("view_error",{"message":str(ex)})
            continue
        if command["kind"]=="shutdown":
            if engine:
                try: engine.close()
                except Exception as ex: emit("release_error",{"message":str(ex)})
            emit("shutdown",{})
            return
        task=command["task_id"]
        try:
            if engine: engine.close()
            cancel.clear(); pause.clear()
            engine=NativeEngine(lambda kind,data:emit(kind,{"task_id":task,**data}),cancel,pause,visible,progid)
            result=engine.run(command["payload"],Path(command["output"]))
            emit("done",{"task_id":task,"result":result})
        except BaseException as ex:
            emit("cancelled" if isinstance(ex,InterruptedError) else "failed",{
                "task_id":task,"message":str(ex),"phase":engine.phase if engine else "connect",
                "hresult":getattr(ex,"hresult",None),"traceback":traceback.format_exc()})
            if engine:
                try: engine.close()
                except Exception as close_error: emit("release_error",{"message":str(close_error)})
                engine=None

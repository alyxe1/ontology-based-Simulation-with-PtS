"""Independent synthetic factory publisher through real OPC UA and MES HTTP."""
import asyncio
import copy
import json
from pathlib import Path
import httpx
from asyncua import Server
from .common import canonical

def publisher_main(config,pause,stop,speed,target):
    asyncio.run(publish(config,pause,stop,speed,target))

async def publish(config,pause,stop,speed,target):
    server=Server();await server.init();server.set_endpoint(config["endpoint"]);server.set_server_name("OntologySim Factory Data Source")
    namespace=await server.register_namespace("urn:ontologysim:v2:factory")
    folder=await server.nodes.objects.add_object(namespace,"Cell01");nodes={}
    for r in config["spec"]["resources"]:
        asset=await folder.add_object(namespace,r["id"])
        nodes[r["id"]]=await asset.add_variable(namespace,"Observation","")
        await asset.add_variable(namespace,"SourceId",r["opc_id"])
    aliases={r["id"]:r["mes_id"] for r in config["spec"]["resources"]}
    def remap(value):
        if isinstance(value,dict):
            if set(value)=={"ref"}:return {"ref":aliases.get(value["ref"],value["ref"])}
            return {k:remap(v) for k,v in value.items()}
        if isinstance(value,list):return [remap(v) for v in value]
        return value
    cursor=config.get("cursor",-1)
    previous_time=0.
    async with server, httpx.AsyncClient(base_url=config["url"],timeout=10,trust_env=False,headers={"x-source-token":config["token"]}) as client:
        with Path(config["trace_path"]).open(encoding="utf-8") as stream:
            for line in stream:
                frame=json.loads(line)
                if frame["cursor"]<=cursor:previous_time=frame['time_s'];continue
                while pause.is_set() and not stop.is_set():await asyncio.sleep(.1)
                if stop.is_set():break
                remaining=max(0.,frame['time_s']-previous_time)
                while remaining>0 and not stop.is_set() and target.value!=-2:
                    if pause.is_set():await asyncio.sleep(.1);continue
                    delay=min(.1,remaining/max(1,speed.value));await asyncio.sleep(delay);remaining-=delay*max(1,speed.value)
                if stop.is_set():break
                envelope={"workspace":config["workspace"],"source":"MES","raw_id":f"MES:{frame['cursor']}","cursor":frame["cursor"],"event_time":frame["time_s"],"unit":"s","quality":"good","synthetic":True,
                          "records":remap([r for r in frame["records"] if r["type"]!="AssetState"])}
                while not stop.is_set():
                    try:
                        response=await client.post("/api/v2/internal/mes",json=envelope);response.raise_for_status()
                        for resource,row in zip(sorted(config["spec"]["resources"],key=lambda r:r["id"]),frame["assets"]):
                            value={"workspace":config["workspace"],"source":"OPC","source_id":resource["opc_id"],"raw_id":f"OPC:{frame['cursor']}:{resource['id']}","cursor":frame["cursor"],"event_time":frame["time_s"]*1000,"unit":"ms","quality":"good","synthetic":True,"record":row}
                            await nodes[resource["id"]].write_value(canonical(value))
                        for _ in range(100):
                            if stop.is_set():break
                            reply=await client.get("/api/v2/internal/cursor",params={"workspace":config["workspace"]})
                            if reply.json().get("cursor",-1)>=frame["cursor"]:break
                            await asyncio.sleep(.02)
                        else:raise TimeoutError("等待语义提交确认超时")
                        break
                    except Exception:
                        await asyncio.sleep(.5)
                cursor=frame["cursor"]
                previous_time=frame['time_s']
                if target.value==-2 and frame["incident"] or target.value>=0 and cursor>=target.value:
                    pause.set();target.value=-1
                await asyncio.sleep(.005)

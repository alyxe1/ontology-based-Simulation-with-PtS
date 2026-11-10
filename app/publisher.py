"""Synthetic factory process: the future reference tape lives only here.

Backend prediction inputs never load this tape. Each cursor is committed only
after both a MES HTTP frame and all OPC UA subscriptions have arrived.
"""
from __future__ import annotations
import asyncio
import copy
import json
import logging
from pathlib import Path

def publisher_main(config,pause,paused,stop,speed,target):
    logging.basicConfig(level=logging.ERROR)
    asyncio.run(publish(config,pause,paused,stop,speed,target))

async def publish(config,pause,paused,stop,speed,target):
    from asyncua import Server, ua
    import httpx
    tape=[json.loads(line) for line in Path(config["trace_path"]).read_text(encoding="utf-8").splitlines()]
    spec=config["spec"]; project=config["project"]
    server=Server(); await server.init(); server.set_endpoint(config["endpoint"])
    server.set_security_policy([ua.SecurityPolicyType.NoSecurity])
    idx=await server.register_namespace("urn:ontologysim:synthetic-factory")
    factory=await server.nodes.objects.add_object(idx,"MachiningCell")
    variables={}
    for r in spec["resources"]:
        variables[r["id"]]=await factory.add_variable(ua.NodeId(r["opc_id"],idx),r["id"],"")
    aliases={r["id"]:r["mes_id"] for r in spec["resources"]}
    cursor=config.get("cursor",-1)+1
    incident=None
    for i,state in enumerate(tape):
        if any(a["status"]=="maintenance" for a in state["assets"].values()): incident=i; break
    async with server, httpx.AsyncClient(base_url=config["api"],timeout=5,trust_env=False) as client:
        if cursor>0:
            previous=tape[cursor-1]
            for r in spec["resources"]:
                await variables[r["id"]].write_value(json.dumps({"project_id":project,"source":"OPC_UA",
                    "source_id":r["opc_id"],"raw_id":f"OPC:{cursor-1}:{r['id']}","cursor":cursor-1,
                    "event_time":previous["time"]*1000,"unit":"ms","quality":"good","asset":previous["assets"][r["id"]]}))
        while not stop.is_set():
            if pause.is_set() or cursor>=len(tape):
                paused.set(); await asyncio.sleep(.08); continue
            paused.clear()
            state=tape[cursor]; data=copy.deepcopy(state); data.pop("assets")
            for j in data["jobs"].values():
                j["location"]=aliases.get(j["location"],j["location"])
                j["assigned"]=aliases.get(j["assigned"],j["assigned"])
            data["queues"]={aliases[k]:v for k,v in data["queues"].items()}
            envelope={"project_id":project,"raw_id":f"MES:{cursor}","cursor":cursor,"source":"MES",
                      "event_time":state["time"],"unit":"s","quality":"good","state":data}
            delivered=False
            while not delivered and not stop.is_set():
                try:
                    response=await client.post("/api/ingest/mes",json=envelope); response.raise_for_status()
                    for r in spec["resources"]:
                        observation={"project_id":project,"source":"OPC_UA","source_id":r["opc_id"],
                            "raw_id":f"OPC:{cursor}:{r['id']}","cursor":cursor,"event_time":state["time"]*1000,
                            "unit":"ms","quality":"good","asset":state["assets"][r["id"]]}
                        await variables[r["id"]].write_value(json.dumps(observation))
                    for _ in range(70):
                        ack=(await client.get("/api/internal/source-cursor",params={"project_id":project})).json()
                        if ack.get("cursor",-1)>=cursor: delivered=True; break
                        if stop.is_set(): break
                        await asyncio.sleep(.06)
                except (httpx.HTTPError,ValueError): await asyncio.sleep(.3)
            if not delivered: continue
            cursor+=1
            if target.value>=0 and cursor-1>=target.value:
                pause.set(); target.value=-1
            elif target.value==-2 and incident is not None and cursor-1>=incident:
                pause.set(); target.value=-1
            if target.value==-1:
                # Demonstration speed is simulation seconds per wall-clock second.
                dt=tape[cursor]["time"]-state["time"] if cursor<len(tape) else 0
                delay=min(1.5,max(.02,dt/max(1,speed.value)))
                for _ in range(max(1,int(delay/.05))):
                    if pause.is_set() or stop.is_set(): break
                    await asyncio.sleep(.05)
            else: await asyncio.sleep(.002)

class SubscriptionHandler:
    def __init__(self, accept): self.accept=accept
    async def datachange_notification(self,node,value,data):
        if not value: return
        try:
            payload=json.loads(value)
            self.accept(payload)
        except Exception as ex:
            self.accept({"invalid":str(ex),"raw":str(value)[:1000]})

async def subscribe(endpoint,spec,accept,status,stop_event):
    from asyncua import Client, ua
    retries=0
    while not stop_event.is_set():
        try:
            async with Client(endpoint,timeout=3) as client:
                idx=await client.get_namespace_index("urn:ontologysim:synthetic-factory")
                nodes=[client.get_node(ua.NodeId(r.opc_id,idx)) for r in spec.resources]
                subscription=await client.create_subscription(50,SubscriptionHandler(accept))
                await subscription.subscribe_data_change(nodes)
                # Explicit reads close the reconnect gap even if a variable has not changed.
                for node in nodes:
                    val=await node.read_value()
                    if val: accept(json.loads(val))
                status.update(state="connected",reconnects=retries,error=None)
                while not stop_event.is_set():
                    await asyncio.sleep(.25)
                    await client.check_connection()
                await subscription.delete()
        except asyncio.CancelledError: return
        except Exception as ex:
            retries+=1; status.update(state="reconnecting",reconnects=retries,error=str(ex))
            await asyncio.sleep(.5)
    status["state"]="disconnected"

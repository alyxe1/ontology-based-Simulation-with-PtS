"""Single-instance local server with owned, graceful shutdown."""
import json
import os
import secrets
import socket
from pathlib import Path
import uvicorn
import psutil

def main():
    from app.main import app
    from app.service import RUNTIME
    port=int(os.environ.get("ONTOLOGYSIM_PORT","8820"))
    # Binding fails early before creating COM workers or touching existing server state.
    probe=socket.socket(); probe.bind(("127.0.0.1",port)); probe.close()
    token=secrets.token_hex(24)
    server=uvicorn.Server(uvicorn.Config(app,host="127.0.0.1",port=port,log_level="warning",access_log=False,timeout_graceful_shutdown=8))
    app.state.server=server; app.state.shutdown_token=token
    RUNTIME.mkdir(parents=True,exist_ok=True)
    lock=RUNTIME/"server.json"
    lock.write_text(json.dumps({"pid":os.getpid(),"created":psutil.Process().create_time(),"port":port,"token":token}),encoding="utf-8")
    try: server.run()
    finally:
        if lock.exists() and json.loads(lock.read_text())["pid"]==os.getpid(): lock.unlink()

if __name__=="__main__": main()

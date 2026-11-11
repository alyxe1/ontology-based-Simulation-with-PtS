import argparse
import json
import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
import httpx
import psutil
import hashlib
from app_v2.common import BASE, DATA as RUNTIME

def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['start','stop']);parser.add_argument('--no-browser',action='store_true');args=parser.parse_args()
    port=int(os.environ.get('ONTOLOGYSIM_PORT','8820'));url=f'http://127.0.0.1:{port}'
    if args.action=='stop':
        path=RUNTIME/'server.json'
        if not path.exists(): print('No owned server is running.');return
        info=json.loads(path.read_text(encoding='utf-8'))
        response=httpx.post(f"http://127.0.0.1:{info['port']}/api/v2/internal/shutdown",headers={'x-shutdown-token':info['token']},timeout=15,trust_env=False)
        response.raise_for_status()
        for _ in range(100):
            try:owned_alive=psutil.pid_exists(info['pid']) and abs(psutil.Process(info['pid']).create_time()-info['created'])<.001
            except psutil.NoSuchProcess:owned_alive=False
            if not path.exists() and not owned_alive:print('Server, publisher and owned COM worker stopped.');return
            time.sleep(.3)
        raise RuntimeError('Shutdown is not yet confirmed; inspect .runtime-v02/shutdown.json and server.stderr.log')
    alive=False
    try:
        response=httpx.get(url+'/api/v2/session',timeout=2,trust_env=False)
        if response.is_success and response.json().get('version')=='0.2.0' and response.json().get('data_directory_id')==hashlib.sha256(str(RUNTIME.resolve()).casefold().encode()).hexdigest():alive=True
        else:raise RuntimeError(f'Port {port} is used by another service. Set ONTOLOGYSIM_PORT.')
    except (httpx.ConnectError,httpx.ConnectTimeout):pass
    if not alive:
        RUNTIME.mkdir(parents=True,exist_ok=True)
        if not (BASE/'frontend/dist/index.html').exists():raise RuntimeError('Frontend is not built. Run install.cmd first.')
        stdout=(RUNTIME/'server.stdout.log').open('w');stderr=(RUNTIME/'server.stderr.log').open('w')
        subprocess.Popen([sys.executable,'-m','scripts.v2_serve'],cwd=BASE,stdout=stdout,stderr=stderr,creationflags=subprocess.CREATE_NO_WINDOW)
        stdout.close();stderr.close()
        for _ in range(100):
            try:
                response=httpx.get(url+'/api/v2/session',timeout=1,trust_env=False)
                if response.is_success:alive=True;break
            except httpx.HTTPError:pass
            time.sleep(.2)
    if not alive:raise RuntimeError('Server did not become ready. Inspect .runtime-v02/server.stderr.log')
    print('OntologySim is ready at '+url)
    if not args.no_browser:webbrowser.open(url)

if __name__=='__main__':main()

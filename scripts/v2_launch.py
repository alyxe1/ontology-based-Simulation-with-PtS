import argparse
from hashlib import sha256
import json
import os
import subprocess
import sys
import time
import webbrowser

import httpx
import psutil

from app_v2.build import backend_build_id
from app_v2.common import BASE, DATA as RUNTIME


def owned_process(info):
    try:
        return abs(psutil.Process(info["pid"]).create_time() - info["created"]) < .001
    except psutil.NoSuchProcess:
        return False


def stop_server(port=None, *, upgrade=False):
    path = RUNTIME / "server.json"
    if not path.exists():
        if upgrade:
            raise RuntimeError("找不到当前工作台的进程记录，无法自动更新。")
        print("工作台服务已停止。")
        return
    info = json.loads(path.read_text(encoding="utf-8"))
    if (port is not None and info["port"] != port) or not owned_process(info):
        raise RuntimeError("无法确认工作台服务进程，请检查 .runtime-v02/server.json。")
    action = "upgrade-shutdown" if upgrade else "shutdown"
    response = httpx.post(f"http://127.0.0.1:{info['port']}/api/v2/internal/{action}",
                          headers={"x-shutdown-token":info["token"]}, timeout=15, trust_env=False)
    if upgrade and response.status_code == 409:
        raise RuntimeError(response.json()["detail"])
    response.raise_for_status()
    for _ in range(100):
        if not path.exists() and not owned_process(info):
            print("工作台服务及所属工作进程已停止。")
            return
        time.sleep(.3)
    raise RuntimeError("尚未确认工作台已退出，请检查 .runtime-v02/server.stderr.log。")


def read_session(url):
    directory_id = sha256(str(RUNTIME.resolve()).casefold().encode()).hexdigest()
    try:
        response = httpx.get(url+"/api/v2/session",timeout=2,trust_env=False)
    except (httpx.ConnectError,httpx.ConnectTimeout):
        return None
    try:
        data = response.json() if response.is_success else {}
    except ValueError:
        data = {}
    if not isinstance(data, dict) or data.get("version")!="0.2.0" or data.get("data_directory_id")!=directory_id:
        raise RuntimeError(f"{url} 正由其他服务使用。可通过 ONTOLOGYSIM_PORT 指定端口。")
    return data


def start_server(port):
    url = f"http://127.0.0.1:{port}"
    if not (BASE/"frontend/dist/index.html").exists():
        raise RuntimeError("前端尚未构建，请先运行 install.cmd。")
    expected = backend_build_id()
    session = read_session(url)
    if session:
        actual = session.get("backend_build")
        if actual == expected:
            return url
        if not actual:
            raise RuntimeError("检测到旧版服务。请等当前任务结束，运行 stop.cmd，再运行 start.cmd。")
        print("已检测到新的工作台代码，正在更新空闲服务。")
        stop_server(port,upgrade=True)
    RUNTIME.mkdir(parents=True,exist_ok=True)
    with (RUNTIME/"server.stdout.log").open("w") as stdout, (RUNTIME/"server.stderr.log").open("w") as stderr:
        process = subprocess.Popen([sys.executable,"-m","scripts.v2_serve"],cwd=BASE,stdout=stdout,stderr=stderr,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
    for _ in range(100):
        if process.poll() is not None:
            break
        try:
            session = read_session(url)
        except httpx.ReadTimeout:
            session = None
        if session and session.get("backend_build")==expected:
            return url
        time.sleep(.2)
    raise RuntimeError("未能启动匹配当前代码的服务，请检查 .runtime-v02/server.stderr.log。")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action",choices=["start","stop"])
    parser.add_argument("--no-browser",action="store_true")
    args = parser.parse_args()
    try:
        if args.action=="stop":
            stop_server()
            return
        url = start_server(int(os.environ.get("ONTOLOGYSIM_PORT","8820")))
    except (RuntimeError,OSError,httpx.HTTPError) as error:
        parser.exit(1,str(error)+"\n")
    print("工作台已就绪 "+url)
    if not args.no_browser:
        webbrowser.open(url)


if __name__=="__main__":
    main()

"""Publish the completed, verified native default trajectory for fast first use."""
import argparse
import hashlib
import json
import shutil
from pathlib import Path
import pyoxigraph as ox
from app_v2.common import DATA, canonical, atomic_write, digest, now
from app_v2.evidence import Signer
from app_v2.graph import OntologyStore
from app_v2.reference import REFERENCE_DIR, model_contract
from app_v2.specification import default_spec


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',default=str(DATA));args=parser.parse_args()
    root=Path(args.root)
    catalog=object.__new__(OntologyStore);catalog.store=ox.Store.read_only(str(root/'catalog'))
    matches=[]
    for task in catalog.entities('Task'):
        payload=json.loads(task['payload'])
        if task['kind']=='baseline' and task['status']=='completed' and payload.get('model_contract')==model_contract() and payload.get('spec')==default_spec().model_dump():matches.append(task)
    if not matches:raise RuntimeError('没有当前模型版本下已完成的默认车间原生轨迹')
    task=max(matches,key=lambda row:row['created_at']);folder=Path(task['output'])
    native=json.loads(task['native']);trace=folder/'source.jsonl'
    if not native['verified'] or hashlib.sha256(trace.read_bytes()).hexdigest()!=task['trace_hash']:raise ValueError('原生结果或轨迹核对失败')
    # Check the retained RDF journal and native result before publishing the artifact.
    graph=OntologyStore.restore(folder/'runtime.trig')
    if not graph.verify()['valid']:raise ValueError('参考运行的本体签名校验失败')
    payload=dict(schema=1,source_kind='synthetic',native_verified=True,engine=native['engine'],
        native_result_hash=digest(native),runtime_hash=hashlib.sha256((folder/'runtime.trig').read_bytes()).hexdigest(),
        specification_hash=digest(default_spec().model_dump()),model_contract=model_contract(),trace_hash=task['trace_hash'],
        native_task=task['id'],generated_at=task['created_at'],packaged_at=now(),seed=2606,algorithm='sha256-paired-v1',
        frames=sum(1 for _ in trace.open(encoding='utf-8')))
    signer=Signer(root/'signing-key.dpapi');envelope=dict(payload=payload,signature=signer.sign(payload),public_key=signer.public)
    REFERENCE_DIR.mkdir(exist_ok=True);shutil.copyfile(trace,REFERENCE_DIR/'source.jsonl')
    atomic_write(REFERENCE_DIR/'manifest.json',canonical(envelope))
    atomic_write(REFERENCE_DIR/'manifest.sha256',hashlib.sha256((REFERENCE_DIR/'manifest.json').read_bytes()).hexdigest())
    print(canonical({'prepared':True,'frames':payload['frames'],'trace_hash':payload['trace_hash']}))


if __name__=='__main__':main()

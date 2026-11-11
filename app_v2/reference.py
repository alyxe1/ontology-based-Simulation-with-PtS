"""Verified, packaged factory observations; never used as prediction inputs."""
import hashlib
import base64
import json
from pathlib import Path
from .common import BASE, digest
from .evidence import check_signature

REFERENCE_DIR = BASE / 'factory-reference'
REFERENCE_FINGERPRINT = '5a8a5a4ac1c50c201debc5d199402b4a713038a41466961d452de62c0db0290c'


def model_contract():
    files = ('app_v2/runtime.py', 'app_v2/native.py', 'app_v2/specification.py',
             'app_v2/worker.py', 'ontology/v2/core.ttl')
    return digest({str(Path(name)): hashlib.sha256((BASE/name).read_bytes().replace(b'\r\n',b'\n')).hexdigest() for name in files})


def verified_reference(specification, directory=REFERENCE_DIR):
    directory = Path(directory)
    manifest = directory / 'manifest.json'
    if not manifest.exists():
        return None
    envelope = json.loads(manifest.read_text(encoding='utf-8'))
    payload = envelope['payload']
    if hashlib.sha256(base64.b64decode(envelope['public_key'])).hexdigest() != REFERENCE_FINGERPRINT:
        raise ValueError('参考轨迹签名公钥不属于本次发布')
    check_signature(payload, envelope['signature'], envelope['public_key'])
    # The release pins the artifact separately from its self-contained signature.
    expected = (directory/'manifest.sha256').read_text(encoding='ascii').strip()
    if hashlib.sha256(manifest.read_bytes()).hexdigest() != expected:
        raise ValueError('随附参考轨迹的发布清单校验失败')
    if payload['specification_hash'] != digest(specification) or payload['model_contract'] != model_contract():
        return None
    if not payload['native_verified'] or payload['source_kind'] != 'synthetic':
        raise ValueError('参考轨迹缺少原生核对证明')
    path = directory/'source.jsonl'
    if hashlib.sha256(path.read_bytes()).hexdigest() != payload['trace_hash']:
        raise ValueError('随附参考轨迹内容校验失败')
    return path, payload, envelope

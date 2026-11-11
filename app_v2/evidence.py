"""Signed append-only evidence and independent verification; no production state here."""
from __future__ import annotations
import base64
import hashlib
import json
import os
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives import serialization
from .common import atomic_write, canonical, digest

class Signer:
    def __init__(self, key_path=None):
        self.path = Path(key_path) if key_path else None
        if self.path and self.path.exists():
            import win32crypt
            raw = win32crypt.CryptUnprotectData(self.path.read_bytes(), None, None, None, 0)[1]
            self.key = Ed25519PrivateKey.from_private_bytes(raw)
        else:
            self.key = Ed25519PrivateKey.generate()
            if self.path:
                if os.name != "nt":
                    raise RuntimeError("持久签名密钥要求 Windows DPAPI；测试请使用内存密钥")
                import win32crypt
                raw = self.key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
                atomic_write(self.path, win32crypt.CryptProtectData(raw, "OntologySim V0.2", None, None, None, 0))
        self.public = base64.b64encode(self.key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
        self.fingerprint = hashlib.sha256(base64.b64decode(self.public)).hexdigest()

    def sign(self, value):
        return base64.b64encode(self.key.sign(canonical(value).encode())).decode()

def check_signature(value, signature, public):
    Ed25519PublicKey.from_public_bytes(base64.b64decode(public)).verify(base64.b64decode(signature), canonical(value).encode())

def verify_records(records, trusted_fingerprint=None, anchor=None):
    previous = "0" * 64
    errors = []
    heads = {}
    for index, record in enumerate(records, 1):
        try:
            payload = record["payload"]
            if payload["sequence"] != index or payload["previous"] != previous:
                raise ValueError("序号或前序摘要不连续")
            if digest(payload) != record["hash"]:
                raise ValueError("内容摘要不匹配")
            fingerprint = hashlib.sha256(base64.b64decode(record["public_key"])).hexdigest()
            if trusted_fingerprint and fingerprint != trusted_fingerprint:
                raise ValueError("签名公钥与独立保管的指纹不一致")
            check_signature(payload, record["signature"], record["public_key"])
            previous = record["hash"]
            heads[index] = previous
        except Exception as ex:
            errors.append({"sequence": index, "error": str(ex) or "签名无效"})
            break
    if anchor and heads.get(anchor["sequence"]) != anchor["hash"]:
        errors.append({"error": "独立回执对应的历史缺失或被改写"})
    return {"valid": not errors, "records": len(records), "head": previous, "errors": errors,
            "independently_anchored": bool(trusted_fingerprint and anchor),
            "assurance": "应用层只追加；独立指纹和回执用于检测整体替换及历史截断"}

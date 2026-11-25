"""Identify the backend source loaded by a local workbench process."""
from hashlib import sha256

from .common import BASE, digest


def backend_build_id():
    files = [
        *(BASE / "app_v2").glob("*.py"),
        *(BASE / "app").glob("*.py"),
        *(BASE / "ontology/v2").glob("*.ttl"),
        BASE / "scripts/v2_serve.py",
    ]
    return digest({
        path.relative_to(BASE).as_posix(): sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        for path in files
    })

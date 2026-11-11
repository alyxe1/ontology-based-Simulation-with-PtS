from __future__ import annotations
import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("ONTOLOGYSIM_V2_DATA", BASE / ".runtime-v02"))
PROGID = os.environ.get("ONTOLOGYSIM_PROGID", "Tecnomatix.PlantSimulation.RemoteControl.26.6")
NS = "urn:ontologysim:v2:"
EPS = 1e-6

def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)

def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()

def now():
    return datetime.now(timezone.utc).isoformat()

def uid(prefix):
    return prefix + "_" + uuid.uuid4().hex[:16]

def atomic_write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".writing")
    if isinstance(content, bytes):
        temporary.write_bytes(content)
    else:
        temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)

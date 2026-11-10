from __future__ import annotations

import hashlib
import json
from typing import Literal
from pydantic import BaseModel, Field, model_validator

VERSION = "1.0"

def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)

def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()

class Strict(BaseModel):
    model_config = {"extra": "forbid", "allow_inf_nan": False, "validate_default": True}

class Capability(Strict):
    operation: str
    materials: list[str]
    tolerance_um: float = Field(gt=0)

class Resource(Strict):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,39}$")
    name: str
    capabilities: list[Capability] = Field(min_length=1)
    shift_hours: float = Field(default=8, gt=0, le=24)
    tool_limit: int = Field(default=0, ge=0)
    maintenance_s: float = Field(default=2700, gt=0)
    mes_id: str
    opc_id: str

class Operation(Strict):
    id: str
    name: str
    operation: str
    material: str
    tolerance_um: float = Field(gt=0)
    duration_s: float = Field(gt=0, le=86400)
    preferred: str

class Product(Strict):
    id: str
    name: str
    operations: list[Operation] = Field(min_length=1)

class Order(Strict):
    id: str
    product: str
    quantity: int = Field(gt=0, le=1000)
    due_s: float = Field(gt=0)
    release_s: float = Field(default=0, ge=0)

class ProductionSpec(Strict):
    version: Literal["1.0"] = VERSION
    name: str = "机加工柔性车间"
    seed: int = 2606
    horizon_s: float = Field(default=129600, gt=0, le=604800)
    transfer_s: float = Field(default=30, ge=0, le=3600)
    resources: list[Resource] = Field(min_length=1, max_length=30)
    products: list[Product] = Field(min_length=1)
    orders: list[Order] = Field(min_length=1)

    @model_validator(mode="after")
    def references(self):
        for xs in [self.resources, self.products, self.orders]:
            if len({x.id for x in xs}) != len(xs):
                raise ValueError("实体 ID 不得重复")
        if sum(o.quantity for o in self.orders) > 1000:
            raise ValueError("PoC 单次最多 1000 件")
        resources = {r.id for r in self.resources}
        products = {p.id for p in self.products}
        for p in self.products:
            if len({op.id for op in p.operations}) != len(p.operations):
                raise ValueError("产品工序 ID 不得重复")
            for op in p.operations:
                if op.preferred not in resources:
                    raise ValueError(f"不存在的设备引用: {op.preferred}")
        for o in self.orders:
            if o.product not in products:
                raise ValueError(f"不存在的产品引用: {o.product}")
        return self

class ScenarioPatch(Strict):
    version: Literal["1.0"] = VERSION
    name: str
    rule: Literal["FIFO", "EDD"] = "FIFO"
    reroute: bool = False
    shift_hours: float | None = Field(default=None, gt=0, le=24)
    assignments: dict[str, str] = Field(default_factory=dict)

class TwinSnapshot(Strict):
    version: Literal["1.0"] = VERSION
    id: str
    project_id: str
    spec_hash: str
    source_cursor: int
    state: dict
    state_hash: str
    samples: dict[str, float]
    assumptions: list[str]

class RunResult(Strict):
    version: Literal["1.0"] = VERSION
    run_id: str
    snapshot_id: str | None
    patch: ScenarioPatch
    state: dict
    kpi: dict
    orders: list[dict]
    segments: list[dict]
    events: list[dict]
    native: dict
    assumptions: list[str]
    input_hash: str

class CheckpointManifest(Strict):
    version: Literal["1.0"] = VERSION
    id: str
    project_id: str
    snapshot: TwinSnapshot
    spec: ProductionSpec
    created_at: str
    event_cursor: int
    files: dict[str, str]
    recovery: Literal["rebuild-and-verify"] = "rebuild-and-verify"

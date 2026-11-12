from typing import Literal
from pydantic import Field, StringConstraints
from typing import Annotated
from .specification import Strict

class ScenarioDefinition(Strict):
    name: str = "资格约束改派"
    rule: Literal["FIFO","EDD"] = "EDD"
    reroute: bool = True
    shift_hours: float = Field(default=0,ge=0,le=24)
    assignments: dict[str,str] = {}

class Login(Strict):
    username: str
    password: str

class NewAccount(Login):
    name: str
    role: Literal["admin","analyst","auditor"] = "analyst"

class RunRequest(Strict):
    checkpoint_id: str
    patch: ScenarioDefinition

class CompareRequest(Strict):
    checkpoint_id: str
    repetitions: Literal[1,5] = 1
    scenarios: list["ComparisonScenario"] | None = Field(default=None,min_length=1,max_length=3)

class ComparisonScenario(Strict):
    name: Annotated[str,StringConstraints(strip_whitespace=True,min_length=1,max_length=80)]
    rule: Literal["FIFO","EDD"] | None = None
    reroute: bool | None = None
    shift_hours: float | None = Field(default=None,ge=0,le=24)
    assignments: dict[str,str] = Field(default_factory=dict)

class CheckpointRequest(Strict):
    incident_id: str | None = None
    source_cursor: int | None = Field(default=None,ge=0)

class BusinessRevision(Strict):
    workspace: str
    base_revision: int
    specification: dict
    reason: str = Field(min_length=3,max_length=1000)

class SourceRecordEnvelope(Strict):
    workspace: str
    source: Literal["MES","OPC"]
    raw_id: str = Field(min_length=1,max_length=150)
    cursor: int = Field(ge=0)
    event_time: float = Field(ge=0)
    unit: Literal["s","ms"]
    quality: Literal["good","bad","uncertain"]
    synthetic: bool
    records: list[dict] = []
    source_id: str = ""
    record: dict = {}

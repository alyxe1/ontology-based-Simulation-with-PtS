import io
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

from app_v2 import cases
from app_v2.common import atomic_write, canonical
from app_v2.graph import OntologyStore
from app_v2.runtime import OntologyRuntime


def test_case_keeps_observations_and_detects_tampering_and_truncation(tmp_path):
    catalog = OntologyStore()
    baseline = OntologyStore()
    baseline.commit([
        dict(id="O1", type="WorkOrder", quantity=8),
        dict(id="CNC1", type="Equipment", name="CNC1"),
        dict(id="CNC1/state", type="AssetState", resource=cases.Ref("CNC1"), status="maintenance"),
        dict(id="J1", type="Workpiece", order=cases.Ref("O1"), assigned=cases.Ref("CNC1")),
    ], "fixture")
    workbench = SimpleNamespace(
        catalog=catalog, signer=catalog.signer, root=tmp_path, context=lambda identity:OntologyRuntime(baseline),
        audit=lambda kind, actor, changes, **kwargs: catalog.commit(changes, kind, actor=actor, **kwargs),
    )
    case_id, run, actor = "case_fixture", "run_fixture", "user/test"
    artifact = canonical({"summary": {"kpi": {"window_start_s": 50, "window_end_s": 150},
                                     "orders": [{"id": "O1", "quantity": 8, "completed": 7, "completion_s": None}]}}).encode()
    files = {f"{run}/result.json": artifact, f"{run}/runtime.trig": b"run fixture",
             "baseline/runtime.trig": b"baseline fixture"}
    files.update({f"tbox/{name}": (cases.TBOX / name).read_bytes() for name in cases.TBOX_FILES})
    for name, data in files.items():
        atomic_write(tmp_path / "evidence-cases" / case_id / name, data)
    cases.append(workbench, case_id, "case_sealed", actor, [
        dict(id=case_id, type="EvidenceCase", checkpoint="baseline_fixture", name="Case", objective="Order progress",
             created_at="2026-01-01", baseline=canonical({"graph_head": "baseline-test",
                                                         "incident_id": "CNC1/state", "incident_jobs": ["J1"]}),
             runs=canonical([{"id": run, "name": "Trial", "status": "completed", "inputs": {}}]),
             files=canonical({name: cases.checksum(data) for name, data in files.items()})),
    ], [], "sealed")
    decision = cases.Adoption(run_id=run, rationale="More output")
    cases.adopt(workbench, case_id, decision, actor)
    with pytest.raises(ValueError, match="已登记"):
        cases.adopt(workbench, case_id, decision, actor)
    at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    cases.execute(workbench, case_id, cases.FieldExecution(
        execution_ref="FIELD-1", executor="Shift A", start_at=at, end_at=at + timedelta(seconds=100), description="Executed"
    ), actor)
    for same_scope in [False, True]:
        cases.observe(workbench, case_id, cases.FieldOutcome(
            source="Shift report", source_record="O1: 6", same_scope=same_scope,
            orders=[cases.FieldOrder(order_id="O1", completed=6)]
        ), actor)
    detail = cases.case_detail(workbench, case_id)
    rows = [json.loads(r["rows"])[0] for r in detail["entries"] if r["type"] == "OutcomeEvaluation"]
    assert [r["delta"] for r in rows] == [None, -1]
    evaluation = next(r for r in detail["entries"] if r["type"] == "OutcomeEvaluation")
    graph = cases.case_graph_view(workbench, case_id, focus=str(cases.OS[evaluation["id"]]), depth=2)
    edge = next(row for row in graph["edges"] if row["predicate"] == str(cases.OS.comparesOrder))
    target = next(row for row in graph["nodes"] if row["id"] == edge["to"])
    assert target["layer"] == "制造对象" and target["action"] == {"inspect": "O1", "context": "baseline"}
    impact = cases.case_graph_view(workbench, case_id, depth=2)
    assert {"CNC1", "J1", "O1"} <= {row["label"] for row in impact["nodes"]}
    from app_v2.evidence_graph import schema_view
    schema = schema_view(focus=str(cases.OS.ArchivedBaseline), depth=1)
    assert schema["version"].endswith("prov-o-20130430")
    assert any(edge["from"] == schema["focus"] and edge["to"] == str(cases.PROV.Entity) for edge in schema["edges"])
    receipt = detail["receipt"]
    with cases.export_case(workbench, case_id) as stream:
        original = stream.read()
    assert cases.verify_case_bundle(original, catalog.signer.fingerprint, receipt)["independently_anchored"]
    future = {**receipt, "sequence": receipt["sequence"] + 1}
    assert not cases.verify_case_bundle(original, catalog.signer.fingerprint, future)["valid"]
    changed = io.BytesIO()
    with ZipFile(io.BytesIO(original)) as source, ZipFile(changed, "w") as target:
        for name in source.namelist():
            target.writestr(name, b"altered" if name == f"{run}/result.json" else source.read(name))
    assert not cases.verify_case_bundle(changed.getvalue(), catalog.signer.fingerprint, receipt)["valid"]

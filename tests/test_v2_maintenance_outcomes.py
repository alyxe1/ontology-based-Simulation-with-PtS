from app_v2.graph import OntologyStore, Ref
from app_v2.maintenance_outcomes import maintenance_outcomes


def test_outcomes_track_the_frozen_operation_not_later_operations_or_queue_moves():
    baseline = OntologyStore()
    scenario = OntologyStore()
    job = dict(id="J1", type="Workpiece", product=Ref("P"), order=Ref("O"), batch=Ref("B"),
               status="queued", assigned=Ref("CNC01"), attempt=0, op_index=0, released_at=0., completed_at=-1.)
    waiting = dict(job, id="J2")
    spec = [
        dict(id="CNC01/state", type="AssetState", status="maintenance", resource=Ref("CNC01"), maintenance_until=40.),
        dict(id="P", type="Product", operations=[Ref("P/mill"), Ref("P/drill")]),
        dict(id="P/mill", type="OperationPlan", index=0, name="Mill"),
        dict(id="P/drill", type="OperationPlan", index=1, name="Drill"),
        dict(id="O", type="WorkOrder", quantity=2, due_s=75.),
    ]
    baseline.commit([*spec, job, waiting, dict(id="clock", type="Clock", time_s=20.)], "baseline")
    mill = dict(id="mill", type="Execution", job=Ref("J1"), resource=Ref("CNC02"), kind="processing",
                attempt=0, operation=Ref("P/mill"), start_s=30., end_s=43., actual_end_s=43.)
    drill = dict(mill, id="drill", resource=Ref("CNC01"), operation=Ref("P/drill"),
                 start_s=50., end_s=60., actual_end_s=60.)
    scenario.commit([
        *spec, dict(job, status="done", assigned="", op_index=2, completed_at=60.),
        dict(waiting, assigned=Ref("CNC03")), mill, drill,
        dict(id="clock", type="Clock", time_s=80.),
        dict(id="policy", type="Policy", window_start_s=20.),
    ], "run")
    report = maintenance_outcomes(baseline, scenario, {"incident_id": "CNC01/state", "incident_jobs": ["J1", "J2"]})
    finished, queued = report["jobs"]
    assert finished["destination"] == "CNC02"
    assert finished["operation_completed_s"] == 43.
    assert finished["completed_s"] == 60.
    assert queued["destination"] is None and queued["waiting_original_operation"]
    assert queued["final_resource"] == "CNC03"
    assert report["orders"][0]["affected_completed"] == 1 and report["orders"][0]["completion_s"] is None
    assert maintenance_outcomes(baseline, scenario, {}) is None

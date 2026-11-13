import pytest

from app_v2.graph import OntologyStore, Ref
from app_v2.result_analysis import analyze_result


def test_result_analysis_recovers_open_state_and_splits_rerouted_queue():
    graph = OntologyStore()
    job = dict(id="J1", type="Workpiece", product=Ref("P1"), status="queued",
               assigned=Ref("A"), released_at=90., op_index=0)
    asset = dict(id="A/state", type="AssetState", status="maintenance", resource=Ref("A"))
    graph.commit([
        dict(id="policy", type="Policy", window_start_s=100., shift_hours=0.),
        dict(id="clock", type="Clock", time_s=100.),
        dict(id="P1", type="Product", name="Part"),
        dict(id="P1/op", type="OperationPlan", name="Process"),
        dict(id="A", type="Equipment", name="Machine A", shift_hours=.05),
        dict(id="B", type="Equipment", name="Machine B", shift_hours=.05),
        dict(id="F", type="FixturePool", name="Fixture", quantity=1),
        dict(id="J2", type="Workpiece", product=Ref("P1"), status="done", released_at=50., completed_at=100.),
        job, asset,
    ], "initial", time_s=100.)
    graph.commit([dict(id="analysis_basis", type="Scenario", branch="sample")], "branch_created", time_s=100.)
    job = {**job, "assigned": Ref("B")}
    graph.commit([job], "piece_rerouted", time_s=130.)
    graph.commit([{**asset, "status": "idle"}], "maintenance_finished", time_s=150.)
    job = {**job, "status": "processing"}
    reservation = dict(id="J1/fixture", type="FixtureReservation", fixture=Ref("F"), status="reserved")
    execution = dict(id="J1/process", type="Execution", job=Ref("J1"), resource=Ref("B"),
                     operation=Ref("P1/op"), kind="processing", attempt=0, status="active", start_s=165., end_s=195.)
    graph.commit([job, reservation, execution], "processing_started", time_s=165.)
    graph.commit([
        {**job, "status": "done", "completed_at": 195.},
        {**reservation, "status": "released"}, {**execution, "status": "completed", "actual_end_s": 195.},
        dict(id="J1/inspection", type="Inspection", job=Ref("J1"), execution=Ref("J1/process"), time_s=195., result="pass"),
    ], "operation_completed", time_s=195.)
    graph.commit([dict(id="clock", type="Clock", time_s=220.)], "native_clock_advanced", time_s=220.)

    result = analyze_result(graph, "sample")
    a, b = result["resources"]
    assert [a["queue_area_s"], b["queue_area_s"]] == [30., 35.]
    assert result["job_wait_s"] == {"J1": 65.}
    assert a["maintenance_s"] == 50. and a["off_shift_s"] == 40.
    assert b["processing_s"] == 30. and b["off_shift_s"] == 25.
    assert result["completion_times"] == [195.]
    assert result["average_wip"] == pytest.approx(95 / 120)
    assert result["fixtures"][0]["peak"] == 1 and result["fixtures"][0]["average"] == .25
    assert result["inspections"][0]["attempt"] == 0
    assert result["curve"][-1] == [220., 0, 1, 0, 0, 0]

from rdflib import Graph

from app_v2.graph import OntologyStore, Ref
from app_v2.material_flow import flow_route, workpiece_history
from app_v2.ontology_overview import download_graph, overview, resource_network, triples_page
from app_v2.runtime import OntologyRuntime
from app_v2.specification import default_spec


def test_global_graph_describes_the_same_assertions_as_its_download():
    graph = OntologyRuntime.create(OntologyStore(), default_spec()).graph
    for scope in ("state", "schema", "evidence"):
        snapshot = overview(graph, scope)
        head = snapshot["head"]
        first = triples_page(graph, scope, head, 0, 30, "", "", "")
        second = triples_page(graph, scope, head, 30, 30, "", "", "")
        downloaded = Graph().parse(data=download_graph(graph, scope, head), format="turtle")
        assert first["total"] == second["total"] == snapshot["triples"] == len(downloaded)
        assert all(triple not in first["items"] for triple in second["items"])
    schema = overview(graph, "schema")
    network = resource_network(graph, "schema", schema["head"], "")
    assert len(network["nodes"]) == schema["resources"]
    assert any(n["id"] == "http://www.w3.org/ns/prov#Entity" for n in network["nodes"])
    assert download_graph(OntologyStore(), "schema", schema["head"]) is None


def test_pinned_graph_and_workpiece_trace_do_not_read_later_updates():
    runtime = OntologyRuntime.create(OntologyStore(), default_spec())
    graph = runtime.graph
    route = flow_route(runtime, "P1")
    rough = next(step for step in route["steps"] if step["id"] == "P1/ROUGH")
    assert rough["preferred"] == "CNC02" and "CNC02" in rough["qualified"]
    assert route["rework_to"] == "P1/MILL"
    snapshot = overview(graph, "state")
    piece = graph.get("J00001")
    piece.update(status="queued", assigned=Ref("SAW"), location=Ref("SAW"))
    graph.commit([piece], "piece_released", time_s=5)
    page = triples_page(graph, "state", snapshot["head"], 0, 50, "", "", "urn:ontologysim:v2:J00001")
    states = {row["object"]["text"] for row in page["items"] if row["predicate"]["text"] == "status"}
    assert states == {"pending"}
    frozen = workpiece_history(runtime, piece["id"], through=snapshot["sequence"])
    current = workpiece_history(runtime, piece["id"])
    assert len(frozen["steps"]) == 1
    assert current["steps"][-1]["assigned"] == "SAW"
    assert current["steps"][-1]["time_s"] == 5

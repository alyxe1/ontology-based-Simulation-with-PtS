"""Read the defined factory route and the recorded movements of one workpiece."""
from pyoxigraph import NamedNode

from .graph import AUDIT, PROV, decode_record, iri
from .specification import qualification
from .views import product_route


def flow_route(runtime, product_id):
    graph = runtime.graph
    with graph.lock:
        product = graph.get(product_id)
        if not product or product["type"] != "Product":
            raise ValueError("产品版本不存在")
        route = product_route(graph, product_id)
        for step in route["steps"]:
            step["qualified"] = [row["resource"] for row in qualification(graph, step["id"]) if row["eligible"]]
        return {**route, "transfer_s": runtime.config["transfer_s"]}


def workpiece_history(runtime, identity, through=None):
    graph = runtime.graph
    with graph.lock:
        job = graph.get(identity)
        if not job or job["type"] != "Workpiece":
            raise ValueError("工件不存在")
        through = graph.sequence if through is None else min(through, graph.sequence)
        sequences = {
            int(q.subject.value.rsplit("/", 1)[-1])
            for q in graph.store.quads_for_pattern(None, NamedNode(PROV + "specializationOf"),
                                                   iri("record/" + identity), AUDIT)
        }
        steps = []
        for sequence in sorted(n for n in sequences if n <= through):
            records = graph.records(after=sequence - 1, through=sequence, limit=1)
            if not records:
                continue
            commit = records[0]["payload"]
            change = next((item for item in commit["changes"] if item["after"]["id"] == identity), None)
            if not change:
                continue
            fields = ("status", "location", "assigned", "op_index", "attempt")
            after = decode_record(change["after"])
            step = {field: after.get(field) for field in fields}
            if steps and all(steps[-1][field] == step[field] for field in fields):
                continue
            steps.append(dict(sequence=sequence, time_s=commit["simulation_time_s"], activity=commit["kind"], **step))
        return dict(workpiece=identity, sequence=through, steps=steps)

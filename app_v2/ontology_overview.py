"""Read the complete asserted RDF graphs without sending them to a single canvas."""
from collections import Counter, OrderedDict, defaultdict
from functools import lru_cache
from threading import Lock
from urllib.parse import unquote
from weakref import ref

import pyoxigraph as ox

from .common import BASE, NS
from .graph import AUDIT, PROV, RDF, SCHEMA, STATE
from .views import LABELS, RELATIONS, business_layer

GRAPHS = {"state": STATE, "evidence": AUDIT, "schema": SCHEMA}
RDF_TYPE = ox.NamedNode(RDF + "type")
OWL = "http://www.w3.org/2002/07/owl#"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"
SCHEMA_TYPES = {
    OWL + "Class": "类", RDFS + "Class": "类",
    OWL + "ObjectProperty": "对象属性", OWL + "DatatypeProperty": "数据属性",
    OWL + "AnnotationProperty": "注解属性", OWL + "Ontology": "本体及导入",
    OWL + "Restriction": "OWL 属性限制", RDF + "Property": "RDF 属性",
}
TYPE_LABELS = {**LABELS, "SourceRecord": "来源记录", "SourceGeneration": "数据生成记录",
               "SourceValidation": "接入校验", "Mapping": "数据映射", "ProductionPlan": "生产计划",
               "OntologyVersion": "本体版本", "prov:Entity": "证据实体", "prov:Activity": "活动",
               "prov:SoftwareAgent": "软件责任者", "prov:Person": "人员",
               "prov:Association": "责任关联", "prov:Usage": "输入引用"}
PREDICATE_LABELS = {**RELATIONS, "rdf:type": "实例或声明的类型", "rdfs:subClassOf": "是其子类",
                    "rdfs:domain": "定义域", "rdfs:range": "值域", "owl:imports": "导入",
                    "prov:used": "使用", "prov:wasGeneratedBy": "生成自",
                    "prov:wasDerivedFrom": "派生自", "prov:wasAssociatedWith": "责任主体"}
SNAPSHOTS = OrderedDict()
SNAPSHOT_LOCK = Lock()


def compact(uri):
    for base, prefix in ((NS, ""), (PROV, "prov:"), (OWL, "owl:"), (RDFS, "rdfs:"), (RDF, "rdf:")):
        if uri.startswith(base):
            return prefix + unquote(uri.removeprefix(base))
    return uri


@lru_cache(maxsize=4)
def schema_store(core):
    store = ox.Store()
    store.load(input=core, format=ox.RdfFormat.TURTLE, to_graph=SCHEMA)
    for name in ("ontology.ttl", "simulation.ttl", "evidence.ttl", "prov-o.ttl"):
        store.load(path=str(BASE / "ontology/v2" / name), format=ox.RdfFormat.TURTLE, to_graph=SCHEMA)
    return store


def capture(graph, scope):
    graph_name = GRAPHS[scope]
    with graph.lock:
        head, sequence = graph.head, graph.sequence
        if pinned := pinned_dataset(graph, scope, head):
            return pinned[0], graph_name, head, sequence
        data = graph.store.dump(format=ox.RdfFormat.TURTLE, from_graph=graph_name)
    if scope == "schema":
        store = schema_store(data)
    else:
        store = ox.Store()
        store.load(input=data, format=ox.RdfFormat.TURTLE, to_graph=graph_name)
    with SNAPSHOT_LOCK:
        key = (id(graph), scope, head)
        if (saved := SNAPSHOTS.get(key)) and saved[0]() is graph:
            store = saved[1]
        SNAPSHOTS[key] = (ref(graph), store, sequence)
        SNAPSHOTS.move_to_end(key)
        while len(SNAPSHOTS) > 6:
            SNAPSHOTS.popitem(last=False)
    return store, graph_name, head, sequence


def pinned_dataset(graph, scope, head):
    key = (id(graph), scope, head)
    with SNAPSHOT_LOCK:
        selected = SNAPSHOTS.get(key)
        if not selected or selected[0]() is not graph:
            return None
        SNAPSHOTS.move_to_end(key)
        return selected[1], GRAPHS[scope], selected[2]


def explicit_types(store, graph_name, scope):
    types = defaultdict(set)
    for q in store.quads_for_pattern(None, RDF_TYPE, None, graph_name):
        if isinstance(q.object, ox.NamedNode):
            types[q.subject].add(q.object.value)
    if scope == "schema":
        return {node: next((label for uri, label in SCHEMA_TYPES.items() if uri in values), "其他声明")
                for node, values in types.items()}
    return {node: compact(min(values)) for node, values in types.items()}


def node_group(node, types, scope):
    if node in types:
        return types[node]
    if isinstance(node, ox.BlankNode):
        return "空白节点"
    if scope == "schema":
        return "引用术语"
    return "引用节点"


def overview(graph, scope):
    store, graph_name, head, sequence = capture(graph, scope)
    types = explicit_types(store, graph_name, scope)
    resources = set()
    subjects = set()
    outgoing = Counter()
    links = Counter()
    literals = 0
    triples = 0
    for q in store.quads_for_pattern(None, None, None, graph_name):
        subjects.add(q.subject)
        resources.add(q.subject)
        source = node_group(q.subject, types, scope)
        outgoing[source] += 1
        triples += 1
        if isinstance(q.object, ox.Literal):
            literals += 1
        else:
            resources.add(q.object)
            links[(source, node_group(q.object, types, scope))] += 1
    nodes = Counter(node_group(node, types, scope) for node in resources)
    return dict(scope=scope, sequence=sequence, head=head, graph=graph_name.value,
                triples=triples, literals=literals, resources=len(resources), subjects=len(subjects),
                groups=[dict(id=group, label=TYPE_LABELS.get(group, group), nodes=count,
                             layer=business_layer(group) if scope == "state" else "标准",
                             triples=outgoing[group]) for group, count in sorted(nodes.items())],
                links=[dict(source=source, target=target, count=count)
                       for (source, target), count in sorted(links.items())])


def term_view(term):
    if isinstance(term, ox.Literal):
        value = term.value
        return dict(kind="literal", text=value[:320], truncated=len(value) > 320,
                    datatype=compact(term.datatype.value), language=term.language)
    if isinstance(term, ox.BlankNode):
        return dict(kind="blank", text=str(term), id=str(term))
    return dict(kind="iri", text=compact(term.value), id=term.value)


def triples_page(graph, scope, head, offset, limit, group, query, term_id):
    selected = pinned_dataset(graph, scope, head)
    if not selected:
        return None
    store, graph_name, sequence = selected
    types = explicit_types(store, graph_name, scope) if group else {}
    items = []
    matched = 0
    query = query.strip().casefold()
    for q in store.quads_for_pattern(None, None, None, graph_name):
        if group and node_group(q.subject, types, scope) != group:
            continue
        if query or term_id:
            terms = [q.subject, q.predicate]
            if not isinstance(q.object, ox.Literal):
                terms.append(q.object)
            identifiers = [str(term) if isinstance(term, ox.BlankNode) else term.value for term in terms]
            if term_id and term_id not in identifiers:
                continue
            if query and not any(query in compact(value).casefold() for value in identifiers):
                continue
        if offset <= matched < offset + limit:
            predicate = term_view(q.predicate)
            items.append(dict(subject=term_view(q.subject), predicate=predicate, object=term_view(q.object),
                              label=PREDICATE_LABELS.get(predicate["text"], predicate["text"])))
        matched += 1
    return dict(sequence=sequence, total=matched, offset=offset, items=items)


def resource_network(graph, scope, head, group):
    selected = pinned_dataset(graph, scope, head)
    if not selected:
        return None
    store, graph_name, sequence = selected
    types = explicit_types(store, graph_name, scope)
    nodes = set()
    edges = []
    labels = {}
    display_names = {RDFS + "label", NS + "name"}
    for q in store.quads_for_pattern(None, None, None, graph_name):
        if group and node_group(q.subject, types, scope) != group:
            continue
        nodes.add(q.subject)
        if isinstance(q.object, ox.Literal):
            if q.predicate.value in display_names:
                labels.setdefault(q.subject, q.object.value)
        else:
            nodes.add(q.object)
            predicate = term_view(q.predicate)
            edges.append(dict(source=term_view(q.subject)["id"], target=term_view(q.object)["id"],
                              predicate=predicate, label=PREDICATE_LABELS.get(predicate["text"], predicate["text"])))
        if len(nodes) > 720 or len(edges) > 4800:
            return dict(sequence=sequence, too_large=True)
    rows = []
    for node in nodes:
        view = term_view(node)
        kind = node_group(node, types, scope)
        rows.append(dict(**view, label=labels.get(node, view["text"]), group=kind,
                         layer=business_layer(kind) if scope=="state" else "标准"))
    return dict(sequence=sequence, nodes=sorted(rows, key=lambda row:(row["group"],row["id"])), edges=edges)


def download_graph(graph, scope, head):
    selected = pinned_dataset(graph, scope, head)
    if not selected:
        return None
    store, graph_name, _ = selected
    return store.dump(format=ox.RdfFormat.TURTLE, from_graph=graph_name)

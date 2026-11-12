from app_v2.graph import OntologyStore, Ref
from app_v2.runtime import OntologyRuntime
from app_v2.specification import default_spec, validate_ontology
from app_v2.views import focused_graph


def test_route_view_and_shacl_detect_cross_product_link_and_bad_machine():
    graph=OntologyStore();runtime=OntologyRuntime.create(graph,default_spec())
    route=focused_graph(runtime,'P1',view='route')['route']
    assert route['linked'] and route['steps'][0]['next_operation']==route['steps'][1]['id']
    assert route['steps'][-1]['next_operation']==''
    assert validate_ontology(graph)['conforms']
    step=graph.get('P1/CUT');step.update(next_operation=Ref('P2/MILL'),preferred=Ref('CNC01'))
    graph.commit([step],'test_invalid_process')
    report=validate_ontology(graph)
    assert not report['conforms']
    assert any('工艺链' in r['message'] and r['object']=='P1/CUT' for r in report['issues'])
    assert any('首选设备' in r['message'] and r['object']=='P1/CUT' for r in report['issues'])

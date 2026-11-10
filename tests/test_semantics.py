import copy
import pytest
from rdflib import RDF
from pyshacl import validate
from app.contracts import ProductionSpec,ScenarioPatch,digest
from app.demo import demo_spec
from app.semantic import compile_input,validate_spec,validate_patch,spec_graph,node,OS,BASE,SemanticProjection
from app.native import samples_for,compare_states
from app.storage import Repository

def test_graph_compiler_preserves_contract_and_uses_capabilities():
    spec=demo_spec(); result=compile_input(spec)
    assert result['spec']==spec.model_dump()
    assert digest(result['spec'])==digest(spec.model_dump())
    assert digest(ProductionSpec.model_validate(spec.model_dump()).model_dump())==digest(spec.model_dump())
    assert result['candidates']['P1/OP2']==['CNC01']
    spec.resources[2].capabilities[0].tolerance_um=10
    assert compile_input(spec)['candidates']['P1/OP2']==['CNC01','CNC02']

def test_invalid_precision_preferred_and_started_assignments_rejected():
    spec=demo_spec()
    state={'jobs':{'J1':{'product':'P1','op':1,'status':'queued'}}}
    with pytest.raises(ValueError,match='CNC03'):
        validate_patch(spec,state,ScenarioPatch(name='invalid',assignments={'J1':'CNC03'}))
    state['jobs']['J1']['status']='processing'
    with pytest.raises(ValueError,match='禁止改派'):
        validate_patch(spec,state,ScenarioPatch(name='invalid',assignments={'J1':'CNC01'}))
    spec.products[0].operations[1].preferred='CNC03'
    assert not validate_spec(spec)['conforms']

def test_shacl_rejects_two_locations_for_one_piece():
    g=spec_graph(demo_spec());u=node('J1')
    for triple in [(u,RDF.type,OS.Workpiece),(u,OS.forOrder,node('WO001')),(u,OS.locatedAt,node('CNC01')),(u,OS.locatedAt,node('CNC02'))]:g.add(triple)
    conforms,_,_=validate(g,shacl_graph=str(BASE/'ontology/shapes.ttl'))
    assert not conforms

def test_samples_bound_to_job_operation_and_seed():
    spec=demo_spec();first=samples_for(spec)
    assert len(first)==480 and first==samples_for(spec)
    assert first['J0001/1']!=first['J0002/1']
    spec.seed+=1
    assert first!=samples_for(spec)

def test_snapshot_numeric_tolerance_and_queue_order():
    a={'clock':1.0,'queue':['J1','J2']}
    assert not compare_states(a,{'clock':1.0000001,'queue':['J1','J2']})
    assert compare_states(a,{'clock':1.0,'queue':['J2','J1']})

def test_duplicate_raw_events_scoped_to_project(tmp_path):
    repo=Repository(tmp_path/'test.sqlite')
    assert repo.record('A','MES','same',1,{'value':1})
    assert not repo.record('A','MES','same',1,{'value':1})
    with pytest.raises(ValueError,match='消息内容不一致'):
        repo.record('A','MES','same',1,{'value':2})
    assert repo.record('B','MES','same',1,{'value':1})
    assert len(repo.records('A'))==1
    repo.close()

def test_graph_projection_rebuild_drops_previous_project(tmp_path):
    graph=SemanticProjection(tmp_path/'graph')
    spec=demo_spec();graph.rebuild('first',spec)
    spec.orders[0].id='NEW_ORDER';graph.rebuild('second',spec,cursor=3)
    serialized=graph.export()
    assert 'NEW_ORDER' in serialized and 'WO001' not in serialized
    assert graph.meta['project_id']=='second' and graph.meta['cursor']==3

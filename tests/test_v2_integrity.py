import copy,io,json,zipfile
import pytest
from app_v2.graph import OntologyStore,Ref,AUDIT,PROV,iri
from app_v2.runtime import OntologyRuntime
from app_v2.specification import default_spec
from app_v2.bundles import make_bundle,verify_bundle
from app_v2.invariants import validate_boundary

def test_signed_prov_edges_and_input_versions():
    graph=OntologyStore();graph.commit([dict(id='rule',type='Policy',value=1)],'created')
    graph.commit([dict(id='rule',type='Policy',value=2)],'revised',used=['rule'])
    record=graph.records()[-1]['payload'];assert record['input_versions']['rule']=='rule/revision/000000001'
    assert graph.verify()['valid']
    graph.store.update(f'DELETE WHERE {{ GRAPH {AUDIT} {{ {iri("commit/000000002")} <{PROV}wasAssociatedWith> ?agent }} }}')
    assert not graph.verify()['valid']

def test_native_ack_graph_failure_is_not_retried(monkeypatch):
    graph=OntologyStore();r=OntologyRuntime.create(graph,default_spec());calls=[];commit=graph.commit
    def failed(changes,kind,**kwargs):
        if kind=='native_move':raise OSError('injected graph commit failure after COM acknowledgment')
        return commit(changes,kind,**kwargs)
    monkeypatch.setattr(graph,'commit',failed)
    with pytest.raises(OSError):r.command(None,'native_move',lambda:calls.append('moved'),[])
    assert calls==['moved'] and graph.entities('Command')[0]['status']=='prepared'
    with pytest.raises(ValueError,match='待核对'):r.assert_restorable()

def test_snapshot_invariants_and_forecast_pairing():
    graph=OntologyStore();r=OntologyRuntime.create(graph,default_spec());job=r.jobs()[0];op=r.operation(job)
    assert validate_boundary(graph)['conforms']
    sample=r.sample(job,op);other=copy.copy(job);other['assigned']='CNC03'
    assert r.sample(other,op)==sample
    other['attempt']=1;assert r.sample(other,op)!=sample
    clock=r.clock;clock['seed']=2606;graph.commit([clock],'truth_seed');assert r.sample(job,op)!=sample
    job['status']='processing';job['assigned']='CNC01';job['location']='CNC02'
    with pytest.raises(ValueError):validate_boundary(graph,[job])

def test_bundle_mutation_and_independent_anchor():
    graph=OntologyStore();graph.commit([dict(id='a',type='Policy',value=1)],'create')
    early=make_bundle(graph,graph.signer,{'restorable':True})
    graph.commit([dict(id='a',type='Policy',value=2)],'revise')
    report,_,_=verify_bundle(early,graph.signer.fingerprint,{'sequence':2,'hash':graph.head});assert not report['valid']
    current=make_bundle(graph,graph.signer,{'restorable':True})
    with zipfile.ZipFile(io.BytesIO(current)) as z:files={n:z.read(n) for n in z.namelist()}
    files['runtime.trig']+=b'\n# injected change'
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as z:
        for n,value in files.items():z.writestr(n,value)
    with pytest.raises(ValueError,match='摘要'):verify_bundle(out.getvalue())

def test_schema_tamper_is_detected():
    from app_v2.graph import SCHEMA
    graph=OntologyStore();OntologyRuntime.create(graph,default_spec());assert graph.verify()['schema_sealed']
    graph.store.update(f'INSERT DATA {{ GRAPH {SCHEMA} {{ {iri("Equipment")} <http://www.w3.org/2000/01/rdf-schema#label> "changed" }} }}')
    report=graph.verify();assert not report['valid'] and not report['schema_matches_contract']

import copy
import json
import pytest
from app_v2.graph import OntologyStore, STATE, iri, term, Ref
from app_v2.evidence import Signer, verify_records
from app_v2.specification import default_spec, spec_records, spec_from_graph, qualification, validate_ontology
from app_v2.runtime import OntologyRuntime

def test_spec_is_rdf_roundtrip():
    graph=OntologyStore();spec=default_spec();graph.commit(spec_records(spec),"import")
    assert spec_from_graph(graph)==spec
    assert graph.verify()["valid"]
    assert validate_ontology(graph)["conforms"]
    assert {r["resource"] for r in qualification(graph,"P2/MILL") if r["eligible"]}=={"CNC01","CNC02"}
    cap=graph.get("CNC02/cap/0");cap["tolerance_um"]=50.
    graph.commit([cap],"qualification_revision",actor="user/engineer",used=["CNC02/cap/0"])
    assert {r["resource"] for r in qualification(graph,"P2/MILL") if r["eligible"]}=={"CNC01"}

def test_trig_only_restore_and_tamper_detection(tmp_path):
    signer=Signer();g=OntologyStore(signer=signer);runtime=OntologyRuntime.create(g,default_spec())
    path=tmp_path/"runtime.trig";g.export(path)
    fresh=OntologyStore.restore(path,signer=signer)
    assert OntologyRuntime(fresh).summary()==runtime.summary()
    assert fresh.verify(signer.fingerprint,{"sequence":g.sequence,"hash":g.head})["valid"]
    fresh.store.update('DELETE WHERE {GRAPH %s {%s %s ?v}}; INSERT DATA {GRAPH %s {%s %s %s}}' % (STATE,iri("clock"),iri("time_s"),STATE,iri("clock"),iri("time_s"),term(500.)))
    assert not fresh.verify()["valid"]

def test_journal_reordering_truncation_and_signature():
    signer=Signer();g=OntologyStore(signer=signer)
    g.commit([dict(id="x",type="Policy",value=1)],"create")
    anchor={"sequence":g.sequence,"hash":g.head}
    g.commit([dict(id="x",type="Policy",value=2)],"revise")
    records=g.records()
    assert verify_records(records,signer.fingerprint,anchor)["valid"]
    assert not verify_records(list(reversed(records)),signer.fingerprint)["valid"]
    assert not verify_records(records[:1],signer.fingerprint,{"sequence":2,"hash":g.head})["valid"]
    bad=copy.deepcopy(records);bad[0]["payload"]["actor"]="user/forged"
    assert not verify_records(bad)["valid"]

def test_failed_transaction_and_revision_conflict():
    g=OntologyStore();g.commit([dict(id="x",type="Policy",value=1)],"create")
    with pytest.raises(ValueError):g.commit([dict(id="x",type="Policy",value=2)],"revise",expected=0)
    assert g.get("x")["value"]==1
    before=set(g.store)
    with pytest.raises(Exception):g.store.update('INSERT DATA {GRAPH %s {%s %s 99}}; LOAD <file:///__missing_ontologysim_test__>' % (STATE,iri("x"),iri("value")))
    assert set(g.store)==before

def test_no_shadow_state_and_material_plan():
    g=OntologyStore();r=OntologyRuntime.create(g,default_spec())
    assert len(r.jobs())==120
    assert len(g.entities("Batch"))==12
    assert r.__dict__=={"graph":g}
    assert all(isinstance(j["batch"],Ref) for j in r.jobs())
    assert len(r.pending_events())==120

"""Checks the actual native acceptance artifacts, never substitutes a simulator."""
import json
from pathlib import Path
import pytest
from app.demo import demo_spec
from app.semantic import validate_spec

@pytest.fixture(scope='module')
def results():
    root=Path('.runtime/kernel-acceptance')
    if not (root/'branch2/result.json').exists():pytest.skip('Run scripts.kernel_acceptance with Plant Simulation first')
    return [json.loads((root/f'branch{i}/result.json').read_text(encoding='utf-8')) for i in range(3)]

def test_native_maintenance_nonpreemption_and_qualification(results):
    spec=demo_spec();candidates=validate_spec(spec)['candidates']; products={p.id:p for p in spec.products}
    for result in results:
        assert result['native']['verified']
        assert result['native']['completed']==result['state']['completed']
        events=result['events'];active={};done_count={r.id:0 for r in spec.resources}
        starts={}
        for e in events:
            rid=e.get('resource')
            if e['kind']=='maintenance_started':
                assert rid not in starts, 'Maintenance preempted a running job'
                assert e['tool_count']==next(r.tool_limit for r in spec.resources if r.id==rid)
                active[rid]=e['ends']
            elif e['kind']=='started':
                assert e['time']>=active.get(rid,0)-1e-6
                assert rid not in starts, 'Overlapping occupation'
                starts[rid]=e
                job=result['state']['jobs'][e['job']]
                op=products[job['product']].operations[e['op']]
                assert rid in candidates[f"{job['product']}/{op.id}"]
            elif e['kind']=='completed_operation':
                start=starts.pop(rid)
                assert e['job']==start['job'] and abs(e['time']-start['time']-start['sample_s'])<1e-6

def test_common_window_and_paired_samples(results):
    assert len({r['snapshot_id'] for r in results})==1
    assert len({r['kpi']['window_start_s'] for r in results})==1
    assert len({r['kpi']['window_end_s'] for r in results})==1
    samples={}
    for result in results:
        for e in result['events']:
            if e['kind']=='started':
                key=(e['job'],e['op'])
                assert samples.setdefault(key,e['sample_s'])==e['sample_s']

"""The authoritative RDF state, with an atomic signed delta journal.

Records returned by this API are disposable read views. Mutations only become
state through commit(); no independent Python simulation state is maintained.
"""
from __future__ import annotations
import json
import threading
from pathlib import Path
from urllib.parse import quote, unquote
import pyoxigraph as ox
from .common import NS, canonical, digest, now, atomic_write
from .evidence import Signer, verify_records

RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
PROV = "http://www.w3.org/ns/prov#"
STATE = ox.NamedNode(NS + "graph/state")
AUDIT = ox.NamedNode(NS + "graph/evidence")
SCHEMA = ox.NamedNode(NS + "graph/schema")

class Ref(str):
    pass

def iri(value):
    return ox.NamedNode(NS + quote(str(value), safe="/_-."))

def term(value):
    if isinstance(value, Ref):
        return iri(value)
    return ox.Literal(value)

def decode(value):
    if isinstance(value, ox.NamedNode):
        return Ref(unquote(value.value.removeprefix(NS)))
    t = value.datatype.value.rsplit("#", 1)[-1]
    if t == "boolean": return value.value == "true"
    if t in ("integer", "int", "long"): return int(value.value)
    if t in ("double", "float", "decimal"): return float(value.value)
    return value.value

def encode_record(record):
    return {k: ([{"ref": str(x)} if isinstance(x, Ref) else x for x in v] if isinstance(v, list)
                else {"ref": str(v)} if isinstance(v, Ref) else v) for k, v in record.items()}

def decode_record(record):
    def item(x): return Ref(x["ref"]) if isinstance(x, dict) and set(x) == {"ref"} else x
    return {k: [item(x) for x in v] if isinstance(v, list) else item(v) for k, v in record.items()}

class OntologyStore:
    def __init__(self, path=None, signer=None):
        self.store = ox.Store(str(path)) if path else ox.Store()
        self.lock = threading.RLock()
        self.signer = signer or Signer()
        self.defer_non_intent_flush = False
        self.run_journal = None
        self.sequence = 0
        self.head = "0" * 64
        self._refresh_journal()

    def _refresh_journal(self):
        self._latest_versions = {}
        rows = list(self.store.quads_for_pattern(None, iri("sequence"), None, AUDIT))
        if rows:
            latest = max(rows, key=lambda q: int(q.object.value))
            self.sequence = int(latest.object.value)
            self.head = next(self.store.quads_for_pattern(latest.subject, iri("hash"), None, AUDIT)).object.value

    def schema(self, path):
        self.store.load(path=str(path), format=ox.RdfFormat.TURTLE, to_graph=SCHEMA)
        # Finite TBox closure is isolated from asserted operational records.
        from rdflib import Graph, Literal
        from owlrl import DeductiveClosure, OWLRL_Semantics
        schema=Graph().parse(path,format="turtle")
        DeductiveClosure(OWLRL_Semantics,axiomatic_triples=False,datatype_axioms=False).expand(schema)
        for triple in list(schema):
            if isinstance(triple[0],Literal):schema.remove(triple)
        self.store.load(input=schema.serialize(format="turtle"),format=ox.RdfFormat.TURTLE,to_graph=ox.NamedNode(NS+"graph/inference"))

    def schema_digest(self):
        from rdflib import Graph
        from rdflib.compare import to_canonical_graph
        contents={}
        for name in ('schema','inference'):
            graph=Graph().parse(data=self.store.dump(format=ox.RdfFormat.TURTLE,from_graph=ox.NamedNode(NS+'graph/'+name)),format='turtle')
            contents[name]=sorted(to_canonical_graph(graph).serialize(format='nt').splitlines())
        return digest(contents)

    def get(self, identity):
        result = {"id": str(identity)}
        list_keys = []
        for q in self.store.quads_for_pattern(iri(identity), None, None, STATE):
            key = unquote(q.predicate.value.removeprefix(NS))
            if q.predicate.value == RDF + "type": key = "type"
            if key.startswith("list_"):
                list_keys.append(key[5:]); continue
            value = decode(q.object)
            if key == "type": value = str(value)
            if key in result:
                if not isinstance(result[key], list): result[key] = [result[key]]
                result[key].append(value)
            else: result[key] = value
        for key in list_keys:
            if key not in result: result[key] = []
            elif not isinstance(result[key], list): result[key] = [result[key]]
        if result.get("type")=="Execution" and "operation_plan" in result:result["operation"]=result.pop("operation_plan")
        return result if len(result) > 1 else None

    def entities(self, kind=None):
        if kind:
            ids = {decode(q.subject) for q in self.store.quads_for_pattern(None, ox.NamedNode(RDF+"type"), iri(kind), STATE)}
        else:
            ids = {decode(q.subject) for q in self.store.quads_for_pattern(None, None, None, STATE)}
        return [self.get(i) for i in sorted(ids)]

    def find(self, kind, **filters):
        # Start with the indexed status for hot queries (pending events, queued jobs).
        status = filters.get('status')
        assigned = filters.get('assigned')
        if isinstance(assigned, Ref):
            ids = (q.subject for q in self.store.quads_for_pattern(None, iri('assigned'), iri(assigned), STATE)
                   if status is None or next(self.store.quads_for_pattern(q.subject, iri('status'), term(status), STATE),None))
            rows = (self.get(decode(s)) for s in sorted(set(ids))
                    if next(self.store.quads_for_pattern(s, ox.NamedNode(RDF+'type'), iri(kind), STATE),None))
        elif isinstance(status, str) and not isinstance(status, Ref):
            ids = (q.subject for q in self.store.quads_for_pattern(None, iri('status'), term(status), STATE))
            rows = (self.get(decode(s)) for s in sorted(set(ids), key=lambda s:s.value)
                    if next(self.store.quads_for_pattern(s, ox.NamedNode(RDF+'type'), iri(kind), STATE),None))
        else:
            rows = self.entities(kind)
        return [r for r in rows if r and all(r.get(k) == v for k, v in filters.items())]

    @staticmethod
    def _triples(record):
        subject = iri(record["id"])
        for key, values in record.items():
            if key == "id" or values is None: continue
            if isinstance(values, list):
                yield f"{subject} {iri('list_'+key)} {term(True)} ."
            values = values if isinstance(values, list) else [values]
            for value in values:
                predicate = ox.NamedNode(RDF+"type") if key == "type" else iri("operation_plan" if key=="operation" and record["type"]=="Execution" else key)
                obj = iri(value) if key == "type" else term(value)
                yield f"{subject} {predicate} {obj} ."

    def commit(self, changes, kind, *, actor="service/runtime", time_s=0.0, used=(), reason="", expected=None, details=None):
        """Replace only named current records; journal and old values are immutable."""
        with self.lock:
            if self.run_journal and self.run_journal.failed:
                raise OSError("仿真日志已中断，需要核对")
            if expected is not None and expected != self.sequence:
                raise ValueError("版本已变化，请重新读取后提交")
            records = list(changes)
            if len({r["id"] for r in records}) != len(records):
                raise ValueError("一次提交不得重复更新同一对象")
            existing_rows={r['id']: self.get(r['id']) for r in records}
            for record in records:
                if "type" not in record: raise ValueError("记录必须具有本体类型")
                existing=existing_rows[record['id']]
                if existing and existing["type"] in {"SourceRecord","Inspection","Checkpoint","BusinessRevision","RunResult","Mapping","SourceGeneration","SourceValidation","OntologyVersion"}:
                    raise ValueError("已提交的证据实体不可覆盖，请创建修订版本")
            sequence = self.sequence + 1
            def latest_version(identity):
                if identity in self._latest_versions:return self._latest_versions[identity]
                versions=[q.subject for target in (iri(identity),iri('record/'+str(identity))) for q in self.store.quads_for_pattern(None,ox.NamedNode(PROV+"specializationOf"),target,AUDIT)]
                value=max(versions,key=lambda n:int(n.value.rsplit('/',1)[-1])) if versions else iri(identity)
                self._latest_versions[identity]=value
                return value
            input_versions={str(identity):str(decode(latest_version(identity))) for identity in used}
            payload = {"sequence": sequence, "previous": self.head, "kind": kind, "actor": actor,
                       "occurred_at": now(), "simulation_time_s": float(time_s), "used": list(used),
                       "input_versions": input_versions,"software_version":"0.2.0",
                       "reason": reason, "details": details or {},
                       "changes": [{"before": encode_record(existing_rows[r["id"]] or {}), "after": encode_record(r)} for r in records]}
            event = iri(f"commit/{sequence:09}")
            generated = iri(f"revision/{sequence:09}")
            actor_node = iri(actor)
            audit = [f"{event} <{RDF}type> <{PROV}Activity> .",
                     f"{event} {iri('sequence')} {term(sequence)} .",
                     f"{event} {iri('publicKey')} {term(self.signer.public)} .",
                     f"{event} <{PROV}wasAssociatedWith> {actor_node} .",
                     f"{actor_node} <{RDF}type> <{PROV}{'Person' if actor.startswith('user/') else 'SoftwareAgent'}> .",
                     f"{generated} <{RDF}type> <{PROV}Entity> .",
                     f"{generated} <{PROV}wasGeneratedBy> {event} ."]
            if self.sequence:
                audit.append(f"{generated} <{PROV}wasRevisionOf> {iri(f'revision/{self.sequence:09}')} .")
            for identity in used:
                audit.append(f"{event} <{PROV}used> {iri(input_versions[str(identity)])} .")
                usage=iri(f"usage/{sequence:09}/{len(audit)}")
                audit += [f"{event} <{PROV}qualifiedUsage> {usage} .",f"{usage} <{RDF}type> <{PROV}Usage> .",f"{usage} <{PROV}entity> {iri(input_versions[str(identity)])} ."]
            association=iri(f"association/{sequence:09}")
            audit += [f"{event} <{PROV}qualifiedAssociation> {association} .",
                      f"{association} <{RDF}type> <{PROV}Association> .",
                      f"{association} <{PROV}agent> {actor_node} .",
                      f"{association} <{PROV}hadRole> {iri('role/'+('operator' if actor.startswith('user/') else 'executor'))} .",
                      f"{event} <{PROV}startedAtTime> {ox.Literal(payload['occurred_at'],datatype=ox.NamedNode('http://www.w3.org/2001/XMLSchema#dateTime'))} .",
                      f"{event} <{PROV}endedAtTime> {ox.Literal(payload['occurred_at'],datatype=ox.NamedNode('http://www.w3.org/2001/XMLSchema#dateTime'))} ."]
            if self.get("policy"):audit.append(f"{association} <{PROV}hadPlan> {latest_version('policy')} .")
            for record in records:
                version=iri(f"{record['id']}/revision/{sequence:09}")
                description=iri('record/'+record['id'])
                audit += [f"{version} <{RDF}type> <{PROV}Entity> .",f"{version} <{PROV}wasGeneratedBy> {event} .",
                          f"{version} <{PROV}specializationOf> {description} .",f"{description} <{RDF}type> <{PROV}Entity> .",
                          f"{description} {iri('describes')} {iri(record['id'])} .",f"{generated} <{PROV}hadMember> {version} ."]
                if existing_rows[record['id']]:audit.append(f"{version} <{PROV}wasRevisionOf> {latest_version(record['id'])} .")
                for identity in used:audit.append(f"{version} <{PROV}wasDerivedFrom> {iri(input_versions[str(identity)])} .")
            payload['provenance']=[triple for triple in audit if str(iri('sequence')) not in triple and str(iri('publicKey')) not in triple]
            checksum=digest(payload)
            signature = self.signer.sign(payload)
            audit += [f"{event} {iri('hash')} {term(checksum)} .",f"{event} {iri('payload')} {term(canonical(payload))} .",f"{event} {iri('signature')} {term(signature)} ."]
            inserts = "\n".join(t for r in records for t in self._triples(r))
            deletes = "\n".join(f"DELETE WHERE {{ GRAPH {STATE} {{ {iri(r['id'])} ?p ?o }} }};" for r in records)
            update = deletes + f" INSERT DATA {{ GRAPH {STATE} {{ {inserts} }} GRAPH {AUDIT} {{ {' '.join(audit)} }} }}"
            self.store.update(update)
            # Force intent evidence to disk before the native side effect. Private
            # branches sync the sequential signed log instead of the RDF indexes.
            try:
                durable_intent = kind in ('command_prepared','command_unknown')
                if self.run_journal:
                    self.run_journal.append({"payload":payload,"hash":checksum,"signature":signature,"public_key":self.signer.public},
                                            sync=durable_intent)
                elif not self.defer_non_intent_flush or durable_intent:
                    self.store.flush()
            except Exception:
                if self.run_journal:self.run_journal.failed=True
                self._refresh_journal()
                raise
            self.sequence = sequence; self.head = checksum
            self._latest_versions.update({r['id']:iri(f"{r['id']}/revision/{sequence:09}") for r in records})
            return {"sequence": sequence, "hash": checksum, "activity": decode(event)}

    def records(self, after=0, limit=None, through=None):
        result = []
        end=min(self.sequence,through) if through is not None else self.sequence
        first=max(after+1,end-limit+1 if limit else 1)
        for sequence in range(first,end+1):
            q=next(self.store.quads_for_pattern(iri(f"commit/{sequence:09}"),iri("payload"),None,AUDIT),None)
            if q is None:continue
            payload = json.loads(q.object.value)
            if payload["sequence"] <= after: continue
            def val(key): return next(self.store.quads_for_pattern(q.subject, iri(key), None, AUDIT)).object.value
            result.append({"payload": payload, "hash": val("hash"), "signature": val("signature"), "public_key": val("publicKey")})
        result.sort(key=lambda r: r["payload"]["sequence"])
        return result[-limit:] if limit else result

    def verify(self, fingerprint=None, anchor=None):
        records = self.records()
        report = verify_records(records, fingerprint, anchor)
        expected = {}
        for record in records:
            for change in record["payload"]["changes"]:
                expected[change["after"]["id"]] = change["after"]
        actual = {r["id"]: encode_record(r) for r in self.entities()}
        # RDF has set semantics, so normalize multi-valued properties for comparison.
        def normal(d):
            return {i: {k: sorted(v, key=canonical) if isinstance(v, list) else v for k,v in r.items()} for i,r in d.items()}
        report["state_matches_journal"] = normal(expected) == normal(actual)
        report["valid"] &= report["state_matches_journal"]
        contract=self.get('ontology_contract')
        report['schema_sealed']=bool(contract)
        report['schema_matches_contract']=not contract or contract['schema_hash']==self.schema_digest()
        report['valid'] &= report['schema_matches_contract']
        expected_prov=ox.Store();sealed=[r for r in records if 'provenance' in r['payload']]
        for r in sealed:expected_prov.load(input='\n'.join(r['payload']['provenance']),format=ox.RdfFormat.N_TRIPLES,to_graph=AUDIT)
        metadata={iri(k) for k in ('hash','signature','publicKey','payload','sequence')}
        actual_prov={q for q in self.store.quads_for_pattern(None,None,None,AUDIT) if q.predicate not in metadata}
        expected_quads=set(expected_prov)
        if len(sealed)==len(records):provenance_ok=actual_prov==expected_quads
        else:
            subjects={q.subject for q in expected_quads};provenance_ok={q for q in actual_prov if q.subject in subjects}==expected_quads
        report.update(provenance_matches_signed_content=provenance_ok,provenance_sealed_commits=len(sealed),provenance_total_commits=len(records))
        report['valid'] &= provenance_ok
        return report

    def export(self, path=None):
        if self.run_journal and not self.run_journal.failed:self.run_journal.flush()
        self.store.flush()
        data = self.store.dump(format=ox.RdfFormat.TRIG)
        if path: atomic_write(path, data)
        return data

    @classmethod
    def restore(cls, path, *, signer=None, destination=None):
        result = cls(destination, signer)
        if len(result.store): raise ValueError("恢复目标必须为空")
        result.store.load(path=str(path), format=ox.RdfFormat.TRIG)
        result._refresh_journal()
        report = result.verify()
        if not report["valid"]: raise ValueError("本体历史或状态完整性校验失败")
        return result

    def view_at(self, sequence):
        result = {}
        for event in self.records(through=sequence):
            if event["payload"]["sequence"] > sequence: break
            for change in event["payload"]["changes"]:
                row = decode_record(change["after"]); result[row["id"]] = row
        return list(result.values())

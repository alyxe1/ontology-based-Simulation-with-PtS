import hashlib
import io
import json
import zipfile
from .common import canonical
from .evidence import check_signature, verify_records
from .graph import OntologyStore

def make_bundle(graph,signer,metadata,artifacts=None):
    files={"runtime.trig":graph.export(),"evidence.json":canonical(graph.records()).encode(),"receipt.json":canonical({"sequence":graph.sequence,"hash":graph.head,"fingerprint":signer.fingerprint}).encode()}
    files.update(artifacts or {})
    manifest={"version":"2.0","metadata":metadata,"public_key":signer.public,"fingerprint":signer.fingerprint,"files":{name:hashlib.sha256(data).hexdigest() for name,data in files.items()}}
    files["manifest.json"]=canonical({"manifest":manifest,"signature":signer.sign(manifest)}).encode()
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,"w",zipfile.ZIP_DEFLATED) as archive:
        for name,data in files.items():archive.writestr(name,data)
    return stream.getvalue()

def verify_bundle(data,fingerprint=None,anchor=None):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names=set(archive.namelist());required={"runtime.trig","evidence.json","receipt.json","manifest.json"}
        if len(names)!=len(archive.namelist()) or not required<=names or names-required-{'native.spp'}:raise ValueError("存档文件列表无效")
        if sum(i.file_size for i in archive.infolist())>250_000_000:raise ValueError("存档解压体积超过上限")
        signed=json.loads(archive.read("manifest.json"));manifest=signed["manifest"]
        if set(manifest['files'])!=names-{'manifest.json'}:raise ValueError('签名清单未覆盖全部文件')
        check_signature(manifest,signed["signature"],manifest["public_key"])
        if fingerprint and manifest["fingerprint"]!=fingerprint:raise ValueError("公钥指纹与独立保管值不符")
        for name,checksum in manifest["files"].items():
            if hashlib.sha256(archive.read(name)).hexdigest()!=checksum:raise ValueError("存档内容摘要不匹配: "+name)
        records=json.loads(archive.read("evidence.json"));report=verify_records(records,fingerprint,anchor)
        graph=OntologyStore();import pyoxigraph as ox
        graph.store.load(input=archive.read("runtime.trig"),format=ox.RdfFormat.TRIG);graph._refresh_journal()
        report["ontology"]=graph.verify(fingerprint,anchor)
        report["valid"] &= report["ontology"]["valid"] and graph.records()==records
        return report,archive.read("runtime.trig"),manifest

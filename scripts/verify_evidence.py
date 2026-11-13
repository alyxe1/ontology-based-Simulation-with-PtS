"""Offline verifier; never opens COM and does not need the private signing key."""
import argparse,json
from pathlib import Path
from zipfile import ZipFile
from app_v2.bundles import verify_bundle
from app_v2.cases import verify_case_bundle

def main():
    parser=argparse.ArgumentParser(description='Verify an OntologySim V0.2 evidence bundle offline')
    parser.add_argument('bundle',type=Path);parser.add_argument('--fingerprint');parser.add_argument('--receipt',type=Path)
    args=parser.parse_args();anchor=json.loads(args.receipt.read_text(encoding='utf-8')) if args.receipt else None
    with ZipFile(args.bundle) as bundle:
        kind=json.loads(bundle.read("manifest.json"))["manifest"].get("kind")
    if kind=="what-if-case":
        with args.bundle.open("rb") as content:
            report=verify_case_bundle(content,args.fingerprint,anchor)
    else:
        report,_,_=verify_bundle(args.bundle.read_bytes(),args.fingerprint,anchor)
    print(json.dumps(report,ensure_ascii=False,indent=2));raise SystemExit(0 if report['valid'] else 2)

if __name__=='__main__':main()

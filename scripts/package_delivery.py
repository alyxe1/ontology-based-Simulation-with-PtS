"""Package source, built offline frontend, validated models and documentation."""
import hashlib
import json
import zipfile
from pathlib import Path

def main():
    root=Path(__file__).resolve().parents[1];target=root/'delivery';target.mkdir(exist_ok=True)
    selected=[]
    for folder in ['app','ontology','scripts','tests','docs','artifacts/acceptance','artifacts/demo','artifacts/screenshots','frontend/src','frontend/dist']:
        selected.extend(p for p in (root/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc')
    for name in ['README.md','start.cmd','stop.cmd','install.cmd','requirements.txt','requirements.lock.txt','environment.yml','conda-win64.lock.txt','pytest.ini','frontend/package.json','frontend/package-lock.json','frontend/tsconfig.json','frontend/vite.config.ts','frontend/index.html']:
        selected.append(root/name)
    manifest={str(p.relative_to(root)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest() for p in selected}
    (target/'SHA256.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    with zipfile.ZipFile(target/'OntologySim-PoC.zip','w',zipfile.ZIP_DEFLATED) as archive:
        for path in selected:archive.write(path,path.relative_to(root))
        archive.write(target/'SHA256.json','SHA256.json')
    print(f'Packaged {len(selected)} files; {(target/"OntologySim-PoC.zip").stat().st_size} bytes')

if __name__=='__main__':main()

"""Package V0.2 source, offline frontend and public acceptance artifacts only."""
import hashlib,zipfile,subprocess
from app_v2.common import BASE,canonical

def main():
    target=BASE/'delivery';target.mkdir(exist_ok=True);selected=[]
    for folder in ('app','app_v2','ontology','scripts','tests','docs','artifacts/v02','frontend/src','frontend/dist'):
        selected.extend(p for p in (BASE/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc')
    for name in ('README.md','start.cmd','stop.cmd','backup.cmd','install.cmd','requirements.txt','requirements.lock.txt','environment.yml','conda-win64.lock.txt','pytest.ini','frontend/package.json','frontend/package-lock.json','frontend/tsconfig.json','frontend/vite.config.ts','frontend/index.html','frontend/v2-ui-acceptance.cjs'):
        path=BASE/name
        if path.exists():selected.append(path)
    selected=sorted(set(selected));manifest={p.relative_to(BASE).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in selected}
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=BASE,text=True).strip();destination=target/'OntologySim-V0.2.zip'
    with zipfile.ZipFile(destination,'w',zipfile.ZIP_DEFLATED) as bundle:
        for path in selected:bundle.write(path,path.relative_to(BASE))
        bundle.writestr('SHA256.json',canonical(manifest));bundle.writestr('RELEASE.json',canonical({'version':'0.2.0','commit':commit,'contains_private_runtime':False}))
    with zipfile.ZipFile(destination) as bundle:
        assert bundle.testzip() is None
        for name,expected in manifest.items():assert hashlib.sha256(bundle.read(name)).hexdigest()==expected
    print(canonical({'path':str(destination),'files':len(selected),'bytes':destination.stat().st_size,'sha256':hashlib.sha256(destination.read_bytes()).hexdigest(),'verified':True}))

if __name__=='__main__':main()

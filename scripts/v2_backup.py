"""Consistent stopped-workspace backup. No signing keys or accounts leave the local host."""
import argparse,hashlib,json,zipfile
from pathlib import Path
from app_v2.common import BASE,DATA,canonical,now
def main():
    p=argparse.ArgumentParser();p.add_argument('output',type=Path,nargs='?');args=p.parse_args()
    if (DATA/'server.json').exists():raise SystemExit('请先运行 stop.cmd，待服务退出后再备份。')
    root=DATA.resolve();target=(args.output or BASE/'delivery'/('workspace-backup-'+now().replace(':','').replace('+','_')+'.zip')).resolve()
    if target.exists():raise SystemExit('输出文件已存在，请选择新的备份文件名。')
    if target==root or root in target.parents:raise SystemExit('备份输出路径必须位于数据目录之外。')
    target.parent.mkdir(parents=True,exist_ok=True);manifest={}
    with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as z:
        for file in root.rglob('*'):
            if file.is_file():
                name=file.relative_to(root).as_posix();data=file.read_bytes();z.writestr(name,data);manifest[name]=hashlib.sha256(data).hexdigest()
        z.writestr('backup-manifest.json',canonical({'created':now(),'files':manifest,'key_scope':'DPAPI requires the original Windows user profile'}))
    print(f'本机备份已保存：{target}（{len(manifest)} 个文件）')
if __name__=='__main__':main()

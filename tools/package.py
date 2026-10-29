"""Deterministic allowlist package. Evidence and source locks stay outside ZIP."""
from pathlib import Path
import argparse
import ast
import hashlib
import json
import subprocess
import zipfile

ROOT=Path(__file__).resolve().parents[1]
FIXED=('plugin.py','_manifest.json','README.md','UPDATE_NOTES.md','LICENSE','requirements.txt','config.example.toml','_locales/zh-CN.json')


def allowed():
    names=set(FIXED)
    names.update(p.relative_to(ROOT).as_posix() for p in (ROOT/'maihush').rglob('*.py'))
    return sorted(names)


def verify_source():
    manifest=json.loads((ROOT/'_manifest.json').read_text(encoding='utf8'))
    assert manifest['capabilities']==['send.text','message.get_by_id','llm.generate','llm.get_available_models']
    assert manifest['host_application']=={'min_version':'1.3.12','max_version':'1.3.18'}
    assert manifest['sdk']=={'min_version':'2.15.0','max_version':'2.19.0'}
    for name in allowed():
        p=ROOT/name
        assert p.is_file() and not p.is_symlink() and p.resolve().is_relative_to(ROOT),name
        data=p.read_bytes()
        assert b'PRIVATE KEY' not in data and b'sk-proj-' not in data,name
        if p.suffix=='.py':
            tree=ast.parse(data)
            for node in ast.walk(tree):
                if isinstance(node,ast.Import): assert all(not a.name.startswith('src.') for a in node.names),name
                if isinstance(node,ast.ImportFrom): assert not (node.module or '').startswith('src.'),name
                if isinstance(node,ast.Attribute): assert node.attr!='set_adjust',name
    # Audit tracked content, not just ignored/unpacked files.
    result=subprocess.run(['git','-c','safe.directory='+ROOT.as_posix(),'-C',str(ROOT),'ls-files','-z'],capture_output=True)
    if (ROOT/'.git').exists(): assert result.returncode==0,result.stderr
    if result.returncode==0:
        for name in result.stdout.decode().split('\0'):
            if not name: continue
            path=Path(name)
            assert not (path.suffix=='.md' and name not in ('README.md','UPDATE_NOTES.md')),name
            assert name!='config.toml' and not any(x in path.parts for x in ('.integration','.venv','evidence','docs')),name
            assert path.suffix not in ('.db','.sqlite3','.wal','.pyc','.zip'),name
            assert not path.name.startswith('MaiHush_'),name


def build(target):
    verify_source()
    target=Path(target).absolute()
    if target.exists(): raise ValueError('Preserve existing artifact; choose a new target')
    target.parent.mkdir(parents=True,exist_ok=True)
    names=allowed()
    # Export the reviewed Git index, checking every selected blob against the
    # worktree. No ignored material can be picked up by a directory ZIP.
    blobs={}
    for name in names:
        result=subprocess.run(['git','-c','safe.directory='+ROOT.as_posix(),'-C',str(ROOT),'show',':'+name],capture_output=True)
        assert result.returncode==0,result.stderr
        assert result.stdout==(ROOT/name).read_bytes(),'unstaged package input: '+name
        blobs[name]=result.stdout
    with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        for name in names:
            info=zipfile.ZipInfo('maihush/'+name,date_time=(2026,10,22,0,0,0))
            info.compress_type=zipfile.ZIP_DEFLATED; info.external_attr=0o100644<<16
            archive.writestr(info,blobs[name])
    with zipfile.ZipFile(target) as archive:
        assert archive.namelist()==['maihush/'+n for n in names]
        for name in names: assert archive.read('maihush/'+name)==(ROOT/name).read_bytes()
        assert archive.testzip() is None
    return {'path':str(target),'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),
            'bytes':target.stat().st_size,'files':{n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in names},
            'allowlist_scan':True,'runtime_only':True,'source':'clean Git index export'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--output',type=Path,required=True); parser.add_argument('--evidence',type=Path,required=True); args=parser.parse_args()
    if args.evidence.resolve().is_relative_to(ROOT): parser.error('evidence must be outside repository')
    report=build(args.output); args.evidence.write_text(json.dumps(report,indent=2),encoding='utf8')
    print(json.dumps({k:v for k,v in report.items() if k!='files'},indent=2))

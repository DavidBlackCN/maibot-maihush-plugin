"""Stage synthetic instances and run the unchanged pinned runtime.

Usage: python tools/run_isolated.py --evidence-dir ABS --host-source ABS --layer T1|T2
All generated material goes to the explicit external evidence directory.
"""
from pathlib import Path
from datetime import datetime, timezone
import argparse
import hashlib
import importlib.metadata
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import zipfile
import tomli_w

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
ID='davidblackcn.maibot-maihush-plugin'

def digest(p): return hashlib.sha256(p.read_bytes()).hexdigest()

def verify(host,source_lock):
    lock=json.loads(source_lock.read_text(encoding='utf8'))
    import tomllib
    assert tomllib.loads((host/'pyproject.toml').read_text(encoding='utf8'))['project']['version']==lock['host_version']
    for rel,expected in lock['host_files'].items(): assert digest(host/rel)==expected,rel
    import maibot_sdk
    site=Path(maibot_sdk.__file__).parent.parent
    assert importlib.metadata.version('maibot-plugin-sdk')==lock['sdk_version']
    for rel,expected in lock['sdk_files'].items(): assert digest(site/rel)==expected,rel
    return lock

def stage(work,neighbors=False,package=None):
    candidate=work/'plugins/candidate'; candidate.mkdir(parents=True)
    if package:
        from tools.package import allowed
        with zipfile.ZipFile(package) as archive:
            assert set(archive.namelist())=={'maihush/'+n for n in allowed()}
            for name in allowed():
                data=archive.read('maihush/'+name)
                assert data==(ROOT/name).read_bytes(),name
                target=candidate/name; target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes(data)
    else:
        for name in ('plugin.py','_manifest.json'): shutil.copyfile(ROOT/name,candidate/name)
        shutil.copytree(ROOT/'maihush',candidate/'maihush',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        (candidate/'_locales').mkdir(); (candidate/'_locales/zh-CN.json').write_text('{}')
    cfg={'plugin':{'config_version':'1','enabled':True},'routes':[{'enabled':True,'admin_accounts':['10001'],'owner_accounts':['10002']}]}
    (candidate/'config.toml').write_text(tomli_w.dumps(cfg),encoding='utf8')
    consumer=work/'plugins/consumer'; consumer.mkdir()
    manifest=json.loads((ROOT/'_manifest.json').read_text()); manifest.update(id='maihush.test.consumer',name='Synthetic test consumer',capabilities=['api.call','send.text'])
    (consumer/'_manifest.json').write_text(json.dumps(manifest),encoding='utf8')
    shutil.copyfile(ROOT/'tools/test_consumer.py',consumer/'plugin.py')
    if neighbors:
        hashes={}
        for folder,package in [('maibot-maivita-plugin','maivita'),('maibot-maiIdentity-plugin','maiidentity')]:
            ref=ROOT.parent/folder; target=work/'neighbors'/folder; target.mkdir(parents=True)
            # Read-only source staging. Do not copy user configs, databases or environments.
            for name in ('plugin.py','_manifest.json','LICENSE'):
                shutil.copyfile(ref/name,target/name); hashes[folder+'/'+name]=digest(ref/name)
            for src in (ref/package).rglob('*'):
                if src.is_file() and src.suffix in ('.py','.sql','.json') and '__pycache__' not in src.parts:
                    rel=src.relative_to(ref); dest=target/rel; dest.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(src,dest)
                    hashes[folder+'/'+rel.as_posix()]=digest(src)
            if package=='maivita':
                cfg={'plugin':{'enabled':False,'config_version':'3'}}
            else:
                cfg={'plugin':{'enabled':False,'config_version':'3'}}
            (target/'config.toml').write_text(tomli_w.dumps(cfg),encoding='utf8')
        (work/'neighbor-source-hashes.json').write_text(json.dumps(hashes,indent=2),encoding='utf8')
    return candidate

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--evidence-dir',type=Path,required=True); ap.add_argument('--host-source',type=Path,required=True); ap.add_argument('--layer',choices=['T1','T2'],required=True); ap.add_argument('--package',type=Path); ap.add_argument('--source-lock',type=Path,default=ROOT/'tests/fixtures/source-lock.json'); args=ap.parse_args()
    host=args.host_source.resolve(); lock=verify(host,args.source_lock)
    work=args.evidence_dir.resolve()/args.layer
    if work.exists(): raise SystemExit('Choose a new evidence directory; existing evidence is preserved')
    work.mkdir(parents=True); stage(work,neighbors=args.layer=='T2',package=args.package); shutil.copyfile(host/'pyproject.toml',work/'pyproject.toml')
    safety=work/'safety'; safety.mkdir()
    (safety/'sitecustomize.py').write_text("import os,sys\ndef audit(event,args):\n    if event=='socket.connect':\n        a=args[1]\n        if isinstance(a,str) and a==os.environ.get('MAIHUSH_TEST_SOCKET_PATH'): return\n        if not isinstance(a,tuple) or a[0] not in ('127.0.0.1','::1'): raise PermissionError('MAIHUSH_TEST_EXTERNAL_NETWORK_DENIED')\nsys.addaudithook(audit)\n",encoding='utf8')
    env={k:v for k,v in os.environ.items() if k.upper() in ('SYSTEMROOT','WINDIR','COMSPEC','PATHEXT','NUMBER_OF_PROCESSORS')}
    env.update(PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1',PYTHONUNBUFFERED='1',PYTHONPATH=os.pathsep.join((str(safety),str(ROOT),str(host))),MAIBOT_INSTANCE_DIR=str(work),TEMP=str(work),TMP=str(work),USERPROFILE=str(work),MAIHUSH_TEST_LAYER=args.layer,MAIBOT_HOST_VERSION=lock["host_version"])
    socket_dir=tempfile.TemporaryDirectory(prefix='mh-test-') if os.name!='nt' else None
    if socket_dir: env['MAIHUSH_TEST_SOCKET_PATH']=str(Path(socket_dir.name)/'rpc.sock')
    started=datetime.now(timezone.utc).isoformat()
    runtime_files=[ROOT/'plugin.py',ROOT/'_manifest.json',*(ROOT/'maihush').rglob('*.py')]
    fingerprint={p.relative_to(ROOT).as_posix():digest(p) for p in runtime_files}
    process=subprocess.Popen([sys.executable,'-m','tools.native_scenario'],cwd=work,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0,start_new_session=os.name!='nt')
    try: output,_=process.communicate(timeout=150)
    except BaseException:
        if process.poll() is None:
            if os.name=='nt':
                subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True)
            else:
                os.killpg(process.pid,signal.SIGKILL)
            process.wait()
        raise
    finally:
        if socket_dir: socket_dir.cleanup()
    (work/'console.log').write_bytes(output)
    verify(host,args.source_lock)
    assert fingerprint=={p.relative_to(ROOT).as_posix():digest(p) for p in runtime_files}
    metadata={'started_at':started,'finished_at':datetime.now(timezone.utc).isoformat(),
              'command':[sys.executable,*sys.argv],'exit_code':process.returncode,
              'python':sys.version,'host_version':lock['host_version'],'sdk_version':lock['sdk_version'],'source_lock_sha256':digest(args.source_lock),
              'host_files_verified':len(lock['host_files']),'sdk_files_verified':len(lock['sdk_files']),
              'runtime_fingerprint':fingerprint,'config_sha256':digest(work/'plugins/candidate/config.toml')}
    metadata['package_sha256']=digest(args.package) if args.package else None
    metadata['verification_files']={p.relative_to(ROOT).as_posix():digest(p)
        for directory in ('tools','tests') for p in (ROOT/directory).rglob('*.py')}
    if (work/'neighbor-source-hashes.json').exists():
        neighbors=json.loads((work/'neighbor-source-hashes.json').read_text(encoding='utf8'))
        assert neighbors=={name:digest(ROOT.parent/name) for name in neighbors}
        metadata['neighbor_source_unchanged']=True
    (work/'run.json').write_text(json.dumps(metadata,indent=2),encoding='utf8')
    if process.returncode:
        print(output.decode('utf8',errors='replace')[-4500:])
    else:
        print((work/'result.json').read_text(encoding='utf8'))
    print('exit_code=',process.returncode,'evidence=',work)
    raise SystemExit(process.returncode)

if __name__=='__main__': main()

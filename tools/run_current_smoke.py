"""Run current Hush + Identity on an explicit official Host, without Vita."""
from pathlib import Path
import argparse
import hashlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file() and p.suffix in (".py", ".json", ".sql")
            and not any(part.startswith(".") or part == "__pycache__" for part in p.relative_to(root).parts)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host-source", type=Path, required=True)
    parser.add_argument("--identity-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    host, work = args.host_source.resolve(), args.output.resolve()
    work.mkdir(parents=True, exist_ok=False)
    evidence = {}
    for folder, source, package in (("maibot-maihush-plugin", ROOT, "maihush"),
            ("maibot-maiIdentity-plugin", args.identity_source.resolve(), "maiidentity")):
        target = work / "plugins" / folder
        target.mkdir(parents=True)
        for name in ("plugin.py", "_manifest.json"):
            shutil.copyfile(source / name, target / name)
        shutil.copytree(source / package, target / package, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        (target / "_locales").mkdir(); (target / "_locales/zh-CN.json").write_text("{}", encoding="utf-8")
        evidence[folder] = hashes(target)
    version = tomllib.loads((host / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    safety = work / "safety"; safety.mkdir()
    (safety / "sitecustomize.py").write_text(
        "import sys\ndef audit(e,a):\n"
        " if e=='socket.connect':\n"
        "  t=a[1]\n"
        "  if not isinstance(t,tuple) or t[0] not in ('127.0.0.1','::1'): raise PermissionError('EXTERNAL_NETWORK_DENIED')\n"
        "sys.addaudithook(audit)\n", encoding="utf-8")
    env = {k:v for k,v in os.environ.items() if k.upper() in ("SYSTEMROOT","WINDIR","COMSPEC","PATHEXT")}
    env.update(PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1",
        PYTHONPATH=os.pathsep.join((str(safety),str(ROOT),str(host))),
        MAIBOT_INSTANCE_DIR=str(work), MAIBOT_HOST_VERSION=version, TEMP=str(work), TMP=str(work))
    with (work / "console.log").open("wb") as output:
        process = subprocess.Popen([sys.executable,"-m","tools.current_scenario"], cwd=work, env=env,
            stdout=output, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0)
        try:
            code = process.wait(timeout=150)
        except BaseException:
            if os.name=="nt":
                subprocess.run(["taskkill","/PID",str(process.pid),"/T","/F"], capture_output=True)
            else:
                process.kill()
            process.wait()
            raise
    if code:
        print((work / "console.log").read_text(encoding="utf-8", errors="replace")[-6000:])
        raise SystemExit(code)
    report = json.loads((work / "result.json").read_text(encoding="utf-8"))
    report.update(plugin_sources=evidence, host_files=hashes(host / "src"),
        sdk=importlib.metadata.version("maibot-plugin-sdk"), full_bot_cli=False)
    (work / "result.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "cases": report["cases"]},ensure_ascii=False))


if __name__ == "__main__":
    main()

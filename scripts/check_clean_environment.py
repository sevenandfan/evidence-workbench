"""Build an isolated local venv and retain actual install/run evidence.

Not a container or a new OS. All dependencies stay under project .tools.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from hashlib import sha256

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verified-scipy", type=Path)
    args = parser.parse_args()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-clean-env-" + uuid.uuid4().hex[:8]
    output = ROOT / "runs" / run_id
    output.mkdir(parents=True, exist_ok=False)
    env_dir = ROOT / ".tools" / run_id
    python = env_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    environment = dict(os.environ)
    for key in ("PYTHONPATH", "PYTHONHOME"):
        environment.pop(key, None)
    environment.update(PYTHONNOUSERSITE="1", PYTHONUTF8="1", PIP_DISABLE_PIP_VERSION_CHECK="1")
    commands = []
    runner_bytes = Path(__file__).read_bytes()
    (output / "executed-runner.py").write_bytes(runner_bytes)

    def run(label, args):
        started = time.perf_counter()
        print("Running:", label, flush=True)
        lines = []
        with (output / (label + ".log")).open("w", encoding="utf-8") as log:
            process = subprocess.Popen([str(a) for a in args], cwd=ROOT, env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="backslashreplace")
            for line in process.stdout:
                log.write(line)
                log.flush()
                lines.append(line)
                if label.startswith("install"):
                    print(line.rstrip(), flush=True)
            code = process.wait()
        commands.append({"label": label, "args": [str(a) for a in args], "exit_code": code, "elapsed_seconds": time.perf_counter() - started})
        if code:
            raise RuntimeError(f"{label} failed ({code}); see saved log")
        return "".join(lines)

    status, error = "incomplete", None
    try:
        run("create-venv", [sys.executable, "-m", "venv", env_dir])
        cfg = (env_dir / "pyvenv.cfg").read_text(encoding="utf-8")
        (output / "pyvenv.cfg").write_text(cfg, encoding="utf-8")
        if "include-system-site-packages = false" not in cfg:
            raise RuntimeError("Virtual environment inherits system dependencies")
        if args.verified_scipy:
            wheel = args.verified_scipy.resolve(strict=True)
            if wheel.name != "scipy-1.11.4-cp311-cp311-win_amd64.whl" or sha256(wheel.read_bytes()).hexdigest() != "acf8ed278cc03f5aff035e69cb511741e0418681d25fbbb86ca65429c4f4d9cd":
                raise RuntimeError("Recovery wheel not identical to official audited source")
            run("install-verified-scipy", [python, "-m", "pip", "install", "--no-deps", "--report", output / "scipy-install-report.json", wheel])
        run("install", [python, "-m", "pip", "install", "--timeout", "30", "--retries", "2", "--only-binary=:all:", "--report", output / "install-report.json", "-r", ROOT / "requirements.txt"])
        probe = run("environment", [python, "-c", "import sys,json,site,numpy,scipy,matplotlib; assert sys.prefix!=sys.base_prefix; assert not site.ENABLE_USER_SITE; print(json.dumps({'python':sys.version,'executable':sys.executable,'prefix':sys.prefix,'base_prefix':sys.base_prefix,'sys_path':sys.path,'user_site_enabled':site.ENABLE_USER_SITE,'versions':{'numpy':numpy.__version__,'scipy':scipy.__version__,'matplotlib':matplotlib.__version__}}))"])
        (output / "environment.json").write_text(probe, encoding="utf-8")
        frozen = run("freeze", [python, "-m", "pip", "freeze"])
        (output / "requirements-resolved.txt").write_text(frozen, encoding="utf-8")
        run("pip-check", [python, "-m", "pip", "check"])
        run("regression", [python, "scripts/evaluate.py"])
        regression = json.loads((ROOT / "evals/regression-results.json").read_bytes())
        (output / "regression-results.json").write_text(json.dumps(regression, indent=2, ensure_ascii=False), encoding="utf-8")
        if regression["passed"] != regression["tests_run"] or regression["skipped"]:
            raise RuntimeError("Regression not fully passed")
        real_text = run("replicate", [python, "-m", "b1", "replicate"])
        real = json.loads(real_text)
        run("verify-replication", [python, "-m", "b1", "verify-replication", real["output"]])
        identity = json.loads(run("site-identity", [python, "scripts/audit_site_identity.py"]))
        if identity["checks_passed"] != 259 or identity["sites"] != 36:
            raise RuntimeError("Site identity audit failed")
        (output / "linked-runs.json").write_text(json.dumps({"aggregate": real["output"], "site_identity": identity["output"]}, ensure_ascii=False, indent=2), encoding="utf-8")
        status = "passed"
    except Exception as exc:
        error = str(exc)
    summary = {"run_id": run_id, "status": status, "error": error, "scope": "New dependency-isolated venv on existing Windows/Python base; not new OS/container/cloud CI", "base_interpreter": sys.executable, "venv_interpreter": str(python), "commands": commands, "model_calls": 0, "api_cost": 0, "requirements_sha256": sha256((ROOT / "requirements.txt").read_bytes()).hexdigest(), "runner_sha256": sha256(runner_bytes).hexdigest(), "verified_scipy": str(args.verified_scipy) if args.verified_scipy else None}
    (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    hashes = {p.name: sha256(p.read_bytes()).hexdigest() for p in output.iterdir()}
    (output / "manifest.json").write_text(json.dumps({"run_id": run_id, "status": status, "files": hashes}, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "status": status, "error": error}))
    return 0 if status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

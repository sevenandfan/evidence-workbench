"""Read-only hashes + numerical replay of a saved real-data experiment."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from b1.engine import encode, parse_json, sha
from b1.summary import execute


def verify(directory):
    path = Path(directory).resolve(strict=True)
    manifest = parse_json((path/"manifest.json").read_bytes())
    if manifest["status"] != "complete":
        raise ValueError("Incomplete run")
    expected = {"summary.json", "comparison.json", "b0.json", "b1.json", "report.html", "dataset.json", "gold.json", "plan.json", "forest.png"}
    # v0.2 adds a stage trace; historical v0.1 experiment manifests remain verifiable.
    if "events.jsonl" in manifest["files"]:
        expected.add("events.jsonl")
    if set(manifest["files"]) != expected:
        raise ValueError("Unexpected/missing artifacts")
    for name, digest in manifest["files"].items():
        target = path/name
        if target.is_symlink() or sha(target.read_bytes()) != digest:
            raise ValueError("Artifact changed: " + name)
    replay = execute((path/"dataset.json").read_bytes(), (path/"gold.json").read_bytes(), (path/"plan.json").read_bytes())
    saved = parse_json((path/"b1.json").read_bytes())
    if replay != saved:
        raise ValueError("Numerical replay differs")
    summary = parse_json((path/"summary.json").read_bytes())
    if summary["run_id"] != manifest["run_id"] or summary["B1_author_checks_passed"] != replay["checks_passed"]:
        raise ValueError("Summary differs from evidence")
    print(json.dumps({"status": "verified", "run_id": manifest["run_id"], "artifacts_checked": len(expected), "numeric_checks": replay["checks_total"], "passed": replay["checks_passed"]}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    verify(parser.parse_args().directory)

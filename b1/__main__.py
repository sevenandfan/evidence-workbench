import argparse
import json
import sys
from pathlib import Path

from .engine import ROOT, Rejected, run, verify


def main():
    parser = argparse.ArgumentParser(description="Evidence Workbench B1 — deterministic research claim audit")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("demo", help="Run the supplied synthetic study")
    sub.add_parser("replicate", help="Recalculate the pinned Many Labs 1 Sunk Costs table")
    sub.add_parser("end-to-end", help="Rebuild participant statistics then run B0/B1 against author gold")
    retail_request = sub.add_parser("retail", help="Review real retail transactions with SQL/Decimal cross-checks")
    retail_request.add_argument("--metric", action="append", default=[], help="Validate requested metric before running; missing-source metrics are rejected")
    retail_replay = sub.add_parser("verify-retail", help="Read-only retail hash and numeric replay")
    retail_replay.add_argument("directory", type=Path)
    full_replay = sub.add_parser("verify-end-to-end", help="Hash verification and participant-to-result replay")
    full_replay.add_argument("directory", type=Path)
    replay = sub.add_parser("verify-replication", help="Verify a Many Labs run and replay its statistics")
    replay.add_argument("directory", type=Path)
    execute = sub.add_parser("run", help="Run an explicit reviewed analysis plan")
    for name in ("data", "plan", "claims"):
        execute.add_argument("--" + name, required=True, type=Path)
    audit = sub.add_parser("verify", help="Hash verification and numerical replay from snapshots")
    audit.add_argument("directory", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "retail":
            from .retail import run as retail_run, require_metric
            for metric in args.metric: require_metric(metric)
            result = retail_run()
            print(json.dumps(result, ensure_ascii=False))
            return 0 if result["status"] == "passed" else 1
        elif args.command == "verify-retail":
            from .retail import verify as retail_verify
            print(json.dumps(retail_verify(args.directory), ensure_ascii=False))
        elif args.command == "end-to-end":
            from .end_to_end import run as full_run
            result = full_run()
            print(json.dumps(result, ensure_ascii=False))
            return 0 if result["status"] == "passed" else 1
        elif args.command == "verify-end-to-end":
            from .end_to_end import verify as full_verify
            print(json.dumps(full_verify(args.directory), ensure_ascii=False))
        elif args.command == "replicate":
            from scripts.compare_manylabs import main as replicate
            return replicate()
        elif args.command == "verify-replication":
            from scripts.verify_manylabs import verify as verify_replication
            verify_replication(args.directory)
        elif args.command == "verify":
            print(json.dumps(verify(args.directory), ensure_ascii=False))
        else:
            if args.command == "demo":
                args.data, args.plan, args.claims = (ROOT / "examples" / n for n in ("study.csv", "plan.json", "claims.json"))
            output, bundle = run(args.data, args.plan, args.claims)
            print(json.dumps({"status": "complete", "output": str(output), "report": str(output / "report.html"), "estimate": bundle["result"]["estimate"], "claim_verdicts": [c["status"] for c in bundle["claims"]]}, ensure_ascii=False))
    except (ValueError, OSError) as error:
        print(json.dumps({"status": "rejected", "code": getattr(error, "code", "io_error"), "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

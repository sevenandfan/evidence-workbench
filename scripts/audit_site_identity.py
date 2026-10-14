"""Independent author-label binding followed by named-site reaggregation."""
import csv
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from b1.engine import encode, sha
from b1.participants import reaggregate
from b1.site_mapping import resolve_site_map

DIRECTORY = ROOT / "data/manylabs1/participants/7a1334c053201ccdd2054417f74aa3d62776dd03"


def read_source(name, manifest):
    path = DIRECTORY / name
    source = json.loads((DIRECTORY / manifest).read_bytes())
    if sha(path.read_bytes()) != source["sha256"]:
        raise ValueError("Source hash differs")
    rows, records = [], []
    with path.open(encoding="latin-1", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        fields = {"session_id", "sample", "sunkgroup", "sunkDV", "sunkcosta", "sunkcostb"}
        if len(reader.fieldnames) != len(set(reader.fieldnames)) or not fields.issubset(reader.fieldnames):
            raise ValueError("Invalid headers")
        for row in reader:
            subset = {key: row[key] for key in fields}
            if any(value is None or not value.isascii() for value in subset.values()):
                raise ValueError("Selected field not ASCII")
            rows.append(subset)
            records.append((reader.line_num, subset))
    return path, source, rows, records


def main():
    vp, vs, values, records = read_source("Tab.delimited.Cleaned.dataset.WITH.variable.values.dat", "source_manifest.json")
    lp, ls, labels, _ = read_source("Tab.delimited.Cleaned.dataset.WITH.variable.labels.dat", "labels_source_manifest.json")
    mapping = resolve_site_map(values, labels)
    if set(mapping) != set(range(1, 37)):
        raise ValueError("Incomplete site map")
    summaries, _ = reaggregate(records, include_all_sites=True)
    reference_raw = (ROOT / "data/manylabs1/sunk-costs-v1/dataset.json").read_bytes()
    plan = json.loads((ROOT / "data/manylabs1/sunk-costs-v1/plan.json").read_bytes())
    if sha(reference_raw) != plan["dataset_sha256"]:
        raise ValueError("Reference drift")
    reference = {r["site"]: r for r in json.loads(reference_raw)["samples"]}
    if set(mapping.values()) != set(reference) - {"Overall:"}:
        raise ValueError("Author labels do not match reference site identities")
    checks = []
    for row in summaries:
        old_label = row["site"]
        code = 1 if old_label == "abington" else int(old_label.split(":")[1]) if old_label.startswith("site_code:") else None
        site = mapping[code] if code else "Overall:"
        row["site"] = site
        ref = reference[site]
        for metric in ("n_paid", "n_free", "excluded_or_no_response", "mean_paid", "mean_free", "sd_paid", "sd_free"):
            tolerance = 0 if metric.startswith("n_") or metric == "excluded_or_no_response" else 1e-7
            error = abs(row[metric] - ref[metric])
            checks.append({"site": site, "site_code": code, "metric": metric, "observed": row[metric], "author": ref[metric], "error": error, "tolerance": tolerance, "passed": error <= tolerance, "reference_range": ref["source_range"]})
    if sha(vp.read_bytes()) != vs["sha256"] or sha(lp.read_bytes()) != ls["sha256"]:
        raise ValueError("Source changed during run")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-site-identity-" + uuid.uuid4().hex[:8]
    output = ROOT / "runs" / run_id
    output.mkdir(parents=True, exist_ok=False)
    passed = sum(c["passed"] for c in checks)
    record = {"run_id": run_id, "status": "passed" if passed == len(checks) else "mismatch", "matched_records": len(values), "site_count": len(mapping), "identity_method": "Unique session_id join; response and condition equality; no gold numeric matching", "sources": [vs, ls], "reference_sha256": sha(reference_raw), "code_hashes": {str(p.relative_to(ROOT)): sha(p.read_bytes()) for p in (ROOT / "b1/site_mapping.py", ROOT / "b1/participants.py", Path(__file__))}, "source_unchanged": True, "checks_passed": passed, "checks_total": len(checks), "summaries": summaries, "checks": checks, "limits": ["Author-cleaned source; original cleaning pipeline not audited", "Original R environment not executed"]}
    (output / "audit.json").write_bytes(encode(record))
    (output / "site-map.json").write_bytes(encode({"method": record["identity_method"], "matched_records": len(values), "site_code_to_author_label": mapping}))
    lines = "\n".join(f"| {code} | {name} |" for code, name in sorted(mapping.items()))
    (output / "report.md").write_text(f"# 完整站点身份核验\n\n运行：{run_id}；状态：{record['status']}。\n\n{len(values)}条记录以唯一session_id连接；所选响应及条件一致；36个站点代码与作者标签一一对应。不使用gold统计量推断名称。\n\n按独立名称重新对照作者表：{passed}/{len(checks)}统计量通过。原数据hash保持。\n\n| 代码 | 作者标签 |\n|---|---|\n{lines}\n\n来源文件是作者已清洗数据，不证明原始清洗流程；原生R、干净环境和生产能力未验证。\n", encoding="utf-8")
    (output / "manifest.json").write_bytes(encode({"run_id": run_id, "files": {p.name: sha(p.read_bytes()) for p in output.iterdir()}}))
    print(json.dumps({"output": str(output), "status": record["status"], "matched_records": len(values), "sites": len(mapping), "checks_passed": passed, "checks_total": len(checks)}))
    return 0 if record["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

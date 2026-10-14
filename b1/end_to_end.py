"""Participant-derived statistics -> B0/B1 -> external author gold.

Author summaries are only a comparison, never the computational input.
"""
import html
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .engine import ROOT, encode, parse_json, sha
from .participants import reaggregate
from .site_mapping import resolve_site_map
from .summary import calculate, execute
from scripts.audit_site_identity import DIRECTORY, read_source
from scripts.baseline_manylabs import run as baseline

PREPARED = ROOT / "data/manylabs1/sunk-costs-v1"
ARTIFACTS = {"dataset.json", "lineage.json", "plan.json", "gold.json", "b0.json", "b1.json", "comparison.json", "summary.json", "events.jsonl", "report.html"}


def rebuild(records, mapping, reference):
    if set(mapping) != set(range(1, 37)) or set(mapping.values()) != set(reference) - {"Overall:"}:
        raise ValueError("Incomplete or conflicting independent site identity map")
    summaries, extracted = reaggregate(records, include_all_sites=True)
    inputs, checks, exclusions = [], [], []
    metrics = ("n_paid", "n_free", "excluded_or_no_response", "mean_paid", "mean_free", "sd_paid", "sd_free")
    for row in summaries:
        label = row["site"]
        code = 1 if label == "abington" else int(label.split(":")[1]) if label.startswith("site_code:") else None
        site = mapping[code] if code else "Overall:"
        sample = {key: row[key] for key in metrics}
        sample.update(site=site, source_range=f"participant numeric TSV: sample={code if code else 'all'}; sunkgroup/sunkDV")
        inputs.append(sample)
        exclusions.append({"site": site, "excluded": row["excluded"]})
        for metric in metrics:
            tolerance = 0 if metric.startswith("n_") or metric == "excluded_or_no_response" else 1e-7
            error = abs(sample[metric] - reference[site][metric])
            checks.append({"site": site, "metric": metric, "observed": sample[metric], "author": reference[site][metric], "error": error, "tolerance": tolerance, "passed": error <= tolerance, "reference_range": reference[site]["source_range"]})
    # Stable source-identity order; values remain independently reconstructed.
    inputs.sort(key=lambda r: (r["site"] != "Overall:", r["site"]))
    return inputs, {"site_map": mapping, "descriptive_checks": checks, "exclusions": exclusions, "selected_records": extracted}


def compare(samples, gold, plan):
    b0 = baseline(samples)
    b1 = [calculate(row) for row in samples]
    reference = {row["site"]: row for row in gold["rows"]}
    if len(reference) != len(gold["rows"]) or {r["site"] for r in samples} != set(reference):
        raise ValueError("Reference/site coverage differs")
    checks = []
    for a, b in zip(b0, b1):
        ref = reference[a["site"]]
        for metric in ("effect", "ci_low", "ci_high"):
            tolerance = plan["effect_tolerance"] if metric == "effect" else plan["ci_tolerance"]
            for pair, observed, expected in (("B0_author", a[metric], ref[metric]), ("B1_author", b[metric], ref[metric]), ("B0_B1", a[metric], b[metric])):
                error = abs(observed - expected)
                checks.append({"site": a["site"], "metric": metric, "pair": pair, "observed": observed, "expected": expected, "error": error, "tolerance": tolerance, "passed": error <= tolerance, "reference_range": ref["source_range"]})
    return b0, b1, checks


def payloads():
    raw = {name: (PREPARED / name).read_bytes() for name in ("dataset.json", "gold.json", "plan.json")}
    # Validate the frozen author contract/gold before using it as a reference.
    # Its calculated values are deliberately discarded, not used as inputs.
    execute(raw["dataset.json"], raw["gold.json"], raw["plan.json"])
    author, gold, plan = [parse_json(raw[name]) for name in ("dataset.json", "gold.json", "plan.json")]
    upstream = ROOT / "data/manylabs1/upstream" / plan["upstream"]["commit"]
    hashes = {}
    for name, record in plan["upstream"]["files"].items():
        path = upstream / name
        if sha(path.read_bytes()) != record["sha256"]:
            raise ValueError("Author source drift: " + name)
        hashes[str(path.relative_to(ROOT))] = record["sha256"]
    vp, vs, values, records = read_source("Tab.delimited.Cleaned.dataset.WITH.variable.values.dat", "source_manifest.json")
    lp, ls, labels, _ = read_source("Tab.delimited.Cleaned.dataset.WITH.variable.labels.dat", "labels_source_manifest.json")
    if vs["commit"] != plan["upstream"]["commit"] or ls["commit"] != vs["commit"]:
        raise ValueError("Participant/reference source versions differ")
    for path, source in ((vp, vs), (lp, ls)):
        hashes[str(path.relative_to(ROOT))] = source["sha256"]
    for name, content in raw.items():
        hashes[str((PREPARED / name).relative_to(ROOT))] = sha(content)
    mapping = resolve_site_map(values, labels)
    samples, lineage = rebuild(records, mapping, {row["site"]: row for row in author["samples"]})
    dataset = {"level": "participant_reaggregation", "study": author["study"], "synthetic": False, "upstream_commit": vs["commit"], "samples": samples}
    b0, b1, checks = compare(samples, gold, plan)
    for name, digest in hashes.items():
        if sha((ROOT / name).read_bytes()) != digest:
            raise ValueError("Source changed during computation")
    bundle = {"dataset.json": dataset, "lineage.json": lineage, "plan.json": plan, "gold.json": gold, "b0.json": b0, "b1.json": b1, "comparison.json": checks}
    counts = {pair: sum(c["passed"] for c in checks if c["pair"] == pair) for pair in ("B0_author", "B1_author", "B0_B1")}
    descriptive = sum(c["passed"] for c in lineage["descriptive_checks"])
    passed = descriptive == 259 and all(value == 111 for value in counts.values())
    return bundle, {"status": "passed" if passed else "mismatch", "input_records": len(records), "site_count": len(mapping), "n_overall": samples[0]["n_paid"] + samples[0]["n_free"], "excluded_overall": samples[0]["excluded_or_no_response"], "descriptive_passed": descriptive, "descriptive_total": 259, "effect_checks_per_pair": 111, "effect_checks_passed": counts, "source_hashes": hashes, "model_calls": 0, "api_cost": 0, "calculation_input": "participant-derived means/sample-SDs/n, not author aggregate table", "plan_binding": "plan.json retains author reference hashes; derived dataset separately bound by run manifest", "limits": ["Author-cleaned data; original cleaning chain unverified", "No native R rerun; B0/B1 share SciPy nct", "Single study, not heldout accuracy; no user productivity measurement"]}


def run():
    started = time.perf_counter()
    bundle, summary = payloads()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-end-to-end-" + uuid.uuid4().hex[:8]
    output = ROOT / "runs" / run_id
    output.mkdir(parents=True, exist_ok=False)
    summary.update(run_id=run_id, elapsed_seconds=time.perf_counter()-started, code_hashes={str(p.relative_to(ROOT)): sha(p.read_bytes()) for p in (Path(__file__), ROOT/"b1/participants.py", ROOT/"b1/site_mapping.py", ROOT/"b1/summary.py", ROOT/"scripts/audit_site_identity.py", ROOT/"scripts/baseline_manylabs.py")})
    bundle["summary.json"] = summary
    for name, value in bundle.items():
        (output / name).write_bytes(encode(value))
    events = [{"stage": stage, "status": "completed", "run_id": run_id} for stage in ("source_hashes", "identity_join", "participant_reaggregation", "descriptive_comparison", "B0_B1_calculation", "external_gold_comparison", "source_unchanged")]
    (output/"events.jsonl").write_text("\n".join(json.dumps(e) for e in events)+"\n", encoding="utf-8")
    rows = "".join(f"<tr><td>{html.escape(r['site'])}</td><td>{r['n_paid']}/{r['n_free']}</td><td>{r['effect']:.9f}</td><td>[{r['ci_low']:.6f}, {r['ci_high']:.6f}]</td><td>{r['confidence']:.0%}</td></tr>" for r in bundle["b1.json"])
    report = f'''<!doctype html><html lang="zh"><meta charset="utf-8"><title>参与者到研究结果：完整链</title><style>body{{font:16px system-ui;max-width:1100px;margin:30px auto;padding:20px}}td,th{{padding:8px;border-bottom:1px solid #ddd;text-align:left}}table{{border-collapse:collapse}}pre{{white-space:pre-wrap}}</style><h1>Many Labs 1 沉没成本：完整计算链</h1><p>参与者数据 → 唯一ID站点标签 → n/均值/样本SD → B0/B1 → 作者预先发布结果。</p><p>状态：{summary['status']}；6344条记录，纳入6330，缺失/未应答14；36站点加总体。描述统计{summary['descriptive_passed']}/259；三组效应/区间对照：{html.escape(str(summary['effect_checks_passed']))}，每组111项。</p><p>效应量计算使用本轮参与者重建统计量；作者汇总表仅作描述统计对照。逐行排除见lineage.json，具体差异见comparison.json。</p><table><tr><th>站点</th><th>Paid/Free n</th><th>修正g</th><th>作者兼容区间</th><th>置信度</th></tr>{rows}</table><h2>限制</h2><p>已清洗数据再计算，不是重新招募实验；原始清洗链/原生R/新OS/生产未验证。B0/B1共享SciPy分布。本计划的作者输入hash保留，派生输入另由manifest绑定。运行ID：{run_id}。</p><h2>复核命令</h2><pre>python -m b1 verify-end-to-end runs/{run_id}</pre></html>'''
    (output/"report.html").write_text(report, encoding="utf-8")
    (output/"manifest.json").write_bytes(encode({"run_id": run_id, "status": "complete", "experiment_status": summary["status"], "files": {name: sha((output/name).read_bytes()) for name in sorted(ARTIFACTS)}}))
    return {"output": str(output), **summary}


def verify(directory):
    path = Path(directory).resolve(strict=True)
    manifest = parse_json((path/"manifest.json").read_bytes())
    if manifest["status"] != "complete" or set(manifest["files"]) != ARTIFACTS:
        raise ValueError("Incomplete/unexpected artifact set")
    for name, digest in manifest["files"].items():
        if (path/name).is_symlink() or sha((path/name).read_bytes()) != digest:
            raise ValueError("Artifact changed: " + name)
    saved = parse_json((path/"summary.json").read_bytes())
    if saved["run_id"] != manifest["run_id"]:
        raise ValueError("Run identity differs")
    for name, digest in saved["code_hashes"].items():
        if sha((ROOT/name).read_bytes()) != digest:
            raise ValueError("Replay code version differs")
    bundle, summary = payloads()
    for name, value in bundle.items():
        if parse_json((path/name).read_bytes()) != json.loads(encode(value)):
            raise ValueError("Participant-to-result replay differs: " + name)
    for key, value in summary.items():
        if saved[key] != value:
            raise ValueError("Summary replay differs: " + key)
    return {"status": "verified", "experiment_status": summary["status"], "run_id": saved["run_id"], "artifacts_checked": len(ARTIFACTS), "descriptive_passed": summary["descriptive_passed"], "effect_checks_passed": summary["effect_checks_passed"]}

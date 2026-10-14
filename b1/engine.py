"""Reviewed operations only. No generated code, SQL, network, or plugins."""
from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import math
import platform
import re
import statistics
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import scipy
from scipy import stats

from . import __version__

ROOT = Path(__file__).resolve().parent.parent
MAX_BYTES = 2_000_000
MAX_ROWS = 10_000


class Rejected(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def reject(code, message):
    raise Rejected(code, message)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encode(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")


def fields(obj, required, optional=()):
    if not isinstance(obj, dict) or set(obj) - set(required) - set(optional) or set(required) - set(obj):
        reject("contract", f"Required keys: {list(required)}; allowed optional keys: {list(optional)}")


def number(x):
    return type(x) in (int, float) and math.isfinite(x)


def parse_json(raw):
    def unique(pairs):
        obj = {}
        for k, v in pairs:
            if k in obj:
                reject("contract", "Duplicate JSON key: " + k)
            obj[k] = v
        return obj
    try:
        return json.loads(raw, object_pairs_hook=unique, parse_constant=lambda _: reject("contract", "Nonfinite JSON"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        reject("json_parse", str(e))
    except RecursionError:
        reject("resource_limit", "JSON nesting exceeds parser limit")


@dataclass(frozen=True)
class Plan:
    schema_version: str
    title: str
    dataset_sha256: str
    method: str
    missing: str
    confidence: float
    control: str
    treatment: str
    score_min: float
    score_max: float
    exclude_ids: tuple[str, ...]

    @classmethod
    def load(cls, obj):
        fields(obj, cls.__dataclass_fields__)
        for key in ("schema_version", "title", "dataset_sha256", "method", "missing", "control", "treatment"):
            if not isinstance(obj[key], str) or not obj[key] or len(obj[key]) > 200:
                reject("contract", "Invalid string: " + key)
        if obj["schema_version"] != "1" or obj["method"] != "welch_change":
            reject("unsupported_method", "Only schema 1 / welch_change is reviewed")
        if not re.fullmatch(r"[0-9a-f]{64}", obj["dataset_sha256"]):
            reject("contract", "Expected SHA-256 digest")
        if obj["missing"] not in ("reject", "complete_case"):
            reject("contract", "Specify reject or complete_case explicitly")
        if obj["control"] == obj["treatment"]:
            reject("contract", "Groups must differ")
        for key in ("confidence", "score_min", "score_max"):
            if not number(obj[key]):
                reject("contract", "Finite numeric value required: " + key)
        if not 0.8 <= obj["confidence"] <= 0.99 or obj["score_min"] >= obj["score_max"]:
            reject("contract", "Invalid confidence or score range")
        if obj["score_min"] < -1_000_000 or obj["score_max"] > 1_000_000:
            reject("contract", "Reviewed score range is bounded to +/- 1,000,000")
        ids = obj["exclude_ids"]
        if not isinstance(ids, list) or any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
            reject("contract", "exclude_ids must be unique nonempty strings")
        return cls(**{**obj, "exclude_ids": tuple(ids)})


def load_rows(raw: bytes, plan: Plan):
    if len(raw) > MAX_BYTES:
        reject("resource_limit", "CSV exceeds 2 MB")
    if sha(raw) != plan.dataset_sha256:
        reject("dataset_version", "Dataset digest does not match approved plan")
    try:
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")), strict=True)
        if reader.fieldnames != ["participant_id", "group", "pre", "post"]:
            reject("data_schema", "CSV columns/order must be participant_id,group,pre,post")
        rows = []
        seen = set()
        excluded = []
        missing_count = 0
        for line, row in enumerate(reader, 2):
            if line - 1 > MAX_ROWS:
                reject("resource_limit", "Too many rows")
            if None in row or any(v is None for v in row.values()):
                reject("data_schema", f"Ragged row at line {line}")
            pid, group = row["participant_id"], row["group"]
            if not pid or pid in seen:
                reject("duplicate_id", f"Missing/duplicate participant ID at line {line}")
            seen.add(pid)
            if group not in (plan.control, plan.treatment):
                reject("group", f"Unknown group at line {line}")
            values = {}
            for key in ("pre", "post"):
                if row[key].strip() == "":
                    values[key] = None
                    missing_count += 1
                else:
                    try:
                        value = float(row[key])
                    except ValueError:
                        reject("numeric", f"Invalid {key} at line {line}")
                    if not math.isfinite(value) or not plan.score_min <= value <= plan.score_max:
                        reject("range", f"Nonfinite/out-of-range {key} at line {line}")
                    values[key] = value
            if pid in plan.exclude_ids:
                excluded.append({"participant_id": pid, "line": line, "reason": "plan_exclusion"})
            elif None in values.values():
                if plan.missing == "reject":
                    reject("missing", f"Missing value at line {line}; explicit policy rejects")
                excluded.append({"participant_id": pid, "line": line, "reason": "incomplete_pair"})
            else:
                rows.append({"participant_id": pid, "group": group, **values, "change": values["post"] - values["pre"], "line": line})
        if set(plan.exclude_ids) - seen:
            reject("exclusion", "Excluded IDs are absent from dataset")
        return rows, {"input_n": len(seen), "included_n": len(rows), "missing_cells": missing_count, "excluded": excluded}
    except (UnicodeDecodeError, csv.Error) as e:
        reject("data_schema", str(e))


def analyze(raw, plan):
    rows, quality = load_rows(raw, plan)
    groups = {g: [r["change"] for r in rows if r["group"] == g] for g in (plan.control, plan.treatment)}
    if any(len(v) < 3 for v in groups.values()):
        reject("sample_size", "Each group needs at least three complete pairs")
    a, b = groups[plan.treatment], groups[plan.control]
    va, vb = statistics.variance(a), statistics.variance(b)
    if va <= 0 or vb <= 0:
        reject("zero_variance", "Constant changes are outside reviewed inference scope")
    result = stats.ttest_ind(a, b, equal_var=False, alternative="two-sided")
    ci = result.confidence_interval(confidence_level=plan.confidence)
    na, nb = len(a), len(b)
    difference = statistics.mean(a) - statistics.mean(b)
    se = math.sqrt(va / na + vb / nb)
    df = (va / na + vb / nb) ** 2 / ((va / na) ** 2 / (na - 1) + (vb / nb) ** 2 / (nb - 1))
    # Independent formula cross-check catches direction, variance and sample-size mistakes.
    if not math.isclose(float(result.statistic), difference / se, rel_tol=1e-10, abs_tol=1e-10) or not math.isclose(float(result.df), df, rel_tol=1e-10):
        reject("statistical_check", "Independent Welch formula check failed")
    pooled_sd = math.sqrt(((na - 1) * va + (nb - 1) * vb) / (na + nb - 2))
    warnings = ["Synthetic demonstration; no real study or causal claim verified.", "Independence and study design require human review; diagnostics cannot certify assumptions.", "Complete-case analysis may bias results when missingness is informative.", "P-values and intervals are unadjusted for multiple comparisons; one prespecified contrast only."]
    if min(na, nb) < 20:
        warnings.append("Small samples: inspect distributions and sensitivity before using inferential results.")
    summaries = {}
    for group, changes in groups.items():
        diagnostics = stats.shapiro(changes) if len(changes) <= 5000 else None
        summaries[group] = {"n": len(changes), "mean_change": statistics.mean(changes), "sd_change": statistics.stdev(changes), "shapiro_p": float(diagnostics.pvalue) if diagnostics else None}
    out = {"evidence_id": "E-main", "method": "Welch two-sided t-test on individual change scores", "contrast": f"{plan.treatment} minus {plan.control}", "formula": "change=post-pre; estimate=mean(change_treatment)-mean(change_control)", "estimate": difference, "standard_error": se, "df": df, "t": float(result.statistic), "p": float(result.pvalue), "confidence": plan.confidence, "ci_low": float(ci.low), "ci_high": float(ci.high), "cohens_d_change": difference / pooled_sd, "effect_definition": "Uncorrected pooled-SD Cohen d on change scores; descriptive effect, not ANCOVA", "groups": summaries, "quality": quality, "warnings": warnings, "statistical_cross_check": "passed"}
    return out, rows


def check_claims(obj, result):
    fields(obj, ("schema_version", "source", "claims"))
    if obj["schema_version"] != "1" or not isinstance(obj["source"], str) or not obj["source"]:
        reject("claims", "Claim source and version required")
    if not isinstance(obj["claims"], list) or not 1 <= len(obj["claims"]) <= 50:
        reject("claims", "Between 1 and 50 explicit numeric claims required")
    allowed = ("estimate", "ci_low", "ci_high", "p", "t", "df", "cohens_d_change")
    seen, verdicts = set(), []
    for claim in obj["claims"]:
        fields(claim, ("id", "metric", "expected", "abs_tolerance"))
        if not isinstance(claim["id"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", claim["id"]) or claim["id"] in seen:
            reject("claims", "Unique safe claim ID required")
        seen.add(claim["id"])
        if claim["metric"] not in allowed or not number(claim["expected"]) or not number(claim["abs_tolerance"]) or claim["abs_tolerance"] < 0:
            reject("claims", "Only finite numeric claims with explicit nonnegative tolerance")
        observed = result[claim["metric"]]
        error = abs(observed - claim["expected"])
        verdicts.append({**claim, "observed": observed, "absolute_error": error, "status": "numeric_match" if error <= claim["abs_tolerance"] else "numeric_mismatch", "evidence_id": result["evidence_id"]})
    return verdicts


def chart_svg(rows, plan):
    # Explicit plotting spec; generated SVG is passive markup, never executable code.
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["svg.hashsalt"] = "evidence-b1"
    fig, axes = plt.subplots(1, 2, figsize=(11, 4), constrained_layout=True)
    for idx, g in enumerate((plan.control, plan.treatment)):
        subset = [r for r in rows if r["group"] == g]
        changes = [r["change"] for r in subset]
        jitter = [idx + ((i % 7) - 3) * .025 for i in range(len(changes))]
        axes[0].scatter(jitter, changes, alpha=.65, label=g)
        axes[0].plot([idx - .15, idx + .15], [statistics.mean(changes)] * 2, color="black", linewidth=3)
        axes[1].scatter([r["pre"] for r in subset], changes, alpha=.65, label=g)
    axes[0].set_xticks([0, 1], [plan.control, plan.treatment])
    axes[0].set(ylabel="Individual change (post - pre)", title="Change distributions; black line = mean")
    axes[1].set(xlabel="Baseline score", ylabel="Change", title="Baseline vs change: inspect imbalance")
    axes[1].legend()
    buf = io.StringIO()
    fig.savefig(buf, format="svg", metadata={"Date": None})
    plt.close(fig)
    return buf.getvalue()


def render_report(bundle, svg):
    esc = lambda x: html.escape(str(x))
    result = bundle["result"]
    claims = bundle["claims"]
    cards = "".join(f'<tr><td>{esc(c["id"])}</td><td>{esc(c["metric"])}</td><td>{c["expected"]:.6g}</td><td>{c["observed"]:.6g}</td><td>{c["abs_tolerance"]:.6g}</td><td class="{c["status"]}">{esc(c["status"])}</td><td><a href="#E-main">E-main</a></td></tr>' for c in claims)
    warnings = "".join("<li>" + esc(w) + "</li>" for w in result["warnings"])
    payload = esc(json.dumps(bundle, ensure_ascii=False, indent=2, allow_nan=False))
    return f'''<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(bundle['plan']['title'])}</title>
<style>body{{font:16px system-ui;background:#f2f5f8;color:#18283d;margin:0}}main{{max-width:1100px;margin:32px auto;padding:32px;background:white;border-radius:18px}}h1{{font-size:30px}}.tag{{color:#42607a}}.summary{{background:#eaf1f9;padding:22px;border-radius:12px;font-size:19px}}table{{width:100%;border-collapse:collapse}}td,th{{padding:12px;text-align:left;border-bottom:1px solid #dde4ed}}.numeric_match{{color:#167145}}.numeric_mismatch{{color:#b02b2b}}svg{{width:100%;height:auto}}pre{{overflow:auto;font-size:12px;background:#f5f7fa;padding:18px}}code{{overflow-wrap:anywhere}}li{{margin:8px 0}}a{{color:#165d9c}}</style>
<main><div class="tag">EVIDENCE WORKBENCH · B1 v{__version__} · SYNTHETIC STUDY</div><h1>{esc(bundle['plan']['title'])}</h1><p>研究主张数值复核报告 / Numeric replication audit</p>
<div class="summary">变化差异：{result['estimate']:.4g} 分（{esc(result['contrast'])}）<br>{result['confidence']:.0%} CI [{result['ci_low']:.4g}, {result['ci_high']:.4g}] · p={result['p']:.4g}<br>主张匹配 {sum(c['status']=='numeric_match' for c in claims)}/{len(claims)} · 保留样本 {result['quality']['included_n']}/{result['quality']['input_n']}</div>
<p>数值匹配仅表示预定义计算结果一致；不代表论文可信、因果成立或研究设计合格。容差由输入主张明确给出。</p>
<h2>逐条研究主张</h2><p>来源：{esc(bundle['claim_source'])}</p><table><tr><th>ID</th><th>指标</th><th>原主张</th><th>复算</th><th>绝对容差</th><th>结果</th><th>证据</th></tr>{cards}</table>
<h2>数据与诊断图</h2>{svg}<h2 id="E-main">E-main：方法、样本与证据</h2><p>{esc(result['method'])}；{esc(result['formula'])}。完整配对样本按计划过滤；独立 Welch 公式交叉检查：{esc(result['statistical_cross_check'])}。</p><p>运行 ID：<code>{esc(bundle['run_id'])}</code><br>数据 SHA-256：<code>{bundle['inputs']['data']['sha256']}</code></p>
<h2>限制与人工复核</h2><ul>{warnings}</ul><details><summary>完整机器可读证据（输入、计划、排除、环境、主张）</summary><pre>{payload}</pre></details><p>同目录 evidence.json / events.jsonl / manifest.json 提供审计与完整性核验。</p></main></html>'''


def read_input(path):
    path = Path(path).resolve(strict=True)
    if not path.is_file() or path.stat().st_size > MAX_BYTES:
        reject("resource_limit", "Input must be a file no larger than 2 MB")
    return path, path.read_bytes()


def run(data_path, plan_path, claims_path):
    start = time.perf_counter()
    inputs = {}
    for key, path in (("data", data_path), ("plan", plan_path), ("claims", claims_path)):
        resolved, raw = read_input(path)
        inputs[key] = {"path": str(resolved), "sha256": sha(raw), "bytes": len(raw)}
        inputs[key]["raw"] = raw
    plan = Plan.load(parse_json(inputs["plan"]["raw"]))
    result, rows = analyze(inputs["data"]["raw"], plan)
    claims_obj = parse_json(inputs["claims"]["raw"])
    verdicts = check_claims(claims_obj, result)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    source_hashes = {str(p.relative_to(ROOT)): sha(p.read_bytes()) for p in sorted((ROOT / "b1").glob("*.py"))}
    bundle = {"schema_version": "1", "run_id": run_id, "created_at": datetime.now(timezone.utc).isoformat(), "mode": "B1_deterministic", "plan": asdict(plan), "inputs": {k: {a: b for a, b in v.items() if a != "raw"} for k, v in inputs.items()}, "software": {"app": __version__, "python": sys.version, "platform": platform.platform(), "scipy": scipy.__version__, "source_hashes": source_hashes}, "result": result, "claim_source": claims_obj["source"], "claims": verdicts, "chart_rows": rows, "cost": {"model_calls": 0, "external_api_cost": 0}, "human_review": "pending"}
    svg = chart_svg(rows, plan)
    report = render_report(bundle, svg)
    # No output is created unless computation and rendering succeeded.
    for metadata in inputs.values():
        if sha(Path(metadata["path"]).read_bytes()) != metadata["sha256"]:
            reject("input_changed", "Input changed during execution")
    output = ROOT / "runs" / run_id
    if any(Path(m["path"]).is_relative_to(output) for m in inputs.values()):
        reject("path", "Output cannot contain inputs")
    output.mkdir(parents=True, exist_ok=False)
    bundle["elapsed_seconds"] = time.perf_counter() - start
    artifacts = {"evidence.json": encode(bundle), "report.html": report.encode("utf-8"), "diagnostics.svg": svg.encode("utf-8")}
    for key, metadata in inputs.items():
        artifacts[f"input-{key}." + ("csv" if key == "data" else "json")] = metadata["raw"]
    events = [{"stage": s, "status": "passed", "run_id": run_id} for s in ("contract", "dataset_digest", "quality", "welch_formula_check", "claims_evaluated", "chart", "inputs_unchanged")]
    artifacts["events.jsonl"] = b"\n".join(encode(e).replace(b"\n", b"") for e in events) + b"\n"
    for name, raw in artifacts.items():
        (output / name).write_bytes(raw)
    manifest = {"schema_version": "1", "run_id": run_id, "status": "complete", "artifacts": {name: sha(raw) for name, raw in artifacts.items()}}
    (output / "manifest.json").write_bytes(encode(manifest))
    return output, bundle


def verify(directory):
    directory = Path(directory).resolve(strict=True)
    manifest = parse_json((directory / "manifest.json").read_bytes())
    fields(manifest, ("schema_version", "run_id", "status", "artifacts"))
    expected_names = {"evidence.json", "report.html", "diagnostics.svg", "input-data.csv", "input-plan.json", "input-claims.json", "events.jsonl"}
    if manifest["status"] != "complete" or set(manifest["artifacts"]) != expected_names:
        reject("integrity", "Incomplete manifest")
    for name, digest in manifest["artifacts"].items():
        target = directory / name
        if target.is_symlink() or not target.is_file() or sha(target.read_bytes()) != digest:
            reject("integrity", "Artifact missing/changed: " + name)
    bundle = parse_json((directory / "evidence.json").read_bytes())
    if bundle["run_id"] != manifest["run_id"]:
        reject("integrity", "Run IDs differ")
    plan = Plan.load(parse_json((directory / "input-plan.json").read_bytes()))
    result, rows = analyze((directory / "input-data.csv").read_bytes(), plan)
    claims_obj = parse_json((directory / "input-claims.json").read_bytes())
    if parse_json(encode(asdict(plan))) != bundle["plan"] or claims_obj["source"] != bundle["claim_source"]:
        reject("integrity", "Plan or claim source lineage is inconsistent")
    for key, suffix in (("data", "csv"), ("plan", "json"), ("claims", "json")):
        if bundle["inputs"][key]["sha256"] != sha((directory / f"input-{key}.{suffix}").read_bytes()):
            reject("integrity", "Input snapshot lineage differs")
    if result != bundle["result"] or rows != bundle["chart_rows"] or check_claims(claims_obj, result) != bundle["claims"]:
        reject("integrity", "Recomputed evidence does not match saved evidence")
    return {"status": "verified", "run_id": manifest["run_id"], "artifacts_checked": len(expected_names), "numeric_replay": "passed", "note": "Local hash integrity, not a cryptographic signature or independent scientific review"}

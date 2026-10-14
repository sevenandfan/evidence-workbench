"""One command produces B0/B1/external-gold comparison and immutable evidence."""
import html
import json
import platform
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from b1.engine import encode, sha
from b1.summary import execute
from scripts.baseline_manylabs import run as baseline
from b1 import __version__
import scipy

PREPARED = ROOT / "data/manylabs1/sunk-costs-v1"


def main():
    raw = {name: (PREPARED / name).read_bytes() for name in ("dataset.json", "gold.json", "plan.json")}
    before = {name: sha(data) for name, data in raw.items()}
    dataset, gold, plan = (json.loads(raw[n]) for n in ("dataset.json", "gold.json", "plan.json"))
    source_root = ROOT / "data/manylabs1/upstream" / plan["upstream"]["commit"]
    for name, record in plan["upstream"]["files"].items():
        if sha((source_root / name).read_bytes()) != record["sha256"]:
            raise ValueError("Upstream source hash mismatch: " + name)
    started = time.perf_counter()
    b0 = baseline(dataset["samples"])
    b0_seconds = time.perf_counter() - started
    started = time.perf_counter()
    b1 = execute(raw["dataset.json"], raw["gold.json"], raw["plan.json"])
    b1_seconds = time.perf_counter() - started
    reference = {r["site"]: r for r in gold["rows"]}
    b0_passed, pair_passed, comparisons = 0, 0, []
    for r0, r1 in zip(b0, b1["rows"]):
        if r0["site"] != r1["site"]:
            raise ValueError("B0/B1 site ordering differs")
        ref = reference[r0["site"]]
        checks = []
        for metric in ("effect", "ci_low", "ci_high"):
            tolerance = plan["effect_tolerance"] if metric == "effect" else plan["ci_tolerance"]
            e0, pair = abs(r0[metric]-ref[metric]), abs(r0[metric]-r1[metric])
            b0_passed += int(e0 <= tolerance)
            pair_passed += int(pair <= tolerance)
            checks.append({"metric": metric, "B0": r0[metric], "B1": r1[metric], "author": ref[metric], "B0_author_error": e0, "B1_author_error": abs(r1[metric]-ref[metric]), "B0_B1_error": pair, "tolerance": tolerance})
        comparisons.append({"site": r0["site"], "input_cells": r1["source_range"], "reference_cells": r1["reference_range"], "confidence": r1["confidence"], "evidence_id": r1["evidence_id"], "checks": checks})
    total = len(comparisons)*3
    # Fail visibly, but still emit comparison evidence for analysis.
    all_passed = b0_passed == total and pair_passed == total and b1["checks_passed"] == total
    for name, digest in before.items():
        if sha((PREPARED/name).read_bytes()) != digest:
            raise ValueError("Input changed: " + name)
    for name, record in plan["upstream"]["files"].items():
        if sha((source_root / name).read_bytes()) != record["sha256"]:
            raise ValueError("Upstream changed: " + name)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-manylabs-" + uuid.uuid4().hex[:8]
    output = ROOT / "runs" / run_id
    output.mkdir(parents=True, exist_ok=False)
    summary = {"run_id": run_id, "study": "Many Labs 1 — Sunk Costs", "level": "real author aggregate statistics", "sites": len(comparisons)-1, "aggregate_rows": 1, "participant_n_overall": dataset["samples"][0]["n_paid"]+dataset["samples"][0]["n_free"], "status": "passed" if all_passed else "mismatch", "B0_author_checks_passed": b0_passed, "B1_author_checks_passed": b1["checks_passed"], "B0_B1_checks_passed": pair_passed, "checks_per_comparison": total, "B0_seconds": b0_seconds, "B1_seconds": b1_seconds, "timing_scope": "Calculation-only single local run; extraction/report/startup not included; not user productivity", "max_B1_author_error": max(c["B1_author_error"] for r in comparisons for c in r["checks"]), "source_commit": plan["upstream"]["commit"], "inputs_unchanged": True, "model_calls": 0, "external_api_cost": 0, "native_author_R_run": False, "B0_definition": "Independent mathematical reference, not native R; SciPy noncentral t distribution shared with B1", "python": sys.version, "scipy": scipy.__version__, "platform": platform.platform(), "input_hashes": before, "code_hashes": {str(p.relative_to(ROOT)): sha(p.read_bytes()) for p in (ROOT/"b1/summary.py", ROOT/"scripts/baseline_manylabs.py", ROOT/"scripts/compare_manylabs.py", ROOT/"scripts/prepare_manylabs.py")}}
    summary["app_version"] = __version__
    summary["code_hashes"].update({str(p.relative_to(ROOT)): sha(p.read_bytes()) for p in sorted((ROOT/"b1").glob("*.py"))})
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    rsites = b1["rows"][1:]
    fig, ax = plt.subplots(figsize=(10, 11))
    y = list(range(len(rsites)))
    ax.errorbar([r["effect"] for r in rsites], y, xerr=[[r["effect"]-r["ci_low"] for r in rsites], [r["ci_high"]-r["effect"] for r in rsites]], fmt="o", color="#2463a5", capsize=3, markersize=4)
    ax.set_yticks(y, [r["site"] for r in rsites])
    ax.invert_yaxis()
    ax.axvline(0, color="#aaaaaa", linewidth=1)
    ax.set(xlabel="Corrected SMD; author-compatible noncentral CI", title="Many Labs 1: Sunk Costs — 36 sites (95%; N>1000: 99%)")
    fig.tight_layout()
    fig.savefig(output / "forest.png", dpi=140)
    plt.close(fig)
    escape = lambda v: html.escape(str(v))
    body = ""
    for row, r1 in zip(comparisons, b1["rows"]):
        cs = {c["metric"]: c for c in row["checks"]}
        body += f'<tr id="{row["evidence_id"]}"><td>{escape(row["site"])}</td><td>{r1["n_paid"]}/{r1["n_free"]}</td><td>{cs["effect"]["author"]:.9f}</td><td>{cs["effect"]["B0"]:.9f}</td><td>{cs["effect"]["B1"]:.9f}</td><td>[{r1["ci_low"]:.6f}, {r1["ci_high"]:.6f}] ({row["confidence"]:.0%})</td><td>{max(c["B1_author_error"] for c in row["checks"]):.2g}</td><td>{escape(row["input_cells"])}<br>{escape(row["reference_cells"])}</td></tr>'
    text = f'''<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Many Labs 1 真实数据复算</title><style>body{{font:15px system-ui;background:#f2f5f8;color:#18283d}}main{{max-width:1250px;margin:24px auto;background:white;padding:28px;border-radius:16px}}table{{width:100%;border-collapse:collapse;font-size:13px}}th,td{{padding:9px;border-bottom:1px solid #ddd;text-align:left}}.scroller{{overflow:auto}}img{{max-width:850px;width:100%}}pre{{overflow:auto;background:#f5f7fa;padding:15px}}</style><main><h1>Many Labs 1：沉没成本研究结果复算</h1><p>来源：作者真实汇总统计；36 个站点 + Overall 汇总行。纳入总体 n={summary['participant_n_overall']}。数据 CC0，作者代码 GPL-3.0。</p><p>B0 对作者表：{b0_passed}/{total}；B1 对作者表：{b1['checks_passed']}/{total}；B0 对 B1：{pair_passed}/{total}。状态：{summary['status']}。</p><p>每行核对点估计和两个区间端点。37 行共享同一个实验，不等于 37 个独立研究或盲测任务。计算方向 Paid−Free。</p><h2>方法审查</h2><p>作者脚本注释称 Cohen d，但 metafor SMD 默认小样本修正得到 Hedges g。本报告同时保留原始 d 与修正 g；为忠实复算，区间继续采用作者将 g 传入 MBESS ci.smd 的历史路径。不是新研究的方法推荐。n&gt;1000 时为99%区间，其他95%；不能偷偷统一改为95%。</p><p>B0 为独立数学参考实现，作者发布的 XLS 数值为外部答案；本机未运行完整 R 脚本。B0/B1共享 SciPy 非中心 t 分布，两者不是完全独立的软件生态。汇总统计不足以检查原始样本清洗、配对或个体异常。</p><div class="scroller"><table><tr><th>站点</th><th>Paid/Free n</th><th>作者 g</th><th>B0 g</th><th>B1 g</th><th>B1区间</th><th>最大误差</th><th>原表单元格</th></tr>{body}</table></div><h2>站点效应与区间</h2><img src="forest.png" alt="36个站点的作者兼容区间"><h2>证据与复现</h2><p>Run ID：{run_id}<br>固定 commit：{summary['source_commit']}<br>同目录 comparison.json、b0.json、b1.json、输入快照和 manifest.json 可回查。</p><pre>{escape(json.dumps(summary,ensure_ascii=False,indent=2))}</pre></main></html>'''
    events = [{"stage": stage, "status": "passed", "run_id": run_id} for stage in ("upstream_hashes", "plan_contract", "source_binding", "B0_calculation", "B1_calculation", "external_gold_comparison", "input_hashes_unchanged", "chart_and_report")]
    artifacts = {"summary.json": encode(summary), "comparison.json": encode(comparisons), "b0.json": encode(b0), "b1.json": encode(b1), "report.html": text.encode("utf-8"), "events.jsonl": b"\n".join(json.dumps(e).encode() for e in events)+b"\n", **raw}
    for name, data in artifacts.items():
        (output/name).write_bytes(data)
    manifest = {"run_id": run_id, "status": "complete", "experiment_status": summary["status"], "files": {name: sha(data) for name, data in artifacts.items()}}
    manifest["files"]["forest.png"] = sha((output/"forest.png").read_bytes())
    (output/"manifest.json").write_bytes(encode(manifest))
    print(json.dumps({"output": str(output), **summary}, ensure_ascii=True, indent=2))
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

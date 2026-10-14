"""Fixed, read-only effect-size workflow for author-provided summary statistics.

Formula compatibility with the archived Many Labs script is explicit:
metafor SMD (corrected g) -> MBESS ci.smd(g), with 99% CI when N>1000.
This historical CI pathway is reproduced as published, not endorsed as the
preferred modern confidence interval for Hedges g.
"""
from __future__ import annotations

import math

from scipy.optimize import brentq
from scipy.special import gammaln
from scipy.stats import nct

from .engine import fields, number, parse_json, reject, sha

PLAN_KEYS = ("schema_version", "method", "contrast", "dataset_sha256", "gold_sha256", "effect_tolerance", "ci_tolerance", "confidence_rule", "excluded_summary_rows", "upstream")
SAMPLE_KEYS = ("site", "n_paid", "n_free", "excluded_or_no_response", "mean_paid", "mean_free", "sd_paid", "sd_free", "source_range")


def calculate(sample):
    fields(sample, SAMPLE_KEYS)
    if not isinstance(sample["site"], str) or not sample["site"]:
        reject("contract", "Nonempty site label required")
    if not isinstance(sample["source_range"], str) or not sample["source_range"]:
        reject("lineage", "Input source cell range required")
    for key in ("n_paid", "n_free"):
        if type(sample[key]) is not int or not 2 <= sample[key] <= 100_000:
            reject("sample_size", "Integer n in [2,100000] required")
    if type(sample["excluded_or_no_response"]) is not int or sample["excluded_or_no_response"] < 0:
        reject("contract", "Nonnegative exclusion count required")
    for key in ("mean_paid", "mean_free", "sd_paid", "sd_free"):
        if not number(sample[key]) or abs(sample[key]) > 1_000_000:
            reject("numeric", "Finite, bounded summary statistic required")
    if sample["sd_paid"] <= 0 or sample["sd_free"] <= 0:
        reject("zero_variance", "Positive SDs required")
    n1, n2 = sample["n_paid"], sample["n_free"]
    df = n1 + n2 - 2
    pooled = math.sqrt(((n1 - 1) * sample["sd_paid"]**2 + (n2 - 1) * sample["sd_free"]**2) / df)
    raw_d = (sample["mean_paid"] - sample["mean_free"]) / pooled
    correction = float(math.exp(gammaln(df / 2) - .5 * math.log(df / 2) - gammaln((df - 1) / 2)))
    effect = correction * raw_d
    scale = math.sqrt(1 / n1 + 1 / n2)
    observed_t = effect / scale
    confidence = .99 if n1 + n2 > 1000 else .95
    tail = (1 - confidence) / 2

    def invert(probability):
        def residual(ncp):
            value = float(nct.cdf(observed_t, df, ncp)) - probability
            if not math.isfinite(value):
                reject("numerical", "Noncentral t calculation failed")
            return value
        width = 10.0
        for _ in range(12):
            lower, upper = observed_t - width, observed_t + width
            if residual(lower) >= 0 and residual(upper) <= 0:
                return float(brentq(residual, lower, upper, xtol=1e-10)) * scale
            width *= 2
        reject("numerical", "Noncentral interval did not bracket")

    return {"site": sample["site"], "n_paid": n1, "n_free": n2, "df": df,
            "pooled_sd": pooled, "cohens_d_unadjusted": raw_d, "J": correction,
            "effect": effect, "effect_definition": "Hedges g (exact J correction), matching metafor SMD default",
            "confidence": confidence, "ci_low": invert(1-tail), "ci_high": invert(tail),
            "ci_definition": "Historical author pathway: MBESS ci.smd(smd=g); do not relabel as a modern g CI",
            "source_range": sample["source_range"]}


def execute(dataset_raw, gold_raw, plan_raw):
    plan = parse_json(plan_raw)
    fields(plan, PLAN_KEYS)
    if plan["schema_version"] != "manylabs_plan_v1" or plan["method"] != "author_smd_nct":
        reject("unsupported_method", "Only reviewed author_smd_nct supported")
    if plan["contrast"] != "Paid minus Free" or plan["confidence_rule"] != "n_total > 1000 => 0.99; else 0.95":
        reject("contract", "Direction and author confidence rule are fixed")
    if sha(dataset_raw) != plan["dataset_sha256"] or sha(gold_raw) != plan["gold_sha256"]:
        reject("dataset_version", "Dataset/gold hash differs from approved plan")
    for key, upper in (("effect_tolerance", 1e-7), ("ci_tolerance", 1e-6)):
        if not number(plan[key]) or not 0 <= plan[key] <= upper:
            reject("contract", "Tolerance exceeds prespecified maximum")
    dataset, gold = parse_json(dataset_raw), parse_json(gold_raw)
    fields(dataset, ("schema_version", "study", "synthetic", "level", "upstream_commit", "samples"))
    fields(gold, ("schema_version", "origin", "upstream_commit", "rows"))
    if dataset["schema_version"] != "manylabs_summary_v1" or gold["schema_version"] != "manylabs_gold_v1":
        reject("contract", "Unknown table schema")
    if dataset["synthetic"] is not False or dataset["level"] != "author_aggregate":
        reject("contract", "Expected explicitly identified real aggregate data")
    if dataset["upstream_commit"] != gold["upstream_commit"] or dataset["upstream_commit"] != plan["upstream"]["commit"]:
        reject("dataset_version", "Source versions differ")
    if not isinstance(dataset["samples"], list) or not 1 <= len(dataset["samples"]) <= 500 or not isinstance(gold["rows"], list):
        reject("resource_limit", "Invalid or excessive table size")
    reference = {}
    for row in gold["rows"]:
        fields(row, ("site", "effect", "ci_low", "ci_high", "source_range"))
        if not isinstance(row["site"], str) or row["site"] in reference:
            reject("duplicate_id", "Duplicate/invalid gold site")
        if any(not number(row[k]) for k in ("effect", "ci_low", "ci_high")) or row["ci_low"] > row["ci_high"]:
            reject("numeric", "Invalid gold values")
        reference[row["site"]] = row
    results, seen = [], set()
    for sample in dataset["samples"]:
        result = calculate(sample)
        site = result["site"]
        if site in seen:
            reject("duplicate_id", "Duplicate input site")
        if site not in reference:
            reject("lineage", "Missing author reference row")
        seen.add(site)
        ref = reference[site]
        checks = []
        for metric in ("effect", "ci_low", "ci_high"):
            tolerance = plan["effect_tolerance"] if metric == "effect" else plan["ci_tolerance"]
            error = abs(result[metric] - ref[metric])
            checks.append({"metric": metric, "observed": result[metric], "expected": ref[metric], "error": error, "tolerance": tolerance, "passed": error <= tolerance})
        result.update({"evidence_id": "E-" + str(len(results)+1), "reference_range": ref["source_range"], "checks": checks, "passed": all(c["passed"] for c in checks)})
        results.append(result)
    if seen != set(reference):
        reject("lineage", "Reference/input sites differ")
    return {"method": plan["method"], "plan": plan, "rows": results, "rows_passed": sum(r["passed"] for r in results), "rows_total": len(results), "checks_passed": sum(c["passed"] for r in results for c in r["checks"]), "checks_total": len(results)*3}

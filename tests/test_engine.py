"""Regression suite. Fixed development tests, NOT an unseen held-out benchmark."""
import copy
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from b1.engine import ROOT, Plan, Rejected, analyze, check_claims, encode, load_rows, parse_json, render_report, run, sha, verify


BASE_RAW = (ROOT / "examples/study.csv").read_bytes()
BASE_PLAN = parse_json((ROOT / "examples/plan.json").read_bytes())
BASE_CLAIMS = parse_json((ROOT / "examples/claims.json").read_bytes())


def plan_for(raw=BASE_RAW, **changes):
    return Plan.load({**BASE_PLAN, "dataset_sha256": sha(raw), **changes})


class EngineTests(unittest.TestCase):
    def rejected(self, code, func, *args):
        with self.assertRaises(Rejected) as ctx:
            func(*args)
        self.assertEqual(ctx.exception.code, code)

    def test_01_hand_calculated_oracle(self):
        r, rows = analyze(BASE_RAW, plan_for())
        self.assertEqual(r["estimate"], 3)
        self.assertEqual(r["df"], 10)
        self.assertAlmostEqual(r["standard_error"], math.sqrt(11 / 30), places=12)
        self.assertAlmostEqual(r["t"], 3 / math.sqrt(11 / 30), places=12)
        critical = 2.2281388519649385
        self.assertAlmostEqual(r["ci_low"], 3 - critical * math.sqrt(11 / 30), places=10)
        self.assertAlmostEqual(r["ci_high"], 3 + critical * math.sqrt(11 / 30), places=10)
        self.assertAlmostEqual(r["cohens_d_change"], 3 / math.sqrt(1.1), places=12)
        self.assertEqual(r["quality"]["included_n"], 12)
        self.assertEqual({e["reason"] for e in r["quality"]["excluded"]}, {"plan_exclusion", "incomplete_pair"})
        self.assertEqual(len(rows), 12)

    def test_02_claim_mismatch(self):
        r, _ = analyze(BASE_RAW, plan_for())
        self.assertEqual([v["status"] for v in check_claims(BASE_CLAIMS, r)], ["numeric_match", "numeric_match", "numeric_mismatch"])

    def test_03_wrong_hash(self):
        self.rejected("dataset_version", analyze, BASE_RAW + b"\n", plan_for())

    def test_04_unknown_method(self):
        self.rejected("unsupported_method", Plan.load, {**BASE_PLAN, "method": "auto_find_significance"})

    def test_05_arbitrary_code_rejected(self):
        self.rejected("contract", Plan.load, {**BASE_PLAN, "python": "import os"})

    def test_06_sql_rejected(self):
        self.rejected("contract", Plan.load, {**BASE_PLAN, "sql": "DROP TABLE research"})

    def test_07_write_action_rejected(self):
        self.rejected("contract", Plan.load, {**BASE_PLAN, "write_path": "study.csv"})

    def test_08_missing_plan_field(self):
        p = dict(BASE_PLAN)
        del p["missing"]
        self.rejected("contract", Plan.load, p)

    def test_09_strict_numeric_type(self):
        self.rejected("contract", Plan.load, {**BASE_PLAN, "confidence": "0.95"})
        self.rejected("contract", Plan.load, {**BASE_PLAN, "confidence": True})

    def test_10_json_parse(self):
        self.rejected("json_parse", parse_json, b'{"a":1,}')

    def test_11_duplicate_json_key(self):
        self.rejected("contract", parse_json, b'{"a":1,"a":2}')

    def test_12_nonfinite_json(self):
        self.rejected("contract", parse_json, b'{"a":NaN}')

    def test_13_missing_column(self):
        raw = BASE_RAW.replace(b"participant_id,group,pre,post", b"participant_id,group,pre")
        self.rejected("data_schema", analyze, raw, plan_for(raw))

    def test_14_duplicate_participant(self):
        raw = BASE_RAW.replace(b"C02,", b"C01,")
        self.rejected("duplicate_id", analyze, raw, plan_for(raw))

    def test_15_unknown_group(self):
        raw = BASE_RAW.replace(b"C01,control", b"C01,other")
        self.rejected("group", analyze, raw, plan_for(raw))

    def test_16_invalid_number(self):
        raw = BASE_RAW.replace(b"40,41", b"40,bad")
        self.rejected("numeric", analyze, raw, plan_for(raw))

    def test_17_nonfinite_number(self):
        raw = BASE_RAW.replace(b"40,41", b"40,nan")
        self.rejected("range", analyze, raw, plan_for(raw))

    def test_18_range(self):
        raw = BASE_RAW.replace(b"40,41", b"40,101")
        self.rejected("range", analyze, raw, plan_for(raw))

    def test_19_missing_reject(self):
        self.rejected("missing", analyze, BASE_RAW, plan_for(missing="reject"))

    def test_20_unknown_exclusion(self):
        self.rejected("exclusion", analyze, BASE_RAW, plan_for(exclude_ids=["absent"]))

    def test_21_constant_change(self):
        raw = b'participant_id,group,pre,post\na,control,1,2\nb,control,2,3\nc,control,3,4\nd,treatment,1,2\ne,treatment,2,4\nf,treatment,3,6\n'
        self.rejected("zero_variance", analyze, raw, plan_for(raw, exclude_ids=[]))

    def test_22_small_sample(self):
        raw = b'participant_id,group,pre,post\na,control,1,2\nb,treatment,2,3\n'
        self.rejected("sample_size", analyze, raw, plan_for(raw, exclude_ids=[]))

    def test_23_ragged_csv(self):
        raw = BASE_RAW.replace(b"40,41", b"40,41,extra")
        self.rejected("data_schema", analyze, raw, plan_for(raw))

    def test_24_invalid_claim(self):
        r, _ = analyze(BASE_RAW, plan_for())
        obj = copy.deepcopy(BASE_CLAIMS)
        obj["claims"][0]["metric"] = "cause"
        self.rejected("claims", check_claims, obj, r)

    def test_25_duplicate_claim(self):
        r, _ = analyze(BASE_RAW, plan_for())
        obj = copy.deepcopy(BASE_CLAIMS)
        obj["claims"].append(obj["claims"][0])
        self.rejected("claims", check_claims, obj, r)

    def test_26_negative_tolerance(self):
        r, _ = analyze(BASE_RAW, plan_for())
        obj = copy.deepcopy(BASE_CLAIMS)
        obj["claims"][0]["abs_tolerance"] = -1
        self.rejected("claims", check_claims, obj, r)

    def test_27_row_order_invariant(self):
        lines = BASE_RAW.decode().splitlines()
        raw = (lines[0] + "\n" + "\n".join(reversed(lines[1:])) + "\n").encode()
        original, _ = analyze(BASE_RAW, plan_for())
        reordered, _ = analyze(raw, plan_for(raw))
        for metric in ("estimate", "p", "ci_low", "ci_high"):
            self.assertAlmostEqual(original[metric], reordered[metric], places=12)

    def test_28_reversed_contrast(self):
        original, _ = analyze(BASE_RAW, plan_for())
        reverse, _ = analyze(BASE_RAW, plan_for(control="treatment", treatment="control"))
        self.assertEqual(reverse["estimate"], -original["estimate"])
        self.assertEqual(reverse["p"], original["p"])
        self.assertAlmostEqual(reverse["ci_low"], -original["ci_high"])

    def test_29_unequal_variance_df(self):
        raw = BASE_RAW.replace(b"T04,treatment,55,62", b"T04,treatment,55,70")
        r, _ = analyze(raw, plan_for(raw))
        # [4,5,6,15,5,6] has sum 41 and sum of squares 363:
        # sample variance=(363-41**2/6)/5=16.566666...
        va, vb, n = (363 - 41**2/6)/5, 1.1, 6
        self.assertAlmostEqual(r["df"], (va/n+vb/n)**2 / ((va/n)**2/5+(vb/n)**2/5), places=9)

    def test_30_end_to_end_replay_and_tamper(self):
        with tempfile.TemporaryDirectory() as tmp, patch("b1.engine.ROOT", Path(tmp)):
            # Use real immutable examples; only derived output goes to temporary run directory.
            paths = [ROOT / "examples" / n for n in ("study.csv", "plan.json", "claims.json")]
            before = [sha(p.read_bytes()) for p in paths]
            output, bundle = run(*paths)
            self.assertEqual(verify(output)["numeric_replay"], "passed")
            self.assertEqual(before, [sha(p.read_bytes()) for p in paths])
            self.assertEqual(bundle["cost"]["model_calls"], 0)
            self.assertTrue(all(v["evidence_id"] == "E-main" for v in bundle["claims"]))
            (output / "report.html").write_text("tampered", encoding="utf-8")
            self.rejected("integrity", verify, output)

    def test_31_render_failure_no_success_output(self):
        with tempfile.TemporaryDirectory() as tmp, patch("b1.engine.ROOT", Path(tmp)), patch("b1.engine.chart_svg", side_effect=OSError("simulated chart failure")):
            with self.assertRaises(OSError):
                run(*(ROOT / "examples" / n for n in ("study.csv", "plan.json", "claims.json")))
            self.assertFalse((Path(tmp) / "runs").exists())

    def test_32_resource_limit(self):
        self.rejected("resource_limit", load_rows, b"x" * 2_000_001, plan_for())

    def test_33_report_escapes_untrusted_text(self):
        with tempfile.TemporaryDirectory() as tmp, patch("b1.engine.ROOT", Path(tmp)):
            output, bundle = run(*(ROOT / "examples" / n for n in ("study.csv", "plan.json", "claims.json")))
            bundle["plan"]["title"] = "<script>alert(1)</script>"
            text = render_report(bundle, "<svg></svg>")
            self.assertNotIn("<script>", text)
            self.assertIn("&lt;script&gt;", text)

    def test_34_extreme_score_range(self):
        self.rejected("contract", Plan.load, {**BASE_PLAN, "score_max": 1e300})

    def test_35_snapshot_lineage_consistency(self):
        with tempfile.TemporaryDirectory() as tmp, patch("b1.engine.ROOT", Path(tmp)):
            output, bundle = run(*(ROOT / "examples" / n for n in ("study.csv", "plan.json", "claims.json")))
            bundle["claim_source"] = "different manuscript"
            (output / "evidence.json").write_bytes(encode(bundle))
            manifest = parse_json((output / "manifest.json").read_bytes())
            manifest["artifacts"]["evidence.json"] = sha(encode(bundle))
            (output / "manifest.json").write_bytes(encode(manifest))
            self.rejected("integrity", verify, output)


if __name__ == "__main__":
    unittest.main()

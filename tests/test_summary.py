"""Real-data compatibility tests and contract failure cases; not held-out tasks."""
import copy
import json
import sys
import unittest

from b1.engine import ROOT, Rejected, encode, sha
from b1.summary import calculate, execute

SLICE = ROOT / "data/manylabs1/sunk-costs-v1"
AVAILABLE = SLICE.is_dir()
if AVAILABLE:
    DATA = (SLICE / "dataset.json").read_bytes()
    GOLD = (SLICE / "gold.json").read_bytes()
    PLAN = (SLICE / "plan.json").read_bytes()


@unittest.skipUnless(AVAILABLE, "Run acquire_manylabs.py + prepare_manylabs.py for real-data tests")
class SummaryTests(unittest.TestCase):
    def setUp(self):
        self.data, self.gold, self.plan = map(json.loads, (DATA, GOLD, PLAN))

    def payload(self):
        data, gold = encode(self.data), encode(self.gold)
        self.plan["dataset_sha256"], self.plan["gold_sha256"] = sha(data), sha(gold)
        return data, gold, encode(self.plan)

    def rejected(self, code, *args):
        with self.assertRaises(Rejected) as ctx:
            execute(*args)
        self.assertEqual(ctx.exception.code, code)

    def test_01_all_external_gold(self):
        result = execute(DATA, GOLD, PLAN)
        self.assertEqual(result["rows_total"], 37)
        self.assertEqual(result["checks_passed"], 111)
        self.assertEqual(result["rows"][0]["n_paid"] + result["rows"][0]["n_free"], 6330)

    def test_02_confidence_rule(self):
        result = execute(DATA, GOLD, PLAN)
        self.assertEqual(result["rows"][0]["confidence"], .99)
        self.assertEqual(result["rows"][1]["confidence"], .95)
        self.assertEqual(next(r for r in result["rows"] if r["site"] == "pi")["confidence"], .99)

    def test_03_independent_b0(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        from baseline_manylabs import run
        b0 = run(self.data["samples"])
        b1 = execute(DATA, GOLD, PLAN)["rows"]
        for a, b in zip(b0, b1):
            for key in ("effect", "ci_low", "ci_high"):
                self.assertAlmostEqual(a[key], b[key], places=7)

    def test_04_reference_corruption_detected(self):
        self.gold["rows"][0]["effect"] += .1
        result = execute(*self.payload())
        self.assertFalse(result["rows"][0]["passed"])
        self.assertEqual(result["checks_passed"], 110)

    def test_05_hash_mismatch(self):
        self.rejected("dataset_version", DATA+b" ", GOLD, PLAN)

    def test_06_unknown_action(self):
        self.plan["python"] = "print('unreviewed')"
        self.rejected("contract", *self.payload())

    def test_07_method_mismatch(self):
        self.plan["method"] = "welch_change"
        self.rejected("unsupported_method", *self.payload())

    def test_08_direction_mismatch(self):
        self.plan["contrast"] = "Free minus Paid"
        self.rejected("contract", *self.payload())

    def test_09_invalid_n(self):
        self.data["samples"][0]["n_paid"] = 90.0833333
        self.rejected("sample_size", *self.payload())

    def test_10_zero_sd(self):
        self.data["samples"][0]["sd_paid"] = 0
        self.rejected("zero_variance", *self.payload())

    def test_11_missing_gold(self):
        self.gold["rows"].pop(0)
        self.rejected("lineage", *self.payload())

    def test_12_duplicate_site(self):
        self.data["samples"].append(self.data["samples"][0])
        self.rejected("duplicate_id", *self.payload())

    def test_13_tolerance_relaxation(self):
        self.plan["effect_tolerance"] = .1
        self.rejected("contract", *self.payload())

    def test_14_source_commit_mismatch(self):
        self.gold["upstream_commit"] = "wrong"
        self.rejected("dataset_version", *self.payload())

    def test_15_non_synthetic_marker(self):
        self.data["synthetic"] = True
        self.rejected("contract", *self.payload())

    def test_16_effect_size_distinction(self):
        overall = calculate(self.data["samples"][0])
        self.assertLess(overall["effect"], overall["cohens_d_unadjusted"])
        self.assertAlmostEqual(overall["effect"], overall["J"] * overall["cohens_d_unadjusted"], places=12)

    def test_17_site_counts_reconcile(self):
        overall, sites = self.data["samples"][0], self.data["samples"][1:]
        for key in ("n_paid", "n_free", "excluded_or_no_response"):
            self.assertEqual(overall[key], sum(r[key] for r in sites))


if __name__ == "__main__":
    unittest.main()

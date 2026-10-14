"""Full-chain regression and faults; not independent research tasks."""
import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from b1.end_to_end import ARTIFACTS, DIRECTORY, compare, rebuild, run, verify
from b1.summary import calculate


class RebuildTests(unittest.TestCase):
    def setUp(self):
        self.mapping = {i: "abington" if i == 1 else f"s{i}" for i in range(1, 37)}
        self.records = []
        for site in self.mapping:
            for condition, values in (("0", (4, 6)), ("1", (2, 4))):
                for value in values:
                    self.records.append((len(self.records)+2, {"sample": str(site), "sunkgroup": condition, "sunkDV": str(value), "sunkcosta": str(value) if condition == "0" else "", "sunkcostb": str(value) if condition == "1" else ""}))
        metrics = {"n_paid": 2, "n_free": 2, "excluded_or_no_response": 0, "mean_paid": 5., "mean_free": 3., "sd_paid": 2**.5, "sd_free": 2**.5, "source_range": "test-only"}
        self.reference = {name: copy.deepcopy(metrics) for name in self.mapping.values()}
        self.reference["Overall:"] = dict(metrics, n_paid=72, n_free=72, sd_paid=(72/71)**.5, sd_free=(72/71)**.5)

    def test_author_means_not_used_as_inputs(self):
        self.reference["abington"]["mean_paid"] = 99.
        samples, lineage = rebuild(self.records, self.mapping, self.reference)
        self.assertEqual(next(r for r in samples if r["site"] == "abington")["mean_paid"], 5.)
        self.assertTrue(any(not c["passed"] for c in lineage["descriptive_checks"]))

    def test_incomplete_map_rejected(self):
        self.mapping.pop(36)
        with self.assertRaises(ValueError): rebuild(self.records, self.mapping, self.reference)

    def test_wrong_reference_identity_rejected(self):
        self.reference["wrong"] = self.reference.pop("s36")
        with self.assertRaises(ValueError): rebuild(self.records, self.mapping, self.reference)

    def test_wrong_gold_is_mismatch(self):
        samples, _ = rebuild(self.records, self.mapping, self.reference)
        sample = samples[0]
        result = calculate(sample)
        gold = {"rows": [{"site": sample["site"], "effect": result["effect"]+1, "ci_low": result["ci_low"], "ci_high": result["ci_high"], "source_range": "test-only"}]}
        _, _, checks = compare([sample], gold, {"effect_tolerance": 1e-7, "ci_tolerance": 1e-6})
        self.assertEqual(sum(not c["passed"] for c in checks), 2)


@unittest.skipUnless((DIRECTORY/"labels_source_manifest.json").exists(), "Participant snapshots required")
class FullChainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = run()
        cls.directory = Path(cls.result["output"])

    def test_real_chain_and_readonly_replay(self):
        self.assertEqual(self.result["descriptive_passed"], 259)
        self.assertEqual(self.result["effect_checks_passed"], {"B0_author": 111, "B1_author": 111, "B0_B1": 111})
        self.assertEqual(verify(self.directory)["status"], "verified")

    def copied(self, temporary):
        target = Path(temporary)
        for name in ARTIFACTS | {"manifest.json"}:
            shutil.copy2(self.directory/name, target/name)
        return target

    def test_changed_derived_data_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = self.copied(temporary)
            with (target/"dataset.json").open("ab") as handle: handle.write(b" ")
            with self.assertRaisesRegex(ValueError, "Artifact changed"): verify(target)

    def test_missing_artifact_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = self.copied(temporary)
            (target/"comparison.json").unlink()
            with self.assertRaises(OSError): verify(target)

    def test_manifest_extra_key_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = self.copied(temporary)
            manifest = json.loads((target/"manifest.json").read_bytes())
            manifest["files"]["../unreviewed"] = "0"*64
            (target/"manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "artifact set"): verify(target)

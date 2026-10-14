import unittest
from b1.engine import Rejected
from b1.participants import reaggregate


def record(line, arm, score, site=1):
    row = {"sample": str(site), "sunkgroup": str(arm), "sunkDV": str(score), "sunkcosta": str(score) if arm==0 else ".", "sunkcostb": str(score) if arm==1 else "."}
    return line, row


class ParticipantTests(unittest.TestCase):
    def setUp(self):
        self.rows = [record(2,0,7),record(3,0,9),record(4,1,5),record(5,1,7)]

    def test_declared_descriptive_statistics(self):
        stats, _ = reaggregate(self.rows)
        self.assertEqual(stats[0]["n_paid"],2)
        self.assertEqual(stats[0]["mean_paid"],8)
        self.assertAlmostEqual(stats[0]["sd_paid"],2**.5)

    def test_missing_response_recorded(self):
        self.rows.append(record(6,0,"."))
        stats, rows = reaggregate(self.rows)
        self.assertEqual(stats[0]["excluded_or_no_response"],1)
        self.assertEqual(rows[-1]["exclusion_reason"],"missing_response")

    def test_condition_mapping_not_tuned_to_gold(self):
        self.rows[0][1]["sunkgroup"]="1"
        with self.assertRaises(Rejected) as ctx:
            reaggregate(self.rows)
        self.assertEqual(ctx.exception.code,"mapping")

    def test_bad_scale_rejected(self):
        self.rows.append(record(6,0,10))
        with self.assertRaises(Rejected) as ctx:
            reaggregate(self.rows)
        self.assertEqual(ctx.exception.code,"range")

    def test_unmapped_site_rejected(self):
        self.rows[0][1]["sample"]="99"
        with self.assertRaises(Rejected) as ctx:
            reaggregate(self.rows)
        self.assertEqual(ctx.exception.code,"group")

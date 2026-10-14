import unittest
from b1.site_mapping import resolve_site_map


def row(key="a", sample="1", condition="0", dv="9"):
    return {"session_id": key, "sample": sample, "sunkgroup": condition, "sunkDV": dv, "sunkcosta": dv, "sunkcostb": " "}


class SiteMappingTests(unittest.TestCase):
    def setUp(self):
        self.values = [row(), row("b", "2")]
        self.labels = [row("b", "other", "paid"), row("a", "abington", "paid")]

    def test_matches_ids_not_order(self):
        self.assertEqual(resolve_site_map(self.values, self.labels), {1: "abington", 2: "other"})

    def test_duplicate_id(self):
        self.labels[0]["session_id"] = "a"
        with self.assertRaises(ValueError): resolve_site_map(self.values, self.labels)

    def test_response_drift(self):
        self.labels[0]["sunkDV"] = "8"
        with self.assertRaises(ValueError): resolve_site_map(self.values, self.labels)

    def test_condition_drift(self):
        self.labels[0]["sunkgroup"] = "free"
        with self.assertRaises(ValueError): resolve_site_map(self.values, self.labels)

    def test_unmatched_id(self):
        self.labels[0]["session_id"] = "c"
        with self.assertRaises(ValueError): resolve_site_map(self.values, self.labels)

    def test_many_to_one(self):
        self.labels[0]["sample"] = "abington"
        with self.assertRaises(ValueError): resolve_site_map(self.values, self.labels)

    def test_count_mismatch(self):
        with self.assertRaises(ValueError): resolve_site_map(self.values, self.labels[:1])

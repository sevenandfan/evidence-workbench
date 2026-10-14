import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from b1.engine import sha
from b1.retail import DIRECTORY, aggregate, contribution, gbp, require_metric, sql_baseline, run, verify


def record(line=2,month="2011-11",invoice="1",product="p",country="UK",qty=2,price=1250,kind="sale",reason=None,missing=0,duplicate=0):
    return (line,month,invoice,product,country,qty,price,qty*price,kind,reason,missing,duplicate)


class RetailTests(unittest.TestCase):
    def test_independent_sql_decimal_hand_oracle(self):
        rows=[record(),record(3,invoice="C1",qty=-1,kind="cancellation"),record(4,month="2011-10",invoice="2")]
        b1,*_=aggregate(rows)
        self.assertEqual(b1["2011-11"],{"sales_mills":2500,"cancellation_mills":1250,"net_mills":1250,"sale_units":2,"sale_invoices":1})
        with sqlite3.connect(":memory:") as db:
            db.execute('CREATE TABLE transactions(source_row,month,invoice,product,country,qty,price_mills,amount_mills,kind,reason,customer_missing,duplicate_candidate)')
            db.executemany('INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',rows)
            self.assertEqual(sql_baseline(db),b1)

    def test_invoice_lines_not_counted_as_orders(self):
        totals,*_=aggregate([record(),record(3,product="q")])
        self.assertEqual(totals["2011-11"]["sale_invoices"],1)
        self.assertEqual(totals["2011-11"]["sales_mills"],5000)

    def test_missing_customer_not_dropped(self):
        totals,_,_,quality,decisions=aggregate([record(missing=1)])
        self.assertEqual(totals["2011-11"]["sales_mills"],2500)
        self.assertEqual(quality["missing_customer"],1)
        self.assertEqual(decisions[0]["source_row"],2)

    def test_duplicates_reported_but_retained(self):
        totals,_,_,quality,_=aggregate([record(),record(3,duplicate=1)])
        self.assertEqual(totals["2011-11"]["sales_mills"],5000)
        self.assertEqual(quality["duplicate_candidate_retained"],1)

    def test_nonpositive_excluded_not_zero_quality(self):
        totals,_,_,quality,decisions=aggregate([record(price=0,kind="excluded",reason="nonpositive_price")])
        self.assertEqual(totals["2011-11"]["net_mills"],0)
        self.assertEqual(quality["nonpositive_price"],1)
        self.assertEqual(decisions[0]["exclusion_reason"],"nonpositive_price")

    def test_amount_not_rounded_per_row(self):
        totals,*_=aggregate([record(qty=10,price=1)])
        self.assertEqual(totals["2011-11"]["sales_mills"],10)
        self.assertEqual(gbp(10),"0.01")

    def test_derived_amount_tamper_rejected(self):
        row=list(record()); row[7]+=1
        with self.assertRaisesRegex(ValueError,"arithmetic"): aggregate([row])

    def test_negative_normal_sale_rejected(self):
        with self.assertRaisesRegex(ValueError,"contract"): aggregate([record(qty=-1)])

    def test_cancellation_positive_sign_rejected(self):
        with self.assertRaisesRegex(ValueError,"contract"): aggregate([record(invoice="C1",kind="cancellation")])

    def test_dimension_contributions_add_up(self):
        rows=[record(month="2011-10"),record(3,country="FR"),record(4,invoice="C1",qty=-1,kind="cancellation")]
        totals,countries,products,_,_=aggregate(rows)
        delta=totals["2011-11"]["net_mills"]-totals["2011-10"]["net_mills"]
        for values in (countries,products): self.assertEqual(sum(r["delta_mills"] for r in contribution(values,"2011-10","2011-11")),delta)

    def test_no_invented_profit_or_conversion(self):
        for metric in ("profit","gross_margin","conversion_rate","matched_order_return_rate","target_attainment"):
            with self.assertRaisesRegex(ValueError,"unsupported"): require_metric(metric)

    def test_unknown_metric_rejected(self):
        with self.assertRaises(ValueError): require_metric("execute_sql")


@unittest.skipUnless((DIRECTORY/"prepared_manifest.json").exists(), "Retail source snapshot required")
class RetailIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result=run()
        cls.directory=Path(cls.result["output"])

    def copied(self,temporary):
        for path in self.directory.iterdir(): shutil.copy2(path,Path(temporary)/path.name)
        return Path(temporary)

    def test_real_replay(self):
        self.assertEqual(self.result["checks_passed"],65)
        self.assertEqual(verify(self.directory)["checks_passed"],65)

    def test_tampered_result_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            target=self.copied(temporary)
            with (target/"b1.json").open("ab") as handle: handle.write(b" ")
            with self.assertRaisesRegex(ValueError,"artifact changed"): verify(target)

    def test_missing_result_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            target=self.copied(temporary)
            (target/"comparison.json").unlink()
            with self.assertRaises(OSError): verify(target)

    def test_false_summary_with_updated_hash_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            target=self.copied(temporary)
            summary=json.loads((target/"summary.json").read_bytes())
            summary["input_rows"]=1
            (target/"summary.json").write_text(json.dumps(summary),encoding="utf-8")
            manifest=json.loads((target/"manifest.json").read_bytes())
            manifest["files"]["summary.json"]=sha((target/"summary.json").read_bytes())
            (target/"manifest.json").write_text(json.dumps(manifest),encoding="utf-8")
            with self.assertRaisesRegex(ValueError,"summary differs"): verify(target)

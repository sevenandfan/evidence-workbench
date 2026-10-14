"""Offline backend tests only: these are not model interpretation accuracy."""
import copy
import unittest

from b2.workflow import execute, validate_plan


class B2ContractTests(unittest.TestCase):
    def setUp(self):
        self.context={"b1_run":"synthetic-test-only","hashes":{},"data":{"b1.json":{"2011-10":{"net_mills":1000},"2011-11":{"net_mills":1500},"2011-12":{"net_mills":200}},"quality.json":{"missing_customer":2},"country-changes.json":[{"dimension":"UK","delta_mills":700},{"dimension":"FR","delta_mills":-200}]}}
        self.task={"kind":"overview","current_month":"2011-11","previous_month":None,"dimension":None,"top_n":5}
        self.plan={"schema_version":"retail_task_plan_v1","status":"execute","reason":None,"tasks":[self.task]}

    def test_overview_uses_backend_value(self):
        result=execute(self.plan,self.context)
        self.assertEqual(result["results"][0]["value"]["net_mills"],1500)
        self.assertEqual(result["results"][0]["evidence"]["reference"],"b1.json#/2011-11")

    def test_multi_task_executes_actual_count(self):
        self.plan["tasks"].append(dict(self.task,kind="compare",previous_month="2011-10"))
        result=execute(self.plan,self.context)
        self.assertEqual(result["executed_tasks"],2)
        self.assertEqual(result["results"][1]["value"]["delta_mills"],500)

    def test_clarify_does_not_execute(self):
        result=execute(dict(self.plan,status="clarify",reason="missing_period",tasks=[]),self.context)
        self.assertEqual(result["executed_tasks"],0)

    def test_unsupported_does_not_execute(self):
        result=execute(dict(self.plan,status="unsupported",reason="unsupported_metric",tasks=[]),self.context)
        self.assertEqual(result["executed_tasks"],0)

    def test_sql_field_rejected(self):
        self.task["sql"]="DELETE FROM transactions"
        with self.assertRaises(ValueError): execute(self.plan,self.context)

    def test_write_path_rejected(self):
        self.plan["write_path"]="../escape"
        with self.assertRaises(ValueError): execute(self.plan,self.context)

    def test_arbitrary_tool_rejected(self):
        self.task["kind"]="python"
        with self.assertRaises(ValueError): execute(self.plan,self.context)

    def test_partial_comparison_rejected(self):
        self.task.update(kind="compare",current_month="2011-12",previous_month="2011-11")
        with self.assertRaisesRegex(ValueError,"Partial"): execute(self.plan,self.context)

    def test_partial_overview_has_warning(self):
        self.task["current_month"]="2011-12"
        self.assertTrue(execute(self.plan,self.context)["results"][0]["warnings"])

    def test_invalid_period_rejected(self):
        self.task["current_month"]="2026-11"
        with self.assertRaises(ValueError): execute(self.plan,self.context)

    def test_bool_top_not_integer(self):
        self.task["top_n"]=True
        with self.assertRaises(ValueError): execute(self.plan,self.context)

    def test_excess_tasks_rejected(self):
        self.plan["tasks"]=[copy.deepcopy(self.task) for _ in range(5)]
        with self.assertRaises(ValueError): execute(self.plan,self.context)

    def test_no_concealed_overview_filter(self):
        self.task["dimension"]="country"
        with self.assertRaises(ValueError): execute(self.plan,self.context)

    def test_contribution_reconciliation(self):
        self.task.update(kind="contributors",previous_month="2011-10",dimension="country")
        result=execute(self.plan,self.context)
        self.assertEqual(result["results"][0]["value"]["all_dimension_delta_mills"],500)
        self.context["data"]["country-changes.json"][0]["delta_mills"]=800
        with self.assertRaisesRegex(ValueError,"reconciliation"): execute(self.plan,self.context)

    def test_quality_not_falsely_filtered(self):
        self.task["kind"]="quality"
        with self.assertRaises(ValueError): execute(self.plan,self.context)
        self.task["current_month"]=None
        self.assertEqual(execute(self.plan,self.context)["results"][0]["value"],{"missing_customer":2})

    def test_malformed_status_is_structured_failure(self):
        self.plan["status"]={}
        with self.assertRaises(ValueError): validate_plan(self.plan,self.context)

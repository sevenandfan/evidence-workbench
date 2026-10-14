import json
import os
import tempfile
import unittest
from decimal import Decimal
from unittest.mock import patch

from b2.live import _parse_and_validate, build_messages, compare_case, plan_live, prior_b2_cost, run_live, verify_live_run
from b2.provider import BudgetLedger, ProviderConfig, ProviderError, request_plan


def config(**changes):
    base = dict(
        base_url="https://api.deepseek.com",
        model="deepseek-flash",
        api_key="secret-for-test",
        api_key_source="test",
        max_output_tokens=100,
        input_price_cny_per_million=Decimal("2"),
        cache_price_cny_per_million=Decimal("0.04"),
        output_price_cny_per_million=Decimal("8"),
        pricing_source="test",
        official_deepseek=True,
    )
    base.update(changes)
    return ProviderConfig(**base)


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.context={"b1_run":"test","hashes":{},"data":{"b1.json":{"2011-10":{"net_mills":1},"2011-11":{"net_mills":2},"2011-12":{"net_mills":1}},"rules.json":{"unsupported":["profit"]},"quality.json":{},"country-changes.json":[{"delta_mills":1}],"product-changes.json":[{"delta_mills":1}]}}

    def test_missing_key_stops_before_call(self):
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaisesRegex(ValueError,"no API call"):
                ProviderConfig.from_env()

    def test_dpapi_file_loader_is_selected_without_plaintext_env(self):
        with patch.dict(os.environ,{"B2_DPAPI_KEY_FILE":r"C:\fake\credential.dpapi"},clear=True), patch("b2.provider.load_windows_dpapi_key",return_value="sk-test-secret-value") as loader:
            value=ProviderConfig.from_env()
        loader.assert_called_once_with(r"C:\fake\credential.dpapi")
        self.assertEqual(value.api_key_source,"B2_DPAPI_KEY_FILE (Windows current-user DPAPI)")
        self.assertNotIn("sk-test",json.dumps(value.public_dict()))

    def test_official_defaults_and_secret_not_public(self):
        with patch.dict(os.environ,{"DEEPSEEK_API_KEY":"secret"},clear=True):
            value=ProviderConfig.from_env()
        self.assertEqual(value.model,"deepseek-flash")
        self.assertNotIn("secret",json.dumps(value.public_dict()))
        self.assertTrue(value.official_deepseek)

    def test_custom_gateway_requires_prices(self):
        with patch.dict(os.environ,{"B2_API_KEY":"secret","B2_API_BASE_URL":"https://example.com/v1","B2_MODEL":"deepseek"},clear=True):
            with self.assertRaisesRegex(ValueError,"requires all three"):
                ProviderConfig.from_env()

    def test_insecure_or_credential_url_rejected(self):
        for url in ("http://example.com/v1","https://user:pass@example.com/v1","https://example.com/v1?q=x"):
            with self.subTest(url=url), patch.dict(os.environ,{"B2_API_KEY":"secret","B2_API_BASE_URL":url},clear=True):
                with self.assertRaises(ValueError): ProviderConfig.from_env()

    def test_budget_blocks_before_transport(self):
        called=[]
        def transport(*args): called.append(True)
        ledger=BudgetLedger(limit_cny=Decimal("0.000001"),target_cny=Decimal("0.000001"))
        with self.assertRaisesRegex(ProviderError,"not sent"):
            request_plan([{"role":"user","content":"x"}],config(),ledger,transport)
        self.assertFalse(called)

    def test_budget_includes_prior_runs(self):
        ledger=BudgetLedger(limit_cny=Decimal("20"),target_cny=Decimal("10"),prior_committed_cny=Decimal("19.999999"))
        with self.assertRaises(ProviderError):
            ledger.authorize(Decimal("0.000002"))
        self.assertEqual(ledger.public_dict()["prior_committed_cny"],"19.999999")

    def test_prior_cost_sums_run_only_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            from pathlib import Path
            root=Path(directory); (root/"runs"/"a-b2-live-x").mkdir(parents=True); (root/"runs"/"b-b2-eval-x").mkdir()
            (root/"runs"/"a-b2-live-x"/"summary.json").write_text(json.dumps({"budget":{"spent_upper_cny":"0.1","uncertain_upper_cny":"0.02"}}),encoding="utf-8")
            (root/"runs"/"b-b2-eval-x"/"summary.json").write_text(json.dumps({"budget":{"run_spent_upper_cny":"0.2","run_uncertain_upper_cny":"0.03","prior_committed_cny":"0.12"}}),encoding="utf-8")
            with patch("b2.live.ROOT",root): self.assertEqual(prior_b2_cost(),Decimal("0.35"))

    def test_usage_cost_and_no_secret_in_payload(self):
        seen={}
        def transport(url,payload,headers,timeout):
            seen.update(url=url,payload=payload,auth=headers["Authorization"])
            return {"id":"r","model":"m","choices":[{"message":{"content":"{}"},"finish_reason":"stop"}],"usage":{"prompt_tokens":100,"completion_tokens":10}}
        ledger=BudgetLedger()
        result=request_plan([{"role":"user","content":"x"}],config(),ledger,transport)
        self.assertEqual(result["usage"]["cost_upper_cny"],"0.00028")
        self.assertGreaterEqual(result["latency_ms"],0)
        self.assertNotIn("secret-for-test",json.dumps(seen["payload"]))
        self.assertIn("Bearer secret-for-test",seen["auth"])

    def test_siliconflow_disables_thinking(self):
        seen={}
        def transport(url,payload,headers,timeout):
            seen.update(payload); return {"choices":[{"message":{"content":"{}"},"finish_reason":"stop"}],"usage":{}}
        request_plan([{"role":"user","content":"x"}],config(base_url="https://api.siliconflow.cn/v1",official_deepseek=False),BudgetLedger(),transport)
        self.assertIs(seen["enable_thinking"],False)

    def test_auth_error_is_safe_and_counted_uncertain(self):
        def transport(*args): raise ProviderError("authentication_failed","Provider returned HTTP 401")
        ledger=BudgetLedger()
        with self.assertRaisesRegex(ProviderError,"401"):
            request_plan([{"role":"user","content":"x"}],config(),ledger,transport)
        self.assertEqual(ledger.calls_attempted,1)
        self.assertGreater(ledger.uncertain_upper_cny,0)

    def test_messages_contain_catalog_schema_and_question(self):
        messages=build_messages("问题",self.context)
        self.assertIn("required_json_schema",messages[1]["content"])
        self.assertIn("2011-11",messages[1]["content"])
        self.assertIn("问题",messages[1]["content"])

    def test_invalid_first_plan_gets_one_repair(self):
        outputs=iter(["not json",json.dumps({"schema_version":"retail_task_plan_v1","status":"clarify","reason":"missing_period","tasks":[]})])
        calls=[]
        def transport(url,payload,headers,timeout):
            calls.append(payload)
            return {"choices":[{"message":{"content":next(outputs)},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1}}
        plan,attempts=plan_live("这个月呢",self.context,config(),BudgetLedger(),transport)
        self.assertEqual(plan["status"],"clarify")
        self.assertEqual(len(attempts),2)
        self.assertEqual(len(calls),2)

    def test_valid_plan_has_no_repair(self):
        content=json.dumps({"schema_version":"retail_task_plan_v1","status":"execute","reason":None,"tasks":[{"kind":"overview","current_month":"2011-11","previous_month":None,"dimension":None,"top_n":5}]})
        calls=[]
        def transport(*args):
            calls.append(1); return {"choices":[{"message":{"content":content},"finish_reason":"stop"}],"usage":{}}
        plan,attempts=plan_live("11月",self.context,config(),BudgetLedger(),transport)
        self.assertEqual(plan["status"],"execute")
        self.assertEqual(len(calls),1)

    def test_unknown_plan_field_rejected(self):
        content=json.dumps({"schema_version":"retail_task_plan_v1","status":"clarify","reason":"missing_period","tasks":[],"sql":"x"})
        with self.assertRaises(ValueError): _parse_and_validate(content,self.context)

    def test_case_comparison_catches_wrong_parameter(self):
        case={"expected_status":"execute","expected_kinds":["overview"],"current_month":"2011-10"}
        plan={"status":"execute","tasks":[{"kind":"overview","current_month":"2011-11","previous_month":None,"dimension":None,"top_n":5}]}
        check=compare_case(case,plan,{"results":[]})
        self.assertFalse(check["passed"])
        self.assertIn("current_month",check["failures"])

    def test_case_comparison_accepts_declared_safe_reason_alternative(self):
        case={"expected_status":"clarify","acceptable_reasons":["missing_period","unclear_question"]}
        plan={"status":"clarify","reason":"unclear_question","tasks":[]}
        self.assertTrue(compare_case(case,plan,{"results":[]})["passed"])

    def test_live_run_artifacts_replay_without_second_model_call(self):
        content=json.dumps({"schema_version":"retail_task_plan_v1","status":"execute","reason":None,"tasks":[{"kind":"overview","current_month":"2011-11","previous_month":None,"dimension":None,"top_n":5}]})
        calls=[]
        def transport(*args):
            calls.append(1); return {"id":"mock","model":"mock","choices":[{"message":{"content":content},"finish_reason":"stop"}],"usage":{"prompt_tokens":10,"completion_tokens":10}}
        with tempfile.TemporaryDirectory() as directory:
            from pathlib import Path
            with patch("b2.live.ROOT",Path(directory)), patch("b2.live.load_context",return_value=self.context):
                result=run_live("2011年11月概览",config(),transport=transport)
                verified=verify_live_run(Path(result["output"]))
        self.assertEqual(len(calls),1)
        self.assertEqual(verified["model_calls"],0)
        self.assertFalse(result["live_B2_verified"])


if __name__=="__main__": unittest.main()

"""Live B2 planning, read-only execution, evidence packaging, and development evaluation."""
from __future__ import annotations

import html
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from b1.engine import ROOT, encode, parse_json, sha
from b2.provider import BudgetLedger, ProviderConfig, ProviderError, request_plan
from b2.workflow import catalog, execute, load_context, validate_plan

PROMPT_PATH = Path(__file__).with_name("planner_prompt.md")
SCHEMA_PATH = Path(__file__).with_name("task-plan.schema.json")
DEV_CASES = ROOT / "evals" / "b2-intent-dev.json"


def build_messages(question: str, context: dict) -> list[dict]:
    if not isinstance(question, str) or not 1 <= len(question) <= 2000:
        raise ValueError("Question length limit")
    system = PROMPT_PATH.read_text(encoding="utf-8")
    schema = parse_json(SCHEMA_PATH.read_bytes())
    user_payload = {
        "catalog": catalog(context),
        "required_json_schema": schema,
        "example_json_shape": {
            "schema_version": "retail_task_plan_v1",
            "status": "clarify",
            "reason": "missing_period",
            "tasks": [],
        },
        "question": question,
    }
    return [
        {"role": "system", "content": system + "\nReturn one JSON object only."},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False, separators=(",", ":"))},
    ]


def _parse_and_validate(content: str, context: dict) -> dict:
    if len(content.encode("utf-8")) > 16_000:
        raise ValueError("Plan size limit")
    try:
        value = json.loads(content)
    except json.JSONDecodeError as error:
        raise ValueError("Model output was not valid JSON") from error
    return validate_plan(value, context)


def plan_live(question: str, context: dict, config: ProviderConfig, ledger: BudgetLedger, transport=None) -> tuple[dict, list[dict]]:
    messages = build_messages(question, context)
    attempts = []
    caller = request_plan if transport is None else lambda m, c, l: request_plan(m, c, l, transport=transport)
    first = caller(messages, config, ledger)
    attempts.append({k: v for k, v in first.items() if k != "content"} | {"content": first["content"]})
    try:
        return _parse_and_validate(first["content"], context), attempts
    except ValueError as first_error:
        repair = messages + [
            {"role": "assistant", "content": first["content"]},
            {"role": "user", "content": "The JSON failed deterministic validation: " + str(first_error) + ". Return one corrected JSON object only; do not add fields."},
        ]
        second = caller(repair, config, ledger)
        attempts.append({k: v for k, v in second.items() if k != "content"} | {"content": second["content"]})
        return _parse_and_validate(second["content"], context), attempts


def _new_run_id(label: str) -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"-{label}-" + uuid.uuid4().hex[:8]


def prior_b2_cost() -> Decimal:
    """Sum earlier saved run-only upper bounds so the experiment cap spans runs."""
    total = Decimal("0")
    runs = ROOT / "runs"
    if not runs.is_dir():
        return total
    for summary_path in runs.glob("*-b2-*/summary.json"):
        try:
            budget = parse_json(summary_path.read_bytes()).get("budget") or {}
            total += Decimal(str(budget.get("run_spent_upper_cny", budget.get("spent_upper_cny", "0"))))
            total += Decimal(str(budget.get("run_uncertain_upper_cny", budget.get("uncertain_upper_cny", "0"))))
        except (OSError, ValueError, TypeError):
            raise ValueError("Cannot establish cumulative B2 budget from " + str(summary_path))
    return total


def _write_run(run_id: str, artifacts: dict) -> Path:
    output = ROOT / "runs" / run_id
    output.mkdir(parents=True, exist_ok=False)
    for name, payload in artifacts.items():
        if name.endswith(".json"):
            (output / name).write_bytes(encode(payload))
        else:
            (output / name).write_text(payload, encoding="utf-8")
    manifest = {"run_id": run_id, "files": {p.name: sha(p.read_bytes()) for p in output.iterdir()}}
    (output / "manifest.json").write_bytes(encode(manifest))
    return output


def run_live(question: str, config: ProviderConfig, budget_cny: Decimal = Decimal("20"), target_cny: Decimal = Decimal("10"), transport=None) -> dict:
    context = load_context()
    ledger = BudgetLedger(limit_cny=budget_cny, target_cny=target_cny, prior_committed_cny=prior_b2_cost())
    plan, attempts = plan_live(question, context, config, ledger, transport=transport)
    result = execute(plan, context)
    run_id = _new_run_id("b2-live")
    summary = {
        "run_id": run_id,
        "mode": "live_model_planning_readonly_execution",
        "live_model_call_completed": True,
        "live_B2_verified": False,
        "question": question,
        "status": result["status"],
        "b1_run": context["b1_run"],
        "provider": config.public_dict(),
        "budget": ledger.public_dict(),
        "model_attempts": len(attempts),
        "prompt_sha256": sha(PROMPT_PATH.read_bytes()),
        "schema_sha256": sha(SCHEMA_PATH.read_bytes()),
        "code_sha256": sha(Path(__file__).read_bytes()),
    }
    body = "".join(
        f"<h2>{html.escape(row['evidence_id'])}</h2><pre>{html.escape(json.dumps(row['value'], ensure_ascii=False, indent=2))}</pre>"
        for row in result["results"]
    )
    report = (
        "<!doctype html><html lang='zh'><meta charset='utf-8'><title>B2经营数据助手</title>"
        "<style>body{font:16px system-ui;max-width:1000px;margin:30px auto}pre{white-space:pre-wrap;background:#f3f5f9;padding:15px}</style>"
        f"<h1>B2经营数据助手</h1><p>问题：{html.escape(question)}</p><p>状态：{html.escape(result['status'])}</p>"
        f"{body}<p>数字由B1只读后端提供；模型只规划任务。费用为token回执按配置价格计算的上界，不是账单核验。</p></html>"
    )
    events = "\n".join(json.dumps(row, ensure_ascii=False) for row in (
        {"stage": "B1_prerequisite_verified", "status": "completed", "run_id": run_id},
        {"stage": "live_model_plan_received", "status": "completed", "run_id": run_id, "attempts": len(attempts)},
        {"stage": "backend_contract_checked", "status": "completed", "run_id": run_id},
        {"stage": "readonly_tools_executed", "status": "completed", "run_id": run_id, "tasks": result["executed_tasks"]},
        {"stage": "evidence_package_written", "status": "completed", "run_id": run_id},
    )) + "\n"
    output = _write_run(run_id, {
        "summary.json": summary,
        "catalog.json": catalog(context),
        "plan.json": plan,
        "model-attempts.json": attempts,
        "results.json": result,
        "events.jsonl": events,
        "report.html": report,
    })
    return {"output": str(output), **summary}


def compare_case(case: dict, plan: dict, result: dict) -> dict:
    failures = []
    if plan["status"] != case["expected_status"]:
        failures.append("status")
    acceptable_reasons = case.get("acceptable_reasons")
    if acceptable_reasons is not None and plan["reason"] not in acceptable_reasons:
        failures.append("reason")
    elif acceptable_reasons is None and "expected_reason" in case and plan["reason"] != case["expected_reason"]:
        failures.append("reason")
    if "expected_kinds" in case and [t["kind"] for t in plan["tasks"]] != case["expected_kinds"]:
        failures.append("task_kinds_or_order")
    for field in ("current_month", "previous_month", "dimension", "top_n"):
        if field in case and not any(task[field] == case[field] for task in plan["tasks"]):
            failures.append(field)
    if case.get("required_warning") == "partial_month":
        warnings = [warning for row in result["results"] for warning in row["warnings"]]
        if not any("Partial month" in warning for warning in warnings):
            failures.append("required_warning")
    return {"passed": not failures, "failures": failures}


def evaluate_live(config: ProviderConfig, budget_cny: Decimal = Decimal("20"), target_cny: Decimal = Decimal("10"), transport=None, suite_path: Path = DEV_CASES) -> dict:
    context = load_context()
    suite_path = Path(suite_path).resolve()
    if suite_path.parent != (ROOT / "evals").resolve() or not suite_path.name.startswith("b2-intent-") or suite_path.suffix != ".json":
        raise ValueError("Evaluation suite must be a b2-intent-*.json file in project evals")
    suite_bytes = suite_path.read_bytes()
    suite = parse_json(suite_bytes)
    ledger = BudgetLedger(limit_cny=budget_cny, target_cny=target_cny, prior_committed_cny=prior_b2_cost())
    rows = []
    for case in suite["cases"]:
        row = {"id": case["id"], "question": case["question"]}
        try:
            plan, attempts = plan_live(case["question"], context, config, ledger, transport=transport)
            result = execute(plan, context)
            check = compare_case(case, plan, result)
            row.update(plan=plan, result=result, model_attempts=attempts, **check)
        except (ProviderError, ValueError) as error:
            row.update(passed=False, failures=[getattr(error, "code", "validation_error")], error=str(error))
            rows.append(row)
            if isinstance(error, ProviderError) and error.code in {"authentication_failed", "budget_exceeded"}:
                break
            continue
        rows.append(row)
    passed = sum(bool(row["passed"]) for row in rows)
    run_id = _new_run_id("b2-eval")
    is_heldout = str(suite["kind"]).startswith("heldout")
    summary = {
        "run_id": run_id,
        "mode": "live_model_intent_evaluation",
        "set_kind": suite["kind"],
        "suite_file": suite_path.name,
        "suite_sha256": sha(suite_bytes),
        "cases_planned": len(suite["cases"]),
        "cases_attempted": len(rows),
        "cases_passed": passed,
        "cases_failed": len(rows) - passed,
        "all_passed": len(rows) == len(suite["cases"]) and passed == len(rows),
        "provider": config.public_dict(),
        "budget": ledger.public_dict(),
        "b1_run": context["b1_run"],
        "is_heldout": is_heldout,
        "development_set_not_heldout": not is_heldout,
        "human_net_value_verified": False,
    }
    report_rows = "".join(
        f"<tr><td>{html.escape(row['id'])}</td><td>{'通过' if row['passed'] else '失败'}</td><td>{html.escape(', '.join(row['failures']))}</td></tr>"
        for row in rows
    )
    report = (
        "<!doctype html><html lang='zh'><meta charset='utf-8'><title>B2开发集评测</title>"
        "<style>body{font:16px system-ui;max-width:1100px;margin:30px auto}table{border-collapse:collapse}td,th{border:1px solid #bbb;padding:8px}</style>"
        f"<h1>B2开发集评测</h1><p>{passed}/{len(rows)}通过；这是冻结开发集，不是盲测。</p>"
        f"<table><tr><th>样例</th><th>结果</th><th>问题</th></tr>{report_rows}</table>"
        "<p>数值执行仍由B1确定性后端完成。真人操作负担与业务收益尚未由本评测证明。</p></html>"
    )
    events = "\n".join(json.dumps(row, ensure_ascii=False) for row in (
        {"stage": "B1_prerequisite_verified", "status": "completed", "run_id": run_id},
        {"stage": "development_cases_attempted", "status": "completed", "run_id": run_id, "count": len(rows)},
        {"stage": "evaluation_scored", "status": "completed", "run_id": run_id, "passed": passed},
        {"stage": "evidence_package_written", "status": "completed", "run_id": run_id},
    )) + "\n"
    output = _write_run(run_id, {"summary.json": summary, "suite.json": suite, "case-results.json": rows, "events.jsonl": events, "report.html": report})
    return {"output": str(output), **summary}


def verify_live_run(run_path: Path) -> dict:
    """Verify saved bytes and replay deterministic backend work without another API call."""
    path = Path(run_path).resolve()
    if not path.is_dir() or path.parent != (ROOT / "runs").resolve():
        raise ValueError("B2 run must be a direct child of the project runs directory")
    manifest = parse_json((path / "manifest.json").read_bytes())
    if manifest.get("run_id") != path.name or not isinstance(manifest.get("files"), dict):
        raise ValueError("Invalid B2 manifest identity")
    expected_files = set(manifest["files"])
    actual_files = {p.name for p in path.iterdir() if p.is_file() and p.name != "manifest.json"}
    if expected_files != actual_files:
        raise ValueError("B2 artifact set changed")
    for name, digest in manifest["files"].items():
        if sha((path / name).read_bytes()) != digest:
            raise ValueError("B2 artifact hash mismatch: " + name)
    summary = parse_json((path / "summary.json").read_bytes())
    context = load_context()
    checks = len(expected_files) + 2
    if summary["mode"] == "live_model_planning_readonly_execution":
        plan = parse_json((path / "plan.json").read_bytes())
        result = parse_json((path / "results.json").read_bytes())
        if execute(plan, context) != result:
            raise ValueError("B2 live result does not replay from saved plan")
        attempts = parse_json((path / "model-attempts.json").read_bytes())
        if not attempts or _parse_and_validate(attempts[-1]["content"], context) != plan:
            raise ValueError("Saved model output does not match saved plan")
        checks += 2
    elif summary["mode"] in {"live_model_frozen_development_evaluation", "live_model_intent_evaluation"}:
        suite_file = path / "suite.json"
        cases = {case["id"]: case for case in parse_json(suite_file.read_bytes() if suite_file.is_file() else DEV_CASES.read_bytes())["cases"]}
        rows = parse_json((path / "case-results.json").read_bytes())
        replayed = 0
        for row in rows:
            if "plan" not in row:
                continue
            if row["id"] not in cases or execute(row["plan"], context) != row["result"]:
                raise ValueError("B2 evaluation row does not replay: " + row.get("id", "unknown"))
            if compare_case(cases[row["id"]], row["plan"], row["result"])["passed"] != row["passed"]:
                raise ValueError("B2 evaluation verdict mismatch: " + row["id"])
            replayed += 1
        checks += replayed * 2
    else:
        raise ValueError("Unknown B2 live run mode")
    return {"run_id": path.name, "status": "verified", "checks_passed": checks, "checks_total": checks, "model_calls": 0}

"""Provider-neutral offline contract and read-only execution, not an LLM."""
import html
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from b1.engine import ROOT, encode, parse_json, sha
from b1.retail import gbp, verify as verify_retail

CURRENT_B1 = ROOT/"runs/20261013T091144Z-retail-704158f1"
FIELDS = {"kind","current_month","previous_month","dimension","top_n"}
STATUSES = {"execute","clarify","unsupported","refuse"}
REASONS = {"missing_period","unsupported_metric","causal_request","unsafe_action","unclear_question","unsupported_grain"}


def load_context():
    check = verify_retail(CURRENT_B1)
    if check["checks_passed"] != check["checks_total"]: raise ValueError("B1 prerequisite not passed")
    names=("b1.json","quality.json","country-changes.json","product-changes.json","prepared.json","rules.json")
    raw={name:(CURRENT_B1/name).read_bytes() for name in names}
    manifest=parse_json((CURRENT_B1/"manifest.json").read_bytes())
    if any(sha(content)!=manifest["files"][name] for name,content in raw.items()): raise ValueError("B1 snapshot changed after verification")
    return {"b1_run":CURRENT_B1.name,"hashes":{name:sha(content) for name,content in raw.items()},"data":{name:parse_json(content) for name,content in raw.items()}}


def catalog(context):
    return {"months":sorted(context["data"]["b1.json"]),"partial_months":["2011-12"],"currency":"GBP","available_tasks":["overview","compare","contributors","quality"],"contributors_available_pair":["2011-10","2011-11"],"metrics":["sales_mills","cancellation_mills","net_mills","sale_units","sale_invoices"],"unsupported_metrics":context["data"]["rules.json"]["unsupported"],"limits":"No arbitrary SQL/code/files/writes; quality is whole-source; contributions arithmetic not causal; currency amounts in exact mills"}


def validate_plan(value,context):
    if not isinstance(value,dict) or set(value)!={"schema_version","status","reason","tasks"} or value["schema_version"]!="retail_task_plan_v1": raise ValueError("Invalid planning contract")
    status=value["status"]
    if not isinstance(status,str) or status not in STATUSES: raise ValueError("Unknown plan status")
    tasks=value["tasks"]
    if not isinstance(tasks,list) or len(tasks)>4: raise ValueError("Task limit exceeded")
    if status!="execute":
        if tasks or not isinstance(value["reason"],str) or value["reason"] not in REASONS: raise ValueError("Nonexecution plans require a declared reason and no tasks")
        return value
    if value["reason"] is not None or not tasks: raise ValueError("Execute plan requires tasks and no refusal reason")
    months=set(context["data"]["b1.json"])
    for task in tasks:
        if not isinstance(task,dict) or set(task)!=FIELDS: raise ValueError("Unknown/absent tool fields")
        kind,current,previous,dimension,top=map(task.get,("kind","current_month","previous_month","dimension","top_n"))
        if type(top) is not int or not 1<=top<=10: raise ValueError("top_n must be integer 1-10")
        if kind not in ("overview","compare","contributors","quality"): raise ValueError("Tool not allowed")
        if kind=="quality":
            if current is not None or previous is not None or dimension is not None: raise ValueError("Quality tool is whole-source only")
            continue
        if not isinstance(current,str) or current not in months: raise ValueError("Unavailable current month")
        if kind=="overview":
            if previous is not None or dimension is not None: raise ValueError("Overview cannot conceal filters")
        else:
            if not isinstance(previous,str) or previous not in months or previous>=current: raise ValueError("Invalid comparison period/direction")
            if "2011-12" in (previous,current): raise ValueError("Partial-month comparison needs explicit alternative scope; clarify")
            if kind=="compare" and dimension is not None: raise ValueError("Compare cannot conceal dimension filters")
            if kind=="contributors" and (dimension not in ("country","product") or (previous,current)!=("2011-10","2011-11")): raise ValueError("Unsupported contribution dimension or period; clarify")
    return value


def execute(plan,context):
    validate_plan(plan,context)
    if plan["status"]!="execute": return {"status":plan["status"],"reason":plan["reason"],"results":[],"executed_tasks":0}
    results=[]
    for index,task in enumerate(plan["tasks"],1):
        kind=task["kind"]
        current,previous=task["current_month"],task["previous_month"]
        warnings=[]
        if kind=="overview":
            value=context["data"]["b1.json"][current]
            evidence="b1.json#/"+current
            if current=="2011-12": warnings.append("Partial month through 2011-12-09, not full-month performance")
        elif kind=="compare":
            a,b=context["data"]["b1.json"][previous],context["data"]["b1.json"][current]
            value={"previous_month":previous,"current_month":current,"previous_net_mills":a["net_mills"],"current_net_mills":b["net_mills"],"delta_mills":b["net_mills"]-a["net_mills"]}
            evidence="b1.json#/"+previous+","+current
        elif kind=="contributors":
            filename="country-changes.json" if task["dimension"]=="country" else "product-changes.json"
            all_rows=context["data"][filename]
            total_delta=context["data"]["b1.json"][current]["net_mills"]-context["data"]["b1.json"][previous]["net_mills"]
            if sum(row["delta_mills"] for row in all_rows)!=total_delta: raise ValueError("Contribution reconciliation failed")
            value={"rows":all_rows[:task["top_n"]],"all_dimension_delta_mills":total_delta,"ranking":"absolute change descending; signs preserved"}
            evidence=filename
            warnings.append("Arithmetic contribution, not a causal explanation")
        else:
            value=context["data"]["quality.json"]
            evidence="quality.json"
            warnings.append("Whole-source quality counts; overlaps are not additive exclusions")
        results.append({"evidence_id":"E-"+str(index),"task":task,"value":value,"warnings":warnings,"evidence":{"b1_run":context["b1_run"],"reference":evidence,"source_hashes":context["hashes"]}})
    return {"status":"completed","results":results,"executed_tasks":len(results)}


def replay(question,response_path):
    if not isinstance(question,str) or not 1<=len(question)<=2000: raise ValueError("Question length limit")
    raw=Path(response_path).read_bytes()
    if len(raw)>16000: raise ValueError("Plan size limit")
    plan=parse_json(raw)
    context=load_context()
    result=execute(plan,context)
    run_id=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"-b2-replay-"+uuid.uuid4().hex[:8]
    output=ROOT/"runs"/run_id
    output.mkdir(parents=True,exist_ok=False)
    summary={"run_id":run_id,"mode":"offline_planning_response_replay","model_calls":0,"api_cost":0,"live_B2_verified":False,"semantic_question_plan_match":"not verified by replay","status":result["status"],"b1_run":context["b1_run"],"question":question,"code_sha256":sha(Path(__file__).read_bytes())}
    artifacts={"summary.json":summary,"catalog.json":catalog(context),"plan.json":plan,"results.json":result}
    for name,payload in artifacts.items(): (output/name).write_bytes(encode(payload))
    events=[{"stage":stage,"status":"completed","run_id":run_id} for stage in ("B1_prerequisite_verified","offline_plan_loaded","backend_contract_checked","readonly_tools_executed","evidence_answer_generated")]
    (output/"events.jsonl").write_text("\n".join(json.dumps(row) for row in events)+"\n",encoding="utf-8")
    body="".join(f"<h2>{r['evidence_id']}：{html.escape(r['task']['kind'])}</h2><pre>{html.escape(json.dumps(r['value'],ensure_ascii=False,indent=2))}</pre><p>{html.escape('；'.join(r['warnings']))}</p>" for r in result["results"])
    (output/"report.html").write_text(f'''<!doctype html><html lang="zh"><meta charset="utf-8"><title>B2离线工具链回放</title><style>body{{font:16px system-ui;max-width:1000px;margin:30px auto}}pre{{white-space:pre-wrap;background:#f3f5f9;padding:15px}}</style><h1>B2离线工具链回放</h1><p>未调用模型，不是自然语言理解能力验收。问题与规划是否语义匹配尚未由真实模型测试。</p><p>输入问题：{html.escape(question)}</p><p>状态：{html.escape(result['status'])}；执行任务{result['executed_tasks']}个。详细证据见results.json。</p>{body}<p>模型来源/预算/在线评测待确认；原始交易明细没有发送到外部。</p>''',encoding="utf-8")
    (output/"manifest.json").write_bytes(encode({"run_id":run_id,"mode":summary["mode"],"files":{p.name:sha(p.read_bytes()) for p in output.iterdir()}}))
    return {"output":str(output),**summary}

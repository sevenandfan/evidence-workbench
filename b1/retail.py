"""Fixed retail review: integer/Decimal money, independent SQL aggregation."""
import html
import json
import sqlite3
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from .engine import ROOT, encode, sha, reject

DIRECTORY = ROOT/"data/retail/uci-352-v1"
RULES = {"current_month":"2011-11","previous_month":"2011-10","currency":"GBP","unit":"0.001 GBP","duplicates":"retain_exact_raw_row_candidates","missing_customer":"retain_for_sales","sale":"quantity>0,price>0,invoice not C-prefixed","cancellation":"C-prefixed invoice,quantity<0,price>0; not confirmed refund","net":"positive sale amount minus cancellation record amount; not financial recognized revenue","money":"aggregate exact mills; round to 2 decimals half-even only for display","partial_month":"2011-12 excluded from full-month change interpretation","unsupported":["profit","gross_margin","conversion_rate","matched_order_return_rate","target_attainment"]}
METRICS = ("sales_mills","cancellation_mills","net_mills","sale_units","sale_invoices")


def sql_baseline(connection):
    sql = '''SELECT month,
        SUM(CASE WHEN kind='sale' THEN amount_mills ELSE 0 END),
        SUM(CASE WHEN kind='cancellation' THEN -amount_mills ELSE 0 END),
        SUM(CASE WHEN kind IN ('sale','cancellation') THEN amount_mills ELSE 0 END),
        SUM(CASE WHEN kind='sale' THEN qty ELSE 0 END),
        COUNT(DISTINCT CASE WHEN kind='sale' THEN invoice END)
        FROM transactions GROUP BY month ORDER BY month'''
    return {row[0]: dict(zip(METRICS,row[1:])) for row in connection.execute(sql)}


def aggregate(rows):
    totals = defaultdict(lambda: {k:Decimal(0) if k.endswith("mills") else 0 for k in METRICS})
    invoices, countries, products = defaultdict(set), defaultdict(int), defaultdict(int)
    quality, decisions = defaultdict(int), []
    for row in rows:
        source_row,month,invoice,product,country,qty,price_mills,amount_mills,kind,reason,missing,duplicate = row
        total = totals[month]  # preserve periods consisting only of excluded rows
        if kind not in ("sale","cancellation","excluded"): raise ValueError("Unknown row classification")
        if qty*price_mills != amount_mills: raise ValueError("Derived row arithmetic differs")
        if kind != "excluded" and (price_mills <= 0 or qty == 0 or (kind == "sale" and (qty<0 or invoice.upper().startswith("C"))) or (kind=="cancellation" and (qty>0 or not invoice.upper().startswith("C")))):
            raise ValueError("Classification violates declared contract")
        if reason or duplicate or missing:
            decisions.append({"source_row":source_row,"month":month,"kind":kind,"exclusion_reason":reason,"duplicate_candidate_retained":bool(duplicate),"missing_customer":bool(missing)})
        if reason: quality[reason] += 1
        if duplicate: quality["duplicate_candidate_retained"] += 1
        if missing: quality["missing_customer"] += 1
        quality["kind_"+kind] += 1
        if kind == "excluded": continue
        money = Decimal(price_mills)/1000*Decimal(qty)*1000
        total["net_mills"] += money
        if kind == "sale":
            total["sales_mills"] += money
            total["sale_units"] += qty
            invoices[month].add(invoice)
        else: total["cancellation_mills"] -= money
        countries[(month,country)] += int(money)
        products[(month,product)] += int(money)
    result = {}
    for month,total in sorted(totals.items()):
        total["sale_invoices"] = len(invoices[month])
        result[month] = {key:int(value) for key,value in total.items()}
    return result,countries,products,dict(quality),decisions


def contribution(values,previous,current):
    dimensions = sorted({name for month,name in values if month in (previous,current)})
    return sorted([{"dimension":name,"previous_net_mills":values.get((previous,name),0),"current_net_mills":values.get((current,name),0),"delta_mills":values.get((current,name),0)-values.get((previous,name),0)} for name in dimensions],key=lambda r:(-abs(r["delta_mills"]),r["dimension"]))


def require_metric(metric):
    if metric in RULES["unsupported"]: reject("unsupported_metric", "unsupported: required source fields or matched order links unavailable")
    if metric not in METRICS: raise ValueError("Unknown metric")
    return metric


def compute():
    source_manifest = json.loads((DIRECTORY/"source_manifest.json").read_bytes())
    prepared = json.loads((DIRECTORY/"prepared_manifest.json").read_bytes())
    source = DIRECTORY/"Online Retail.xlsx"
    db_path = DIRECTORY/"transactions.sqlite"
    if sha(source.read_bytes()) != prepared["source_sha256"] or prepared["source_sha256"] != source_manifest["files"][source.name]["sha256"]:
        raise ValueError("Workbook source drift")
    if sha(db_path.read_bytes()) != prepared["prepared_sha256"]: raise ValueError("Prepared data drift")
    with sqlite3.connect(db_path.resolve().as_uri()+"?mode=ro",uri=True) as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok": raise ValueError("SQLite integrity error")
        b0 = sql_baseline(db)
        b1,countries,products,quality,decisions = aggregate(db.execute('SELECT * FROM transactions ORDER BY source_row'))
        row_count = db.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    previous,current = RULES["previous_month"],RULES["current_month"]
    if current not in b1 or previous not in b1: raise ValueError("Required full comparison months unavailable")
    checks = [{"month":month,"metric":metric,"B0":b0[month][metric],"B1":b1[month][metric],"passed":b0[month][metric]==b1[month][metric]} for month in sorted(b1) for metric in METRICS]
    country_changes = contribution(countries,previous,current)
    product_changes = contribution(products,previous,current)
    delta = b1[current]["net_mills"]-b1[previous]["net_mills"]
    for label,values in (("country",country_changes),("product",product_changes)):
        if sum(row["delta_mills"] for row in values) != delta: raise ValueError(label+" change contributions fail reconciliation")
    if row_count != prepared["rows"] or sum(quality.get("kind_"+k,0) for k in ("sale","cancellation","excluded")) != row_count:
        raise ValueError("Row classification counts do not reconcile")
    if sha(db_path.read_bytes()) != prepared["prepared_sha256"] or sha(source.read_bytes()) != prepared["source_sha256"]:
        raise ValueError("Inputs changed during run")
    return {"rules":RULES,"source":source_manifest,"prepared":prepared,"B0":b0,"B1":b1,"checks":checks,"quality":quality,"row_decisions":decisions,"country_changes":country_changes,"product_changes":product_changes,"comparison":{"previous_month":previous,"current_month":current,"net_delta_mills":delta,"net_growth":delta/b1[previous]["net_mills"] if b1[previous]["net_mills"] else None}}


def gbp(mills):
    return f"{(Decimal(mills)/1000).quantize(Decimal('0.01')):,.2f}"


def run():
    started=time.perf_counter()
    bundle=compute()
    run_id=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"-retail-"+uuid.uuid4().hex[:8]
    output=ROOT/"runs"/run_id
    output.mkdir(parents=True,exist_ok=False)
    checks=bundle["checks"]
    summary={"run_id":run_id,"status":"passed" if all(c["passed"] for c in checks) else "mismatch","checks_passed":sum(c["passed"] for c in checks),"checks_total":len(checks),"input_rows":bundle["prepared"]["rows"],"quality":bundle["quality"],"comparison":bundle["comparison"],"elapsed_seconds":time.perf_counter()-started,"model_calls":0,"api_cost":0,"baseline_limit":"SQL and Decimal paths share declared extraction/classification; not external author gold or independent cleaning","code_hashes":{str(p.relative_to(ROOT)):sha(p.read_bytes()) for p in (Path(__file__),ROOT/"scripts/prepare_retail.py")}}
    artifacts={"summary.json":summary,"rules.json":RULES,"source.json":bundle["source"],"prepared.json":bundle["prepared"],"b0.json":bundle["B0"],"b1.json":bundle["B1"],"comparison.json":checks,"quality.json":bundle["quality"],"row-decisions.json":bundle["row_decisions"],"country-changes.json":bundle["country_changes"],"product-changes.json":bundle["product_changes"]}
    for name,payload in artifacts.items(): (output/name).write_bytes(encode(payload))
    monthly="".join(f"<tr><td>{month}{'（不完整）' if month=='2011-12' else ''}</td><td>{gbp(r['sales_mills'])}</td><td>{gbp(r['cancellation_mills'])}</td><td>{gbp(r['net_mills'])}</td><td>{r['sale_invoices']:,}</td></tr>" for month,r in bundle["B1"].items())
    def changes_table(items):
        return "".join(f"<tr><td>{html.escape(r['dimension'])}</td><td>{gbp(r['previous_net_mills'])}</td><td>{gbp(r['current_net_mills'])}</td><td>{gbp(r['delta_mills'])}</td></tr>" for r in items[:10])
    quality_labels={"kind_sale":"纳入：正向交易行","kind_cancellation":"纳入：取消记录行","kind_excluded":"排除：金额指标不可用行","nonpositive_price":"非正单价（单列，不作正常销售）","missing_customer":"缺客户编号（不因缺失删除金额）","duplicate_candidate_retained":"完全相同行的重复候选（保留）"}
    quality_rows="".join(f"<tr><td>{html.escape(quality_labels.get(reason,reason))}</td><td>{count:,}</td></tr>" for reason,count in sorted(bundle["quality"].items()))
    current=RULES["current_month"]
    metrics=bundle["B1"][current]
    report=f'''<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>经营数据复核与分析助手</title><style>body{{background:#f4f6fa;font:15px system-ui;color:#172b45;margin:0}}main{{max-width:1150px;margin:24px auto;background:white;padding:28px;border-radius:12px}}table{{border-collapse:collapse;width:100%;font-size:14px}}td,th{{padding:10px;text-align:right;border-bottom:1px solid #e0e5ed}}td:first-child,th:first-child{{text-align:left}}.cards{{display:flex;gap:16px;flex-wrap:wrap}}.card{{background:#eef3fa;padding:18px;flex:1;min-width:190px}}.number{{font-size:27px;margin-top:8px}}.note{{background:#fff4dd;padding:16px;line-height:1.6}}h2{{margin-top:30px}}a{{color:#245a9c}}</style><main><h1>经营数据复核与分析助手</h1><p>公开零售交易案例｜{current} 对比 {RULES['previous_month']}｜金额单位 GBP</p><div class="cards"><div class="card">正向交易金额<div class="number">£{gbp(metrics['sales_mills'])}</div></div><div class="card">取消记录金额<div class="number">£{gbp(metrics['cancellation_mills'])}</div></div><div class="card">有效交易净额<div class="number">£{gbp(metrics['net_mills'])}</div></div></div><h2>本期变化</h2><p>有效交易净额较上期变化 £{gbp(bundle['comparison']['net_delta_mills'])}。国家与商品贡献均完整加总到这一变化；这是算术贡献，不是因果解释。</p><div class="note">净额不是财务确认收入。C开头表示取消，不能据此确认现金退款或关联原销售订单。重复候选保留，缺客户ID不丢弃销售额。2011-12仅覆盖观察窗口的一部分，不与完整11月直接作业绩判断。</div><h2>月度概览</h2><table><tr><th>月份</th><th>正向交易金额</th><th>取消记录金额</th><th>有效交易净额</th><th>有效正向发票数</th></tr>{monthly}</table><h2>国家变化贡献（按绝对变化列前10）</h2><table><tr><th>国家</th><th>上期净额</th><th>本期净额</th><th>变化</th></tr>{changes_table(bundle['country_changes'])}</table><h2>商品变化贡献（按绝对变化列前10）</h2><table><tr><th>商品代码</th><th>上期净额</th><th>本期净额</th><th>变化</th></tr>{changes_table(bundle['product_changes'])}</table><h2>数据质量与下一步核查</h2><table><tr><th>问题/分类</th><th>行数</th></tr>{quality_rows}</table><p>优先核查非正价格、非取消的负数量和重复候选；不要直接删除。可从row-decisions.json的source_row追到原Excel。不同问题可能重叠，问题数不能相加当作排除数。核查销售大幅变化的商品和国家时，还需库存、促销和原取消单信息才能解释原因。</p><h2>指标复核</h2><p>固定SQL对照与Decimal流程：{summary['checks_passed']}/{len(checks)}项一致；共享输入分类，不是外部标准答案。利润/毛利率、流量转化、同批订单退货率、目标完成率：缺必要数据，unsupported。</p><p>来源：<a href="https://archive.ics.uci.edu/dataset/352/online+retail">UCI Online Retail</a>，Chen D. (2015)，DOI10.24432/C5BW33，CC BY4.0。原文件不改，金额以0.001 GBP精确聚合，展示时才舍入。公开历史案例不是服务真实企业的业务收益证明。运行ID：{run_id}。</p><p>输入版本与口径见source.json/prepared.json/rules.json；逐项数值见comparison.json，完整国家/商品贡献见对应JSON。</p></main></html>'''
    (output/"report.html").write_text(report,encoding="utf-8")
    manifest={"run_id":run_id,"status":"complete","files":{p.name:sha(p.read_bytes()) for p in output.iterdir()}}
    (output/"manifest.json").write_bytes(encode(manifest))
    return {"output":str(output),**summary}


def verify(directory):
    path=Path(directory).resolve(strict=True)
    manifest=json.loads((path/"manifest.json").read_bytes())
    expected={"summary.json","rules.json","source.json","prepared.json","b0.json","b1.json","comparison.json","quality.json","row-decisions.json","country-changes.json","product-changes.json","report.html"}
    if manifest["status"]!="complete" or set(manifest["files"])!=expected: raise ValueError("Incomplete/unexpected retail artifact set")
    for name,digest in manifest["files"].items():
        if (path/name).is_symlink() or sha((path/name).read_bytes())!=digest: raise ValueError("Retail artifact changed: "+name)
    saved=json.loads((path/"summary.json").read_bytes())
    if saved["run_id"]!=manifest["run_id"]: raise ValueError("Run identity differs")
    for name,digest in saved["code_hashes"].items():
        if sha((ROOT/name).read_bytes())!=digest: raise ValueError("Retail replay code differs")
    bundle=compute()
    keys={"rules.json":"rules","source.json":"source","prepared.json":"prepared","b0.json":"B0","b1.json":"B1","comparison.json":"checks","quality.json":"quality","row-decisions.json":"row_decisions","country-changes.json":"country_changes","product-changes.json":"product_changes"}
    for name,key in keys.items():
        if json.loads((path/name).read_bytes())!=bundle[key]: raise ValueError("Retail numerical replay differs: "+name)
    expected_summary={"status":"passed" if all(c["passed"] for c in bundle["checks"]) else "mismatch","checks_passed":sum(c["passed"] for c in bundle["checks"]),"checks_total":len(bundle["checks"]),"input_rows":bundle["prepared"]["rows"],"quality":bundle["quality"],"comparison":bundle["comparison"]}
    for key,value in expected_summary.items():
        if saved[key]!=value: raise ValueError("Retail summary differs from replay: "+key)
    return {"status":"verified","run_id":saved["run_id"],"artifacts_checked":len(expected),"checks_passed":sum(c["passed"] for c in bundle["checks"]),"checks_total":len(bundle["checks"])}

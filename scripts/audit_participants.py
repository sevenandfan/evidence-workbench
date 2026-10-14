"""Rebuild descriptive statistics from selected ASCII columns of the TSV.

latin-1 is used as a reversible byte-to-text mapping, NOT a claim about the
full survey file's character encoding. Selected column names and numeric
values are checked as ASCII; unrelated multilingual responses are unused.
"""
import csv
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from b1.engine import encode,sha
from b1.participants import reaggregate

DIRECTORY = ROOT / "data/manylabs1/participants/7a1334c053201ccdd2054417f74aa3d62776dd03"
SOURCE = DIRECTORY / "Tab.delimited.Cleaned.dataset.WITH.variable.values.dat"


def main():
    source = json.loads((DIRECTORY/"source_manifest.json").read_text(encoding="utf-8"))
    source_digest = sha(SOURCE.read_bytes())
    if source_digest != source["sha256"]:
        raise ValueError("Participant source digest mismatch")
    selected = ["sample","sunkgroup","sunkDV","sunkcosta","sunkcostb"]
    with SOURCE.open(encoding="latin-1",newline="") as handle:
        reader=csv.DictReader(handle,delimiter="\t")
        if len(reader.fieldnames)!=len(set(reader.fieldnames)) or not set(selected).issubset(reader.fieldnames):
            raise ValueError("Invalid participant TSV headers")
        columns={k:reader.fieldnames.index(k)+1 for k in selected}
        records=[]
        for row in reader:
            subset={k:row[k] for k in selected}
            if any(v is None or not v.isascii() for v in subset.values()):
                raise ValueError("Selected numeric field is missing or non-ASCII")
            records.append((reader.line_num,subset))
    summaries,extracted=reaggregate(records,include_all_sites=True)
    reference_raw=(ROOT/"data/manylabs1/sunk-costs-v1/dataset.json").read_bytes()
    reference_plan=json.loads((ROOT/"data/manylabs1/sunk-costs-v1/plan.json").read_bytes())
    if sha(reference_raw)!=reference_plan["dataset_sha256"]:
        raise ValueError("Author aggregate reference snapshot changed")
    expected=json.loads(reference_raw)
    reference={row["site"]:row for row in expected["samples"]}
    checks=[]
    matches=[]
    metrics=("n_paid","n_free","excluded_or_no_response","mean_paid","mean_free","sd_paid","sd_free")
    for rebuilt in summaries:
        if rebuilt["site"] in ("Overall:","abington"):
            ref=reference[rebuilt["site"]]
            binding="explicit source identity"
        else:
            # Compare the multiset of independently reconstructed statistics.
            # This is NOT independent proof of the names assigned to numeric codes.
            candidates=[r for r in reference.values() if r["site"]!="Overall:" and all(abs(rebuilt[k]-r[k]) <= (0 if k.startswith("n_") or k=="excluded_or_no_response" else 1e-7) for k in metrics)]
            if len(candidates)!=1:
                raise ValueError("No unique numeric signature match; investigate source mapping for "+rebuilt["site"])
            ref=candidates[0]
            binding="numeric signature match; name mapping not independently verified"
        if any(m["reference_site"]==ref["site"] for m in matches):
            raise ValueError("Two numeric sites matched the same author summary row")
        matches.append({"participant_scope":rebuilt["site"],"reference_site":ref["site"],"binding":binding})
        for metric in metrics:
            tolerance=0 if metric in ("n_paid","n_free","excluded_or_no_response") else 1e-7
            error=abs(rebuilt[metric]-ref[metric])
            checks.append({"site":rebuilt["site"],"metric":metric,"observed":rebuilt[metric],"author":ref[metric],"error":error,"tolerance":tolerance,"passed":error<=tolerance,"reference_range":ref["source_range"],"binding":binding})
    if sha(SOURCE.read_bytes())!=source_digest:
        raise ValueError("Participant source changed during audit")
    run_id=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"-participants-"+uuid.uuid4().hex[:8]
    output=ROOT/"runs"/run_id
    output.mkdir(parents=True,exist_ok=False)
    evidence={"run_id":run_id,"status":"passed" if all(c["passed"] for c in checks) else "mismatch","input_rows":len(records),"source":source,"source_columns_1_based":columns,"source_reading":"reversible latin-1 byte mapping; selected fields independently ASCII-validated","site_mapping":{"1":"abington (author Codebook.xlsx, Foglio1 row332)"},"scopes":["Overall:","abington"],"checks_passed":sum(c["passed"] for c in checks),"checks_total":len(checks),"source_unchanged":True,"summary":summaries,"checks":checks,"limits":["Source is author-cleaned, not the original uncleaned collection.","Only Overall and codebook-confirmed abington were matched by name; complete site-code map remains unverified.","No excluded participant was silently removed to match a target statistic."]}
    evidence["scopes"]=[r["site"] for r in summaries]
    evidence["identity_bindings"]=matches
    evidence["matched_by_numeric_signature"]=len(matches)-2
    evidence["reference_sha256"]=sha(reference_raw)
    evidence["code_hashes"]={str(p.relative_to(ROOT)):sha(p.read_bytes()) for p in (ROOT/"b1/participants.py",ROOT/"scripts/audit_participants.py")}
    for name,payload in (("audit.json",evidence),("participants-sunk-costs.json",extracted)):
        (output/name).write_bytes(encode(payload))
    table="\n".join(f'| {c["site"]} | {c["metric"]} | {c["author"]:.10g} | {c["observed"]:.10g} | {c["error"]:.3g} | {c["passed"]} |' for c in checks)
    report=f'''# 参与者级已清洗数据再聚合核验

运行：{run_id}。状态：{evidence["status"]}。

输入记录 {len(records)} 行；核验 {evidence["checks_passed"]}/{len(checks)} 项。只按作者声明的条件/因变量/缺失与1–9量表处理，没有调整规则凑参考答案。

| 范围 | 指标 | 作者表 | 重建 | 绝对误差 | 通过 |
|---|---|---|---|---|---|
{table}

完整列映射、来源hash、参与者来源行号、排除理由保存在 audit.json / participants-sunk-costs.json。

源文件某些无关多语言文本不是有效UTF-8，因此采用可逆字节映射，只有选中列按ASCII数值校验；没有替换或删除原字节。

本轮重建总体与36个numeric站点。总体与abington身份由原来源确认；其余35个站点按预定误差口径做7维统计签名的唯一匹配，且不允许多对一。统计集合一致不代表35个代码到名称的映射已被独立来源证实；不凭表格顺序猜名、不修改原数据凑答案。映射缺口仍需补齐。

源数据已由作者清洗，本轮不能证明作者从未清洗数据开始的完整清洗过程。
'''
    (output/"report.md").write_text(report,encoding="utf-8")
    manifest={"run_id":run_id,"files":{p.name:sha(p.read_bytes()) for p in output.iterdir()}}
    (output/"manifest.json").write_bytes(encode(manifest))
    print(json.dumps({"output":str(output),"status":evidence["status"],"rows":len(records),"checks_passed":evidence["checks_passed"],"checks_total":len(checks)},ensure_ascii=True))
    return 0 if evidence["status"]=="passed" else 1


if __name__=="__main__":
    raise SystemExit(main())

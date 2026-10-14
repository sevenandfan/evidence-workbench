"""Descriptive reaggregation of the author's cleaned Sunk Costs records."""
import math
import statistics

from .engine import reject

MISSING = {"", ".", "NA", "NaN", "nan"}


def numeric(value):
    if value.strip() in MISSING:
        return None
    try:
        result = float(value)
    except ValueError:
        reject("numeric", "Invalid participant numeric field")
    if not math.isfinite(result):
        reject("numeric", "Nonfinite participant field")
    return result


def reaggregate(records, include_all_sites=False):
    """No filtering tuned to gold. Only declared missingness and scale rules.

    Site code 1=abington is explicitly present in the author's codebook.
    Other numeric site labels remain codes until their complete map is verified.
    """
    groups = {"Overall:": {"paid": [], "free": [], "excluded": []}}
    if not include_all_sites:
        groups["abington"] = {"paid": [], "free": [], "excluded": []}
    extracted = []
    for line, row in records:
        needed = {"sample", "sunkgroup", "sunkDV", "sunkcosta", "sunkcostb"}
        if not needed.issubset(row):
            reject("data_schema", "Participant source lacks selected Sunk Costs fields")
        site, condition = numeric(row["sample"]), numeric(row["sunkgroup"])
        value = numeric(row["sunkDV"])
        paid, free = numeric(row["sunkcosta"]), numeric(row["sunkcostb"])
        if site is None or site != int(site) or not 1 <= site <= 36:
            reject("group", "Invalid/unmapped site code")
        if condition not in (0, 1, None):
            reject("group", "Unknown sunkgroup code")
        if value is not None and not 1 <= value <= 9:
            reject("range", "Sunk Costs rating must be 1-9")
        arm = "paid" if condition == 0 else "free" if condition == 1 else None
        if value is not None:
            original = paid if arm == "paid" else free if arm == "free" else None
            if original is None or value != original:
                reject("mapping", "sunkDV/condition does not match its original question column")
        targets = [groups["Overall:"]]
        if include_all_sites:
            label = "abington" if site == 1 else f"site_code:{int(site)}"
            if label not in groups:
                groups[label] = {"paid": [], "free": [], "excluded": []}
            targets.append(groups[label])
        elif site == 1:
            targets.append(groups["abington"])
        reason = "missing_response" if value is None else "missing_condition" if arm is None else None
        for target in targets:
            if reason:
                target["excluded"].append({"source_line": line, "reason": reason})
            else:
                target[arm].append(value)
        extracted.append({"source_line": line, "site_code": int(site), "condition_code": condition, "arm": arm, "rating": value, "original_paid_question": paid, "original_free_question": free, "exclusion_reason": reason})
    summaries = []
    for site, group in groups.items():
        if min(len(group["paid"]), len(group["free"])) < 2:
            reject("sample_size", "Too few participants in selected scope")
        summaries.append({"site": site, "n_paid": len(group["paid"]), "n_free": len(group["free"]), "mean_paid": statistics.mean(group["paid"]), "mean_free": statistics.mean(group["free"]), "sd_paid": statistics.stdev(group["paid"]), "sd_free": statistics.stdev(group["free"]), "excluded_or_no_response": len(group["excluded"]), "excluded": group["excluded"]})
    return summaries, extracted

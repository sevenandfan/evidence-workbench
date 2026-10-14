"""Inspect only the selected anonymized study columns, not general survey text."""
import csv
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "data/manylabs1/participants/7a1334c053201ccdd2054417f74aa3d62776dd03/Tab.delimited.Cleaned.dataset.WITH.variable.values.dat"
with SOURCE.open("rb") as handle:
    prefix = handle.read(4)
encoding = "utf-16" if prefix[:2] in (b"\xff\xfe", b"\xfe\xff") else "latin-1"
with SOURCE.open(encoding=encoding, newline="") as handle:
    reader = csv.DictReader(handle, delimiter="\t")
    selected = [c for c in reader.fieldnames if "sunk" in c.lower() or c.lower() in ("sample", "id", "participant_id", "participantid", "responseid")]
    print("ENCODING", encoding)
    print("SELECTED_HEADERS", selected)
    counts = {k: Counter() for k in selected if k in ("sample", "sunkgroup", "sunkDV")}
    count = 0
    for row in reader:
        if count < 5:
            print(json.dumps({k: row[k] for k in selected}, ensure_ascii=True))
        for key in counts:
            counts[key][row[key]] += 1
        count += 1
    print("ROWS", count)
    for key, counts in counts.items():
        print(key, json.dumps(dict(counts), ensure_ascii=True))

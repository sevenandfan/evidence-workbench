"""Acquire one pinned, CC0 de-identified participant file; do not execute code."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
COMMIT = "7a1334c053201ccdd2054417f74aa3d62776dd03"
NAME = "Tab.delimited.Cleaned.dataset.WITH.variable.values.dat"
EXPECTED_BYTES = 36072878
DIRECTORY = ROOT / "data/manylabs1/participants" / COMMIT
URL = "https://raw.githubusercontent.com/ManyLabsOpenScience/ManyLabs1/" + COMMIT + "/Data/" + quote(NAME)


def main():
    target = DIRECTORY / NAME
    manifest = DIRECTORY / "source_manifest.json"
    if target.exists():
        raw = target.read_bytes()
        saved = json.loads(manifest.read_text(encoding="utf-8"))
        if hashlib.sha256(raw).hexdigest() != saved["sha256"]:
            raise ValueError("Participant snapshot changed")
    else:
        with urlopen(Request(URL, headers={"User-Agent": "EvidenceWorkbench/0.2"}), timeout=90) as response:
            raw = response.read(EXPECTED_BYTES+1)
        if len(raw) != EXPECTED_BYTES:
            raise ValueError("Unexpected source size; do not accept a changed file")
        DIRECTORY.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        record = {"commit": COMMIT, "url": URL, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "license": "CC0; same project data license preserved in upstream snapshot", "retrieved_at": datetime.now(timezone.utc).isoformat(), "level": "author-cleaned de-identified participant data; not original raw collection"}
        manifest.write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(json.dumps({"path": str(target), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}, ensure_ascii=True))


if __name__ == "__main__":
    main()

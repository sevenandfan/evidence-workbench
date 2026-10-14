"""Download small, pinned Many Labs 1 sources; never execute upstream code."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
COMMIT = "7a1334c053201ccdd2054417f74aa3d62776dd03"
FILES = {
    "README.md": 301,
    "LICENSE": 35141,
    "Data/Data released under CC0 license.txt": 376,
    "Data/Codebook.xlsx": 23015,
    "MLforR.xlsx": 125325,
    "ML_ESCIfromR.xls": 111104,
    "Manylabs_ReplicationStudiesESCI.R": 6849,
}
DEST = ROOT / "data" / "manylabs1" / "upstream" / COMMIT


def acquire():
    # Stage all bytes in memory before creating the immutable source directory.
    downloaded = {}
    for name, size in FILES.items():
        url = "https://raw.githubusercontent.com/ManyLabsOpenScience/ManyLabs1/" + COMMIT + "/" + quote(name)
        if DEST.exists():
            data = (DEST / name).read_bytes()
        else:
            request = Request(url, headers={"User-Agent": "EvidenceWorkbench/0.1"})
            with urlopen(request, timeout=30) as response:
                data = response.read(size + 1)
        if len(data) != size:
            raise ValueError(f"Unexpected upstream size: {name}")
        downloaded[name] = (data, url)
    if DEST.exists():
        manifest = json.loads((DEST / "source_manifest.json").read_text(encoding="utf-8"))
        for name, (data, _) in downloaded.items():
            if hashlib.sha256(data).hexdigest() != manifest["files"][name]["sha256"]:
                raise ValueError("Snapshot hash mismatch: " + name)
        print("Existing snapshot verified:", DEST)
        return
    DEST.mkdir(parents=True, exist_ok=False)
    records = {}
    for name, (data, url) in downloaded.items():
        target = DEST / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        records[name] = {"url": url, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    manifest = {
        "source": "ManyLabsOpenScience/ManyLabs1", "commit": COMMIT,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "data_license": "CC0 1.0; see Data/Data released under CC0 license.txt",
        "code_license": "GPL-3.0; see LICENSE; upstream source preserved, not executed",
        "paper_doi": "10.1027/1864-9335/a000178", "data_paper_doi": "10.5334/jopd.ad",
        "dataset_level": "Author-provided aggregate statistics, not participant-level observations",
        "files": records,
    }
    (DEST / "source_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("Saved pinned sources:", DEST)
    print("Bytes:", sum(len(data) for data, _ in downloaded.values()))


if __name__ == "__main__":
    acquire()

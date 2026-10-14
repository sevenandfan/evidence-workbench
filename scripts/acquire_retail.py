"""Acquire CC BY 4.0 UCI data, preserve bytes; never execute downloaded code."""
import hashlib
import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
DIRECTORY = ROOT / "data/retail/uci-352-v1"
URL = "https://archive.ics.uci.edu/static/public/352/online+retail.zip"


def main():
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    manifest = DIRECTORY / "source_manifest.json"
    if manifest.exists():
        saved = json.loads(manifest.read_bytes())
        for name, info in saved["files"].items():
            if hashlib.sha256((DIRECTORY/name).read_bytes()).hexdigest() != info["sha256"]:
                raise ValueError("Existing source changed; do not overwrite")
        print(json.dumps({"status": "verified_existing", "directory": str(DIRECTORY)}))
        return
    raw = bytearray()
    with urlopen(Request(URL, headers={"User-Agent": "EvidenceWorkbench-retail/0.1"}), timeout=60) as response:
        expected = response.headers.get("Content-Length")
        while True:
            part = response.read(1024*1024)
            if not part: break
            raw.extend(part)
            if len(raw) > 30*1024*1024: raise ValueError("Unexpected archive size")
            print(f"Downloaded {len(raw)} bytes", flush=True)
    if expected and len(raw) != int(expected): raise ValueError("Truncated archive")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        if archive.namelist() != ["Online Retail.xlsx"] or archive.testzip() is not None:
            raise ValueError("Unexpected or damaged archive")
        content = archive.read("Online Retail.xlsx")
    if len(content) > 40*1024*1024: raise ValueError("Unexpected workbook size")
    files = {"online-retail.zip": bytes(raw), "Online Retail.xlsx": content}
    for name, payload in files.items():
        target = DIRECTORY/name
        if target.exists() and target.read_bytes() != payload: raise ValueError("Refuse overwrite")
        target.write_bytes(payload)
    record = {"source": "UCI Online Retail", "author": "Daqing Chen", "citation": "Chen, D. (2015). Online Retail. UCI Machine Learning Repository. DOI:10.24432/C5BW33", "url": URL, "metadata_url": "https://archive.ics.uci.edu/dataset/352/online+retail", "license": "CC BY 4.0", "license_url": "https://creativecommons.org/licenses/by/4.0/", "retrieved_at": datetime.now(timezone.utc).isoformat(), "version": "First local immutable snapshot uci-352-v1; upstream vendor SHA not supplied", "integrity": "HTTPS retrieval, content length when present, ZIP CRC; local SHA256 binds later use", "files": {name: {"bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()} for name,payload in files.items()}}
    manifest.write_text(json.dumps(record,ensure_ascii=False,indent=2), encoding="utf-8")
    print(json.dumps({"status":"acquired","directory":str(DIRECTORY),"files":record["files"]}))


if __name__ == "__main__": main()

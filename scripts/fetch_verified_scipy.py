"""Recover a truncated official wheel with bounded, validated range requests."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
NAME = "scipy-1.11.4-cp311-cp311-win_amd64.whl"
SIZE = 44095631
DIGEST = "acf8ed278cc03f5aff035e69cb511741e0418681d25fbbb86ca65429c4f4d9cd"
METADATA = "https://pypi.org/pypi/scipy/1.11.4/json"


def main():
    directory = ROOT / ".tools/wheelhouse"
    directory.mkdir(parents=True, exist_ok=True)
    with urlopen(METADATA, timeout=30) as response:
        metadata = json.load(response)
    item = next(x for x in metadata["urls"] if x["filename"] == NAME)
    if item["size"] != SIZE or item["digests"]["sha256"] != DIGEST or not item["url"].startswith("https://files.pythonhosted.org/"):
        raise ValueError("Official metadata differs from audited wheel")
    target = directory / NAME
    evidence = ROOT / "evals/scipy-wheel-recovery.json"
    parts = []
    content = bytearray()
    chunk = 4 * 1024 * 1024
    for start in range(0, SIZE, chunk):
        end = min(start + chunk, SIZE) - 1
        error = None
        for attempt in range(1, 4):
            try:
                with urlopen(Request(item["url"], headers={"Range": f"bytes={start}-{end}", "User-Agent": "EvidenceWorkbench/0.2"}), timeout=45) as response:
                    if response.status != 206 or response.headers.get("Content-Range") != f"bytes {start}-{end}/{SIZE}":
                        raise ValueError("Unexpected range response")
                    raw = response.read(end-start+2)
                if len(raw) != end-start+1:
                    raise ValueError("Truncated range")
                break
            except Exception as exc:
                error = str(exc)
                if attempt == 3:
                    raise RuntimeError(f"Range {start}-{end} failed: {error}") from exc
        content.extend(raw)
        parts.append({"start": start, "end": end, "attempts": attempt, "sha256": hashlib.sha256(raw).hexdigest()})
        print(f"Verified length: {len(content)}/{SIZE}", flush=True)
    if len(content) != SIZE or hashlib.sha256(content).hexdigest() != DIGEST:
        raise ValueError("Complete official wheel hash mismatch; do not install")
    if target.exists() and target.read_bytes() != content:
        raise ValueError("Refusing to overwrite different wheel")
    target.write_bytes(content)
    evidence.write_text(json.dumps({"retrieved_at": datetime.now(timezone.utc).isoformat(), "metadata_url": METADATA, "url": item["url"], "filename": NAME, "path": str(target), "bytes": SIZE, "sha256": DIGEST, "ranges": parts, "status": "verified", "method": "Official PyPI metadata and pythonhosted HTTPS byte ranges; full SHA256 required before installation"}, indent=2), encoding="utf-8")
    print(json.dumps({"status": "verified", "wheel": str(target), "evidence": str(evidence)}))


if __name__ == "__main__":
    main()

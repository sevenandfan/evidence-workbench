"""Execute all fixed regression cases and save machine-readable case outcomes."""
import json
import platform
import sys
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from b1.engine import sha


class RecordedResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cases = []

    def record(self, test, status, detail=None):
        self.cases.append({"id": test.id(), "status": status, "detail": detail})

    def addSuccess(self, test):
        super().addSuccess(test)
        self.record(test, "passed")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.record(test, "failed", self._exc_info_to_string(err, test))

    def addError(self, test, err):
        super().addError(test, err)
        self.record(test, "error", self._exc_info_to_string(err, test))

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.record(test, "skipped", reason)


if __name__ == "__main__":
    start = time.perf_counter()
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
    result = unittest.TextTestRunner(verbosity=2, resultclass=RecordedResult).run(suite)
    output = ROOT / "evals" / "regression-results.json"
    output.parent.mkdir(exist_ok=True)
    record = {"created_at": datetime.now(timezone.utc).isoformat(), "kind": "fixed_development_regression_not_heldout", "tests_run": result.testsRun, "passed": result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped), "skipped": len(result.skipped), "elapsed_seconds": time.perf_counter()-start, "python": sys.version, "platform": platform.platform(), "test_sources_sha256": {p.name: sha(p.read_bytes()) for p in sorted((ROOT / "tests").glob("test_*.py"))}, "cases": result.cases}
    output.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Evidence:", output)
    raise SystemExit(0 if result.wasSuccessful() else 1)

"""回归: 在 backend/ 直接跑 pytest 不得收集根目录手工联调脚本(会因连 OpenD 永久挂起)。"""
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def test_bare_pytest_collects_only_tests_dir():
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "--co", "-q", "-p", "no:cacheprovider"],
        cwd=BACKEND, capture_output=True, text=True, timeout=120,
    )
    out = r.stdout
    assert r.returncode == 0, out[-2000:] + r.stderr[-2000:]
    ids = [l for l in out.splitlines() if "::" in l]
    assert ids and all(l.startswith("tests/") for l in ids), [l for l in ids if not l.startswith("tests/")][:5]
    assert "test_futu_option_snapshot" not in out

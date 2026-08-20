import sys
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PKG_ROOT))

from utils.gold_result import load_result  # noqa: E402


def test_load_result_ok():
    assert load_result("ok", 10) == {"status": "ok", "rows_loaded": 10, "error": None}


def test_load_result_defaults():
    assert load_result("skipped") == {"status": "skipped", "rows_loaded": 0, "error": None}


def test_load_result_error():
    r = load_result("error", 0, "boom")
    assert r == {"status": "error", "rows_loaded": 0, "error": "boom"}

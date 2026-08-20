import sys
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PKG_ROOT))

import inspect  # noqa: E402
import etl_trans  # noqa: E402


def test_drop_duplicates_is_active_not_commented_out():
    source = inspect.getsource(etl_trans)
    lines = source.splitlines()
    active_lines = [
        line for line in lines
        if ".drop_duplicates(keep=" in line and not line.strip().startswith("#")
    ]
    assert active_lines, "expected an active (uncommented) .drop_duplicates(keep=...) call"

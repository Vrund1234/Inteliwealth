import sys
import uuid
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PKG_ROOT))

from sqlalchemy import text  # noqa: E402
from etl_pipeline import dedup  # noqa: E402
from utils.db import engine  # noqa: E402


def _cleanup(content_hash):
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM pipeline.etl_processed_files WHERE content_hash = :h"),
            {"h": content_hash},
        )


def test_is_already_processed_returns_none_for_unknown_hash():
    assert dedup.is_already_processed("no-such-hash") is None


def test_is_already_processed_returns_none_for_empty_hash():
    assert dedup.is_already_processed(None) is None
    assert dedup.is_already_processed("") is None


def test_mark_processed_then_is_already_processed():
    content_hash = f"test-{uuid.uuid4()}"
    handoff_id = str(uuid.uuid4())
    try:
        dedup.mark_processed(content_hash, handoff_id, 42)
        result = dedup.is_already_processed(content_hash)
        assert result == {"handoff_id": handoff_id, "rows_extracted": 42}
    finally:
        _cleanup(content_hash)


def test_mark_processed_is_idempotent_on_reinsert():
    content_hash = f"test-{uuid.uuid4()}"
    handoff_id_1 = str(uuid.uuid4())
    handoff_id_2 = str(uuid.uuid4())
    try:
        dedup.mark_processed(content_hash, handoff_id_1, 10)
        dedup.mark_processed(content_hash, handoff_id_2, 20)
        result = dedup.is_already_processed(content_hash)
        assert result == {"handoff_id": handoff_id_2, "rows_extracted": 20}
    finally:
        _cleanup(content_hash)

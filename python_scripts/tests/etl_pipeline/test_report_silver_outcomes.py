"""_report_silver_outcomes() is how the automated runner surfaces the
bronze -> silver promotion outcome in pipeline.etl_pipeline_log.

load_silver() sweeps every flag=0 row table-wide (it isn't scoped to a
single file), so its result is logged once per completed group -- the
same "GROUP" convention _report_gold_outcomes already uses for the gold
layer -- rather than once per file.
"""
import sys
import uuid
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PKG_ROOT))

from sqlalchemy import text  # noqa: E402
from etl_pipeline import runner  # noqa: E402
from utils.db import engine  # noqa: E402
from utils.gold_result import load_result  # noqa: E402


def _cleanup(group_key):
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM pipeline.etl_pipeline_log WHERE group_key = :k"), {"k": group_key}
        )


def _fetch(group_key):
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT layer, status, report_code, total_processed, error_message "
                "FROM pipeline.etl_pipeline_log WHERE group_key = :k"
            ),
            {"k": group_key},
        ).fetchone()
    return row


def test_logs_silver_completed_with_the_matching_table_row_count():
    group_key = f"CAMS|WBR2|ARN-1|{uuid.uuid4()}"
    run_id = runner.logging_repo.new_run_id()
    completed = [(
        group_key, "CAMS", "ARN-1",
        {"WBR2": {"handoff_id": str(uuid.uuid4()), "dtype": "transaction"}},
    )]
    silver_result = {"transaction_master_new": load_result("ok", 42)}

    try:
        runner._report_silver_outcomes(run_id, completed, silver_result)
        row = _fetch(group_key)
        assert row.layer == "SILVER"
        assert row.status == "COMPLETED"
        assert row.report_code == "GROUP"
        assert row.total_processed == 42
        assert row.error_message is None
    finally:
        _cleanup(group_key)


def test_logs_silver_failed_with_the_error_message():
    group_key = f"CAMS|WBR9|ARN-1|{uuid.uuid4()}"
    run_id = runner.logging_repo.new_run_id()
    completed = [(
        group_key, "CAMS", "ARN-1",
        {"WBR9": {"handoff_id": str(uuid.uuid4()), "dtype": "investor"}},
    )]
    silver_result = {"investor_master": load_result("error", 0, "silver table not found")}

    try:
        runner._report_silver_outcomes(run_id, completed, silver_result)
        row = _fetch(group_key)
        assert row.status == "FAILED"
        assert row.error_message == "silver table not found"
    finally:
        _cleanup(group_key)


def test_falls_back_to_dispatch_when_dtype_missing_from_a_stale_member():
    """A group that was already PROCESSING before this fix shipped has no
    'dtype' persisted on its member -- report_code + DISPATCH must still
    resolve the right silver table."""
    group_key = f"CAMS|WBR49|ARN-1|{uuid.uuid4()}"
    run_id = runner.logging_repo.new_run_id()
    completed = [(
        group_key, "CAMS", "ARN-1",
        {"WBR49": {"handoff_id": str(uuid.uuid4())}},  # no "dtype" key
    )]
    silver_result = {"sip_master_new": load_result("ok", 7)}

    try:
        runner._report_silver_outcomes(run_id, completed, silver_result)
        row = _fetch(group_key)
        assert row.status == "COMPLETED"
        assert row.total_processed == 7
    finally:
        _cleanup(group_key)


def test_skips_a_group_whose_only_member_was_a_duplicate():
    group_key = f"CAMS|WBR2|ARN-1|{uuid.uuid4()}"
    run_id = runner.logging_repo.new_run_id()
    completed = [(
        group_key, "CAMS", "ARN-1",
        {"WBR2": {"handoff_id": str(uuid.uuid4()), "dtype": "transaction", "skipped_duplicate": True}},
    )]

    try:
        runner._report_silver_outcomes(run_id, completed, {})
        assert _fetch(group_key) is None  # nothing logged, no real work happened
    finally:
        _cleanup(group_key)

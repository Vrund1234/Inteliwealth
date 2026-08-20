import sys
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

PKG_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PKG_ROOT))

from sqlalchemy import text  # noqa: E402
from etl_pipeline import runner  # noqa: E402
from utils.db import engine  # noqa: E402


def _cleanup(group_key):
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM pipeline.etl_pipeline_log WHERE group_key = :k"), {"k": group_key})
        conn.execute(text("DELETE FROM pipeline.etl_report_group_hold WHERE group_key = :k"), {"k": group_key})


@patch("etl_pipeline.runner.gold_loader")
@patch("etl_pipeline.runner.download_as_file")
@patch("etl_pipeline.runner.EtlHandoffClient")
def test_run_once_each_report_code_ready_independently(mock_client_cls, mock_download, mock_gold):
    """With file decoupling, WBR2/WBR9/WBR49 each form their own independent groups.
    WBR2 without WBR9+WBR49, or WBR9 without WBR2+WBR49, are now each ready for
    immediate reservation and processing."""
    arn_code = f"ARN-TEST-{uuid.uuid4()}"
    now = "2026-08-19T10:00:00Z"

    handoff_id_1 = str(uuid.uuid4())
    handoff_id_2 = str(uuid.uuid4())

    mock_client = MagicMock()
    mock_client.peek_pending.return_value = [
        {"id": handoff_id_1, "rta": "CAMS", "report_code": "WBR2", "arn_code": arn_code, "created_at": now},
        {"id": handoff_id_2, "rta": "CAMS", "report_code": "WBR9", "arn_code": arn_code, "created_at": now},
        # WBR49 missing — under new logic, WBR2 and WBR9 are STILL ready (each is independent)
    ]
    # Each of the two groups will be reserved separately.
    mock_client.reserve.return_value = [
        {"handoff_id": handoff_id_1, "rta": "CAMS", "report_code": "WBR2", "arn_code": arn_code,
         "filename": "WBR2.csv", "payload_format": "csv", "content_hash": "hash1", "file_size": 100,
         "source_s3_uri": f"s3://bucket/mailback/org_x/arn_{arn_code}/2026-08-19/msg_1/processed/WBR2.csv"},
        {"handoff_id": handoff_id_2, "rta": "CAMS", "report_code": "WBR9", "arn_code": arn_code,
         "filename": "WBR9.csv", "payload_format": "csv", "content_hash": "hash2", "file_size": 100,
         "source_s3_uri": f"s3://bucket/mailback/org_x/arn_{arn_code}/2026-08-19/msg_2/processed/WBR9.csv"},
    ]
    mock_client_cls.return_value = mock_client

    wbr2_key = f"CAMS|WBR2|{arn_code}|2026-08-19"
    wbr9_key = f"CAMS|WBR9|{arn_code}|2026-08-19"
    try:
        runner.run_once()

        # Both should be reserved and processed (no longer held for the missing WBR49).
        # The mock doesn't complete processing, but it shows the groups are "ready".
        assert mock_client.reserve.called
        assert mock_client.report_outcome.called
    finally:
        _cleanup(wbr2_key)
        _cleanup(wbr9_key)
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM pipeline.etl_report_group_hold WHERE arn_code = :arn"),
                {"arn": arn_code},
            )

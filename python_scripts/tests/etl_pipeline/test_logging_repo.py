import sys
import uuid
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PKG_ROOT))

from sqlalchemy import text  # noqa: E402
from etl_pipeline import logging_repo  # noqa: E402
from utils.db import engine  # noqa: E402


def _cleanup(group_key):
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM pipeline.etl_pipeline_log WHERE group_key = :k"), {"k": group_key}
        )
        conn.execute(
            text("DELETE FROM pipeline.etl_report_group_hold WHERE group_key = :k"), {"k": group_key}
        )


def test_new_run_id_is_a_uuid_string():
    run_id = logging_repo.new_run_id()
    uuid.UUID(run_id)  # raises if not a valid UUID string


def test_log_event_writes_a_row():
    group_key = f"TEST|ARN-1|{uuid.uuid4()}"
    run_id = logging_repo.new_run_id()
    try:
        logging_repo.log_event(
            run_id=run_id, handoff_id=str(uuid.uuid4()), group_key=group_key,
            rta="CAMS", arn_code="ARN-1", report_code="WBR2", filename="WBR2.csv",
            source_s3_uri="s3://bucket/x.csv", content_hash="h1",
            layer="BRONZE", status="COMPLETED", total_processed=10,
        )
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT status, total_processed FROM pipeline.etl_pipeline_log WHERE group_key = :k"),
                {"k": group_key},
            ).fetchone()
        assert row is not None
        assert row[0] == "COMPLETED"
        assert row[1] == 10
    finally:
        _cleanup(group_key)


def test_log_event_stores_api_response_for_reference():
    group_key = f"TEST|ARN-3|{uuid.uuid4()}"
    run_id = logging_repo.new_run_id()
    api_response = {
        "success": False, "status_code": 422, "message": "Unprocessable Entity",
        "detail": [{"loc": ["body", "failure_reason"], "msg": "failure_reason must be one of [...]"}],
    }
    try:
        logging_repo.log_event(
            run_id=run_id, handoff_id=str(uuid.uuid4()), group_key=group_key,
            rta="CAMS", arn_code="ARN-3", report_code="WBR2", filename="WBR2.csv",
            source_s3_uri="s3://bucket/x.csv", content_hash="h1",
            layer="BRONZE", status="FAILED", error_message="boom",
            api_response=api_response,
        )
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT api_response FROM pipeline.etl_pipeline_log WHERE group_key = :k"),
                {"k": group_key},
            ).fetchone()
        assert row is not None
        assert row[0] == api_response
    finally:
        _cleanup(group_key)


def test_log_event_api_response_defaults_to_null():
    group_key = f"TEST|ARN-4|{uuid.uuid4()}"
    run_id = logging_repo.new_run_id()
    try:
        logging_repo.log_event(
            run_id=run_id, handoff_id=str(uuid.uuid4()), group_key=group_key,
            rta="CAMS", arn_code="ARN-4", report_code="WBR2", filename="WBR2.csv",
            source_s3_uri="s3://bucket/x.csv", content_hash="h1",
            layer="HOLD", status="HOLDING",
        )
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT api_response FROM pipeline.etl_pipeline_log WHERE group_key = :k"),
                {"k": group_key},
            ).fetchone()
        assert row is not None
        assert row[0] is None
    finally:
        _cleanup(group_key)


def test_upsert_group_hold_insert_then_update():
    group_key = f"TEST|ARN-2|{uuid.uuid4()}"
    try:
        logging_repo.upsert_group_hold(
            group_key=group_key, rta="CAMS", arn_code="ARN-2", s3_date=None,
            required_report_codes=["WBR2", "WBR9", "WBR49"],
            members={"WBR2": {"handoff_id": "h1"}}, status="HOLDING",
        )
        groups = logging_repo.get_groups_with_status(["HOLDING"])
        match = next(g for g in groups if g["group_key"] == group_key)
        assert match["status"] == "HOLDING"
        assert match["members"] == {"WBR2": {"handoff_id": "h1"}}

        logging_repo.upsert_group_hold(
            group_key=group_key, rta="CAMS", arn_code="ARN-2", s3_date="2026-08-19",
            required_report_codes=["WBR2", "WBR9", "WBR49"],
            members={"WBR2": {"handoff_id": "h1"}, "WBR9": {"handoff_id": "h2"}},
            status="READY",
        )
        groups = logging_repo.get_groups_with_status(["READY"])
        match = next(g for g in groups if g["group_key"] == group_key)
        assert match["status"] == "READY"
        assert set(match["members"].keys()) == {"WBR2", "WBR9"}
    finally:
        _cleanup(group_key)

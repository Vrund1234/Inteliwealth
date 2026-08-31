"""logging_repo against the real database, following the existing
bronze._test_* pattern: rows carry a per-test sentinel run_id and the fixture
deletes them afterwards."""

import json
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from etl_pipeline.logging_repo import (
    is_already_processed,
    log_event,
    mark_processed,
    new_run_id,
    redact,
)
from utils.db import engine


@pytest.fixture
def run_id():
    value = new_run_id()
    yield value
    with engine.begin() as conn:
        conn.execute(text(
            "DELETE FROM pipeline.etl_pipeline_log WHERE run_id = CAST(:r AS uuid)"
        ), {"r": value})


@pytest.fixture
def content_hash():
    value = "sha256:" + uuid.uuid4().hex
    yield value
    with engine.begin() as conn:
        conn.execute(text(
            "DELETE FROM pipeline.etl_processed_files WHERE content_hash = :h"
        ), {"h": value})


def _rows(run_id):
    with engine.begin() as conn:
        result = conn.execute(text(
            "SELECT * FROM pipeline.etl_pipeline_log "
            "WHERE run_id = CAST(:r AS uuid) ORDER BY created_at, log_id"
        ), {"r": run_id})
        return [dict(row._mapping) for row in result]


# ---- run ids -------------------------------------------------------------

def test_new_run_id_is_a_uuid_string():
    value = new_run_id()

    assert isinstance(value, str)
    assert uuid.UUID(value)


def test_new_run_ids_are_distinct():
    assert new_run_id() != new_run_id()


# ---- redaction -----------------------------------------------------------

@pytest.mark.parametrize("key", [
    "password", "Password", "access_token", "refresh_token", "authorization", "token",
])
def test_every_secret_key_is_masked(key):
    assert redact({key: "s3cret"})[key] == "***"


def test_redaction_recurses_into_nested_structures():
    payload = {"body": {"creds": [{"password": "p"}, {"email": "a@b.c"}]}}

    result = redact(payload)

    assert result["body"]["creds"][0]["password"] == "***"
    assert result["body"]["creds"][1]["email"] == "a@b.c"


def test_redaction_leaves_ordinary_values_alone():
    payload = {"runner": "de-etl-worker-1", "limit": 10, "status": "COMPLETED"}

    assert redact(payload) == payload


def test_redaction_handles_none_and_scalars():
    assert redact(None) is None
    assert redact("plain") == "plain"
    assert redact(7) == 7


# ---- log_event -----------------------------------------------------------

def test_a_minimal_event_is_written(run_id):
    log_id = log_event(run_id, "RUN", "STARTED")

    rows = _rows(run_id)
    assert len(rows) == 1
    assert str(rows[0]["log_id"]) == log_id
    assert rows[0]["layer"] == "RUN"
    assert rows[0]["status"] == "STARTED"
    assert rows[0]["handoff_id"] is None
    assert rows[0]["created_at"] is not None


def test_every_documented_field_round_trips(run_id):
    handoff_id = str(uuid.uuid4())
    started = datetime.now(timezone.utc)
    ended = started + timedelta(seconds=3)

    log_event(
        run_id, "BRONZE", "COMPLETED",
        handoff_id=handoff_id, entity="transaction",
        rta="CAMS", arn_code="ARN-266051", report_code="WBR2",
        filename="WBR2.dbf", report_date=date(2026, 8, 25),
        source_s3_uri="s3://b/2026-08-25/WBR2.dbf",
        content_hash="sha256:abc", payload_format="dbf", file_size=1234,
        total_records=100, total_processed=90, total_duplicate=10,
        comment="ok", http_status=200,
        started_at=started, ended_at=ended,
    )

    row = _rows(run_id)[0]
    assert str(row["handoff_id"]) == handoff_id
    assert row["entity"] == "transaction"
    assert row["rta"] == "CAMS"
    assert row["arn_code"] == "ARN-266051"
    assert row["report_code"] == "WBR2"
    assert row["filename"] == "WBR2.dbf"
    assert row["report_date"] == date(2026, 8, 25)
    assert row["payload_format"] == "dbf"
    assert row["file_size"] == 1234
    assert row["total_records"] == 100
    assert row["total_processed"] == 90
    assert row["total_duplicate"] == 10
    assert row["http_status"] == 200


def test_duration_is_derived_from_the_timestamps(run_id):
    started = datetime.now(timezone.utc)
    log_event(run_id, "SILVER", "COMPLETED",
              started_at=started, ended_at=started + timedelta(milliseconds=1500))

    assert _rows(run_id)[0]["duration_ms"] == 1500


def test_an_explicit_duration_is_not_overwritten(run_id):
    started = datetime.now(timezone.utc)
    log_event(run_id, "SILVER", "COMPLETED", started_at=started,
              ended_at=started + timedelta(seconds=9), duration_ms=42)

    assert _rows(run_id)[0]["duration_ms"] == 42


def test_json_fields_are_stored_as_jsonb(run_id):
    log_event(run_id, "RESERVE", "COMPLETED",
              api_request={"method": "POST", "path": "/etl-handoff/reservations",
                           "body": {"runner": "w1", "limit": 10}},
              api_response={"data": {"items": []}})

    row = _rows(run_id)[0]
    assert row["api_request"]["body"]["limit"] == 10
    assert row["api_response"]["data"]["items"] == []


def test_a_secret_in_an_api_payload_never_reaches_the_table(run_id):
    log_event(run_id, "RUN", "STARTED",
              api_request={"method": "POST", "path": "/auth/login",
                           "body": {"email": "de-runner@intelliwealth.com",
                                    "password": "de-runner@123"}},
              api_response={"data": {"access_token": "ey.real.token"}})

    row = _rows(run_id)[0]
    serialized = json.dumps({"q": row["api_request"], "r": row["api_response"]})
    assert "de-runner@123" not in serialized
    assert "ey.real.token" not in serialized
    assert row["api_request"]["body"]["password"] == "***"


def test_an_unserializable_value_does_not_break_the_write(run_id):
    # A datetime inside an API payload must not raise -- losing the log row
    # would lose the only record of what happened.
    log_event(run_id, "REPORT", "COMPLETED",
              api_response={"reserved_at": datetime.now(timezone.utc)})

    assert len(_rows(run_id)) == 1


def test_an_unknown_field_raises(run_id):
    with pytest.raises(TypeError):
        log_event(run_id, "RUN", "STARTED", not_a_column="x")


def test_a_long_comment_is_truncated(run_id):
    log_event(run_id, "BRONZE", "FAILED", comment="x" * 5000)

    assert len(_rows(run_id)[0]["comment"]) == 2000


# ---- processed files -----------------------------------------------------

def test_an_unseen_hash_is_not_already_processed(content_hash):
    assert is_already_processed(content_hash) is False


def test_a_marked_hash_is_already_processed(content_hash):
    mark_processed(content_hash, str(uuid.uuid4()), rows_extracted=100)

    assert is_already_processed(content_hash) is True


def test_marking_the_same_hash_twice_does_not_raise(content_hash):
    first = str(uuid.uuid4())
    mark_processed(content_hash, first, rows_extracted=100)
    mark_processed(content_hash, str(uuid.uuid4()), rows_extracted=200)

    with engine.begin() as conn:
        rows = conn.execute(text(
            "SELECT rows_extracted FROM pipeline.etl_processed_files "
            "WHERE content_hash = :h"
        ), {"h": content_hash}).fetchall()
    assert len(rows) == 1


def test_a_null_hash_is_never_treated_as_processed():
    # content_hash is nullable on EtlHandoffItem; a file without one must be
    # processed rather than skipped.
    assert is_already_processed(None) is False
    assert is_already_processed("") is False


def test_marking_a_null_hash_is_a_noop():
    mark_processed(None, str(uuid.uuid4()))

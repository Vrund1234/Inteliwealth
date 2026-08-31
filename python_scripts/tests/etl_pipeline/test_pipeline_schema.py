"""Post-migration state check for the pipeline log schema. Mirrors
tests/test_dedup_migration.py: assert the objects the migration is supposed
to have created actually exist in the live database."""

from sqlalchemy import text

from utils.db import engine

EXPECTED_LOG_COLUMNS = {
    "log_id", "run_id", "handoff_id", "layer", "entity", "status",
    "rta", "arn_code", "report_code", "filename", "report_date",
    "source_s3_uri", "content_hash", "payload_format", "file_size",
    "total_records", "total_processed", "total_duplicate", "comment",
    "api_request", "api_response", "http_status",
    "started_at", "ended_at", "duration_ms", "created_at",
}

EXPECTED_INDEXES = {
    "ix_etl_pipeline_log_run_id",
    "ix_etl_pipeline_log_handoff_id",
    "ix_etl_pipeline_log_status",
    "ix_etl_pipeline_log_layer",
    "ix_etl_pipeline_log_report_date",
    "ix_etl_pipeline_log_created_at",
}


def _columns(table):
    with engine.begin() as conn:
        return {
            row[0]
            for row in conn.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'pipeline' AND table_name = :t"
            ), {"t": table})
        }


def test_pipeline_schema_exists():
    with engine.begin() as conn:
        found = conn.execute(text(
            "SELECT 1 FROM information_schema.schemata WHERE schema_name = 'pipeline'"
        )).scalar()
    assert found == 1


def test_etl_pipeline_log_has_every_column():
    assert EXPECTED_LOG_COLUMNS <= _columns("etl_pipeline_log")


def test_etl_processed_files_has_every_column():
    assert {"content_hash", "handoff_id", "rows_extracted", "processed_at"} <= _columns(
        "etl_processed_files"
    )


def test_content_hash_is_the_primary_key():
    # Cross-run idempotency depends on this: a second run inserting an
    # already-recorded hash must conflict, not duplicate.
    with engine.begin() as conn:
        cols = [
            row[0]
            for row in conn.execute(text("""
                SELECT kcu.column_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                  ON tc.constraint_name = kcu.constraint_name
                 AND tc.table_schema = kcu.table_schema
                WHERE tc.table_schema = 'pipeline'
                  AND tc.table_name = 'etl_processed_files'
                  AND tc.constraint_type = 'PRIMARY KEY'
            """))
        ]
    assert cols == ["content_hash"]


def test_expected_indexes_exist():
    with engine.begin() as conn:
        found = {
            row[0]
            for row in conn.execute(text(
                "SELECT indexname FROM pg_indexes WHERE schemaname = 'pipeline'"
            ))
        }
    assert EXPECTED_INDEXES <= found


def test_api_request_and_api_response_are_jsonb():
    with engine.begin() as conn:
        types = dict(conn.execute(text(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = 'pipeline' AND table_name = 'etl_pipeline_log' "
            "AND column_name IN ('api_request', 'api_response')"
        )).fetchall())
    assert types == {"api_request": "jsonb", "api_response": "jsonb"}

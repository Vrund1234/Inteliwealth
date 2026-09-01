-- ============================================================
-- Pipeline run log -- see
-- docs/superpowers/specs/2026-08-31-etl-automation-pipeline-design.md
--
-- Every statement is idempotent (IF NOT EXISTS), so re-running this
-- file against an already-migrated database is a no-op. Applied with the
-- values from python_scripts/.env -- do NOT hardcode a database name here,
-- the project DB is currently "25_08_2025_intelliwealth_layer_db" and has
-- been renamed before:
--   set -a; . python_scripts/.env; set +a
--   PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "$DB_PORT" \
--     -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 \
--     -f sql_scripts/etl_pipeline_schema_2026-08-31.sql
-- ============================================================

CREATE SCHEMA IF NOT EXISTS pipeline;

CREATE TABLE IF NOT EXISTS pipeline.etl_pipeline_log (
    log_id           uuid PRIMARY KEY,
    run_id           uuid        NOT NULL,   -- one cron invocation
    handoff_id       uuid,                   -- NULL for run-scoped rows
    layer            text        NOT NULL,   -- RUN|RESERVE|BRONZE|SCHEME_MAPPING|SILVER|GOLD|REPORT
    entity           text,                   -- transaction|investor|sip, or the gold entity name
    status           text        NOT NULL,   -- STARTED|COMPLETED|FAILED|SKIPPED|SKIPPED_DUPLICATE
    rta              text,
    arn_code         text,
    report_code      text,
    filename         text,
    report_date      date,                   -- parsed from the S3 URI partition segment
    source_s3_uri    text,
    content_hash     text,
    payload_format   text,
    file_size        bigint,
    total_records    integer,                -- "total data"
    total_processed  integer,                -- "total processed data"
    total_duplicate  integer,                -- "total duplicate data"
    comment          text,                   -- error text, or a note
    api_request      jsonb,                  -- redacted; never carries a token or password
    api_response     jsonb,
    http_status      integer,
    started_at       timestamptz,
    ended_at         timestamptz,
    duration_ms      integer,                -- "time taken to process data"
    created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_etl_pipeline_log_run_id      ON pipeline.etl_pipeline_log (run_id);
CREATE INDEX IF NOT EXISTS ix_etl_pipeline_log_handoff_id  ON pipeline.etl_pipeline_log (handoff_id);
CREATE INDEX IF NOT EXISTS ix_etl_pipeline_log_status      ON pipeline.etl_pipeline_log (status);
CREATE INDEX IF NOT EXISTS ix_etl_pipeline_log_layer       ON pipeline.etl_pipeline_log (layer);
CREATE INDEX IF NOT EXISTS ix_etl_pipeline_log_report_date ON pipeline.etl_pipeline_log (report_date);
CREATE INDEX IF NOT EXISTS ix_etl_pipeline_log_created_at  ON pipeline.etl_pipeline_log (created_at);

-- Cross-run idempotency against the API's content_hash (SHA-256 of the file
-- bytes, stable across RTA re-sends). A file whose hash is already here is
-- logged SKIPPED_DUPLICATE and reported COMPLETED without re-processing.
CREATE TABLE IF NOT EXISTS pipeline.etl_processed_files (
    content_hash   text PRIMARY KEY,
    handoff_id     uuid        NOT NULL,
    rows_extracted integer,
    processed_at   timestamptz NOT NULL DEFAULT now()
);

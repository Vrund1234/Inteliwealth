-- ETL pipeline operational tracking schema.
-- Idempotent: every statement is CREATE ... IF NOT EXISTS, safe to re-run.

CREATE SCHEMA IF NOT EXISTS pipeline;

CREATE TABLE IF NOT EXISTS pipeline.etl_pipeline_log (
    log_id                   UUID PRIMARY KEY,
    run_id                   UUID NOT NULL,
    handoff_id               UUID,
    group_key                VARCHAR NOT NULL,
    rta                      VARCHAR NOT NULL,
    arn_code                 VARCHAR,
    report_code              VARCHAR NOT NULL,
    filename                 VARCHAR,
    source_s3_uri            VARCHAR,
    content_hash             VARCHAR,
    layer                    VARCHAR NOT NULL,
    status                   VARCHAR NOT NULL,
    total_records_in_report  INTEGER,
    total_processed          INTEGER,
    error_message            VARCHAR,
    started_at               TIMESTAMPTZ,
    ended_at                 TIMESTAMPTZ,
    api_response             JSONB,                     -- raw backend response body for this
                                                          -- event's report_outcome() PATCH call,
                                                          -- kept for reference/debugging
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Retrofit for a table that already existed before api_response was added —
-- CREATE TABLE IF NOT EXISTS above is a no-op on an existing table, so this
-- is what actually adds the column on a pre-existing install. Safe to re-run.
ALTER TABLE pipeline.etl_pipeline_log ADD COLUMN IF NOT EXISTS api_response JSONB;

CREATE INDEX IF NOT EXISTS ix_etl_pipeline_log_group_key ON pipeline.etl_pipeline_log (group_key);
CREATE INDEX IF NOT EXISTS ix_etl_pipeline_log_handoff_id ON pipeline.etl_pipeline_log (handoff_id);
CREATE INDEX IF NOT EXISTS ix_etl_pipeline_log_status ON pipeline.etl_pipeline_log (status);
CREATE INDEX IF NOT EXISTS ix_etl_pipeline_log_created_at ON pipeline.etl_pipeline_log (created_at);

-- s3_date is nullable: a group first appears from GET /pending's coarse key
-- (rta|arn_code|created_at-date), before we've reserved anything and learned
-- the real S3 partition date. It's filled in once reservation succeeds.
CREATE TABLE IF NOT EXISTS pipeline.etl_report_group_hold (
    group_key               VARCHAR PRIMARY KEY,
    rta                     VARCHAR NOT NULL,
    arn_code                VARCHAR,
    s3_date                 DATE,
    required_report_codes   JSONB NOT NULL,
    members                 JSONB NOT NULL DEFAULT '{}'::jsonb,
    status                  VARCHAR NOT NULL,
    first_seen_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at            TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_etl_report_group_hold_status ON pipeline.etl_report_group_hold (status);

CREATE TABLE IF NOT EXISTS pipeline.etl_processed_files (
    content_hash    VARCHAR PRIMARY KEY,
    handoff_id      UUID NOT NULL,
    rows_extracted  INTEGER,
    processed_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

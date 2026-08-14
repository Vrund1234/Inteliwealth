-- 003_audit.sql
--
-- Provenance, load accounting, rejected rows.
--
-- The existing pipeline has no equivalent of any of these. It drops 10,862
-- transactions between silver and gold with no reject table and no counter, so a
-- healthy run and a lossy one are indistinguishable. It also declares source_file_id
-- on two gold tables and never populates it, so no row can be traced to an upload.

-- ---------------------------------------------------------------------
-- Every file the pipeline has ingested.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_wbr.source_files (
    source_file_id     uuid         PRIMARY KEY,
    file_name          text         NOT NULL,
    file_path          text         NOT NULL,
    sha256             text         NOT NULL,
    byte_size          bigint       NOT NULL,
    entity             text,
    report_variant     text,
    format             text         NOT NULL,
    rows_in_file       integer      NOT NULL,
    columns_in_file    integer      NOT NULL,
    period_from        date,
    period_to          date,
    ingested_at        timestamptz  NOT NULL DEFAULT now(),

    -- Content hash, not filename: the same report re-downloaded under a different
    -- name is the same delivery and must not be ingested twice.
    CONSTRAINT uq_source_files_sha256 UNIQUE (sha256)
);

CREATE INDEX IF NOT EXISTS ix_source_files_entity
    ON audit_wbr.source_files (entity, ingested_at DESC);


-- ---------------------------------------------------------------------
-- One row per (file, entity, layer). Answers "was that run lossy?".
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_wbr.load_summary (
    load_id            uuid         PRIMARY KEY,
    source_file_id     text,
    entity             text         NOT NULL,
    layer              text         NOT NULL
                                    CHECK (layer IN ('bronze', 'silver', 'gold')),
    rows_read          integer      NOT NULL DEFAULT 0,
    rows_written       integer      NOT NULL DEFAULT 0,
    rows_rejected      integer      NOT NULL DEFAULT 0,
    status             text         NOT NULL
                                    CHECK (status IN ('RUNNING', 'OK', 'FAILED')),
    message            text,
    started_at         timestamptz  NOT NULL,
    finished_at        timestamptz  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_load_summary_entity_layer
    ON audit_wbr.load_summary (entity, layer, finished_at DESC);

CREATE INDEX IF NOT EXISTS ix_load_summary_failed
    ON audit_wbr.load_summary (finished_at DESC)
    WHERE status = 'FAILED' OR rows_rejected > 0;


-- ---------------------------------------------------------------------
-- Every refused row, with the rule that refused it.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_wbr.rejects (
    reject_id          uuid         PRIMARY KEY,
    entity             text         NOT NULL,
    rule               text         NOT NULL,
    reason             text         NOT NULL,
    source_file_id     text,
    row_number_in_file integer,
    payload            jsonb,
    created_at         timestamptz  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_rejects_entity_rule
    ON audit_wbr.rejects (entity, rule, created_at DESC);

CREATE INDEX IF NOT EXISTS ix_rejects_source_file
    ON audit_wbr.rejects (source_file_id, row_number_in_file);


-- ---------------------------------------------------------------------
-- Convenience view: the last outcome per entity and layer.
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW audit_wbr.last_load AS
SELECT DISTINCT ON (entity, layer)
       entity,
       layer,
       status,
       rows_read,
       rows_written,
       rows_rejected,
       message,
       finished_at
FROM   audit_wbr.load_summary
ORDER  BY entity, layer, finished_at DESC;

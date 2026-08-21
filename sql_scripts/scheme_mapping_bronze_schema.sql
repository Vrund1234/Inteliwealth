-- Scheme-mapping schema, bronze.
--
-- Replaces sql_scripts/scheme_mapping_phase2.sql, which creates these tables
-- in `public`. Every reader in python_scripts/ addresses them as `bronze.*`
-- (scheme_mapping.py, scheme_matching/reference.py, scheme_matching/aliases.py,
-- map_unmatched_nav_name.py, promote_approved_mappings.py, suggest_aliases.py,
-- app.py), so a database built from the phase2 script has the tables in a
-- schema nothing reads and the pipeline fails at the first query.
--
-- Generated from the live layer database, so the definitions match what the
-- code has actually been running against rather than what was intended.
--
-- Idempotent: safe to run against a fresh database or an existing one.
--
-- RUN AGAINST: the LAYER database (utils.db.PROJECT_DATABASE).
-- NOT the master database -- public.amfi_scheme_master, public.scheme_master,
-- public.nav_master and public.rta_amc_code live there and are not created
-- here.
--
-- Deliberately NOT carried over from phase2.sql:
--   ALTER TABLE public.rta_amc_code ADD COLUMN amfi_amc_code
-- reference.load_amc_map() derives amfi_amc_code in the SELECT, by testing the
-- RTA's amc_code against amfi_scheme_master. Nothing reads a stored column,
-- and the live master database does not have one.

BEGIN;

CREATE SCHEMA IF NOT EXISTS bronze;


-- ---------------------------------------------------------------------------
-- scheme_mapping : one row per (rta, rta_scheme_code), the pipeline's output.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bronze.scheme_mapping (
    mapping_id             uuid DEFAULT gen_random_uuid() NOT NULL,
    scheme_id              varchar(50),
    rta                    varchar(20)  NOT NULL,
    rta_amc_code           varchar(50),
    rta_scheme_code        varchar(100) NOT NULL,
    rta_scheme_name        text,
    normalized_scheme_name text,
    amfi_scheme_code       varchar(50),
    mapping_source         varchar(50),
    mapping_confidence     integer,
    mapping_status         varchar(20),
    mapping_rule_version   varchar(20),
    first_seen_at          timestamp,
    last_seen_at           timestamp,
    created_at             timestamp DEFAULT CURRENT_TIMESTAMP,
    updated_at             timestamp DEFAULT CURRENT_TIMESTAMP,
    -- verified_at doubles as the marker that a mapping came from an approved
    -- review. scheme_mapping.py's upsert reads it to avoid overwriting such a
    -- row with NULL on a later run: those schemes are in the review queue
    -- precisely because the engine cannot resolve them, so it contributes
    -- nothing for them every time it runs.
    verified_by            varchar(100),
    verified_at            timestamp,
    review_notes           text,
    valid_from             date,
    valid_to               date,
    is_active              boolean,
    rta_isin               varchar(20),
    CONSTRAINT scheme_mapping_pkey PRIMARY KEY (mapping_id),
    -- The upsert in scheme_mapping.py conflicts on exactly this pair. An RTA
    -- scheme code is a single share class with a single NAV, so it resolves to
    -- exactly one AMFI scheme.
    CONSTRAINT uq_scheme_mapping UNIQUE (rta, rta_scheme_code)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_scheme_mapping_amfi
    ON bronze.scheme_mapping (rta, rta_scheme_code, amfi_scheme_code);


-- ---------------------------------------------------------------------------
-- scheme_name_alias : the vocabulary. TOKEN is word-level and global;
-- FUND_RENAME is phrase-level and may be scoped to one AMC.
--
-- Seeded separately by scheme_name_alias_seed.sql. An empty table is valid but
-- costs matches: AMC renames (Nippon/Reliance, Bandhan/IDFC) leave the RTA and
-- master spellings of the same fund unable to match.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bronze.scheme_name_alias (
    alias_id        uuid NOT NULL,
    raw_term        text NOT NULL,
    normalized_term text DEFAULT '' NOT NULL,
    alias_type      varchar NOT NULL,
    amc_code        varchar,
    is_active       boolean DEFAULT true NOT NULL,
    created_at      timestamp DEFAULT now() NOT NULL,
    CONSTRAINT scheme_name_alias_pkey PRIMARY KEY (alias_id),
    CONSTRAINT scheme_name_alias_alias_type_check
        CHECK (alias_type::text = ANY (ARRAY['TOKEN'::varchar::text,
                                             'FUND_RENAME'::varchar::text]))
);

-- COALESCE, not a plain column list: a NULL amc_code means "all AMCs", and
-- NULLs are never equal to each other, so a plain unique index would allow the
-- same global alias to be inserted repeatedly. The seed script's ON CONFLICT
-- targets this exact expression.
CREATE UNIQUE INDEX IF NOT EXISTS uq_scheme_name_alias
    ON bronze.scheme_name_alias (alias_type, raw_term, COALESCE(amc_code, ''));


-- ---------------------------------------------------------------------------
-- scheme_mapping_override : hand-curated mappings. Outrank every rule.
-- A row with a NULL amfi_scheme_code is a positive assertion that the scheme
-- has no AMFI counterpart, not a missing value.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bronze.scheme_mapping_override (
    override_id      uuid NOT NULL,
    rta              varchar NOT NULL,
    rta_scheme_code  varchar NOT NULL,
    amfi_scheme_code varchar,
    reason           text NOT NULL,
    mapped_by        varchar,
    mapped_at        timestamp DEFAULT now() NOT NULL,
    is_active        boolean DEFAULT true NOT NULL,
    CONSTRAINT scheme_mapping_override_pkey PRIMARY KEY (override_id),
    CONSTRAINT uq_scheme_mapping_override UNIQUE (rta, rta_scheme_code)
);


-- ---------------------------------------------------------------------------
-- scheme_mapping_review : the approval queue.
--
-- Two writers share this table and must not clobber each other:
--   * scheme_mapping.py replaces its own pending rows on every run
--   * map_unmatched_nav_name.py writes NAV_NAME_MATCH / NAME_KEY_MATCH rows on
--     its own schedule
-- Both legitimately produce reviewer_decision IS NULL rows, so each writer's
-- delete is scoped by rule_name (see reference.FOREIGN_REVIEW_RULES).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bronze.scheme_mapping_review (
    review_id           uuid NOT NULL,
    rta                 varchar NOT NULL,
    rta_scheme_code     varchar NOT NULL,
    rta_scheme_name     text,
    candidate_rank      integer NOT NULL,
    candidate_amfi_code varchar,
    candidate_amfi_name text,
    candidate_score     numeric,
    rule_name           varchar NOT NULL,
    structured_mismatch varchar,
    reviewer_decision   varchar,
    reviewed_by         varchar,
    reviewed_at         timestamp,
    created_at          timestamp DEFAULT now() NOT NULL,
    CONSTRAINT scheme_mapping_review_pkey PRIMARY KEY (review_id),
    CONSTRAINT uq_scheme_mapping_review UNIQUE (rta, rta_scheme_code, candidate_rank),
    CONSTRAINT scheme_mapping_review_reviewer_decision_check
        CHECK (reviewer_decision::text = ANY (ARRAY['APPROVED'::varchar::text,
                                                    'REJECTED'::varchar::text]))
);


-- ---------------------------------------------------------------------------
-- scheme_mapping_audit : every candidate every rule produced, for one run.
-- TRUNCATEd and rewritten on each run of scheme_mapping.py.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bronze.scheme_mapping_audit (
    audit_id            uuid NOT NULL,
    rta                 varchar NOT NULL,
    rta_scheme_code     varchar NOT NULL,
    rule_name           varchar NOT NULL,
    execution_outcome   varchar NOT NULL,
    confidence_score    integer,
    candidate_scheme_id varchar,
    evaluated_at        timestamp DEFAULT now() NOT NULL,
    CONSTRAINT scheme_mapping_audit_pkey PRIMARY KEY (audit_id)
);

CREATE INDEX IF NOT EXISTS ix_scheme_mapping_audit_scheme
    ON bronze.scheme_mapping_audit (rta, rta_scheme_code);


-- ---------------------------------------------------------------------------
-- Upgrade path for a database created before verified_at existed. Without the
-- column the upsert guard in scheme_mapping.py cannot compile, and every
-- approved fallback mapping is erased on the next run.
-- ---------------------------------------------------------------------------
ALTER TABLE bronze.scheme_mapping ADD COLUMN IF NOT EXISTS verified_by varchar(100);
ALTER TABLE bronze.scheme_mapping ADD COLUMN IF NOT EXISTS verified_at timestamp;
ALTER TABLE bronze.scheme_mapping ADD COLUMN IF NOT EXISTS rta_isin varchar(20);

-- ---------------------------------------------------------------------------
-- Upgrade path for a database created before structured_mismatch existed.
-- Carries a PLAN_MISMATCH/OPTION_MISMATCH reason from scheme_master's own
-- plan_type/option_type onto a Phase-3 fallback candidate queued for review,
-- so a human reviewer sees the disagreement instead of approving on name
-- match alone. NULL means either no structured signal was available or the
-- signal agreed with the parsed name -- both read as "nothing to flag."
-- ---------------------------------------------------------------------------
ALTER TABLE bronze.scheme_mapping_review ADD COLUMN IF NOT EXISTS structured_mismatch varchar;

COMMIT;

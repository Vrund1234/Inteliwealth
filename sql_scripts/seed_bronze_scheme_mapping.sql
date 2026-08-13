-- ============================================================
-- SEED FILE: Run against PROJECT DB (inteliwealth_db)
-- Creates and seeds all tables required by scheme_mapping.py
-- ============================================================

-- 1. scheme_name_alias — Token & fund-rename aliases for name normalization
CREATE TABLE IF NOT EXISTS bronze.scheme_name_alias (
    alias_id        UUID PRIMARY KEY,
    raw_term        TEXT NOT NULL,
    normalized_term TEXT NOT NULL DEFAULT '',
    alias_type      VARCHAR NOT NULL CHECK (alias_type IN ('TOKEN', 'FUND_RENAME')),
    amc_code        VARCHAR,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_scheme_name_alias
    ON bronze.scheme_name_alias (alias_type, raw_term, COALESCE(amc_code, ''));

-- 2. scheme_mapping_override — Manual curator overrides
CREATE TABLE IF NOT EXISTS bronze.scheme_mapping_override (
    override_id      UUID PRIMARY KEY,
    rta              VARCHAR NOT NULL,
    rta_scheme_code  VARCHAR NOT NULL,
    amfi_scheme_code VARCHAR,
    reason           TEXT NOT NULL,
    mapped_by        VARCHAR,
    mapped_at        TIMESTAMP NOT NULL DEFAULT NOW(),
    is_active        BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT uq_scheme_mapping_override UNIQUE (rta, rta_scheme_code)
);

-- 3. scheme_mapping_review — Review candidates for ambiguous mappings
CREATE TABLE IF NOT EXISTS bronze.scheme_mapping_review (
    review_id           UUID PRIMARY KEY,
    rta                 VARCHAR NOT NULL,
    rta_scheme_code     VARCHAR NOT NULL,
    rta_scheme_name     TEXT,
    candidate_rank      INT NOT NULL,
    candidate_amfi_code VARCHAR,
    candidate_amfi_name TEXT,
    candidate_score     NUMERIC,
    rule_name           VARCHAR NOT NULL,
    reviewer_decision   VARCHAR CHECK (reviewer_decision IN ('APPROVED', 'REJECTED')),
    reviewed_by         VARCHAR,
    reviewed_at         TIMESTAMP,
    created_at          TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_scheme_mapping_review UNIQUE (rta, rta_scheme_code, candidate_rank)
);

-- 4. scheme_mapping_audit — Per-rule execution log
CREATE TABLE IF NOT EXISTS bronze.scheme_mapping_audit (
    audit_id            UUID PRIMARY KEY,
    rta                 VARCHAR NOT NULL,
    rta_scheme_code     VARCHAR NOT NULL,
    rule_name           VARCHAR NOT NULL,
    execution_outcome   VARCHAR NOT NULL,
    confidence_score    INT,
    candidate_scheme_id VARCHAR,
    evaluated_at        TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_scheme_mapping_audit_scheme
    ON bronze.scheme_mapping_audit (rta, rta_scheme_code);

-- 5. Unique index on existing scheme_mapping table (prevents duplicate upserts)
CREATE UNIQUE INDEX IF NOT EXISTS uq_scheme_mapping_amfi
    ON bronze.scheme_mapping (rta, rta_scheme_code, amfi_scheme_code);

-- ============================================================
-- SEED DATA
-- ============================================================

-- Token aliases (global, no AMC scope)
INSERT INTO bronze.scheme_name_alias (alias_id, raw_term, normalized_term, alias_type, amc_code)
VALUES
    (gen_random_uuid(), 'GR',              'GROWTH',              'TOKEN', NULL),
    (gen_random_uuid(), 'FTP',             'FIXED TERM PLAN',     'TOKEN', NULL),
    (gen_random_uuid(), 'FMP',             'FIXED MATURITY PLAN', 'TOKEN', NULL),
    (gen_random_uuid(), 'MIP',             'MONTHLY INCOME PLAN', 'TOKEN', NULL),
    (gen_random_uuid(), 'REG',             'REGULAR',             'TOKEN', NULL),
    (gen_random_uuid(), 'DIV',             'IDCW',                'TOKEN', NULL),
    (gen_random_uuid(), 'FOF',             'FUND OF FUNDS',       'TOKEN', NULL),
    (gen_random_uuid(), 'MID CAP',         'MIDCAP',              'TOKEN', NULL),
    (gen_random_uuid(), 'REGULAR SAVINGS', 'REGULARSAVINGS',      'TOKEN', NULL)
ON CONFLICT DO NOTHING;

-- Fund rename alias (AMC-scoped: Reliance -> Nippon India for RMF only)
INSERT INTO bronze.scheme_name_alias (alias_id, raw_term, normalized_term, alias_type, amc_code)
VALUES
    (gen_random_uuid(), 'RELIANCE', 'NIPPON INDIA', 'FUND_RENAME', 'RMF')
ON CONFLICT DO NOTHING;

-- Manual overrides for AMCs not present in AMFI master
INSERT INTO bronze.scheme_mapping_override (override_id, rta, rta_scheme_code, amfi_scheme_code, reason, mapped_by)
VALUES
    (gen_random_uuid(), 'KFIN', '906HLRG', NULL, 'Altiva AMC has no schemes in amfi_scheme_master', 'phase2-seed'),
    (gen_random_uuid(), 'KFIN', '908S1GP', NULL, 'Diviniti AMC has no schemes in amfi_scheme_master', 'phase2-seed')
ON CONFLICT (rta, rta_scheme_code) DO NOTHING;

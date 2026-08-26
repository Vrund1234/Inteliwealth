-- ============================================================
-- SECTION A — run against MASTER db (intelli_wealth_28_07_2026)
-- ============================================================

-- Explicit RTA -> AMFI amc_code link. Today the two vocabularies happen to
-- agree for 27 of 29 codes; storing it makes a future divergence a data edit
-- rather than a code change. amc_slug is retained for display only.
ALTER TABLE public.rta_amc_code
    ADD COLUMN IF NOT EXISTS amfi_amc_code VARCHAR;

CREATE TABLE IF NOT EXISTS public.scheme_name_alias (
    alias_id        UUID PRIMARY KEY,
    raw_term        TEXT NOT NULL,
    normalized_term TEXT NOT NULL DEFAULT '',
    alias_type      VARCHAR NOT NULL CHECK (alias_type IN ('TOKEN', 'FUND_RENAME')),
    amc_code        VARCHAR,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_scheme_name_alias
    ON public.scheme_name_alias (alias_type, raw_term, COALESCE(amc_code, ''));

CREATE TABLE IF NOT EXISTS public.scheme_mapping_override (
    override_id      UUID PRIMARY KEY,
    rta              VARCHAR NOT NULL,
    rta_scheme_code  VARCHAR NOT NULL,
    -- NULL is meaningful: a curator asserting the fund is absent from AMFI.
    amfi_scheme_code VARCHAR,
    reason           TEXT NOT NULL,
    mapped_by        VARCHAR,
    mapped_at        TIMESTAMP NOT NULL DEFAULT NOW(),
    is_active        BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT uq_scheme_mapping_override UNIQUE (rta, rta_scheme_code)
);

CREATE TABLE IF NOT EXISTS public.scheme_mapping_review (
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

-- Seed TOKEN aliases. Abbreviations observed in the current 515 RTA names.
INSERT INTO public.scheme_name_alias (alias_id, raw_term, normalized_term, alias_type, amc_code)
VALUES
    (gen_random_uuid(), 'GR',   'GROWTH',              'TOKEN', NULL),
    (gen_random_uuid(), 'FTP',  'FIXED TERM PLAN',     'TOKEN', NULL),
    (gen_random_uuid(), 'FMP',  'FIXED MATURITY PLAN', 'TOKEN', NULL),
    (gen_random_uuid(), 'MIP',  'MONTHLY INCOME PLAN', 'TOKEN', NULL),
    (gen_random_uuid(), 'REG',  'REGULAR',             'TOKEN', NULL),
    (gen_random_uuid(), 'DIV',  'IDCW',                'TOKEN', NULL),
    (gen_random_uuid(), 'FOF',  'FUND OF FUNDS',       'TOKEN', NULL),
    -- The RTAs write "Mid Cap", AMFI writes "Midcap", for the same funds.
    (gen_random_uuid(), 'MID CAP', 'MIDCAP',           'TOKEN', NULL),
    -- "Regular" is stripped as plan filler, but "Regular Savings Fund" is a
    -- fund name. Welding it keeps it out of the filler pass.
    (gen_random_uuid(), 'REGULAR SAVINGS', 'REGULARSAVINGS', 'TOKEN', NULL)
ON CONFLICT DO NOTHING;

-- Reliance Mutual Fund was rebranded Nippon India in 2019. The RTAs use the
-- new name; the AMFI master still carries the pre-rebrand name on schemes that
-- closed before it, so the two sides only meet once the old name is rewritten.
-- AMC-scoped: "Reliance" is a common word and must not be rewritten elsewhere.
INSERT INTO public.scheme_name_alias (alias_id, raw_term, normalized_term, alias_type, amc_code)
VALUES
    (gen_random_uuid(), 'RELIANCE', 'NIPPON INDIA', 'FUND_RENAME', 'RMF')
ON CONFLICT DO NOTHING;

-- ============================================================
-- SECTION B — run against PROJECT db (inteliwealth_db)
-- ============================================================

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

-- The duplicate-expansion INSERT at scheme_mapping.py:1394 declares
-- ON CONFLICT (rta, rta_scheme_code, amfi_scheme_code) but no such constraint
-- exists, so that branch raises as soon as it receives rows. It is currently
-- masked only because target_names is empty.
CREATE UNIQUE INDEX IF NOT EXISTS uq_scheme_mapping_amfi
    ON bronze.scheme_mapping (rta, rta_scheme_code, amfi_scheme_code);

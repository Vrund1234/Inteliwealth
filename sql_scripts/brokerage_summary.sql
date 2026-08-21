-- =====================================================
-- BROKERAGE SUMMARY BY SCHEME
--
-- Source reports
--   CAMS WBR36   - brokerage summary by scheme (current period)
--   CAMS WBR36H  - brokerage summary by scheme (historic / adjustments)
--   KFINTECH     - equivalent brokerage report, added later
--
-- One table per layer holds every RTA and every report
-- variant. `source` carries the RTA, `report_type`
-- carries the report (WBR36 / WBR36H / ...), because the
-- same product_code appears in more than one report.
--
-- Bronze keeps every value as TEXT (house rule: bronze is
-- a faithful copy of the file). Money columns are typed
-- for the first time in Silver.
-- =====================================================


-- =====================================================
-- BRONZE
-- =====================================================

CREATE TABLE IF NOT EXISTS bronze.brokerage_summary (

    -- system
    source              TEXT,
    report_type         TEXT,

    -- scheme identification
    product_code        TEXT,
    product_name        TEXT,

    -- brokerage components (TEXT in bronze, by design)
    upfront             TEXT,
    afe                 TEXT,
    trailer_fee         TEXT,
    trxn_charges        TEXT,
    clawback            TEXT,
    incentives          TEXT,

    -- reporting period
    -- CAMS WBR36/WBR36H carry no period column; these
    -- stay NULL for CAMS and are filled by RTAs that do
    -- report a period.
    report_from_date    DATE,
    report_to_date      DATE,
    rep_date            DATE,

    -- optional context, alias-ready for other RTAs
    amc_code            TEXT,
    broker_code         TEXT,
    sub_broker_code     TEXT,

    -- audit
    flag                INTEGER,
    created_at          TIMESTAMP,
    updated_at          TIMESTAMP

);


CREATE INDEX IF NOT EXISTS idx_bronze_brokerage_summary_flag
    ON bronze.brokerage_summary (flag);

CREATE INDEX IF NOT EXISTS idx_bronze_brokerage_summary_key
    ON bronze.brokerage_summary (source, report_type, product_code);


-- =====================================================
-- SILVER
--
-- Column-identical to Bronze plus scheme_id, and the
-- money columns typed as NUMERIC.
-- =====================================================

CREATE TABLE IF NOT EXISTS silver.brokerage_summary (

    source              VARCHAR(20),
    report_type         VARCHAR(20),

    product_code        VARCHAR(50),
    product_name        TEXT,

    upfront             NUMERIC(20, 8),
    afe                 NUMERIC(20, 8),
    trailer_fee         NUMERIC(20, 8),
    trxn_charges        NUMERIC(20, 8),
    clawback            NUMERIC(20, 8),
    incentives          NUMERIC(20, 8),

    report_from_date    DATE,
    report_to_date      DATE,
    rep_date            DATE,

    amc_code            VARCHAR(20),
    broker_code         VARCHAR(50),
    sub_broker_code     VARCHAR(50),

    -- resolved from bronze.scheme_mapping.rta_scheme_code
    scheme_id           VARCHAR(50),

    flag                INTEGER,
    created_at          TIMESTAMP,
    updated_at          TIMESTAMP

);


CREATE INDEX IF NOT EXISTS idx_silver_brokerage_summary_flag
    ON silver.brokerage_summary (flag);

CREATE INDEX IF NOT EXISTS idx_silver_brokerage_summary_key
    ON silver.brokerage_summary (source, report_type, product_code);


-- =====================================================
-- GOLD
--
-- Natural key : rta | report_type | scheme_code
--               | report_from_date | report_to_date
--
-- `id` is a deterministic uuid5 of that key, so a
-- re-uploaded file updates the same row instead of
-- inserting a second copy.
-- =====================================================

CREATE TABLE IF NOT EXISTS gold.brokerage_summary (

    id                  UUID PRIMARY KEY,

    rta                 VARCHAR(20),
    report_type         VARCHAR(20),

    scheme_code         VARCHAR(50),
    scheme_name         TEXT,

    scheme_id           VARCHAR(50),
    amc_id              UUID,
    amc_code            VARCHAR(20),

    arn                 VARCHAR(50),
    sub_arn             VARCHAR(50),

    upfront             NUMERIC(20, 8),
    afe                 NUMERIC(20, 8),
    trailer_fee         NUMERIC(20, 8),
    trxn_charges        NUMERIC(20, 8),
    clawback            NUMERIC(20, 8),
    incentives          NUMERIC(20, 8),

    -- upfront + afe + trailer_fee + trxn_charges
    -- + incentives - clawback
    total_brokerage     NUMERIC(20, 8),

    report_from_date    DATE,
    report_to_date      DATE,
    rep_date            DATE,

    created_at          TIMESTAMP,
    updated_at          TIMESTAMP

);


CREATE INDEX IF NOT EXISTS idx_gold_brokerage_summary_scheme
    ON gold.brokerage_summary (scheme_id);

CREATE INDEX IF NOT EXISTS idx_gold_brokerage_summary_key
    ON gold.brokerage_summary (rta, report_type, scheme_code);

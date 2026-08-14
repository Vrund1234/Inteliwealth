-- 002_tables.sql
--
-- Bronze, silver and gold tables for the three WBR entities.
--
-- Two things every table here has that 13 of the 14 existing tables do not:
--   1. A UNIQUE constraint on the natural key. Without it ON CONFLICT is impossible,
--      every uniqueness guarantee is non-atomic Python, and the planner has no index.
--   2. Real column types in silver and gold. 114 of 116 columns in
--      silver.transaction_master_new are text, which is why a 128,766-row table costs
--      583 MB in pandas while PostgreSQL scans it in 26 ms.
--
-- Bronze data columns are deliberately text: bronze is structurally conformed and
-- semantically untouched, and typing happens in silver where a failure can be rejected.
--
-- Idempotent: safe to run repeatedly.

-- =====================================================================
-- WBR36 / WBR36H — Brokerage summary by scheme
-- =====================================================================
-- report_variant is part of the key because WBR36 and WBR36H share 10 of their 11
-- product codes. Without it the H variant overwrites the standard one.
--
-- Measures are numeric(20,8): the sample carries 8 decimal places (3950.45636848)
-- and negative values (-5327.04630385). Rounding to 4 places, as the existing
-- round_decimal_columns() does, would lose real precision.

CREATE TABLE IF NOT EXISTS bronze_wbr.brokerage_by_scheme (
    source_file_id      uuid         NOT NULL,
    row_number_in_file  integer      NOT NULL,
    report_variant      text         NOT NULL,
    ingested_at         timestamptz  NOT NULL,

    product_code        text         NOT NULL,
    product_name        text,
    upfront             text,
    afe                 text,
    trailer_fee         text,
    trxn_charges        text,
    clawback            text,
    incentives          text,

    CONSTRAINT uq_bronze_brokerage UNIQUE (report_variant, product_code)
);

CREATE TABLE IF NOT EXISTS silver_wbr.brokerage_by_scheme (
    source_file_id      uuid         NOT NULL,
    row_number_in_file  integer      NOT NULL,
    report_variant      text         NOT NULL,
    ingested_at         timestamptz  NOT NULL,

    product_code        text         NOT NULL,
    product_name        text,
    upfront             numeric(20,8),
    afe                 numeric(20,8),
    trailer_fee         numeric(20,8),
    trxn_charges        numeric(20,8),
    clawback            numeric(20,8),
    incentives          numeric(20,8),
    total_brokerage     numeric(20,8),

    CONSTRAINT uq_silver_brokerage UNIQUE (report_variant, product_code)
);

CREATE TABLE IF NOT EXISTS gold_wbr.brokerage_by_scheme (
    id                  uuid         NOT NULL,
    report_period       text         NOT NULL,
    report_variant      text         NOT NULL,
    product_code        text         NOT NULL,
    product_name        text,
    upfront             numeric(20,8),
    afe                 numeric(20,8),
    trailer_fee         numeric(20,8),
    trxn_charges        numeric(20,8),
    clawback            numeric(20,8),
    incentives          numeric(20,8),
    total_brokerage     numeric(20,8),
    source_file_id      uuid,
    created_at          timestamptz  NOT NULL DEFAULT now(),

    CONSTRAINT pk_gold_brokerage PRIMARY KEY (id),
    CONSTRAINT uq_gold_brokerage UNIQUE (report_period, report_variant, product_code)
);

CREATE INDEX IF NOT EXISTS ix_gold_brokerage_product
    ON gold_wbr.brokerage_by_scheme (product_code);


-- =====================================================================
-- WBR56 — KYC status of Investor
-- =====================================================================
-- folio stays text throughout: the sample carries both '1049217049' and
-- '42213157/43'. Numeric coercion is what produces the trailing '.0' the existing
-- pipeline has to strip back off.

CREATE TABLE IF NOT EXISTS bronze_wbr.investor_kyc_status (
    source_file_id      uuid         NOT NULL,
    row_number_in_file  integer      NOT NULL,
    report_variant      text         NOT NULL,
    ingested_at         timestamptz  NOT NULL,

    brok_dlr_code       text,
    folio               text         NOT NULL,
    inv_name            text,
    tax_no              text,
    jname1              text,
    jointpan1           text,
    jname2              text,
    jointpan2           text,
    guardian            text,
    guardian_panno      text,
    address1            text,
    address2            text,
    address3            text,
    city                text,
    pincode             text,
    phone_res           text,
    phone_off           text,
    mobile_no           text,
    email               text,
    location            text,
    state               text,
    fax_res             text,
    fax_off             text,
    fh_kyc              text,
    gu_kyc              text,
    jh1_kyc             text,
    jh2_kyc             text,
    brok_name           text,
    rep_from_date       text,
    rep_to_date         text,
    rep_date            text,
    amc_code            text         NOT NULL,
    fh_kyc_desc         text,
    gu_kyc_desc         text,
    jh1_kyc_desc        text,
    jh2_kyc_desc        text,
    fh_g_aadharlink     text,
    jh1_aadharlink      text,
    jh2_aadharlink      text,
    country             text,

    CONSTRAINT uq_bronze_kyc UNIQUE (amc_code, folio)
);

CREATE TABLE IF NOT EXISTS silver_wbr.investor_kyc_status (
    source_file_id      uuid         NOT NULL,
    row_number_in_file  integer      NOT NULL,
    report_variant      text         NOT NULL,
    ingested_at         timestamptz  NOT NULL,

    brok_dlr_code       text,
    folio               text         NOT NULL,
    inv_name            text,
    tax_no              text,
    jname1              text,
    jointpan1           text,
    jname2              text,
    jointpan2           text,
    guardian            text,
    guardian_panno      text,
    address1            text,
    address2            text,
    address3            text,
    city                text,
    pincode             text,
    phone_res           text,
    phone_off           text,
    mobile_no           text,
    email               text,
    location            text,
    state               text,
    fax_res             text,
    fax_off             text,
    fh_kyc              text,
    gu_kyc              text,
    jh1_kyc             text,
    jh2_kyc             text,
    brok_name           text,
    -- Real dates. Three source columns, TWO different formats in one file:
    -- rep_from_date/rep_to_date are %d-%b-%Y, rep_date is %m/%d/%Y.
    rep_from_date       date,
    rep_to_date         date,
    rep_date            date,
    amc_code            text         NOT NULL,
    fh_kyc_desc         text,
    gu_kyc_desc         text,
    jh1_kyc_desc        text,
    jh2_kyc_desc        text,
    fh_g_aadharlink     text,
    jh1_aadharlink      text,
    jh2_aadharlink      text,
    country             text,

    -- Standardised lookup values, alongside the raw ones. The raw column is kept so
    -- the generated report can reproduce the provider's wording exactly.
    fh_kyc_std              text,
    gu_kyc_std              text,
    jh1_kyc_std             text,
    jh2_kyc_std             text,
    fh_kyc_desc_std         text,
    gu_kyc_desc_std         text,
    jh1_kyc_desc_std        text,
    jh2_kyc_desc_std        text,
    fh_g_aadharlink_std     text,
    jh1_aadharlink_std      text,
    jh2_aadharlink_std      text,

    -- Derived. location and state arrive as compound 'code/label' values
    -- ('A1/Ahmedabad', 'GU/Gujarat'), and a bare '/' means unknown.
    location_code       text,
    location_city       text,
    state_code          text,
    state_name          text,
    mobile_e164         text,
    kyc_ok_any          boolean,

    CONSTRAINT uq_silver_kyc UNIQUE (amc_code, folio)
);

CREATE INDEX IF NOT EXISTS ix_silver_kyc_pan
    ON silver_wbr.investor_kyc_status (tax_no);
CREATE INDEX IF NOT EXISTS ix_silver_kyc_rep_date
    ON silver_wbr.investor_kyc_status (rep_date DESC);

CREATE TABLE IF NOT EXISTS gold_wbr.investor_kyc_status (
    id                  uuid         NOT NULL,
    brok_dlr_code       text,
    folio               text         NOT NULL,
    inv_name            text,
    tax_no              text,
    jname1              text,
    jointpan1           text,
    jname2              text,
    jointpan2           text,
    guardian            text,
    guardian_panno      text,
    address1            text,
    address2            text,
    address3            text,
    city                text,
    pincode             text,
    phone_res           text,
    phone_off           text,
    mobile_no           text,
    email               text,
    location            text,
    state               text,
    fax_res             text,
    fax_off             text,
    fh_kyc              text,
    gu_kyc              text,
    jh1_kyc             text,
    jh2_kyc             text,
    brok_name           text,
    rep_from_date       date,
    rep_to_date         date,
    rep_date            date,
    amc_code            text         NOT NULL,
    fh_kyc_desc         text,
    gu_kyc_desc         text,
    jh1_kyc_desc        text,
    jh2_kyc_desc        text,
    fh_g_aadharlink     text,
    jh1_aadharlink      text,
    jh2_aadharlink      text,
    country             text,

    fh_kyc_std          text,
    gu_kyc_std          text,
    jh1_kyc_std         text,
    jh2_kyc_std         text,
    fh_kyc_desc_std     text,
    gu_kyc_desc_std     text,
    jh1_kyc_desc_std    text,
    jh2_kyc_desc_std    text,
    fh_g_aadharlink_std text,
    jh1_aadharlink_std  text,
    jh2_aadharlink_std  text,
    location_code       text,
    location_city       text,
    state_code          text,
    state_name          text,
    mobile_e164         text,
    kyc_ok_any          boolean,

    source_file_id      uuid,
    created_at          timestamptz  NOT NULL DEFAULT now(),

    CONSTRAINT pk_gold_kyc PRIMARY KEY (id),
    CONSTRAINT uq_gold_kyc UNIQUE (amc_code, folio)
);

CREATE INDEX IF NOT EXISTS ix_gold_kyc_pan
    ON gold_wbr.investor_kyc_status (tax_no);


-- =====================================================================
-- WBR68 — Invalid EUIN Report
-- =====================================================================
-- folio_no and folio hold the same value in every sample row. Both are retained
-- because the output layout requires both, at positions 4 and 23.

CREATE TABLE IF NOT EXISTS bronze_wbr.invalid_euin (
    source_file_id      uuid         NOT NULL,
    row_number_in_file  integer      NOT NULL,
    report_variant      text         NOT NULL,
    ingested_at         timestamptz  NOT NULL,

    amc_code            text         NOT NULL,
    arn_code            text,
    appln_no            text,
    folio_no            text         NOT NULL,
    inv_name            text,
    inv_pan             text,
    trade_date          text,
    sch_code            text,
    sch_name            text,
    trxn_no             text         NOT NULL,
    trxn_type           text,
    trxn_desc           text,
    amount              text,
    subbrokcod          text,
    location            text,
    euin                text,
    euin_valid          text,
    email               text,
    posted_date         text,
    cons_code           text,
    usertxn_no          text,
    alt_folio           text,
    folio               text,
    subbrok_arn         text,
    sys_reg_dt          text,
    reason              text,
    user_code           text,
    sip_regn_date       text,
    auto_trxn_no        text,
    folio_old           text,
    scheme_folio_number text,

    CONSTRAINT uq_bronze_euin UNIQUE (amc_code, trxn_no)
);

CREATE TABLE IF NOT EXISTS silver_wbr.invalid_euin (
    source_file_id      uuid         NOT NULL,
    row_number_in_file  integer      NOT NULL,
    report_variant      text         NOT NULL,
    ingested_at         timestamptz  NOT NULL,

    amc_code            text         NOT NULL,
    arn_code            text,
    appln_no            text,
    folio_no            text         NOT NULL,
    inv_name            text,
    inv_pan             text,
    trade_date          date,
    sch_code            text,
    sch_name            text,
    trxn_no             text         NOT NULL,
    trxn_type           text,
    trxn_desc           text,
    amount              numeric(20,4),
    subbrokcod          text,
    location            text,
    euin                text,
    euin_valid          text,
    email               text,
    posted_date         date,
    cons_code           text,
    usertxn_no          text,
    alt_folio           text,
    folio               text,
    subbrok_arn         text,
    sys_reg_dt          date,
    reason              text,
    user_code           text,
    -- Mixed format in the source: '9/17/2025' in one row, '20250928' in another.
    -- cleaners.parse_dates tries %m/%d/%Y then %Y%m%d, then rejects.
    sip_regn_date       date,
    auto_trxn_no        text,
    folio_old           text,
    scheme_folio_number text,

    euin_valid_std      text,
    -- TRUE when euin_valid <> 'Y'. Not '= N': the sample carries an 'F' row with the
    -- same reason, which a '= N' filter would silently drop from the report.
    euin_is_invalid     boolean,
    location_code       text,
    location_city       text,
    state_code          text,
    state_name          text,

    CONSTRAINT uq_silver_euin UNIQUE (amc_code, trxn_no)
);

CREATE INDEX IF NOT EXISTS ix_silver_euin_trade_date
    ON silver_wbr.invalid_euin (trade_date DESC);
CREATE INDEX IF NOT EXISTS ix_silver_euin_folio
    ON silver_wbr.invalid_euin (amc_code, folio_no);

CREATE TABLE IF NOT EXISTS gold_wbr.invalid_euin (
    id                  uuid         NOT NULL,
    amc_code            text         NOT NULL,
    arn_code            text,
    appln_no            text,
    folio_no            text         NOT NULL,
    inv_name            text,
    inv_pan             text,
    trade_date          date,
    sch_code            text,
    sch_name            text,
    trxn_no             text         NOT NULL,
    trxn_type           text,
    trxn_desc           text,
    amount              numeric(20,4),
    subbrokcod          text,
    location            text,
    euin                text,
    euin_valid          text,
    email               text,
    posted_date         date,
    cons_code           text,
    usertxn_no          text,
    alt_folio           text,
    folio               text,
    subbrok_arn         text,
    sys_reg_dt          date,
    reason              text,
    user_code           text,
    sip_regn_date       date,
    auto_trxn_no        text,
    folio_old           text,
    scheme_folio_number text,

    euin_valid_std      text,
    euin_is_invalid     boolean,
    location_code       text,
    location_city       text,

    source_file_id      uuid,
    created_at          timestamptz  NOT NULL DEFAULT now(),

    CONSTRAINT pk_gold_euin PRIMARY KEY (id),
    CONSTRAINT uq_gold_euin UNIQUE (amc_code, trxn_no)
);

CREATE INDEX IF NOT EXISTS ix_gold_euin_trade_date
    ON gold_wbr.invalid_euin (trade_date DESC);
CREATE INDEX IF NOT EXISTS ix_gold_euin_arn
    ON gold_wbr.invalid_euin (arn_code);


-- =====================================================================
-- 002b — source_row, added after verification
-- =====================================================================
-- Report row ORDER is part of the layout contract, and a bare SELECT * returns heap
-- order, which changes after an UPDATE — two consecutive runs produced byte-different
-- CSVs. source_row carries the row's position in the delivered file so the exporter
-- can ORDER BY it and reproduce the provider's own ordering deterministically.

ALTER TABLE gold_wbr.brokerage_by_scheme  ADD COLUMN IF NOT EXISTS source_row integer;
ALTER TABLE gold_wbr.investor_kyc_status  ADD COLUMN IF NOT EXISTS source_row integer;
ALTER TABLE gold_wbr.invalid_euin         ADD COLUMN IF NOT EXISTS source_row integer;

-- =====================================================================
-- CAMS WBR REPORTS - GOLD TABLES
-- =====================================================================
--
-- Three report-shaped tables, derived from the existing silver layer. There is
-- no bronze or silver stage of their own: the WBR reports are OUTPUT, built out
-- of silver.transaction_master_new and silver.investor_master, which are
-- themselves fed by the CAMS R2 / R9 / R49 and KFIN MFSD files.
--
-- Gold here is report-shaped rather than entity-shaped, one table per report,
-- because the deliverable is the report. That is a deliberate difference from
-- gold.transactions / gold.clients / gold.holdings, which model business
-- entities, and it is why these three never compete with them.
--
-- Each table carries a UNIQUE constraint on its natural key. That constraint is
-- the ON CONFLICT target for the upsert, and it is what keeps the grain from
-- drifting.
--
-- source_row preserves the order rows are emitted in. Without it an export
-- returns heap order, which changes after an UPDATE, and two consecutive runs
-- produce byte-different files.
--
-- Columns the CAMS feed cannot source are still present and still in the
-- provider's position, holding NULL. Dropping them would change the report
-- layout, which is the contract with whoever consumes it. Which columns those
-- are, and why, is recorded in etl_gold_wbr.py next to UNAVAILABLE.

CREATE SCHEMA IF NOT EXISTS gold;


-- =====================================================================
-- WBR36 / WBR36H - BROKERAGE SUMMARY BY SCHEME
-- =====================================================================
--
-- One row per (report_period, report_variant, product_code).
--
-- report_variant is in the natural key because the provider delivers two
-- variants of this report that share most of their product codes. Only STD can
-- be produced from the CAMS transaction feed: nothing in R2 marks which schemes
-- belong to the H variant.
--
-- The measures are numeric(20,8) because the provider delivers 8 decimal places
-- (3950.45636848). They are NOT derivable from R2 — see UNAVAILABLE in
-- etl_gold_wbr.py — and load as NULL rather than as zero, so that "no data" and
-- "genuinely nil" stay distinguishable.

DROP TABLE IF EXISTS gold.brokerage_by_scheme;

CREATE TABLE gold.brokerage_by_scheme (

    id                  uuid PRIMARY KEY,

    report_period       text NOT NULL,
    report_variant      text NOT NULL,

    product_code        text NOT NULL,
    product_name        text,

    upfront             numeric(20,8),
    afe                 numeric(20,8),
    trailer_fee         numeric(20,8),
    trxn_charges        numeric(20,8),
    clawback            numeric(20,8),
    incentives          numeric(20,8),

    source              text,
    source_row          integer,

    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT gold_brokerage_by_scheme_nk
        UNIQUE (report_period, report_variant, product_code)
);


-- =====================================================================
-- WBR56 - KYC STATUS OF INVESTOR
-- =====================================================================
--
-- One row per (amc_code, folio).
--
-- silver.investor_master carries one row per folio per scheme, so the folio
-- grain here is reached by deduplication, not by assumption. The KYC status and
-- Aadhaar-link columns are populated only for folios the KFIN feed supplies;
-- for CAMS-fed folios they are NULL, because the CAMS R9 file does not carry
-- them.

DROP TABLE IF EXISTS gold.investor_kyc_status;

CREATE TABLE gold.investor_kyc_status (

    id                  uuid PRIMARY KEY,

    amc_code            text NOT NULL,
    folio               text NOT NULL,

    brok_dlr_code       text,
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
    location            text,
    state               text,
    country             text,

    phone_res           text,
    phone_off           text,
    mobile_no           text,
    email               text,
    fax_res             text,
    fax_off             text,

    fh_kyc              text,
    gu_kyc              text,
    jh1_kyc             text,
    jh2_kyc             text,
    fh_kyc_desc         text,
    gu_kyc_desc         text,
    jh1_kyc_desc        text,
    jh2_kyc_desc        text,

    fh_g_aadharlink     text,
    jh1_aadharlink      text,
    jh2_aadharlink      text,

    brok_name           text,

    rep_from_date       date,
    rep_to_date         date,
    rep_date            date,

    source              text,
    source_row          integer,

    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT gold_investor_kyc_status_nk UNIQUE (amc_code, folio)
);


-- =====================================================================
-- WBR68 - INVALID EUIN REPORT
-- =====================================================================
--
-- One row per (amc_code, trxn_no) — a transaction ledger, not a dimension.
--
-- The filter is euin_valid <> 'Y' with a non-blank euin, never euin_valid = 'N'.
-- The provider's own file carries both 'N' and 'F' under the same reason, and a
-- blank euin_valid means no EUIN was quoted at all rather than an invalid one.

DROP TABLE IF EXISTS gold.invalid_euin;

CREATE TABLE gold.invalid_euin (

    id                  uuid PRIMARY KEY,

    amc_code            text NOT NULL,
    trxn_no             text NOT NULL,

    arn_code            text,
    appln_no            text,
    folio_no            text,
    folio               text,
    folio_old           text,
    alt_folio           text,
    scheme_folio_number text,

    inv_name            text,
    inv_pan             text,
    email               text,

    sch_code            text,
    sch_name            text,

    trxn_type           text,
    trxn_desc           text,
    amount              numeric(20,4),

    subbrokcod          text,
    subbrok_arn         text,
    location            text,
    user_code           text,
    cons_code           text,
    usertxn_no          text,

    euin                text,
    euin_valid          text,
    reason              text,

    trade_date          date,
    posted_date         date,
    sys_reg_dt          date,
    sip_regn_date       date,
    auto_trxn_no        text,

    source              text,
    source_row          integer,

    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT gold_invalid_euin_nk UNIQUE (amc_code, trxn_no)
);


-- =====================================================================
-- INDEXES
-- =====================================================================

CREATE INDEX IF NOT EXISTS ix_gold_wbr_euin_trade_date
    ON gold.invalid_euin (trade_date);

CREATE INDEX IF NOT EXISTS ix_gold_wbr_kyc_amc
    ON gold.investor_kyc_status (amc_code);

CREATE INDEX IF NOT EXISTS ix_gold_wbr_brokerage_variant
    ON gold.brokerage_by_scheme (report_variant, product_code);

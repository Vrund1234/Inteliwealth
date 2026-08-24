-- =====================================================
-- KYC STATUS OF INVESTOR
--
-- Source reports
--   CAMS WBR56  - KYC status of investor
--   KFINTECH    - equivalent report, added later
--
-- One table per layer holds every RTA. `source` carries
-- the RTA, `report_type` carries the report, because the
-- same folio can appear in more than one RTA's report.
--
-- Bronze keeps every value as TEXT (house rule: bronze is
-- a faithful copy of the file).
--
-- GRAIN
--   Bronze / Silver : one row per (amc_code, folio), the
--                     file's own wide shape, with the KYC
--                     columns repeated per holder.
--   Gold            : one row per HOLDER. The four holder
--                     slots (first / guardian / joint 1 /
--                     joint 2) are unpivoted, the same way
--                     gold.folio_nominees unpivots the
--                     three-nominees-per-folio layout.
--
-- PAN is stored in plain text in every layer, matching
-- gold.transactions and gold.clients.
-- =====================================================


-- =====================================================
-- BRONZE
-- =====================================================

CREATE TABLE IF NOT EXISTS bronze.kyc_status (

    -- system
    source              TEXT,
    report_type         TEXT,

    -- broker / distributor
    brok_dlr_code       TEXT,
    brok_name           TEXT,

    -- folio identification
    amc_code            TEXT,
    folio               TEXT,

    -- first holder
    inv_name            TEXT,
    tax_no              TEXT,

    -- joint holders
    jname1              TEXT,
    jointpan1           TEXT,
    jname2              TEXT,
    jointpan2           TEXT,

    -- guardian
    guardian            TEXT,
    guardian_panno      TEXT,

    -- KYC status per holder (short form)
    fh_kyc              TEXT,
    gu_kyc              TEXT,
    jh1_kyc             TEXT,
    jh2_kyc             TEXT,

    -- KYC status per holder (long description)
    fh_kyc_desc         TEXT,
    gu_kyc_desc         TEXT,
    jh1_kyc_desc        TEXT,
    jh2_kyc_desc        TEXT,

    -- aadhaar link status
    -- fh_g_aadharlink covers the first holder AND the
    -- guardian: the report has no separate guardian column.
    fh_g_aadharlink     TEXT,
    jh1_aadharlink      TEXT,
    jh2_aadharlink      TEXT,

    -- address
    address1            TEXT,
    address2            TEXT,
    address3            TEXT,
    city                TEXT,
    pincode             TEXT,
    state               TEXT,
    country             TEXT,
    location            TEXT,

    -- contact
    phone_res           TEXT,
    phone_off           TEXT,
    mobile_no           TEXT,
    email               TEXT,
    fax_res             TEXT,
    fax_off             TEXT,

    -- reporting period
    rep_from_date       DATE,
    rep_to_date         DATE,
    rep_date            DATE,

    -- audit
    flag                INTEGER,
    created_at          TIMESTAMP,
    updated_at          TIMESTAMP

);


CREATE INDEX IF NOT EXISTS idx_bronze_kyc_status_flag
    ON bronze.kyc_status (flag);

CREATE INDEX IF NOT EXISTS idx_bronze_kyc_status_key
    ON bronze.kyc_status (source, report_type, amc_code, folio);


-- =====================================================
-- SILVER
--
-- Column-identical to Bronze, typed.
--
-- No scheme_id here: WBR56 reports the investor and the
-- folio, never a scheme.
-- =====================================================

CREATE TABLE IF NOT EXISTS silver.kyc_status (

    source              VARCHAR(20),
    report_type         VARCHAR(20),

    brok_dlr_code       VARCHAR(50),
    brok_name           VARCHAR(255),

    amc_code            VARCHAR(20),
    folio               VARCHAR(40),

    inv_name            VARCHAR(255),
    tax_no              VARCHAR(10),

    jname1              VARCHAR(255),
    jointpan1           VARCHAR(10),
    jname2              VARCHAR(255),
    jointpan2           VARCHAR(10),

    guardian            VARCHAR(255),
    guardian_panno      VARCHAR(10),

    fh_kyc              VARCHAR(50),
    gu_kyc              VARCHAR(50),
    jh1_kyc             VARCHAR(50),
    jh2_kyc             VARCHAR(50),

    fh_kyc_desc         VARCHAR(100),
    gu_kyc_desc         VARCHAR(100),
    jh1_kyc_desc        VARCHAR(100),
    jh2_kyc_desc        VARCHAR(100),

    fh_g_aadharlink     VARCHAR(50),
    jh1_aadharlink      VARCHAR(50),
    jh2_aadharlink      VARCHAR(50),

    address1            TEXT,
    address2            TEXT,
    address3            TEXT,
    city                VARCHAR(100),
    pincode             VARCHAR(20),
    state               VARCHAR(100),
    country             VARCHAR(100),
    location            VARCHAR(100),

    phone_res           VARCHAR(30),
    phone_off           VARCHAR(30),
    mobile_no           VARCHAR(30),
    email               VARCHAR(255),
    fax_res             VARCHAR(30),
    fax_off             VARCHAR(30),

    rep_from_date       DATE,
    rep_to_date         DATE,
    rep_date            DATE,

    flag                INTEGER,
    created_at          TIMESTAMP,
    updated_at          TIMESTAMP

);


CREATE INDEX IF NOT EXISTS idx_silver_kyc_status_flag
    ON silver.kyc_status (flag);

CREATE INDEX IF NOT EXISTS idx_silver_kyc_status_key
    ON silver.kyc_status (source, report_type, amc_code, folio);


-- =====================================================
-- GOLD
--
-- One row per HOLDER.
--
-- Natural key : rta | report_type | amc_code | folio
--               | holder_role
--
-- `id` is a deterministic uuid5 of that key, so a
-- re-uploaded file updates the same row instead of
-- inserting a second copy.
--
-- holder_role is one of FH (first holder), GU (guardian),
-- JH1 (joint 1), JH2 (joint 2).
--
-- The folio-level columns (address, contact, broker,
-- period) repeat on every holder row of that folio. That
-- is deliberate: it keeps one flat row answerable without
-- a second join, exactly like gold.folio_nominees.
-- =====================================================

CREATE TABLE IF NOT EXISTS gold.investor_kyc_status (

    id                  UUID PRIMARY KEY,

    rta                 VARCHAR(20),
    report_type         VARCHAR(20),

    amc_code            VARCHAR(20),
    amc_id              UUID,

    folio_number        VARCHAR(40),

    -- holder
    holder_role         VARCHAR(10),
    holder_seq          INTEGER,
    holder_name         VARCHAR(255),
    pan                 VARCHAR(10),
    client_id           UUID,

    -- KYC
    kyc_status          VARCHAR(50),
    kyc_status_desc     VARCHAR(100),
    aadhaar_link_status VARCHAR(50),

    -- TRUE when kyc_status reads "KYC OK", FALSE for any
    -- other stated status, NULL when the RTA reported no
    -- status for that holder.
    is_kyc_compliant    BOOLEAN,

    -- folio-level context, repeated per holder row
    broker_name         VARCHAR(255),
    arn                 VARCHAR(50),

    address_line1       TEXT,
    address_line2       TEXT,
    address_line3       TEXT,
    city                VARCHAR(100),
    pincode             VARCHAR(20),
    state               VARCHAR(100),
    country             VARCHAR(100),
    location            VARCHAR(100),

    email               VARCHAR(255),
    mobile              VARCHAR(30),
    phone_res           VARCHAR(30),
    phone_off           VARCHAR(30),
    fax_res             VARCHAR(30),
    fax_off             VARCHAR(30),

    report_from_date    DATE,
    report_to_date      DATE,
    rep_date            DATE,

    created_at          TIMESTAMP,
    updated_at          TIMESTAMP

);


CREATE INDEX IF NOT EXISTS idx_gold_investor_kyc_status_pan
    ON gold.investor_kyc_status (pan);

CREATE INDEX IF NOT EXISTS idx_gold_investor_kyc_status_client
    ON gold.investor_kyc_status (client_id);

CREATE INDEX IF NOT EXISTS idx_gold_investor_kyc_status_folio
    ON gold.investor_kyc_status (rta, amc_code, folio_number);

CREATE INDEX IF NOT EXISTS idx_gold_investor_kyc_status_compliant
    ON gold.investor_kyc_status (is_kyc_compliant);

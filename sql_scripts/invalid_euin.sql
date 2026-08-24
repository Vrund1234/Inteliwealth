-- =====================================================
-- INVALID EUIN REPORT
--
-- Source reports
--   CAMS WBR68  - invalid EUIN report
--   KFINTECH    - equivalent report, added later
--
-- One row per transaction, in every layer.
--
-- trxn_no is the RTA transaction number, which is what
-- gold.transactions stores as rta_txn_no, so a gold row
-- joins back to the transaction it faults on
-- (rta, rta_txn_no). gold.transactions has no surrogate
-- key, so that natural key is carried here instead of a
-- transaction_id.
--
-- Bronze keeps every value as TEXT (house rule: bronze is
-- a faithful copy of the file). amount is typed for the
-- first time in Silver.
--
-- PAN is stored in plain text in every layer, matching
-- gold.transactions and gold.clients.
-- =====================================================


-- =====================================================
-- BRONZE
-- =====================================================

CREATE TABLE IF NOT EXISTS bronze.invalid_euin (

    -- system
    source                  TEXT,
    report_type             TEXT,

    -- transaction identification
    trxn_no                 TEXT,
    usertxn_no              TEXT,
    auto_trxn_no            TEXT,
    appln_no                TEXT,

    -- scheme
    sch_code                TEXT,
    sch_name                TEXT,
    amc_code                TEXT,

    -- folio
    -- The report carries several folio spellings.
    -- folio_no is the one the rest of the pipeline uses;
    -- the others are kept for traceability.
    folio_no                TEXT,
    folio                   TEXT,
    alt_folio               TEXT,
    folio_old               TEXT,
    scheme_folio_number     TEXT,

    -- investor
    inv_name                TEXT,
    inv_pan                 TEXT,
    email                   TEXT,

    -- EUIN
    -- euin_valid is stored exactly as the file spells it;
    -- the derived boolean is computed in Gold.
    euin                    TEXT,
    euin_valid              TEXT,
    reason                  TEXT,

    -- broker
    arn_code                TEXT,
    subbrok_arn             TEXT,
    subbrokcod              TEXT,
    user_code               TEXT,
    cons_code               TEXT,
    location                TEXT,

    -- transaction detail
    trxn_type               TEXT,
    trxn_desc               TEXT,
    amount                  TEXT,

    -- dates
    trade_date              DATE,
    posted_date             DATE,
    sys_reg_dt              DATE,
    sip_regn_date           DATE,

    -- audit
    flag                    INTEGER,
    created_at              TIMESTAMP,
    updated_at              TIMESTAMP

);


CREATE INDEX IF NOT EXISTS idx_bronze_invalid_euin_flag
    ON bronze.invalid_euin (flag);

CREATE INDEX IF NOT EXISTS idx_bronze_invalid_euin_key
    ON bronze.invalid_euin (source, report_type, trxn_no);


-- =====================================================
-- SILVER
--
-- Column-identical to Bronze plus scheme_id, and amount
-- typed as NUMERIC.
--
-- WBR68 splits the RTA scheme code across amc_code and
-- sch_code ("B" + "51"). Silver rebuilds it into
-- rta_scheme_code, which is what
-- bronze.scheme_mapping.rta_scheme_code holds, and
-- resolves scheme_id from that.
-- =====================================================

CREATE TABLE IF NOT EXISTS silver.invalid_euin (

    source                  VARCHAR(20),
    report_type             VARCHAR(20),

    trxn_no                 VARCHAR(50),
    usertxn_no              VARCHAR(50),
    auto_trxn_no            VARCHAR(50),
    appln_no                VARCHAR(50),

    sch_code                VARCHAR(50),
    sch_name                TEXT,
    amc_code                VARCHAR(20),

    -- The RTA scheme code rebuilt from the parts the
    -- report splits it into (CAMS: amc_code || sch_code,
    -- e.g. "B" + "51" = "B51"). This, not sch_code, is
    -- what bronze.scheme_mapping.rta_scheme_code holds,
    -- so scheme_id resolves from here.
    rta_scheme_code         VARCHAR(50),

    folio_no                VARCHAR(40),
    folio                   VARCHAR(40),
    alt_folio               VARCHAR(40),
    folio_old               VARCHAR(40),
    scheme_folio_number     VARCHAR(40),

    inv_name                VARCHAR(255),
    inv_pan                 VARCHAR(10),
    email                   VARCHAR(255),

    euin                    VARCHAR(20),
    euin_valid              VARCHAR(5),
    reason                  VARCHAR(255),

    arn_code                VARCHAR(50),
    subbrok_arn             VARCHAR(50),
    subbrokcod              VARCHAR(50),
    user_code               VARCHAR(50),
    cons_code               VARCHAR(50),
    location                VARCHAR(100),

    trxn_type               VARCHAR(30),
    trxn_desc               VARCHAR(120),
    amount                  NUMERIC(20, 4),

    trade_date              DATE,
    posted_date             DATE,
    sys_reg_dt              DATE,
    sip_regn_date           DATE,

    -- resolved from bronze.scheme_mapping.rta_scheme_code
    scheme_id               VARCHAR(50),

    flag                    INTEGER,
    created_at              TIMESTAMP,
    updated_at              TIMESTAMP

);


CREATE INDEX IF NOT EXISTS idx_silver_invalid_euin_flag
    ON silver.invalid_euin (flag);

CREATE INDEX IF NOT EXISTS idx_silver_invalid_euin_key
    ON silver.invalid_euin (source, report_type, trxn_no);


-- =====================================================
-- GOLD
--
-- Natural key : rta | report_type | rta_txn_no
--
-- `id` is a deterministic uuid5 of that key, so a
-- re-uploaded file updates the same row instead of
-- inserting a second copy.
--
-- (rta, rta_txn_no) is also the join back to
-- gold.transactions.
-- =====================================================

CREATE TABLE IF NOT EXISTS gold.invalid_euin (

    id                      UUID PRIMARY KEY,

    rta                     VARCHAR(20),
    report_type             VARCHAR(20),

    -- joins to gold.transactions (rta, rta_txn_no)
    rta_txn_no              VARCHAR(50),

    -- scheme
    scheme_code             VARCHAR(50),
    scheme_name             TEXT,
    scheme_id               VARCHAR(50),
    amc_code                VARCHAR(20),
    amc_id                  UUID,

    -- investor
    folio_number            VARCHAR(40),
    pan                     VARCHAR(10),
    client_id               UUID,
    investor_name           VARCHAR(255),
    email                   VARCHAR(255),

    -- EUIN
    euin                    VARCHAR(20),

    -- the RTA's own marker, stored verbatim
    euin_valid_raw          VARCHAR(5),

    -- TRUE only when euin_valid_raw reads "Y". Every row
    -- in this report is a fault, so this is FALSE in
    -- practice; it is stored so the rule is visible and a
    -- future RTA spelling cannot change the meaning
    -- silently.
    is_euin_valid           BOOLEAN,

    reason                  VARCHAR(255),

    -- broker
    arn                     VARCHAR(50),
    sub_arn                 VARCHAR(50),
    sub_broker_code         VARCHAR(50),
    user_code               VARCHAR(50),
    cons_code               VARCHAR(50),
    location                VARCHAR(100),

    -- transaction detail
    txn_type_raw            VARCHAR(30),
    txn_desc                VARCHAR(120),
    amount                  NUMERIC(20, 4),

    trade_date              DATE,
    posted_date             DATE,
    sys_reg_date            DATE,
    sip_regn_date           DATE,

    -- secondary identifiers, kept for traceability
    application_no          VARCHAR(50),
    user_txn_no             VARCHAR(50),
    auto_txn_no             VARCHAR(50),
    alt_folio               VARCHAR(40),
    folio_old               VARCHAR(40),
    scheme_folio_number     VARCHAR(40),

    created_at              TIMESTAMP,
    updated_at              TIMESTAMP

);


CREATE INDEX IF NOT EXISTS idx_gold_invalid_euin_txn
    ON gold.invalid_euin (rta, rta_txn_no);

CREATE INDEX IF NOT EXISTS idx_gold_invalid_euin_scheme
    ON gold.invalid_euin (scheme_id);

CREATE INDEX IF NOT EXISTS idx_gold_invalid_euin_client
    ON gold.invalid_euin (client_id);

CREATE INDEX IF NOT EXISTS idx_gold_invalid_euin_euin
    ON gold.invalid_euin (euin);

-- ============================================================
-- SECTION 1: SILVER LAYER
-- ============================================================
--
-- NOTE ON THIS FILE'S HISTORY: the DELETE statements below originally used
-- a self-join on "a.created_at < b.created_at" to pick which duplicate row
-- to keep. That silently failed whenever two duplicate rows shared the
-- identical created_at (the norm, not the exception -- one bulk INSERT
-- stamps every row in a batch with a single now() call): it left 1,595 of
-- 1,764 known transaction duplicates and all 4 SIP duplicates untouched,
-- discovered live when CREATE UNIQUE INDEX then failed on the leftovers.
-- Fixed below to a ROW_NUMBER()-based delete, which always has exactly one
-- winner per key (ctid is unique per row, so ties are impossible). This
-- section reflects what was actually run to reach the current DB state.

-- --- 1.1 Cast amount/units to NUMERIC ---
-- (Already applied live -- re-running this ALTER on an already-NUMERIC
-- column is a harmless no-op type change, not idempotent-guarded further.)
ALTER TABLE silver.transaction_master_new
    ALTER COLUMN amount TYPE NUMERIC USING NULLIF(BTRIM(amount::text), '')::NUMERIC,
    ALTER COLUMN units  TYPE NUMERIC USING NULLIF(BTRIM(units::text), '')::NUMERIC;

-- --- 1.2 Preflight: duplicate census ---
SELECT 'silver.transaction_master_new' AS tbl,
       COUNT(*) - COUNT(DISTINCT (source, trxnno, folio_no, amount, units)) AS dup_count
FROM silver.transaction_master_new
UNION ALL
SELECT 'silver.investor_master',
       COUNT(*) - COUNT(DISTINCT (source, folio_no, product_code))
FROM silver.investor_master
UNION ALL
SELECT 'silver.sip_master_new (CORRECTED key)',
       COUNT(*) - COUNT(DISTINCT (
           source, folio_no, scheme_code, reg_date, auto_amount,
           COALESCE(NULLIF(NULLIF(BTRIM(ft_sip_regno), ''), '0'), NULLIF(BTRIM(request_ref_no), ''), '')
       ))
FROM silver.sip_master_new;

-- --- 1.3 Dedupe: keep newest row per natural key (ROW_NUMBER, not self-join) ---
DELETE FROM silver.transaction_master_new t
USING (
  SELECT ctid,
         ROW_NUMBER() OVER (
           PARTITION BY source, trxnno, folio_no, amount, units
           ORDER BY created_at DESC, ctid DESC
         ) AS rn
  FROM silver.transaction_master_new
) ranked
WHERE t.ctid = ranked.ctid AND ranked.rn > 1;

DELETE FROM silver.investor_master t
USING (
  SELECT ctid,
         ROW_NUMBER() OVER (
           PARTITION BY source, folio_no, product_code
           ORDER BY created_at DESC, ctid DESC
         ) AS rn
  FROM silver.investor_master
) ranked
WHERE t.ctid = ranked.ctid AND ranked.rn > 1;

-- NOTE: this uses the CORRECTED key (includes the reg-no expression) —
-- do NOT simplify this to the naive 5-column key, it will merge distinct SIPs.
DELETE FROM silver.sip_master_new t
USING (
  SELECT ctid,
         ROW_NUMBER() OVER (
           PARTITION BY source, folio_no, scheme_code, reg_date, auto_amount,
                        COALESCE(NULLIF(NULLIF(BTRIM(ft_sip_regno), ''), '0'), NULLIF(BTRIM(request_ref_no), ''), '')
           ORDER BY created_at DESC, ctid DESC
         ) AS rn
  FROM silver.sip_master_new
) ranked
WHERE t.ctid = ranked.ctid AND ranked.rn > 1;

-- --- 1.4 Build unique indexes CONCURRENTLY ---
-- NOTE: CREATE INDEX CONCURRENTLY cannot run inside a transaction block —
-- each statement below runs in its own implicit transaction under psql's
-- default autocommit (this file has no explicit BEGIN/COMMIT).
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_silver_txn_natural_key
    ON silver.transaction_master_new (source, trxnno, folio_no, amount, units)
    NULLS NOT DISTINCT;

CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_silver_investor_natural_key
    ON silver.investor_master (source, folio_no, product_code)
    NULLS NOT DISTINCT;

CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_silver_sip_natural_key
    ON silver.sip_master_new (
        source, folio_no, scheme_code, reg_date, auto_amount,
        (COALESCE(NULLIF(NULLIF(BTRIM(ft_sip_regno), ''), '0'), NULLIF(BTRIM(request_ref_no), ''), ''))
    )
    NULLS NOT DISTINCT;

-- --- 1.5 Promote to constraints (plain-column indexes only) ---
-- uq_silver_sip_natural_key is NOT promoted: Postgres refuses to promote an
-- expression-based index ("Cannot create a primary key or unique constraint
-- using such an index" -- confirmed live). It stays a standalone unique
-- INDEX, which enforces uniqueness on its own and is a valid ON CONFLICT
-- arbiter by giving its exact expression list (see utils/db.py's
-- upsert_dataframe(..., conflict_index_expr=...) once Task 5/6 land).
ALTER TABLE silver.transaction_master_new
    ADD CONSTRAINT uq_silver_txn_natural_key UNIQUE USING INDEX uq_silver_txn_natural_key;
ALTER TABLE silver.investor_master
    ADD CONSTRAINT uq_silver_investor_natural_key UNIQUE USING INDEX uq_silver_investor_natural_key;

-- ============================================================
-- SECTION 2: GOLD LAYER (all tables except gold.sip — see Section 3)
-- ============================================================

-- --- 2.1 Preflight (expect: transactions 130530, all others 0) ---
SELECT 'gold.transactions' AS tbl,
       COUNT(*) - COUNT(DISTINCT (rta, rta_txn_no, folio_number, amount, units)) AS dup_count
FROM gold.transactions
UNION ALL
SELECT 'gold.holdings', COUNT(*) - COUNT(DISTINCT (rta, pan, folio_number, scheme_id)) FROM gold.holdings
UNION ALL
SELECT 'gold.clients', COUNT(*) - COUNT(DISTINCT (pan)) FROM gold.clients
UNION ALL
SELECT 'gold.scheme', COUNT(*) - COUNT(DISTINCT (rta, scheme_code)) FROM gold.scheme
UNION ALL
SELECT 'gold.scheme_nav', COUNT(*) - COUNT(DISTINCT (scheme_id, nav_date)) FROM gold.scheme_nav
UNION ALL
SELECT 'gold.amc', COUNT(*) - COUNT(DISTINCT (rta, amc_code)) FROM gold.amc
UNION ALL
SELECT 'gold.folio_nominees', COUNT(*) - COUNT(DISTINCT (holding_id, seq)) FROM gold.folio_nominees;

-- --- 2.2 Dedupe (ROW_NUMBER, not self-join — see Section 1's note; only
--         gold.transactions has rows to remove) ---
DELETE FROM gold.transactions t
USING (
  SELECT ctid,
         ROW_NUMBER() OVER (
           PARTITION BY rta, rta_txn_no, folio_number, amount, units
           ORDER BY created_at DESC, ctid DESC
         ) AS rn
  FROM gold.transactions
) ranked
WHERE t.ctid = ranked.ctid AND ranked.rn > 1;

-- --- 2.3 Build unique indexes CONCURRENTLY ---
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_gold_txn_natural_key
    ON gold.transactions (rta, rta_txn_no, folio_number, amount, units) NULLS NOT DISTINCT;
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_gold_holdings
    ON gold.holdings (rta, pan, folio_number, scheme_id) NULLS NOT DISTINCT;
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_gold_clients_pan
    ON gold.clients (pan) NULLS NOT DISTINCT;
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_gold_scheme
    ON gold.scheme (rta, scheme_code) NULLS NOT DISTINCT;
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_gold_scheme_nav
    ON gold.scheme_nav (scheme_id, nav_date) NULLS NOT DISTINCT;
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_gold_amc
    ON gold.amc (rta, amc_code) NULLS NOT DISTINCT;
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_gold_folio_nominees
    ON gold.folio_nominees (holding_id, seq) NULLS NOT DISTINCT;

-- --- 2.4 Promote to constraints ---
ALTER TABLE gold.transactions   ADD CONSTRAINT uq_gold_txn_natural_key UNIQUE USING INDEX uq_gold_txn_natural_key;
ALTER TABLE gold.holdings       ADD CONSTRAINT uq_gold_holdings UNIQUE USING INDEX uq_gold_holdings;
ALTER TABLE gold.clients        ADD CONSTRAINT uq_gold_clients_pan UNIQUE USING INDEX uq_gold_clients_pan;
ALTER TABLE gold.scheme         ADD CONSTRAINT uq_gold_scheme UNIQUE USING INDEX uq_gold_scheme;
ALTER TABLE gold.scheme_nav     ADD CONSTRAINT uq_gold_scheme_nav UNIQUE USING INDEX uq_gold_scheme_nav;
ALTER TABLE gold.amc            ADD CONSTRAINT uq_gold_amc UNIQUE USING INDEX uq_gold_amc;
ALTER TABLE gold.folio_nominees ADD CONSTRAINT uq_gold_folio_nominees UNIQUE USING INDEX uq_gold_folio_nominees;

-- ============================================================
-- SECTION 3: GOLD.SIP (run only after the sip_reg_no code fix in
-- etl_gold_sip.py is live and gold.sip has been TRUNCATEd + rebuilt via
-- `python etl_gold_sip.py` — a SQL-only dedupe on the pre-fix data would
-- inherit the same blind spot the code had; see spec for why)
-- ============================================================

-- NOT promoted to a table CONSTRAINT: Postgres refuses to promote an
-- expression-based index ("Cannot create a primary key or unique
-- constraint using such an index" -- confirmed live on the analogous
-- silver SIP index in Section 1). The index alone enforces uniqueness
-- (verified live via a rollback-tested duplicate insert) and is targeted
-- in ON CONFLICT via utils/db.py's upsert_dataframe(..., conflict_index_expr=...).
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_gold_sip_natural_key
    ON gold.sip (
        rta, folio_number, scheme_code, registered_date, amount,
        (COALESCE(NULLIF(sip_reg_no, ''), ''))
    )
    NULLS NOT DISTINCT;

-- ============================================================
-- Dedup constraint hardening -- audit 2026-09-01
--
-- Two gaps the audit of 25_08_2025_intelliwealth_layer_db found:
--
-- 1. bronze.*.row_hash carries a PLAIN btree index, not a unique one.
--    compute_flag_via_row_hash() computes the right answer -- flag = 1
--    marked precisely the 93,372 rows of the accidental second CAMS load
--    on 2026-09-01 -- but nothing stopped those rows being written. A
--    third load would have landed just as happily.
--
-- 2. silver.transaction_master_new's natural key omits transaction_id
--    (KFin's `unqno`). KFin splits a redemption into one row per purchase
--    lot, all sharing a single td_trno, so 1,843 bronze rows collided on
--    (source, trxnno, folio_no, amount, units) despite carrying 1,761
--    distinct unqno values, 1,244 distinct purchase refs and 827 distinct
--    purchase dates. Silver kept 331 of them; 1,512 genuine rows -- and
--    Rs 6,711,494.35 of value -- were discarded as duplicates.
--
-- RUN ORDER: apply this AFTER bronze has been truncated and re-ingested
-- with the fixed date parsers. Running it against the current contents
-- will fail on step 1, by design -- the duplicate CAMS load is still
-- there, and this script must not be the thing that silently deletes it.
-- ============================================================

BEGIN;

-- ------------------------------------------------------------
-- 1. Enforce bronze dedup in the database, not just in Python
-- ------------------------------------------------------------

DROP INDEX IF EXISTS bronze.idx_bronze_txn_row_hash;
DROP INDEX IF EXISTS bronze.idx_bronze_investor_row_hash;
DROP INDEX IF EXISTS bronze.idx_bronze_sip_row_hash;

CREATE UNIQUE INDEX idx_bronze_txn_row_hash
    ON bronze.transaction_master_new (row_hash);

CREATE UNIQUE INDEX idx_bronze_investor_row_hash
    ON bronze.investor_master (row_hash);

CREATE UNIQUE INDEX idx_bronze_sip_row_hash
    ON bronze.sip_master_new (row_hash);

-- ------------------------------------------------------------
-- 2. Let a redemption's purchase lots survive into silver
--
-- transaction_id is KFin's unqno, e.g.
--     CF7082022024FUL11958521012021
--     CF7082022024FUL11958521013120
-- -- two lots of one redemption (td_trno 1195852, folio 7082022024)
-- that the old key could not tell apart. CAMS leaves the column blank,
-- and NULLS NOT DISTINCT keeps that side behaving exactly as before.
-- ------------------------------------------------------------

ALTER TABLE silver.transaction_master_new
    DROP CONSTRAINT IF EXISTS uq_silver_txn_natural_key;

ALTER TABLE silver.transaction_master_new
    ADD CONSTRAINT uq_silver_txn_natural_key
    UNIQUE NULLS NOT DISTINCT
    (source, trxnno, transaction_id, folio_no, amount, units);

COMMIT;

-- ------------------------------------------------------------
-- Verification -- expect 0 rows from each
-- ------------------------------------------------------------

-- No duplicate hashes left in bronze:
--   SELECT row_hash, count(*) FROM bronze.transaction_master_new
--   GROUP BY 1 HAVING count(*) > 1;

-- Bronze (flag = 0) and silver agree per source:
--   SELECT b.source, b.rows AS bronze, s.rows AS silver
--   FROM (SELECT source, count(*) rows FROM bronze.transaction_master_new
--         WHERE flag = 0 GROUP BY 1) b
--   FULL JOIN (SELECT source, count(*) rows
--              FROM silver.transaction_master_new GROUP BY 1) s
--     ON b.source = s.source
--   WHERE b.rows IS DISTINCT FROM s.rows;

-- Dates survived the re-ingest -- this MUST come back non-zero now
-- (it was 0 across every date column before the fix):
--   SELECT count(*) FROM gold.transactions
--   WHERE extract(day FROM txn_date) > 12;

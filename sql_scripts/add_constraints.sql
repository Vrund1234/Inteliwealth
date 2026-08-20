-- ===========================================================================
-- STEP 2 of 2 — add the UNIQUE constraints (17_08_2026_intelliwealth_layer_db
-- and 19_08_2026_intelliwealth_layer_db both use this same script)
--
-- Only run this after dedup_cleanup.sql has been run and its census queries
-- show 0 duplicate rows on every table — every index below fails to build
-- otherwise. Adds indexes/constraints only; does not delete or modify any row.
--
-- TARGET: PostgreSQL 14.23 (confirmed live 2026-08-19 — no NULLS NOT
-- DISTINCT support, that's PG15+). Every nullable key column uses the
-- standard pre-PG15 workaround: TWO expressions per nullable column —
-- "(col IS NULL)" and "COALESCE(col, <fallback>)" — so two NULLs match each
-- other via the boolean flag, and a real value never collides with NULL
-- because its flag is false while NULL's flag is true.
--
-- CORRECTION HISTORY (2026-08-19), both found by actually running this
-- against the live DB, not by inspection:
--   1. The first version used "COALESCE(col, chr(0))" as a single-expression
--      text sentinel, reasoning Postgres refuses to STORE a NUL byte so it
--      could never collide with real data. Wrong: Postgres also refuses to
--      CONSTRUCT a text value containing a NUL byte at all — the expression
--      itself throws "null character not permitted" the moment any row has
--      a NULL in that column. Fixed by switching every text sentinel to the
--      same two-part (IS NULL, COALESCE(col, '')) technique already used for
--      numeric columns — no "unreachable value" assumption, can't fail this
--      way ('' is a perfectly constructible Postgres text value).
--   2. `ALTER TABLE ... ADD CONSTRAINT ... UNIQUE USING INDEX` then failed
--      separately: Postgres refuses to adopt an index built on EXPRESSIONS
--      (or a PARTIAL index) as a formal table constraint — confirmed by the
--      server's own error ("index contains expressions... cannot create a
--      unique constraint using such an index"), not an assumption. This
--      affects every index below that needed the (IS NULL, COALESCE(...))
--      treatment, plus gold.clients' partial index. Fixed by dropping the
--      ADD CONSTRAINT step for those 10 — the CREATE UNIQUE INDEX alone is
--      what actually enforces uniqueness at the database level; a named
--      constraint object is a label on top of that mechanism, not the
--      mechanism itself, so duplicate-prevention is fully in effect either
--      way. Only the 4 indexes on plain non-nullable columns (scheme_nav,
--      scheme, amc, folio_nominees) become formal named constraints.
--
-- Run with: psql -f add_constraints.sql  (CONCURRENTLY builds cannot run
-- inside a transaction block / BEGIN...COMMIT wrapper.)
-- Idempotent: every statement is CREATE INDEX ... IF NOT EXISTS or
-- ADD CONSTRAINT on a plain index — safe to re-run; already-built objects
-- are skipped or (for ADD CONSTRAINT) will error harmlessly if re-run after
-- already succeeding, same as before.
-- ===========================================================================


-- ===========================================================================
-- PART 1 — UNIQUE INDEXES (bronze/silver, and gold's nullable-key tables).
-- These enforce uniqueness at the DB level immediately upon creation — no
-- separate ADD CONSTRAINT step, per the correction note above.
-- ===========================================================================

-- ---- BRONZE ---------------------------------------------------------------

CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_bronze_txn_natural_key
  ON bronze.transaction_master_new (
    (source IS NULL),   (COALESCE(source, '')),
    (trxnno IS NULL),   (COALESCE(trxnno, '')),
    (folio_no IS NULL), (COALESCE(folio_no, '')),
    ((NULLIF(btrim(amount), '')::numeric) IS NULL),
    (COALESCE(NULLIF(btrim(amount), '')::numeric, 0)),
    ((NULLIF(btrim(units), '')::numeric) IS NULL),
    (COALESCE(NULLIF(btrim(units), '')::numeric, 0))
  );

CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_bronze_investor_natural_key
  ON bronze.investor_master (
    (source IS NULL),       (COALESCE(source, '')),
    (folio_no IS NULL),     (COALESCE(folio_no, '')),
    (product_code IS NULL), (COALESCE(product_code, ''))
  );

CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_bronze_sip_natural_key
  ON bronze.sip_master_new (
    (source IS NULL),      (COALESCE(source, '')),
    (folio_no IS NULL),    (COALESCE(folio_no, '')),
    (auto_trno IS NULL),   (COALESCE(auto_trno, '')),
    (scheme_code IS NULL), (COALESCE(scheme_code, '')),
    (inv_iin IS NULL),     (COALESCE(inv_iin, ''))
  );

-- bronze.scheme_mapping (rta, rta_scheme_code) already exists — no change.

-- ---- SILVER -----------------------------------------------------------

CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_silver_txn_natural_key
  ON silver.transaction_master_new (
    (source IS NULL),   (COALESCE(source, '')),
    (trxnno IS NULL),   (COALESCE(trxnno, '')),
    (folio_no IS NULL), (COALESCE(folio_no, '')),
    ((NULLIF(btrim(amount), '')::numeric) IS NULL),
    (COALESCE(NULLIF(btrim(amount), '')::numeric, 0)),
    ((NULLIF(btrim(units), '')::numeric) IS NULL),
    (COALESCE(NULLIF(btrim(units), '')::numeric, 0))
  );

CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_silver_investor_natural_key
  ON silver.investor_master (
    (source IS NULL),       (COALESCE(source, '')),
    (folio_no IS NULL),     (COALESCE(folio_no, '')),
    (product_code IS NULL), (COALESCE(product_code, ''))
  );

-- CORRECTED (2026-08-19): silver.sip_master_new has no auto_trno column —
-- bronze->silver's append_new_rows() filters the dataframe to silver's real
-- column list before insert and silently drops it. Replacement key,
-- validated directly against the live table: reg_date + auto_amount give
-- 1,392/1,396 distinct (was 1,267/1,396 without them) — only 4 true-
-- duplicate groups, already removed by dedup_cleanup.sql.
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_silver_sip_natural_key
  ON silver.sip_master_new (
    (source IS NULL),      (COALESCE(source, '')),
    (folio_no IS NULL),    (COALESCE(folio_no, '')),
    (scheme_code IS NULL), (COALESCE(scheme_code, '')),
    (inv_iin IS NULL),     (COALESCE(inv_iin, '')),
    (reg_date IS NULL),    (COALESCE(reg_date, '0001-01-01'::date)),
    (auto_amount IS NULL), (COALESCE(auto_amount, 0))
  );

-- ---- GOLD (nullable-key tables — index only, see correction note) --------

-- 5 columns only — mirrors the app's uq_transactions_org_rta_txn_value
-- exactly. Do NOT widen this (no scheme_code/txn_date): a wider key here
-- lets gold hold rows the app's ON CONFLICT will treat as the same row and
-- silently overwrite on sync.
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_gold_txn_natural_key
  ON gold.transactions (
    (rta IS NULL),          (COALESCE(rta, '')),
    (rta_txn_no IS NULL),   (COALESCE(rta_txn_no, '')),
    (folio_number IS NULL), (COALESCE(folio_number, '')),
    (amount IS NULL),       (COALESCE(amount, 0)),
    (units IS NULL),        (COALESCE(units, 0))
  );

-- sip_reg_no is unusable alone (blank in 869/1,397 rows). Only takes full
-- effect for the app once its own migration lands (app-repo work, not this
-- script) replacing uq_sip_master_org_rta_reg and the sip sync's
-- sip_reg_no.is_not(None) filter.
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_gold_sip_natural_key
  ON gold.sip (
    (rta IS NULL),              (COALESCE(rta, '')),
    (folio_number IS NULL),     (COALESCE(folio_number, '')),
    (scheme_code IS NULL),      (COALESCE(scheme_code, '')),
    (registered_date IS NULL),  (COALESCE(registered_date, '0001-01-01'::date)),
    (amount IS NULL),           (COALESCE(amount, 0))
  );

CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_gold_holdings_natural_key
  ON gold.holdings (
    (rta IS NULL),           (COALESCE(rta, '')),
    (folio_number IS NULL),  (COALESCE(folio_number, '')),
    (scheme_id IS NULL),     (COALESCE(scheme_id, ''))
  );

-- Partial index (WHERE pan IS NOT NULL), matching the app's uq_client_org_pan
-- style — also can't become a formal constraint (partial indexes are barred
-- the same way expression indexes are), index-only same as the rest above.
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_gold_clients_pan
  ON gold.clients (pan) WHERE pan IS NOT NULL;


-- ===========================================================================
-- PART 2 — FORMAL CONSTRAINTS (gold's plain, non-nullable-key tables).
-- These four have no expression/partial-index restriction, so they do become
-- real named UNIQUE constraints, not just indexes.
-- ===========================================================================

CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_gold_scheme_nav_natural_key
  ON gold.scheme_nav (scheme_id, nav_date);
ALTER TABLE gold.scheme_nav
  ADD CONSTRAINT uq_gold_scheme_nav_natural_key UNIQUE USING INDEX uq_gold_scheme_nav_natural_key;

CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_gold_scheme_natural_key
  ON gold.scheme (rta, scheme_code);
ALTER TABLE gold.scheme
  ADD CONSTRAINT uq_gold_scheme_natural_key UNIQUE USING INDEX uq_gold_scheme_natural_key;

CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_gold_amc_code
  ON gold.amc (amc_code);
ALTER TABLE gold.amc
  ADD CONSTRAINT uq_gold_amc_code UNIQUE USING INDEX uq_gold_amc_code;

CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_gold_nominees_natural_key
  ON gold.folio_nominees (holding_id, seq);
ALTER TABLE gold.folio_nominees
  ADD CONSTRAINT uq_gold_nominees_natural_key UNIQUE USING INDEX uq_gold_nominees_natural_key;


-- ===========================================================================
-- PART 3 — VERIFY. Expect 14 rows total: 4 formal constraints (Part 2) +
-- 10 unique indexes that enforce the same thing without being named
-- constraint objects (Part 1) — both equally prevent duplicate rows.
-- ===========================================================================

-- Formal constraints (4 expected)
SELECT n.nspname                     AS schema,
       c.relname                     AS table,
       con.conname                   AS constraint,
       pg_get_constraintdef(con.oid) AS definition
FROM pg_constraint con
JOIN pg_class      c ON c.oid = con.conrelid
JOIN pg_namespace  n ON n.oid = c.relnamespace
WHERE n.nspname IN ('bronze','silver','gold')
  AND con.contype = 'u'
ORDER BY 1,2,3;

-- All 14 unique indexes (the 4 above plus the 10 index-only ones) — this is
-- the complete, authoritative list of what's actually enforcing uniqueness.
SELECT schemaname, tablename, indexname, indexdef
FROM pg_indexes
WHERE schemaname IN ('bronze','silver','gold')
  AND indexname LIKE 'uq_%'
ORDER BY 1,2,3;

-- Any index left INVALID by a failed CONCURRENTLY build.
SELECT n.nspname, c.relname AS invalid_index
FROM pg_index i
JOIN pg_class c ON c.oid = i.indexrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE NOT i.indisvalid AND n.nspname IN ('bronze','silver','gold');

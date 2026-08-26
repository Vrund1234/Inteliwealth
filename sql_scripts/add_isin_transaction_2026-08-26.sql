-- sql_scripts/add_isin_transaction_2026-08-26.sql
-- ============================================================
-- Adds an `isin` column to all three layers of the transaction table.
--
-- Source: KFIN transaction files now carry an `isin` column (CAMS/other
-- sources may not -- the column stays NULL there, which is expected).
--
-- varchar(20), matching bronze.scheme_mapping.rta_isin and gold.scheme.isin
-- / gold.sip.isin elsewhere in this schema.
--
-- IMPORTANT: bronze.transaction_master_new's duplicate-flag row_hash is a
-- hash of every non-metadata column on the table (see
-- docs/superpowers/specs/2026-08-26-bronze-dedup-performance-design.md).
-- Adding this column changes that hash for every future load. Existing
-- rows' stored row_hash must be recomputed -- run
-- backfill_bronze_row_hash.py (scoped to transaction_master_new) AFTER
-- this migration and BEFORE the next ingestion run, or a resend of an
-- already-loaded transaction that now carries an ISIN will be silently
-- mis-flagged as a duplicate and never reach silver.
-- ============================================================

ALTER TABLE bronze.transaction_master_new ADD COLUMN IF NOT EXISTS isin VARCHAR(20);
ALTER TABLE silver.transaction_master_new ADD COLUMN IF NOT EXISTS isin VARCHAR(20);
ALTER TABLE gold.transactions             ADD COLUMN IF NOT EXISTS isin VARCHAR(20);

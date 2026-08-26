-- ============================================================
-- Bronze row_hash column -- see
-- docs/superpowers/specs/2026-08-26-bronze-dedup-performance-design.md
--
-- Nullable for now: Task 3 backfills existing rows, Task 4 builds the
-- index CONCURRENTLY and only then sets NOT NULL.
-- ============================================================

ALTER TABLE bronze.transaction_master_new ADD COLUMN IF NOT EXISTS row_hash TEXT;
ALTER TABLE bronze.investor_master        ADD COLUMN IF NOT EXISTS row_hash TEXT;
ALTER TABLE bronze.sip_master_new         ADD COLUMN IF NOT EXISTS row_hash TEXT;

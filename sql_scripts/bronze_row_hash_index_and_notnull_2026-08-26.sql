-- sql_scripts/bronze_row_hash_index_and_notnull_2026-08-26.sql
-- ============================================================
-- Run only after backfill_bronze_row_hash.py has been run and confirmed
-- to leave zero NULL row_hash values (Task 3, Step 6). CREATE INDEX
-- CONCURRENTLY cannot run inside a transaction block -- this file has no
-- explicit BEGIN/COMMIT, matching sql_scripts/dedup_constraints_migration_2026-08-25.sql.
-- ============================================================

-- --- Preflight: expect 0 for all three ---
SELECT 'transaction_master_new' AS tbl, COUNT(*) FILTER (WHERE row_hash IS NULL) AS nulls FROM bronze.transaction_master_new
UNION ALL SELECT 'investor_master', COUNT(*) FILTER (WHERE row_hash IS NULL) FROM bronze.investor_master
UNION ALL SELECT 'sip_master_new', COUNT(*) FILTER (WHERE row_hash IS NULL) FROM bronze.sip_master_new;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_bronze_txn_row_hash
    ON bronze.transaction_master_new (row_hash);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_bronze_investor_row_hash
    ON bronze.investor_master (row_hash);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_bronze_sip_row_hash
    ON bronze.sip_master_new (row_hash);

ALTER TABLE bronze.transaction_master_new ALTER COLUMN row_hash SET NOT NULL;
ALTER TABLE bronze.investor_master        ALTER COLUMN row_hash SET NOT NULL;
ALTER TABLE bronze.sip_master_new         ALTER COLUMN row_hash SET NOT NULL;

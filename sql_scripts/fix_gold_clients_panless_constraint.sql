-- ============================================================
-- Fix: gold.clients silently drops every PAN-less client but one
--
-- ROOT CAUSE
--   uq_gold_clients_pan was created as
--       UNIQUE (pan) NULLS NOT DISTINCT
--   Under NULLS NOT DISTINCT Postgres treats two NULL pans as
--   equal, so the table permits exactly ONE PAN-less client in
--   total. Minors have no PAN of their own, so every minor after
--   the first is rejected with
--       duplicate key value violates unique constraint
--       "uq_gold_clients_pan"
--       DETAIL: Key (pan)=(null) already exists.
--   (reproduced live 2026-09-09).
--
--   silver.investor_master holds 28 distinct PAN-less people
--   (27 after the ETL's name normalisation); gold.clients holds 1.
--
--   python_scripts/etl_gold_clients.py already documents this
--   requirement at the upsert -- the migration was simply never
--   applied.
--
-- VERIFIED SAFE
--   SELECT pan FROM gold.clients WHERE pan IS NOT NULL
--   GROUP BY pan HAVING count(*) > 1;   -> 0 rows
--   so the rebuilt constraint cannot fail on existing data.
-- ============================================================

BEGIN;

-- ------------------------------------------------------------
-- 1. Rebuild the PAN uniqueness rule as NULLS DISTINCT
--    (the default). Real PANs stay unique; any number of
--    PAN-less clients is allowed.
-- ------------------------------------------------------------

ALTER TABLE gold.clients
    DROP CONSTRAINT uq_gold_clients_pan;

ALTER TABLE gold.clients
    ADD CONSTRAINT uq_gold_clients_pan UNIQUE (pan);

-- ------------------------------------------------------------
-- 2. Backstop for the PAN-less population.
--
--    etl_gold_clients.load_clients() de-duplicates PAN-less
--    rows on guardian_pan + first-name + date_of_birth and
--    plain-INSERTs them (they can never take the ON CONFLICT
--    path, because upsert_dataframe partitions by `pan` and
--    every PAN-less row would collapse into one partition).
--    That check is code-side only; this partial index is the
--    database-side guarantee that a re-run cannot double-load
--    the same PAN-less person.
--
--    Referenced by name in etl_gold_clients.py (as
--    uq_clients_pan_absent) but never actually created.
-- ------------------------------------------------------------

CREATE UNIQUE INDEX IF NOT EXISTS uq_clients_pan_absent
    ON gold.clients (
        guardian_pan,
        upper(btrim(full_name)),
        date_of_birth
    )
    WHERE pan IS NULL;

COMMIT;

-- ------------------------------------------------------------
-- VERIFY
-- ------------------------------------------------------------
-- SELECT c.relname, i.indnullsnotdistinct
-- FROM pg_index i
-- JOIN pg_class c ON c.oid = i.indexrelid
-- JOIN pg_namespace n ON n.oid = c.relnamespace
-- WHERE n.nspname = 'gold' AND c.relname = 'uq_gold_clients_pan';
--   -> indnullsnotdistinct must now be 'f'
--
-- Then re-run:  python python_scripts/etl_gold_clients.py
-- Expect gold.clients to go from 593 -> ~620 rows.

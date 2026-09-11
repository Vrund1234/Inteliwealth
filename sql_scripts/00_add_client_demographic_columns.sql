-- ============================================================
-- STEP 0 — the demographic columns everything else builds on
--
-- These were added by hand on the first warehouse and never
-- captured as a script, so a second database has no way to
-- reach the same starting point. Every later script and the
-- ETL assume they exist.
--
-- age and is_minor live in BOTH layers: silver carries what the
-- registry file implied at the time it was loaded, gold carries
-- the value recomputed from date_of_birth on every run. The
-- gold one is the answer; silver's is kept because it is what
-- the source said.
-- ============================================================

ALTER TABLE silver.investor_master
    ADD COLUMN IF NOT EXISTS age INTEGER,
    ADD COLUMN IF NOT EXISTS is_minor BOOLEAN;

ALTER TABLE gold.clients
    ADD COLUMN IF NOT EXISTS age INTEGER,
    ADD COLUMN IF NOT EXISTS is_minor BOOLEAN,
    -- The guardian's PAN a minor still transacts on. Not a FK:
    -- the guardian need not be a client, and the value stands on
    -- its own -- it is what the RTA holds the folio under.
    ADD COLUMN IF NOT EXISTS guardian_pan VARCHAR(10),
    -- 18 or over and still on a guardian's PAN: the records have
    -- to be moved to their own.
    ADD COLUMN IF NOT EXISTS is_documentupdaterequired BOOLEAN;

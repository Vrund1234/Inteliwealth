-- =============================================================================
-- gold.client_address_review: remove the decision columns
-- 2026-09-07
-- =============================================================================
--
-- Follow-up to client_address_dedup_2026-09-07.sql, which created this table
-- with decision / decided_by / decided_at. Those columns cannot be used:
--
--   * the app's gold connection is read-only by construction
--     (app/modules/gold_sync/gold_db.py -- "we never write to it", and the
--     module makes zero gold_db.add/commit calls), so the reviewers who
--     actually decide -- organization, distributor, relationship manager --
--     have no way to write a decision into this schema;
--
--   * a decision needs organization_id to scope it to a tenant, a real
--     users.id for who decided, and RBAC over both. Gold has none of them.
--
-- Gold detects; the app decides, in its own client_address_review table
-- alongside the family_import_row pattern it already runs.
--
-- Safe: nothing has been decided here (the columns were never writable), so
-- there is nothing to migrate out. Run only against a database where
-- client_address_dedup_2026-09-07.sql has already been applied; the file it
-- follows now creates the table in this shape directly.
-- =============================================================================

BEGIN;

-- Refuse to run if a decision was somehow recorded -- dropping the column
-- would destroy it silently.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='gold' AND table_name='client_address_review'
          AND column_name='decision'
    ) AND EXISTS (
        SELECT 1 FROM gold.client_address_review WHERE decision IS NOT NULL
    ) THEN
        RAISE EXCEPTION
            'gold.client_address_review holds recorded decisions; migrate them '
            'to the app before dropping these columns.';
    END IF;
END $$;

DROP INDEX IF EXISTS gold.ix_client_address_review_pending;

ALTER TABLE gold.client_address_review
    DROP CONSTRAINT IF EXISTS ck_client_address_review_decision;

ALTER TABLE gold.client_address_review DROP COLUMN IF EXISTS decision;
ALTER TABLE gold.client_address_review DROP COLUMN IF EXISTS decided_by;
ALTER TABLE gold.client_address_review DROP COLUMN IF EXISTS decided_at;

CREATE INDEX IF NOT EXISTS ix_client_address_review_client
    ON gold.client_address_review USING btree (client_id);

COMMIT;

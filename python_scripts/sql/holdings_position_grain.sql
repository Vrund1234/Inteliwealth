-- =====================================================
-- gold.holdings : transaction grain -> position grain
-- =====================================================
--
-- A holding is one folio's position in one scheme. The table was being written
-- at transaction grain instead: 166,996 rows over 4,947 distinct
-- (rta, folio_number, scheme_id), one row per transaction, growing on every
-- run, with a uuid4 id that changed each time.
--
-- etl_gold_holdings.py now rolls the transactions up before loading and upserts
-- on the key below. Its rows cannot be reconciled with what is in the table
-- now, so the table is emptied and rebuilt from silver.
--
-- Nothing is lost that is not re-derivable: gold.holdings is built entirely
-- from silver.transaction_master_new and silver.investor_master, no foreign key
-- references it, and its ids were never stable enough for anything to hold on
-- to.
--
-- Run once, then `python etl_gold_holdings.py` (or Transform in the app).


BEGIN;


TRUNCATE TABLE gold.holdings;


-- The upsert's ON CONFLICT target.
--
-- NULLS NOT DISTINCT matters here: scheme_id is NULL on 938 of the 3,509
-- positions — the schemes that are not in gold.scheme, all KFIN — and under the
-- default NULLS DISTINCT those rows would sit outside the constraint entirely
-- and re-insert on every run.

ALTER TABLE gold.holdings

    ADD CONSTRAINT holdings_position_key

    UNIQUE NULLS NOT DISTINCT (

        rta,

        folio_number,

        scheme_id

    );


COMMIT;


-- Verify: expect 0 rows, then 3,509 after the loader runs, and the key to be
-- listed as a unique constraint.
--
--   SELECT count(*) FROM gold.holdings;
--   \d gold.holdings

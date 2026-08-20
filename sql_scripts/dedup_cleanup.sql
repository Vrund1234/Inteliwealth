-- ===========================================================================
-- STEP 1 of 2 — normalize + dedupe bronze/silver/gold before constraints
-- can be built (17_08_2026_intelliwealth_layer_db).
--
-- ** DESTRUCTIVE. Deletes duplicate rows. Confirm a backup exists before
--    running Part 2 of this file. **
--
-- Run this file COMPLETELY and review its output before running
-- add_constraints.sql — that file assumes this one has already succeeded.
--
-- Written 2026-08-19. Fixed 2026-08-19: silver.sip_master_new does NOT have
-- an auto_trno column (bronze does; the bronze->silver transform in
-- transformations/transform.py's append_new_rows() filters the dataframe
-- down to `db_cols` — silver's real column list — right before insert,
-- silently dropping auto_trno). Corrected key, validated directly against
-- the live table (1,396 rows): (source, folio_no, scheme_code, inv_iin,
-- reg_date, auto_amount) -> 1,392 distinct, 4 true-duplicate groups (8 rows,
-- 4 to delete) — same folios already known duplicated at the gold layer, not
-- a new surprise.
--
-- Run with: psql -f dedup_cleanup.sql
-- ===========================================================================


-- ===========================================================================
-- PART 0 — PREFLIGHT. Read-only. Run this and review before Part 1.
-- ===========================================================================

SELECT current_setting('server_version_num')::int >= 150000 AS nulls_not_distinct_supported,
       version();

SELECT table_schema, table_name, column_name, data_type
FROM information_schema.columns
WHERE table_schema IN ('bronze','silver','gold')
  AND column_name IN ('amount','units','trxnno','rta_txn_no','folio_no','folio_number','pan')
ORDER BY table_schema, table_name, column_name;

SELECT 'gold.sip.sip_reg_no' AS col,
       count(*) FILTER (WHERE sip_reg_no IS NULL)     AS nulls,
       count(*) FILTER (WHERE btrim(sip_reg_no) = '') AS blanks,
       count(*) AS total
FROM gold.sip
UNION ALL
SELECT 'gold.transactions.folio_number',
       count(*) FILTER (WHERE folio_number IS NULL),
       count(*) FILTER (WHERE btrim(folio_number) = ''),
       count(*)
FROM gold.transactions;

-- Duplicate census — every constraint in add_constraints.sql fails to build
-- if its table reports a row here. Run all, compare against Part 1.2's
-- expected delete counts below, stop and re-check on any mismatch.

SELECT source, trxnno, folio_no,
       NULLIF(btrim(amount), '')::numeric AS amount_n,
       NULLIF(btrim(units), '')::numeric  AS units_n,
       count(*)
FROM bronze.transaction_master_new
GROUP BY 1,2,3,4,5 HAVING count(*) > 1 ORDER BY 6 DESC LIMIT 50;

SELECT source, folio_no, product_code, count(*)
FROM bronze.investor_master
GROUP BY 1,2,3 HAVING count(*) > 1 ORDER BY 4 DESC LIMIT 50;

SELECT source, folio_no, auto_trno, scheme_code, inv_iin, count(*)
FROM bronze.sip_master_new
GROUP BY 1,2,3,4,5 HAVING count(*) > 1 ORDER BY 6 DESC LIMIT 50;

SELECT source, trxnno, folio_no,
       NULLIF(btrim(amount), '')::numeric AS amount_n,
       NULLIF(btrim(units), '')::numeric  AS units_n,
       count(*)
FROM silver.transaction_master_new
GROUP BY 1,2,3,4,5 HAVING count(*) > 1 ORDER BY 6 DESC LIMIT 50;

SELECT source, folio_no, product_code, count(*)
FROM silver.investor_master
GROUP BY 1,2,3 HAVING count(*) > 1 ORDER BY 4 DESC LIMIT 50;

-- CORRECTED KEY (was: source, folio_no, auto_trno, scheme_code, inv_iin —
-- auto_trno doesn't exist in this table). Expect 4 groups / 8 rows / 4 to
-- delete (verified 2026-08-19).
SELECT source, folio_no, scheme_code, inv_iin, reg_date, auto_amount, count(*)
FROM silver.sip_master_new
GROUP BY 1,2,3,4,5,6 HAVING count(*) > 1 ORDER BY 7 DESC LIMIT 50;

SELECT rta, rta_txn_no, folio_number, amount, units, count(*)
FROM gold.transactions
GROUP BY 1,2,3,4,5 HAVING count(*) > 1 ORDER BY 6 DESC LIMIT 50;

-- expect 8 groups / 17 rows / 9 rows to delete (verified 2026-08-19)
SELECT rta, folio_number, scheme_code, registered_date, amount, count(*)
FROM gold.sip
GROUP BY 1,2,3,4,5 HAVING count(*) > 1 ORDER BY 6 DESC LIMIT 50;

SELECT rta, folio_number, scheme_id, count(*)
FROM gold.holdings
GROUP BY 1,2,3 HAVING count(*) > 1 ORDER BY 4 DESC LIMIT 50;

SELECT upper(btrim(pan)) AS pan_n, count(*)
FROM gold.clients
WHERE pan IS NOT NULL
GROUP BY 1 HAVING count(*) > 1 ORDER BY 2 DESC LIMIT 50;

SELECT scheme_id, nav_date, count(*) FROM gold.scheme_nav
GROUP BY 1,2 HAVING count(*) > 1 ORDER BY 3 DESC LIMIT 50;

SELECT rta, scheme_code, count(*) FROM gold.scheme
GROUP BY 1,2 HAVING count(*) > 1 ORDER BY 3 DESC LIMIT 50;

SELECT amc_code, count(*) FROM gold.amc
GROUP BY 1 HAVING count(*) > 1 ORDER BY 2 DESC LIMIT 50;

SELECT holding_id, seq, count(*) FROM gold.folio_nominees
GROUP BY 1,2 HAVING count(*) > 1 ORDER BY 3 DESC LIMIT 50;


-- ===========================================================================
-- PART 1 — NORMALIZE, THEN DEDUPE.
-- ** DESTRUCTIVE. Do not run without a backup and a reviewed Part 0 output. **
-- ===========================================================================

-- 1.1 Blank -> NULL on key columns.
UPDATE bronze.transaction_master_new SET
  source = nullif(btrim(source), ''), trxnno = nullif(btrim(trxnno), ''),
  folio_no = nullif(btrim(folio_no), '')
WHERE source IS DISTINCT FROM nullif(btrim(source), '')
   OR trxnno IS DISTINCT FROM nullif(btrim(trxnno), '')
   OR folio_no IS DISTINCT FROM nullif(btrim(folio_no), '');

UPDATE bronze.investor_master SET
  source = nullif(btrim(source), ''), folio_no = nullif(btrim(folio_no), ''),
  product_code = nullif(btrim(product_code), '')
WHERE source IS DISTINCT FROM nullif(btrim(source), '')
   OR folio_no IS DISTINCT FROM nullif(btrim(folio_no), '')
   OR product_code IS DISTINCT FROM nullif(btrim(product_code), '');

UPDATE bronze.sip_master_new SET
  source = nullif(btrim(source), ''), folio_no = nullif(btrim(folio_no), ''),
  auto_trno = nullif(btrim(auto_trno), ''), scheme_code = nullif(btrim(scheme_code), ''),
  inv_iin = nullif(btrim(inv_iin), '')
WHERE source IS DISTINCT FROM nullif(btrim(source), '')
   OR folio_no IS DISTINCT FROM nullif(btrim(folio_no), '')
   OR auto_trno IS DISTINCT FROM nullif(btrim(auto_trno), '')
   OR scheme_code IS DISTINCT FROM nullif(btrim(scheme_code), '')
   OR inv_iin IS DISTINCT FROM nullif(btrim(inv_iin), '');

UPDATE silver.transaction_master_new SET
  source = nullif(btrim(source), ''), trxnno = nullif(btrim(trxnno), ''),
  folio_no = nullif(btrim(folio_no), '')
WHERE source IS DISTINCT FROM nullif(btrim(source), '')
   OR trxnno IS DISTINCT FROM nullif(btrim(trxnno), '')
   OR folio_no IS DISTINCT FROM nullif(btrim(folio_no), '');

UPDATE silver.investor_master SET
  source = nullif(btrim(source), ''), folio_no = nullif(btrim(folio_no), ''),
  product_code = nullif(btrim(product_code), '')
WHERE source IS DISTINCT FROM nullif(btrim(source), '')
   OR folio_no IS DISTINCT FROM nullif(btrim(folio_no), '')
   OR product_code IS DISTINCT FROM nullif(btrim(product_code), '');

-- CORRECTED: silver.sip_master_new has no auto_trno column. Normalize the
-- columns the corrected key actually uses instead.
UPDATE silver.sip_master_new SET
  source = nullif(btrim(source), ''), folio_no = nullif(btrim(folio_no), ''),
  scheme_code = nullif(btrim(scheme_code), ''), inv_iin = nullif(btrim(inv_iin), '')
WHERE source IS DISTINCT FROM nullif(btrim(source), '')
   OR folio_no IS DISTINCT FROM nullif(btrim(folio_no), '')
   OR scheme_code IS DISTINCT FROM nullif(btrim(scheme_code), '')
   OR inv_iin IS DISTINCT FROM nullif(btrim(inv_iin), '');

UPDATE gold.transactions SET
  folio_number = nullif(btrim(folio_number), ''), rta_txn_no = nullif(btrim(rta_txn_no), '')
WHERE folio_number IS DISTINCT FROM nullif(btrim(folio_number), '')
   OR rta_txn_no IS DISTINCT FROM nullif(btrim(rta_txn_no), '');

UPDATE gold.sip SET
  sip_reg_no = nullif(btrim(sip_reg_no), ''), folio_number = nullif(btrim(folio_number), ''),
  scheme_code = nullif(btrim(scheme_code), '')
WHERE sip_reg_no IS DISTINCT FROM nullif(btrim(sip_reg_no), '')
   OR folio_number IS DISTINCT FROM nullif(btrim(folio_number), '')
   OR scheme_code IS DISTINCT FROM nullif(btrim(scheme_code), '');

UPDATE gold.holdings SET folio_number = nullif(btrim(folio_number), '')
WHERE folio_number IS DISTINCT FROM nullif(btrim(folio_number), '');

UPDATE gold.clients SET pan = nullif(upper(btrim(pan)), '')
WHERE pan IS DISTINCT FROM nullif(upper(btrim(pan)), '');


-- 1.2 Dedupe. Keeps the physically-newest row (highest ctid) per key group.
--     ** Confirm row counts against Part 0's census before running each block. **
--
--     Uses ROW_NUMBER() OVER (PARTITION BY <key> ORDER BY ctid) instead of a
--     self-join. A self-join (a.ctid < b.ctid AND a.col IS NOT DISTINCT FROM
--     b.col ...) has no index to use yet (that's what Part 2/add_constraints.sql
--     builds afterward) and IS NOT DISTINCT FROM can't be hash- or merge-joined
--     on PG14 without one, so the planner falls back to a nested loop — on
--     bronze/silver's 128K-row transaction table that's ~128,766 x 128,766
--     comparisons (confirmed via EXPLAIN: cost ~969 million), which never
--     finished in testing. ROW_NUMBER() is a single scan + sort, same result,
--     no self-join, no index required. PARTITION BY also treats NULLs as
--     equal within a group the same way GROUP BY does, so no IS NOT DISTINCT
--     FROM is needed here either.

DELETE FROM bronze.transaction_master_new
WHERE ctid IN (
  SELECT ctid FROM (
    SELECT ctid, ROW_NUMBER() OVER (
      PARTITION BY source, trxnno, folio_no,
                   NULLIF(btrim(amount), '')::numeric,
                   NULLIF(btrim(units), '')::numeric
      ORDER BY ctid
    ) AS rn
    FROM bronze.transaction_master_new
  ) ranked
  WHERE rn > 1
);

DELETE FROM bronze.investor_master
WHERE ctid IN (
  SELECT ctid FROM (
    SELECT ctid, ROW_NUMBER() OVER (
      PARTITION BY source, folio_no, product_code ORDER BY ctid
    ) AS rn
    FROM bronze.investor_master
  ) ranked
  WHERE rn > 1
);

DELETE FROM bronze.sip_master_new
WHERE ctid IN (
  SELECT ctid FROM (
    SELECT ctid, ROW_NUMBER() OVER (
      PARTITION BY source, folio_no, auto_trno, scheme_code, inv_iin ORDER BY ctid
    ) AS rn
    FROM bronze.sip_master_new
  ) ranked
  WHERE rn > 1
);

DELETE FROM silver.transaction_master_new
WHERE ctid IN (
  SELECT ctid FROM (
    SELECT ctid, ROW_NUMBER() OVER (
      PARTITION BY source, trxnno, folio_no,
                   NULLIF(btrim(amount), '')::numeric,
                   NULLIF(btrim(units), '')::numeric
      ORDER BY ctid
    ) AS rn
    FROM silver.transaction_master_new
  ) ranked
  WHERE rn > 1
);

DELETE FROM silver.investor_master
WHERE ctid IN (
  SELECT ctid FROM (
    SELECT ctid, ROW_NUMBER() OVER (
      PARTITION BY source, folio_no, product_code ORDER BY ctid
    ) AS rn
    FROM silver.investor_master
  ) ranked
  WHERE rn > 1
);

-- CORRECTED KEY — expect 4 rows deleted (4 groups, 8 rows involved;
-- verified 2026-08-19 against the live table).
DELETE FROM silver.sip_master_new
WHERE ctid IN (
  SELECT ctid FROM (
    SELECT ctid, ROW_NUMBER() OVER (
      PARTITION BY source, folio_no, scheme_code, inv_iin, reg_date, auto_amount
      ORDER BY ctid
    ) AS rn
    FROM silver.sip_master_new
  ) ranked
  WHERE rn > 1
);

-- gold.transactions — 5-column key only, matches the app exactly
DELETE FROM gold.transactions
WHERE ctid IN (
  SELECT ctid FROM (
    SELECT ctid, ROW_NUMBER() OVER (
      PARTITION BY rta, rta_txn_no, folio_number, amount, units ORDER BY ctid
    ) AS rn
    FROM gold.transactions
  ) ranked
  WHERE rn > 1
);

-- gold.sip — expect 9 rows deleted (8 groups, 17 rows involved)
DELETE FROM gold.sip
WHERE ctid IN (
  SELECT ctid FROM (
    SELECT ctid, ROW_NUMBER() OVER (
      PARTITION BY rta, folio_number, scheme_code, registered_date, amount ORDER BY ctid
    ) AS rn
    FROM gold.sip
  ) ranked
  WHERE rn > 1
);

DELETE FROM gold.holdings
WHERE ctid IN (
  SELECT ctid FROM (
    SELECT ctid, ROW_NUMBER() OVER (
      PARTITION BY rta, folio_number, scheme_id ORDER BY ctid
    ) AS rn
    FROM gold.holdings
  ) ranked
  WHERE rn > 1
);

DELETE FROM gold.clients
WHERE ctid IN (
  SELECT ctid FROM (
    SELECT ctid, ROW_NUMBER() OVER (PARTITION BY pan ORDER BY ctid) AS rn
    FROM gold.clients
    WHERE pan IS NOT NULL
  ) ranked
  WHERE rn > 1
);

-- gold.scheme_nav / gold.scheme / gold.amc / gold.folio_nominees — Part 0
-- reported 0 groups for all four on 2026-08-19; no dedupe DELETE included.
-- If a re-run of Part 0 shows a group, add the matching DELETE before
-- proceeding to add_constraints.sql for that table.


-- ===========================================================================
-- DONE. Re-run Part 0's census queries above to confirm every table now
-- shows 0 rows before running add_constraints.sql.
-- ===========================================================================

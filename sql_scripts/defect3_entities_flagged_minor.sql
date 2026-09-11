-- ============================================================
-- DEFECT 3 -- non-individual entities flagged is_minor = true
--
-- WHAT IS WRONG
--   Companies, LLPs, partnership firms, HUFs and trusts carry
--   an INCORPORATION / formation date in the registry's dob
--   field. The ETL derives age from that date and then applies
--   the age < 18 rule, so a company incorporated in 2023 is
--   stored as a 2-year-old minor.
--
--   DEVSTREE IT SERVICES PVT LTD  -> dob 2013-01-09, age 13, is_minor = t
--   AMICUS RCM SERVICES PVT LTD   -> dob 2023-12-08, age  2, is_minor = t
--
-- HOW A GENUINE MINOR IS TOLD APART
--   A real minor folio always carries a guardian PAN -- the
--   registry cannot open one without a guardian. Every one of
--   the 22 genuine minors in gold.clients has guardian_pan set;
--   not one of the 17 misflagged entities does.
--
--   That makes `is_minor = true AND guardian_pan IS NULL` the
--   detection rule, and it does not depend on decoding every
--   RTA tax_status code (the feeds use both words and
--   single-letter codes: H/HUF/3 = HUF, T/8 = Trust,
--   B/C = Body Corporate/Company, P = Partnership,
--   Y = LLP, W = Sole Proprietorship).
--
--   Name matching is NOT reliable here: a regex on
--   PVT|LTD|LLP|TRUST|HUF... finds only 15 of the 17, because
--   two entities are named like people
--   (e.g. RIDHAM CORPORATION vs a firm with a personal name).
-- ============================================================


\echo '=============================================='
\echo 'A. THE DEFECT -- entities flagged as minors'
\echo '   (is_minor = true with no guardian PAN)'
\echo '=============================================='

SELECT
    full_name,
    tax_status,
    date_of_birth   AS incorporation_date_read_as_dob,
    age             AS derived_age,
    is_minor,
    is_documentupdaterequired
FROM gold.clients
WHERE is_minor = true
  AND guardian_pan IS NULL
ORDER BY age NULLS LAST, full_name;


\echo ''
\echo '=============================================='
\echo 'B. SPLIT -- genuine minors vs misflagged entities'
\echo '=============================================='

SELECT
    CASE
        WHEN guardian_pan IS NOT NULL THEN 'genuine minor (has guardian PAN)'
        ELSE                               'MISFLAGGED entity (no guardian PAN)'
    END AS classification,
    count(*) AS clients
FROM gold.clients
WHERE is_minor = true
GROUP BY 1
ORDER BY 1;


\echo ''
\echo '=============================================='
\echo 'C. BY tax_status -- shows which codes are'
\echo '   individual-minor codes and which are entity'
\echo '   codes. flagged_minor should only ever be'
\echo '   non-zero on the minor codes.'
\echo '=============================================='

SELECT
    tax_status,
    count(*)                                            AS clients,
    count(*) FILTER (WHERE is_minor)                    AS flagged_minor,
    count(*) FILTER (WHERE is_minor AND guardian_pan IS NOT NULL) AS genuine_minor,
    count(*) FILTER (WHERE is_minor AND guardian_pan IS NULL)     AS misflagged,
    min(age) AS min_age,
    max(age) AS max_age
FROM gold.clients
WHERE is_minor = true
GROUP BY 1
ORDER BY misflagged DESC, clients DESC;


\echo ''
\echo '=============================================='
\echo 'D. SAME DEFECT AT THE SILVER LAYER'
\echo '   (fixing gold alone would be undone by the'
\echo '    next pipeline run -- silver is the source)'
\echo '=============================================='

SELECT
    count(*)                          AS silver_rows_flagged_minor,
    count(*) FILTER (
        WHERE guardian_pan IS NULL
           OR NOT (upper(trim(guardian_pan)) ~ '^[A-Z]{5}[0-9]{4}[A-Z]$')
    )                                 AS misflagged_rows,
    count(DISTINCT pan_no) FILTER (
        WHERE guardian_pan IS NULL
           OR NOT (upper(trim(guardian_pan)) ~ '^[A-Z]{5}[0-9]{4}[A-Z]$')
    )                                 AS misflagged_distinct_pans
FROM silver.investor_master
WHERE is_minor = true;


\echo ''
\echo '--- D2. the silver rows themselves ---'

SELECT DISTINCT
    investor_name,
    pan_no,
    tax_status,
    dob,
    age,
    is_minor
FROM silver.investor_master
WHERE is_minor = true
  AND (guardian_pan IS NULL
       OR NOT (upper(trim(guardian_pan)) ~ '^[A-Z]{5}[0-9]{4}[A-Z]$'))
ORDER BY age NULLS LAST, investor_name;


-- ============================================================
-- E. THE CORRECTION  (review section A first, then run)
--
-- An entity has no age and no minor status. date_of_birth is
-- left alone -- it is the incorporation date and is real data;
-- only the two DERIVED columns are wrong.
--
-- Wrapped in a transaction so you can inspect the row count
-- before committing.
-- ============================================================

-- BEGIN;
--
-- UPDATE gold.clients
--    SET age      = NULL,
--        is_minor = NULL
--  WHERE is_minor = true
--    AND guardian_pan IS NULL;
--
-- -- expect: UPDATE 17
--
-- UPDATE silver.investor_master
--    SET age      = NULL,
--        is_minor = NULL
--  WHERE is_minor = true
--    AND (guardian_pan IS NULL
--         OR NOT (upper(trim(guardian_pan)) ~ '^[A-Z]{5}[0-9]{4}[A-Z]$'));
--
-- COMMIT;
--
-- NOTE: this is a one-off data correction. The next run of
-- etl_gold_clients.py will re-derive age/is_minor from dob and
-- reintroduce the defect -- the durable fix is to skip the age
-- derivation for non-individual tax statuses in
-- transform_clients() (python_scripts/etl_gold_clients.py:917).

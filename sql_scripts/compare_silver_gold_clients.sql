-- ============================================================
-- silver.investor_master  <->  gold.clients
-- Side-by-side reconciliation.
--
-- Run:
--   psql -h localhost -p 5433 -U postgres \
--        -d 25_08_2025_intelliwealth_layer_db \
--        -f sql_scripts/compare_silver_gold_clients.sql
--
-- silver.investor_master is FOLIO-level (one row per folio,
-- ~3.5k rows) and gold.clients is PERSON-level (~620 rows), so
-- nothing can be compared until silver is first collapsed to
-- one row per person. That is what silver_person does below,
-- using the same identity rules the ETL itself uses:
--
--   pan present -> the PAN is the identity
--   pan absent  -> guardian PAN + FIRST name + date of birth
--                  (no guardian PAN -> the whole name)
--
-- Section 1 is the one to read: one row per person, every
-- column shown silver-side and gold-side, with a `status`
-- column naming the defect. status = 'OK' means clean.
-- ============================================================


-- ============================================================
-- The reusable person-level view of silver.
-- Repeated in each section so every query below can be copied
-- out and run on its own.
-- ============================================================

\echo '=============================================='
\echo '1. FULL RECONCILIATION -- clients WITH a PAN'
\echo '   one row per person, silver vs gold.'
\echo '   PAN-less clients are section 3, not here.'
\echo '=============================================='

WITH silver_person AS (
    -- The SAME resolution the ETL applies: order a person's folios
    -- newest registry statement first, then take each field from the
    -- most recent folio that actually has one. (array_agg ORDER BY
    -- report_date DESC) FILTER (WHERE x IS NOT NULL))[1] is exactly
    -- "most recent non-null". Comparing against min(dob) instead --
    -- as this query first did -- reports a mismatch every time the
    -- newest statement disagrees with the oldest, which is the very
    -- thing the rule is meant to decide.
    SELECT
        upper(trim(pan_no))                                   AS pan,
        max(investor_name)                                    AS s_name,
        (array_agg(dob ORDER BY report_date DESC NULLS LAST)
            FILTER (WHERE dob IS NOT NULL))[1]                AS s_dob,
        array_agg(DISTINCT dob) FILTER (WHERE dob IS NOT NULL) AS s_all_dobs,
        (array_agg(age ORDER BY report_date DESC NULLS LAST)
            FILTER (WHERE age IS NOT NULL))[1]                AS s_age,
        (array_agg(is_minor ORDER BY report_date DESC NULLS LAST)
            FILTER (WHERE is_minor IS NOT NULL))[1]           AS s_is_minor,
        max(CASE WHEN upper(trim(guardian_pan)) ~ '^[A-Z]{5}[0-9]{4}[A-Z]$'
                 THEN upper(trim(guardian_pan)) END)          AS s_guardian_pan,
        count(*)                                              AS s_folios
    FROM silver.investor_master
    WHERE upper(trim(pan_no)) ~ '^[A-Z]{5}[0-9]{4}[A-Z]$'
    GROUP BY 1
)
SELECT
    coalesce(s.pan, g.pan)          AS pan,
    coalesce(s.s_name, g.full_name) AS name,

    s.s_dob        AS silver_dob,
    g.date_of_birth AS gold_dob,
    s.s_age        AS silver_age,
    g.age          AS gold_age,
    s.s_is_minor   AS silver_is_minor,
    g.is_minor     AS gold_is_minor,
    s.s_guardian_pan AS silver_guardian_pan,
    g.guardian_pan   AS gold_guardian_pan,
    s.s_folios     AS silver_folios,

    CASE
        WHEN g.pan IS NULL THEN 'MISSING FROM GOLD'
        WHEN s.pan IS NULL THEN 'ORPHAN IN GOLD (not in silver)'
        -- A non-individual PAN (4th char <> 'P') is SUPPOSED to have
        -- no age and no minor status -- silver still derives one from
        -- the incorporation date, and gold deliberately does not.
        WHEN g.pan IS NOT NULL AND substr(g.pan,4,1) <> 'P'
             AND g.age IS NULL
             THEN 'OK (entity: age/is_minor correctly suppressed)'
        WHEN g.age IS NULL AND s.s_age IS NOT NULL
             THEN 'DEFECT 2: dob/age dropped by per-PAN collapse'
        WHEN g.is_minor IS TRUE AND g.guardian_pan IS NULL
             THEN 'DEFECT 3: entity flagged as minor'
        WHEN g.date_of_birth IS DISTINCT FROM s.s_dob
             AND g.date_of_birth = ANY(s.s_all_dobs)
             THEN 'SOURCE DOB CONFLICT (silver disagrees with itself)'
        WHEN g.date_of_birth IS DISTINCT FROM s.s_dob THEN 'DOB MISMATCH'
        -- gold.age is now RECOMPUTED from date_of_birth on every
        -- pipeline run, while silver.age stays the snapshot taken
        -- when the folio was last ingested. The two are therefore
        -- SUPPOSED to differ for anybody whose birthday has passed
        -- since then -- comparing them would report a mismatch every
        -- time gold is right. gold is checked against the date of
        -- birth instead, which is the actual contract.
        WHEN g.age IS NOT NULL AND g.date_of_birth IS NOT NULL
             AND g.age <> date_part('year', age(current_date, g.date_of_birth))::int
             THEN 'AGE WRONG (disagrees with date_of_birth)'
        WHEN g.age IS DISTINCT FROM s.s_age
             AND g.age IS NOT NULL AND g.date_of_birth IS NOT NULL
             AND g.age = date_part('year', age(current_date, g.date_of_birth))::int
             THEN 'OK (age refreshed; silver snapshot is older)'
        WHEN g.age          IS DISTINCT FROM s.s_age  THEN 'AGE MISMATCH'
        WHEN g.pan IS NOT NULL AND substr(g.pan,4,1) <> 'P'
             AND g.is_minor IS NULL
             THEN 'OK (entity: age/is_minor correctly suppressed)'
        WHEN g.is_minor     IS DISTINCT FROM s.s_is_minor THEN 'IS_MINOR MISMATCH'
        WHEN g.guardian_pan IS DISTINCT FROM s.s_guardian_pan THEN 'GUARDIAN_PAN MISMATCH'
        ELSE 'OK'
    END AS status,

    CASE WHEN array_length(s.s_all_dobs, 1) > 1
         THEN s.s_all_dobs END AS silver_conflicting_dobs

FROM silver_person s
FULL OUTER JOIN (SELECT * FROM gold.clients WHERE pan IS NOT NULL) g
       ON g.pan = s.pan
WHERE
    -- comment this WHERE out to list all 592 clients
    g.pan IS NULL
 OR s.pan IS NULL
 OR g.date_of_birth IS DISTINCT FROM s.s_dob
 OR g.age          IS DISTINCT FROM s.s_age
 OR g.is_minor     IS DISTINCT FROM s.s_is_minor
 OR g.guardian_pan IS DISTINCT FROM s.s_guardian_pan
ORDER BY status, name;


\echo ''
\echo '=============================================='
\echo '2. SUMMARY -- how many clients per status'
\echo '=============================================='

WITH silver_person AS (
    SELECT
        upper(trim(pan_no)) AS pan,
        (array_agg(dob ORDER BY report_date DESC NULLS LAST)
            FILTER (WHERE dob IS NOT NULL))[1] AS s_dob,
        array_agg(DISTINCT dob) FILTER (WHERE dob IS NOT NULL) AS s_all_dobs,
        (array_agg(age ORDER BY report_date DESC NULLS LAST)
            FILTER (WHERE age IS NOT NULL))[1] AS s_age,
        (array_agg(is_minor ORDER BY report_date DESC NULLS LAST)
            FILTER (WHERE is_minor IS NOT NULL))[1] AS s_is_minor,
        max(CASE WHEN upper(trim(guardian_pan)) ~ '^[A-Z]{5}[0-9]{4}[A-Z]$'
                 THEN upper(trim(guardian_pan)) END) AS s_guardian_pan
    FROM silver.investor_master
    WHERE upper(trim(pan_no)) ~ '^[A-Z]{5}[0-9]{4}[A-Z]$'
    GROUP BY 1
)
SELECT
    CASE
        WHEN g.pan IS NULL THEN 'MISSING FROM GOLD'
        WHEN s.pan IS NULL THEN 'ORPHAN IN GOLD (not in silver)'
        -- A non-individual PAN (4th char <> 'P') is SUPPOSED to have
        -- no age and no minor status -- silver still derives one from
        -- the incorporation date, and gold deliberately does not.
        WHEN g.pan IS NOT NULL AND substr(g.pan,4,1) <> 'P'
             AND g.age IS NULL
             THEN 'OK (entity: age/is_minor correctly suppressed)'
        WHEN g.age IS NULL AND s.s_age IS NOT NULL
             THEN 'DEFECT 2: dob/age dropped by per-PAN collapse'
        WHEN g.is_minor IS TRUE AND g.guardian_pan IS NULL
             THEN 'DEFECT 3: entity flagged as minor'
        WHEN g.date_of_birth IS DISTINCT FROM s.s_dob
             AND g.date_of_birth = ANY(s.s_all_dobs)
             THEN 'SOURCE DOB CONFLICT (silver disagrees with itself)'
        WHEN g.date_of_birth IS DISTINCT FROM s.s_dob THEN 'DOB MISMATCH'
        -- gold.age is now RECOMPUTED from date_of_birth on every
        -- pipeline run, while silver.age stays the snapshot taken
        -- when the folio was last ingested. The two are therefore
        -- SUPPOSED to differ for anybody whose birthday has passed
        -- since then -- comparing them would report a mismatch every
        -- time gold is right. gold is checked against the date of
        -- birth instead, which is the actual contract.
        WHEN g.age IS NOT NULL AND g.date_of_birth IS NOT NULL
             AND g.age <> date_part('year', age(current_date, g.date_of_birth))::int
             THEN 'AGE WRONG (disagrees with date_of_birth)'
        WHEN g.age IS DISTINCT FROM s.s_age
             AND g.age IS NOT NULL AND g.date_of_birth IS NOT NULL
             AND g.age = date_part('year', age(current_date, g.date_of_birth))::int
             THEN 'OK (age refreshed; silver snapshot is older)'
        WHEN g.age          IS DISTINCT FROM s.s_age  THEN 'AGE MISMATCH'
        WHEN g.pan IS NOT NULL AND substr(g.pan,4,1) <> 'P'
             AND g.is_minor IS NULL
             THEN 'OK (entity: age/is_minor correctly suppressed)'
        WHEN g.is_minor     IS DISTINCT FROM s.s_is_minor THEN 'IS_MINOR MISMATCH'
        WHEN g.guardian_pan IS DISTINCT FROM s.s_guardian_pan THEN 'GUARDIAN_PAN MISMATCH'
        ELSE 'OK'
    END AS status,
    count(*) AS clients
FROM silver_person s
FULL OUTER JOIN (SELECT * FROM gold.clients WHERE pan IS NOT NULL) g
       ON g.pan = s.pan
GROUP BY 1
ORDER BY clients DESC;


\echo ''
\echo '=============================================='
\echo '3. RECONCILIATION -- clients WITHOUT a PAN'
\echo '   (the minors). Keyed guardian PAN + FIRST'
\echo '   name + dob, exactly as the ETL keys them.'
\echo '=============================================='

WITH silver_panless AS (
    SELECT
        -- One comparable text key per person. A FULL JOIN cannot be
        -- planned on IS NOT DISTINCT FROM or on a CASE expression
        -- ("FULL JOIN is only supported with merge-joinable or
        -- hash-joinable join conditions"), so the whole composite
        -- identity is folded into one string that joins with plain =.
        -- '~' stands in for NULL because it cannot occur in a PAN,
        -- a normalised name, or a date.
        coalesce(
            CASE WHEN upper(trim(guardian_pan)) ~ '^[A-Z]{5}[0-9]{4}[A-Z]$'
                 THEN upper(trim(guardian_pan)) END, '~')
        || '|' ||
        CASE WHEN upper(trim(guardian_pan)) ~ '^[A-Z]{5}[0-9]{4}[A-Z]$'
             THEN split_part(upper(regexp_replace(trim(investor_name),'\s+',' ','g')),' ',1)
             ELSE upper(regexp_replace(trim(investor_name),'\s+',' ','g'))
        END
        || '|' || coalesce(dob::text, '~')                    AS person_key,

        max(CASE WHEN upper(trim(guardian_pan)) ~ '^[A-Z]{5}[0-9]{4}[A-Z]$'
                 THEN upper(trim(guardian_pan)) END)          AS s_guardian_pan,
        max(investor_name)                                    AS s_name,
        min(dob)                                              AS s_dob,
        max(age)                                              AS s_age,
        bool_or(is_minor)                                     AS s_is_minor,
        count(*)                                              AS s_folios
    FROM silver.investor_master
    WHERE pan_no IS NULL
       OR NOT (upper(trim(pan_no)) ~ '^[A-Z]{5}[0-9]{4}[A-Z]$')
    GROUP BY 1
),
gold_panless AS (
    SELECT
        coalesce(upper(trim(guardian_pan)), '~')
        || '|' ||
        CASE WHEN guardian_pan IS NULL
             THEN upper(regexp_replace(trim(full_name),'\s+',' ','g'))
             ELSE split_part(upper(regexp_replace(trim(full_name),'\s+',' ','g')),' ',1)
        END
        || '|' || coalesce(date_of_birth::text, '~')          AS person_key,

        full_name, date_of_birth, age, is_minor, guardian_pan,
        is_documentupdaterequired
    FROM gold.clients
    WHERE pan IS NULL
)
SELECT
    coalesce(s.s_name, g.full_name)  AS name,
    s.s_guardian_pan                 AS silver_guardian_pan,
    g.guardian_pan                   AS gold_guardian_pan,
    s.s_dob                          AS silver_dob,
    g.date_of_birth                  AS gold_dob,
    s.s_age                          AS silver_age,
    g.age                            AS gold_age,
    s.s_is_minor                     AS silver_is_minor,
    g.is_minor                       AS gold_is_minor,
    g.is_documentupdaterequired      AS gold_doc_update_required,
    s.s_folios                       AS silver_folios,
    CASE
        WHEN g.person_key IS NULL THEN 'MISSING FROM GOLD'
        WHEN s.person_key IS NULL THEN 'ORPHAN IN GOLD (not in silver)'
        WHEN g.age      IS DISTINCT FROM s.s_age      THEN 'AGE MISMATCH'
        -- NOT a defect. silver.is_minor is the raw age test, so an
        -- adult reads false. gold deliberately keeps is_minor = true
        -- for an adult still operating on a guardian's PAN, because
        -- every registry record still stands in the guardian's name
        -- until the documents are updated. The two columns are
        -- SUPPOSED to disagree here, and is_documentupdaterequired
        -- is the flag that says why.
        WHEN g.is_minor IS DISTINCT FROM s.s_is_minor
             AND g.is_documentupdaterequired IS TRUE
             AND g.age >= 18
             THEN 'BY DESIGN: adult on guardian PAN, still treated as minor'
        WHEN g.is_minor IS DISTINCT FROM s.s_is_minor THEN 'IS_MINOR MISMATCH'
        ELSE 'OK'
    END AS status
FROM silver_panless s
FULL OUTER JOIN gold_panless g ON g.person_key = s.person_key
ORDER BY status, name;


\echo ''
\echo '=============================================='
\echo '4. LAYER TOTALS -- the two tables side by side'
\echo '=============================================='

SELECT 'silver.investor_master (folio level)' AS layer,
       count(*)                                 AS rows,
       count(DISTINCT upper(trim(pan_no)))
         FILTER (WHERE upper(trim(pan_no)) ~ '^[A-Z]{5}[0-9]{4}[A-Z]$') AS distinct_pans,
       count(*) FILTER (WHERE is_minor)         AS flagged_minor,
       count(*) FILTER (WHERE upper(trim(guardian_pan)) ~ '^[A-Z]{5}[0-9]{4}[A-Z]$') AS with_guardian_pan,
       count(age)                               AS age_set,
       count(dob)                               AS dob_set
FROM silver.investor_master

UNION ALL

SELECT 'gold.clients (person level)',
       count(*),
       count(pan),
       count(*) FILTER (WHERE is_minor),
       count(guardian_pan),
       count(age),
       count(date_of_birth)
FROM gold.clients;

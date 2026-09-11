-- ============================================================
-- gold.clients <-> silver.investor_master verification suite
--
-- Run:
--   psql -h localhost -p 5433 -U postgres \
--        -d 25_08_2025_intelliwealth_layer_db \
--        -f sql_scripts/verify_gold_clients_sync.sql
--
-- Every check is written so that the "bad" column must be 0.
--
-- A client is identified two different ways, matching what
-- etl_gold_clients.load_clients() actually does:
--   pan present -> the PAN is the identity
--   pan absent  -> guardian PAN + FIRST name + date of birth
--                  (no guardian PAN -> the whole name)
-- ============================================================


\echo '=============================================='
\echo '1. HEADLINE COUNTS'
\echo '=============================================='

SELECT
    (SELECT count(*) FROM silver.investor_master)              AS silver_folio_rows,
    (SELECT count(*) FROM gold.clients)                        AS gold_clients,
    (SELECT count(*) FROM gold.clients WHERE pan IS NOT NULL)  AS gold_with_pan,
    (SELECT count(*) FROM gold.clients WHERE pan IS NULL)      AS gold_panless;


\echo ''
\echo '=============================================='
\echo '2. THE CONSTRAINT THAT CAUSED THE DATA LOSS'
\echo '   indnullsnotdistinct MUST be f (false).'
\echo '   If it is t, gold.clients can hold only ONE'
\echo '   PAN-less client in the entire table and every'
\echo '   minor after the first is silently rejected.'
\echo '=============================================='

SELECT
    c.relname                AS index_name,
    i.indnullsnotdistinct    AS nulls_not_distinct_MUST_BE_FALSE,
    pg_get_indexdef(i.indexrelid) AS definition
FROM pg_index i
JOIN pg_class     c ON c.oid = i.indexrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'gold'
  AND c.relname IN ('uq_gold_clients_pan', 'uq_clients_pan_absent');


\echo ''
\echo '=============================================='
\echo '3. COVERAGE A -- every valid PAN in silver'
\echo '   must exist in gold.  missing MUST be 0.'
\echo '=============================================='

WITH s AS (
    SELECT DISTINCT upper(trim(pan_no)) AS pan
    FROM silver.investor_master
    WHERE upper(trim(pan_no)) ~ '^[A-Z]{5}[0-9]{4}[A-Z]$'
)
SELECT
    count(*)                                   AS silver_distinct_pans,
    count(*) FILTER (WHERE g.pan IS NOT NULL)  AS present_in_gold,
    count(*) FILTER (WHERE g.pan IS NULL)      AS missing
FROM s
LEFT JOIN gold.clients g ON g.pan = s.pan;


\echo ''
\echo '--- 3b. list the missing PANs (expect no rows) ---'

WITH s AS (
    SELECT DISTINCT upper(trim(pan_no)) AS pan
    FROM silver.investor_master
    WHERE upper(trim(pan_no)) ~ '^[A-Z]{5}[0-9]{4}[A-Z]$'
)
SELECT
    s.pan,
    (SELECT string_agg(DISTINCT investor_name, ' | ')
       FROM silver.investor_master im
      WHERE upper(trim(im.pan_no)) = s.pan) AS silver_names
FROM s
LEFT JOIN gold.clients g ON g.pan = s.pan
WHERE g.pan IS NULL;


\echo ''
\echo '=============================================='
\echo '4. COVERAGE B -- every PAN-less person in'
\echo '   silver must exist in gold. missing MUST be 0.'
\echo '   Keyed the way the ETL keys them.'
\echo '=============================================='

WITH silver_key AS (
    SELECT DISTINCT
        CASE WHEN upper(trim(guardian_pan)) ~ '^[A-Z]{5}[0-9]{4}[A-Z]$'
             THEN upper(trim(guardian_pan)) END AS gpan,
        CASE WHEN upper(trim(guardian_pan)) ~ '^[A-Z]{5}[0-9]{4}[A-Z]$'
             THEN split_part(upper(regexp_replace(trim(investor_name), '\s+', ' ', 'g')), ' ', 1)
             ELSE upper(regexp_replace(trim(investor_name), '\s+', ' ', 'g'))
        END AS name_key,
        dob
    FROM silver.investor_master
    WHERE pan_no IS NULL
       OR NOT (upper(trim(pan_no)) ~ '^[A-Z]{5}[0-9]{4}[A-Z]$')
),
gold_key AS (
    SELECT
        upper(trim(guardian_pan)) AS gpan,
        split_part(upper(regexp_replace(trim(full_name), '\s+', ' ', 'g')), ' ', 1) AS first_nm,
        upper(regexp_replace(trim(full_name), '\s+', ' ', 'g')) AS full_nm,
        date_of_birth AS dob
    FROM gold.clients
    WHERE pan IS NULL
)
SELECT
    (SELECT count(*) FROM silver_key)                            AS silver_panless_persons,
    (SELECT count(*) FROM gold.clients WHERE pan IS NULL)        AS gold_panless_rows,
    count(*)                                                     AS missing
FROM silver_key sk
WHERE NOT EXISTS (
    SELECT 1 FROM gold_key gk
    WHERE gk.gpan IS NOT DISTINCT FROM sk.gpan
      AND gk.dob  IS NOT DISTINCT FROM sk.dob
      AND (CASE WHEN sk.gpan IS NULL THEN gk.full_nm ELSE gk.first_nm END) = sk.name_key
);


\echo ''
\echo '=============================================='
\echo '5. FOLIO COVERAGE -- every silver folio row'
\echo '   must resolve to a gold client.'
\echo '   matched_by_pan + matched_panless MUST equal'
\echo '   total_folio_rows.'
\echo '=============================================='

WITH f AS (
    SELECT
        dob,
        CASE WHEN upper(trim(pan_no)) ~ '^[A-Z]{5}[0-9]{4}[A-Z]$'
             THEN upper(trim(pan_no)) END AS pan,
        CASE WHEN upper(trim(guardian_pan)) ~ '^[A-Z]{5}[0-9]{4}[A-Z]$'
             THEN upper(trim(guardian_pan)) END AS gpan,
        split_part(upper(regexp_replace(trim(investor_name), '\s+', ' ', 'g')), ' ', 1) AS first_nm,
        upper(regexp_replace(trim(investor_name), '\s+', ' ', 'g')) AS full_nm
    FROM silver.investor_master
)
SELECT
    count(*) AS total_folio_rows,

    count(*) FILTER (
        WHERE pan IS NOT NULL
          AND EXISTS (SELECT 1 FROM gold.clients g WHERE g.pan = f.pan)
    ) AS matched_by_pan,

    count(*) FILTER (
        WHERE pan IS NULL
          AND EXISTS (
              SELECT 1 FROM gold.clients g
              WHERE g.pan IS NULL
                AND upper(trim(g.guardian_pan)) IS NOT DISTINCT FROM f.gpan
                AND g.date_of_birth            IS NOT DISTINCT FROM f.dob
                AND (CASE WHEN f.gpan IS NULL
                          THEN upper(regexp_replace(trim(g.full_name), '\s+', ' ', 'g'))
                          ELSE split_part(upper(regexp_replace(trim(g.full_name), '\s+', ' ', 'g')), ' ', 1)
                     END) = (CASE WHEN f.gpan IS NULL THEN f.full_nm ELSE f.first_nm END)
          )
    ) AS matched_panless,

    count(*) FILTER (
        WHERE pan IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM gold.clients g WHERE g.pan = f.pan)
    ) AS unmatched_MUST_BE_0
FROM f;


\echo ''
\echo '=============================================='
\echo '6. ORPHANS -- gold clients whose PAN is not in'
\echo '   silver at all. MUST be 0.'
\echo '=============================================='

SELECT count(*) AS orphan_clients_MUST_BE_0
FROM gold.clients g
WHERE g.pan IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM silver.investor_master s
      WHERE upper(trim(s.pan_no)) = g.pan
  );


\echo ''
\echo '=============================================='
\echo '7. DERIVED COLUMN INTEGRITY -- every column'
\echo '   below MUST be 0.'
\echo '=============================================='

SELECT
    count(*) AS total_rows,

    -- age must equal the age implied by date_of_birth
    count(*) FILTER (
        WHERE age IS NOT NULL AND date_of_birth IS NOT NULL
          AND age <> date_part('year', age(current_date, date_of_birth))::int
    ) AS age_vs_dob_mismatch,

    -- Rule 1: a minor is SUPPOSED to be on the guardian's PAN
    count(*) FILTER (
        WHERE age < 18 AND is_documentupdaterequired IS DISTINCT FROM false
    ) AS rule1_violation,

    -- Rule 2: adult still on a guardian's PAN -> update IS required
    count(*) FILTER (
        WHERE age >= 18 AND pan IS NULL AND guardian_pan IS NOT NULL
          AND is_documentupdaterequired IS DISTINCT FROM true
    ) AS rule2_violation,

    -- Rule 3: adult on their own PAN -> already updated
    count(*) FILTER (
        WHERE age >= 18 AND pan IS NOT NULL
          AND (guardian_pan IS NULL OR guardian_pan <> pan)
          AND is_documentupdaterequired IS DISTINCT FROM false
    ) AS rule3_violation,

    -- anyone under 18 must be flagged a minor
    count(*) FILTER (
        WHERE age < 18 AND is_minor IS DISTINCT FROM true
    ) AS minor_not_flagged,

    -- a guardian's PAN must never be stored as the client's own
    count(*) FILTER (
        WHERE guardian_pan IS NOT NULL AND guardian_pan = pan
    ) AS guardian_pan_leaked_into_pan,

    -- guardian_pan must be a real PAN, not the KFIN '0' placeholder
    count(*) FILTER (
        WHERE guardian_pan IS NOT NULL
          AND guardian_pan !~ '^[A-Z]{5}[0-9]{4}[A-Z]$'
    ) AS malformed_guardian_pan
FROM gold.clients;


\echo ''
\echo '=============================================='
\echo '8. THE THREE CLIENT TYPES'
\echo '=============================================='

SELECT
    CASE
        WHEN pan IS NOT NULL AND guardian_pan IS NULL THEN '1. own PAN (ordinary client)'
        WHEN pan IS NULL AND guardian_pan IS NOT NULL AND age < 18
             THEN '2. minor on guardian PAN'
        WHEN pan IS NULL AND guardian_pan IS NOT NULL AND age >= 18
             THEN '3. adult STILL on guardian PAN (doc update due)'
        WHEN pan IS NULL AND guardian_pan IS NULL THEN '4. no PAN, no guardian'
        ELSE '5. other'
    END AS client_type,
    count(*)                                            AS clients,
    count(*) FILTER (WHERE is_minor)                    AS flagged_minor,
    count(*) FILTER (WHERE is_documentupdaterequired)   AS doc_update_required
FROM gold.clients
GROUP BY 1
ORDER BY 1;


\echo ''
\echo '--- 8b. the adults still operating on a guardian PAN ---'

SELECT full_name, date_of_birth, age, guardian_pan, is_minor, is_documentupdaterequired
FROM gold.clients
WHERE is_documentupdaterequired = true
ORDER BY age;


\echo ''
\echo '=============================================='
\echo '9. FILL RATES'
\echo '=============================================='

SELECT
    count(*)                                          AS total,
    count(age)                                        AS age_set,
    count(date_of_birth)                              AS dob_set,
    count(is_minor)                                   AS is_minor_set,
    count(*) FILTER (WHERE is_minor)                  AS minors,
    count(guardian_pan)                               AS guardian_pan_set,
    count(is_documentupdaterequired)                  AS doc_flag_set,
    count(*) FILTER (WHERE is_documentupdaterequired) AS doc_update_required
FROM gold.clients;


\echo ''
\echo '=============================================='
\echo '10. OPEN DEFECT 2 -- gold lost a DOB that'
\echo '    silver still has, because the per-PAN'
\echo '    collapse keeps an arbitrary folio and does'
\echo '    not coalesce NULLs.'
\echo '    (etl_gold_clients.py:1988)'
\echo '=============================================='

WITH sv AS (
    SELECT upper(trim(pan_no)) AS pan,
           min(dob) AS silver_dob,
           max(age) AS silver_age
    FROM silver.investor_master
    WHERE upper(trim(pan_no)) ~ '^[A-Z]{5}[0-9]{4}[A-Z]$'
      AND dob IS NOT NULL
    GROUP BY 1
)
SELECT g.pan, g.full_name, g.age AS gold_age, g.date_of_birth AS gold_dob,
       sv.silver_dob, sv.silver_age
FROM gold.clients g
JOIN sv ON g.pan = sv.pan
WHERE g.age IS NULL
ORDER BY g.full_name;


\echo ''
\echo '=============================================='
\echo '11. OPEN DEFECT 3 -- companies / trusts / HUFs'
\echo '    flagged is_minor because an INCORPORATION'
\echo '    date is being read as a date of birth.'
\echo '=============================================='

SELECT full_name, tax_status, date_of_birth, age, is_minor
FROM gold.clients
WHERE is_minor = true
  AND full_name ~* '(PVT|PRIVATE|LTD|LIMITED|LLP|TRUST|HUF|FUND|SOLUTIONS|ENGINEERS|ASSOCIATES|CORP|COMPANY|SOCIETY|ENTERPRISES|SERVICES|OUTSOURCING)'
ORDER BY age;


\echo ''
\echo '--- 11b. same thing keyed on tax_status instead of name ---'

SELECT tax_status,
       count(*)                         AS clients,
       count(*) FILTER (WHERE is_minor) AS flagged_minor
FROM gold.clients
WHERE is_minor = true
GROUP BY 1
ORDER BY 2 DESC;


\echo ''
\echo '=============================================='
\echo '12. INDIVIDUAL_CATEGORY_TYPE'
\echo '    Every "bad" column MUST be 0.'
\echo '=============================================='

SELECT
    -- Populated everywhere, in both layers.
    (SELECT count(*) FROM gold.clients
      WHERE individual_category_type IS NULL)              AS gold_null,

    (SELECT count(*) FROM silver.investor_master
      WHERE individual_category_type IS NULL)              AS silver_null,

    -- Only the ten constants, never free text.
    (SELECT count(*) FROM gold.clients
      WHERE individual_category_type NOT IN (
          'INDIVIDUAL','COMPANY','HUF','FIRM','TRUST','AOP',
          'BOI','GOVERNMENT','LOCAL_AUTHORITY',
          'ARTIFICIAL_JURIDICAL_PERSON'))                  AS gold_bad_value,

    -- The PAN is authoritative: wherever a client has one, the
    -- category MUST be the one its 4th character dictates. This
    -- is what stops tax_status quietly overriding it -- in this
    -- data "Individual" appears against PAN 4th characters
    -- {P,T} and "Sole Proprietorship" against {H,P}.
    (SELECT count(*) FROM gold.clients
      WHERE pan IS NOT NULL
        AND individual_category_type IS DISTINCT FROM
            CASE substr(pan, 4, 1)
                WHEN 'A' THEN 'AOP'
                WHEN 'B' THEN 'BOI'
                WHEN 'C' THEN 'COMPANY'
                WHEN 'F' THEN 'FIRM'
                WHEN 'G' THEN 'GOVERNMENT'
                WHEN 'H' THEN 'HUF'
                WHEN 'J' THEN 'ARTIFICIAL_JURIDICAL_PERSON'
                WHEN 'L' THEN 'LOCAL_AUTHORITY'
                WHEN 'P' THEN 'INDIVIDUAL'
                WHEN 'T' THEN 'TRUST'
            END)                                           AS pan_disagrees,

    -- Only a natural person has an age or a minor status. This
    -- is the same contract check as section 7, expressed
    -- through the category rather than through the PAN, so the
    -- two can never drift apart silently.
    (SELECT count(*) FROM gold.clients
      WHERE individual_category_type <> 'INDIVIDUAL'
        AND (age IS NOT NULL
             OR is_minor IS NOT NULL
             OR is_documentupdaterequired IS NOT NULL))    AS entity_with_person_fields,

    -- A minor is always a natural person.
    (SELECT count(*) FROM gold.clients
      WHERE is_minor
        AND individual_category_type <> 'INDIVIDUAL')      AS minor_that_is_not_individual;


\echo ''
\echo '--- 12b. distribution ---'

SELECT
    individual_category_type          AS category,
    count(*)                          AS clients,
    count(*) FILTER (WHERE pan IS NULL) AS resolved_via_tax_status,
    count(age)                        AS with_age,
    count(*) FILTER (WHERE is_minor)  AS minors
FROM gold.clients
GROUP BY 1
ORDER BY 2 DESC;

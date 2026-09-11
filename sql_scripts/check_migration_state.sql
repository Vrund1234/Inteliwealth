-- ============================================================
-- Which of the client-identity migrations have been applied?
--
-- Run against any warehouse. Everything in the first block must
-- say `t`; the second block shows whether the backfills ran.
-- ============================================================

\echo '--- objects: every row must be t ---'
SELECT '0  gold.clients.age'                  AS step, EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='gold' AND table_name='clients' AND column_name='age') AS present
UNION ALL SELECT '0  gold.clients.guardian_pan', EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='gold' AND table_name='clients' AND column_name='guardian_pan')
UNION ALL SELECT '0  gold.clients.is_documentupdaterequired', EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='gold' AND table_name='clients' AND column_name='is_documentupdaterequired')
UNION ALL SELECT '0  silver.investor_master.age', EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='silver' AND table_name='investor_master' AND column_name='age')
UNION ALL SELECT '2  gold.clients.individual_category_type', EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='gold' AND table_name='clients' AND column_name='individual_category_type')
UNION ALL SELECT '2  silver.individual_category_type', EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='silver' AND table_name='investor_master' AND column_name='individual_category_type')
UNION ALL SELECT '3  gold.clients.aadhaar_seeding_status', EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='gold' AND table_name='clients' AND column_name='aadhaar_seeding_status')
UNION ALL SELECT '3  silver.aadhaar_seeding_status', EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='silver' AND table_name='investor_master' AND column_name='aadhaar_seeding_status')
UNION ALL SELECT '4  gold.client_folio',          to_regclass('gold.client_folio') IS NOT NULL
UNION ALL SELECT '4  gold.name_token_key()',      to_regprocedure('gold.name_token_key(text)') IS NOT NULL
UNION ALL SELECT '4  gold.normalise_folio()',     to_regprocedure('gold.normalise_folio(text)') IS NOT NULL
UNION ALL SELECT '5  gold.norm_name()',           to_regprocedure('gold.norm_name(text)') IS NOT NULL
UNION ALL SELECT '5  gold.panless_name_key()',    to_regprocedure('gold.panless_name_key(text,text)') IS NOT NULL
UNION ALL SELECT '5  uq_clients_panless_identity',EXISTS(SELECT 1 FROM pg_indexes WHERE schemaname='gold' AND indexname='uq_clients_panless_identity')
UNION ALL SELECT '6  gold.clients.superseded_by', EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='gold' AND table_name='clients' AND column_name='superseded_by')
UNION ALL SELECT '6  gold.client_merge_log',      to_regclass('gold.client_merge_log') IS NOT NULL
UNION ALL SELECT '6  gold.merge_client()',        to_regprocedure('gold.merge_client(uuid,uuid,text,text)') IS NOT NULL
ORDER BY 1;

\echo ''
\echo '--- the critical one: PAN uniqueness must NOT be NULLS NOT DISTINCT ---'
SELECT conname, pg_get_constraintdef(oid) AS definition,
       CASE WHEN pg_get_constraintdef(oid) ILIKE '%NULLS NOT DISTINCT%'
            THEN 'STILL BROKEN — only one PAN-less client allowed'
            ELSE 'ok' END AS verdict
FROM pg_constraint
WHERE conrelid = 'gold.clients'::regclass AND contype = 'u';

\echo ''
\echo '--- backfills: did the data actually get written? ---'
SELECT 'silver rows'                       AS metric, count(*)::text AS value FROM silver.investor_master
UNION ALL SELECT 'silver with a DOB',        count(dob)::text FROM silver.investor_master
UNION ALL SELECT 'silver age  (step 1)',     count(age)::text FROM silver.investor_master
UNION ALL SELECT 'silver is_minor (step 1)', count(is_minor)::text FROM silver.investor_master
UNION ALL SELECT 'silver category (step 2)', count(individual_category_type)::text FROM silver.investor_master
UNION ALL SELECT 'silver aadhaar (step 3)',  count(aadhaar_seeding_status)::text FROM silver.investor_master
UNION ALL SELECT 'gold clients',             count(*)::text FROM gold.clients
UNION ALL SELECT 'gold PAN-less clients',    count(*) FILTER (WHERE pan IS NULL)::text FROM gold.clients
UNION ALL SELECT 'gold category (step 2)',   count(individual_category_type)::text FROM gold.clients
UNION ALL SELECT 'gold aadhaar mis-filed',   count(*) FILTER (WHERE aadhaar IS NOT NULL AND aadhaar !~ '^[0-9]{12}$')::text FROM gold.clients;

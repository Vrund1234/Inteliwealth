-- Transfer ownership of the objects the Streamlit upload path issues DDL
-- against, from `postgres` to the application role.
--
-- WHY THIS EXISTS
-- ---------------
-- The app connects as a restricted role, but every object below is owned by
-- `postgres` (typically because the schema was loaded via pg_restore, which
-- stamps the dumping role as owner). Three statements in the upload path need
-- OWNERSHIP, not merely INSERT/UPDATE:
--
--   1. CREATE OR REPLACE FUNCTION bronze.update_updated_at()
--        -> utils/triggers.py:11
--   2. DROP TRIGGER / CREATE TRIGGER on the six master tables
--        -> utils/triggers.py:33,38  (ownership is of the TABLE, not the trigger)
--   3. TRUNCATE TABLE bronze.scheme_mapping_audit
--        -> scheme_matching/reference.py:59
--
-- Item 1 is what failed live on 2026-09-01 with
--   psycopg2.errors.InsufficientPrivilege: must be owner of function update_updated_at
-- Items 2 and 3 sit immediately behind it on the same code path and would have
-- surfaced as the next two errors, so all three are fixed together here.
--
-- ALTER ... OWNER TO preserves existing GRANTs, so other roles reading these
-- tables (the backend app) keep the access they already have.
--
-- RUN AS: postgres (the current owner), e.g.
--   sudo -u postgres psql -d <dbname> -f fix_object_ownership_2026-09-01.sql
--
-- SCOPE: this affects ONLY the database psql connects to via -d. PostgreSQL
-- DDL cannot cross a database boundary in a session, so no other database in
-- the cluster is touched -- including the separate master database. No roles
-- are modified (pg_roles is only read). The script aborts if it finds itself
-- connected to a database other than expected_db.
--
-- BEFORE RUNNING: supply both values. Either pass them on the command line
-- (preferred -- no edit to this file):
--
--   sudo -u postgres psql -d <dbname> \
--        -v app_user=<role> -v expected_db=<dbname> \
--        -f fix_object_ownership_2026-09-01.sql
--
-- or edit the two \set defaults below.
--
--   app_user    -> DB_USER from the live .env. Confirm it with
--                  `SELECT current_user;` in a session opened using the app's
--                  own credentials.
--   expected_db -> the target database name, matching the -d argument.

\set ON_ERROR_STOP on

-- Command-line -v wins; these only fill in when it was not supplied.
\if :{?app_user}
\else
\set app_user REPLACE_WITH_APP_USER
\endif

\if :{?expected_db}
\else
\set expected_db REPLACE_WITH_DB_NAME
\endif

BEGIN;

-- psql does not interpolate variables inside a dollar-quoted body, so the two
-- values are handed to the DO block through transaction-local GUCs instead.
SELECT set_config('fix_ownership.app_user',    :'app_user',    true),
       set_config('fix_ownership.expected_db', :'expected_db', true);

DO $$
DECLARE
    app_user CONSTANT text := current_setting('fix_ownership.app_user');

    -- Scope guard. Nothing here can reach outside the database psql connected
    -- to -- ALTER FUNCTION/TABLE resolve bronze.* and silver.* within the
    -- current database only, and DDL cannot cross a database boundary in one
    -- session. This check exists because the blast radius is therefore set
    -- entirely by `-d` on the command line, one typo away from transferring
    -- ownership in the wrong database of the same cluster.
    expected_db CONSTANT text := current_setting('fix_ownership.expected_db');

    -- Tables whose triggers utils/triggers.py drops and recreates, plus the
    -- audit table reference.py truncates. Kept as an explicit list rather than
    -- "every table in bronze/silver": this grants the app role DROP/ALTER over
    -- each one, so the set stays deliberately as small as the code requires.
    targets CONSTANT text[][] := ARRAY[
        ['bronze', 'transaction_master_new'],
        ['bronze', 'investor_master'],
        ['bronze', 'sip_master_new'],
        ['bronze', 'scheme_mapping_audit'],
        ['silver', 'transaction_master_new'],
        ['silver', 'investor_master'],
        ['silver', 'sip_master_new']
    ];

    i int;
BEGIN
    IF app_user = 'REPLACE_WITH_APP_USER' THEN
        RAISE EXCEPTION
            'Pass -v app_user=<role> (or edit the \set default) before running.';
    END IF;

    IF expected_db = 'REPLACE_WITH_DB_NAME' THEN
        RAISE EXCEPTION
            'Pass -v expected_db=<dbname> (or edit the \set default).';
    END IF;

    IF current_database() <> expected_db THEN
        RAISE EXCEPTION
            'Refusing to run: connected to database %, expected %. '
            'Check the -d argument.',
            current_database(), expected_db;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = app_user) THEN
        RAISE EXCEPTION 'Role % does not exist.', app_user;
    END IF;

    EXECUTE format(
        'ALTER FUNCTION bronze.update_updated_at() OWNER TO %I', app_user
    );
    RAISE NOTICE 'function bronze.update_updated_at() -> %', app_user;

    FOR i IN 1 .. array_length(targets, 1) LOOP
        -- to_regclass rather than a bare ALTER: a table absent on this
        -- particular database should be skipped with a notice, not abort the
        -- whole transaction and leave the earlier transfers unapplied.
        IF to_regclass(format('%I.%I', targets[i][1], targets[i][2])) IS NULL THEN
            RAISE NOTICE 'SKIP %.% (does not exist)', targets[i][1], targets[i][2];
            CONTINUE;
        END IF;

        EXECUTE format(
            'ALTER TABLE %I.%I OWNER TO %I',
            targets[i][1], targets[i][2], app_user
        );
        RAISE NOTICE 'table %.% -> %', targets[i][1], targets[i][2], app_user;
    END LOOP;
END $$;

COMMIT;


-- ============================================================
-- VERIFY
-- ============================================================
-- Every row below must show the app role, not `postgres`.

SELECT n.nspname AS schema,
       p.proname  AS object,
       pg_get_userbyid(p.proowner) AS owner
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = 'bronze'
  AND p.proname = 'update_updated_at'

UNION ALL

SELECT schemaname, tablename, tableowner
FROM pg_tables
WHERE (schemaname, tablename) IN (
    ('bronze', 'transaction_master_new'),
    ('bronze', 'investor_master'),
    ('bronze', 'sip_master_new'),
    ('bronze', 'scheme_mapping_audit'),
    ('silver', 'transaction_master_new'),
    ('silver', 'investor_master'),
    ('silver', 'sip_master_new')
)
ORDER BY 1, 2;

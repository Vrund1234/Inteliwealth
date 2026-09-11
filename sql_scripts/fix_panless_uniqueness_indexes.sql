-- ============================================================
-- PAN-less client uniqueness: make the constraint actually bite
--
-- WHAT IS WRONG
--
--   uq_clients_pan_absent ON gold.clients
--       (guardian_pan, upper(btrim(full_name)), date_of_birth)
--       WHERE pan IS NULL
--
--   Three faults, and they compound:
--
--   1. IT ENFORCES NOTHING FOR 6 CLIENTS.
--      A unique index is NULLS DISTINCT by default, so any row
--      with a NULL in the key can never collide with another.
--      Six clients have no PAN and no guardian PAN; their
--      guardian_pan is NULL, so the index silently ignores
--      them. They are the population with the WEAKEST identity
--      -- no PAN, no guardian -- and the only one the database
--      does not guard at all.
--
--   2. THE SAME HOLE APPLIES TO A NULL date_of_birth.
--
--   3. IT DOES NOT MATCH THE LOADER.
--      load_clients() keys a PAN-less client on
--      guardian PAN + FIRST name + date of birth, having
--      normalised the name (titles stripped, KFIN's
--      "REP BY <guardian>" suffix removed, punctuation gone).
--      This index compares the WHOLE name verbatim. An index
--      looser than the matcher catches nothing it was meant to.
--
--   Python's tuple comparison in that loader treats None as
--   equal to None -- which is precisely what NULLS DISTINCT
--   does not do. coalesce() below closes that gap.
--
-- WHY MIRROR THE LOADER RATHER THAN IMPROVE ON IT
--
--   An index STRICTER than the loader does not prevent
--   duplicates; it makes the pipeline fail on rows the loader
--   considers legitimate. The index is a safety net for writes
--   arriving by other paths -- the app, a manual statement, a
--   second concurrent run -- so it has to agree with the loader
--   exactly. Strengthening the identity rule is a change to
--   BOTH, together, not to the index alone.
-- ============================================================

BEGIN;

-- norm_name() from client_mapping.py, in SQL. The two must
-- stay in step: this is the loader's notion of a name.
CREATE OR REPLACE FUNCTION gold.norm_name(name text)
RETURNS text
LANGUAGE sql
IMMUTABLE
AS $fn$
    SELECT nullif(
        btrim(
            regexp_replace(
                regexp_replace(
                    regexp_replace(
                        regexp_replace(
                            upper(coalesce(name, '')),
                            -- KFIN stores a minor as
                            -- "DHYANI PATEL REP BY NIKESH PATEL";
                            -- left in place the guardian's name
                            -- becomes part of the child's key.
                            '\s+REP\s+BY\s+.*$', '', 'g'
                        ),
                        '^(MASTER|MSTR|MISS|BABY|KUM|KUMARI|MR|MRS|SMT|SHRI)[ .]+',
                        '', ''
                    ),
                    '[^A-Z0-9 ]', '', 'g'
                ),
                '\s+', ' ', 'g'
            )
        ),
    '')
$fn$;


-- The loader's identity key for a PAN-less client: inside a
-- family the FIRST name identifies the child (it survives
-- middle-name drift while still separating twins -- NIVAA and
-- NIVAAN share a guardian PAN and a date of birth); with no
-- guardian there is no family to disambiguate within, so the
-- whole name is the key.
CREATE OR REPLACE FUNCTION gold.panless_name_key(
    name text,
    guardian text
)
RETURNS text
LANGUAGE sql
IMMUTABLE
AS $fn$
    SELECT CASE
        WHEN nullif(btrim(coalesce(guardian, '')), '') IS NOT NULL
        THEN split_part(gold.norm_name(name), ' ', 1)
        ELSE gold.norm_name(name)
    END
$fn$;

COMMIT;


-- ============================================================
-- THE INDEX
--
-- One partial index covers BOTH PAN-less populations, because
-- coalesce() makes a NULL guardian PAN a comparable value
-- rather than an escape hatch:
--
--   guardian-backed (22)  real guardian PAN + first name + DOB
--   no guardian      (6)  '~' + whole name + DOB
--
-- date_of_birth is coalesced to a sentinel DATE rather than
-- cast to text: date -> text is STABLE, not IMMUTABLE (it
-- depends on DateStyle), so Postgres refuses it in an index.
-- 0001-01-01 reads as "no date of birth on file"; four PAN-less
-- clients are in that state today and the current index cannot
-- see any of them.
-- ============================================================

BEGIN;

DROP INDEX IF EXISTS gold.uq_clients_pan_absent;

-- Dropped as well as created, so this script can be re-run.
-- add_client_merge.sql re-creates the same index with the
-- superseded_by predicate; without this drop, re-running the
-- pair in order fails on "relation already exists" and leaves
-- the migration half-applied.
DROP INDEX IF EXISTS gold.uq_clients_panless_identity;

CREATE UNIQUE INDEX uq_clients_panless_identity
    ON gold.clients (
        coalesce(upper(btrim(guardian_pan)), '~'),
        coalesce(gold.panless_name_key(full_name, guardian_pan), '~'),
        coalesce(date_of_birth, DATE '0001-01-01')
    )
    WHERE pan IS NULL;

COMMIT;


-- ============================================================
-- WHAT THIS STILL DOES NOT CATCH
--
--   A PAN-less client whose FIRST name changes, or whose name
--   arrives in a different order ("Heer Pritipal Shah" ->
--   "Shah Heer Pritipal"), gets a different key and is inserted
--   as a second client. The index agrees with the loader, so it
--   raises nothing.
--
--   That is deliberate: an index that disagreed with the loader
--   would abort the pipeline rather than prevent the duplicate.
--   The real answer is to stop identifying these clients by
--   name at all and resolve them through gold.client_folio, the
--   way an arriving PAN already is -- a folio does not change
--   when a name is respelled. That is a change to the loader
--   and the index together.
-- ============================================================

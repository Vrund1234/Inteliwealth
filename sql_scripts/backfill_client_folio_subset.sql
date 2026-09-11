-- Second pass for folios the exact token match missed.
--
-- Within one guardian_pan + date_of_birth there is at most one
-- child per birthday except twins, so the name only has to
-- distinguish siblings -- and a SUBSET match is enough for that
-- while surviving middle-name drift:
--
--   silver "Dhyani Patel"    -> {DHYANI, PATEL}
--   gold   "Dhyani N Patel"  -> {DHYANI, N, PATEL}     subset -> match
--
-- Twins still separate, because neither is a subset of the other:
--   {NIVAA, SHAH, VAISHAL}  vs  {NIVAAN, SHAH, VAISHAL}
--
-- Guarded on exactly one candidate: ambiguity is left unlinked
-- rather than guessed.
BEGIN;

WITH unlinked AS (
    SELECT DISTINCT
        upper(btrim(i.source))           AS source,
        gold.normalise_folio(i.folio_no) AS folio_no,
        upper(btrim(i.guardian_pan))     AS guardian_pan,
        i.dob,
        string_to_array(gold.name_token_key(i.investor_name), ' ') AS tokens
    FROM silver.investor_master i
    WHERE upper(btrim(i.guardian_pan)) ~ '^[A-Z]{5}[0-9]{4}[A-Z]$'
      -- coalesce, not a bare btrim: pan_no is NULL for exactly the
      -- rows this pass is for, and btrim(NULL) is NULL, so the regex
      -- yields NULL, NOT NULL is NULL, and the WHERE drops the very
      -- rows it was meant to keep.
      AND NOT (coalesce(upper(btrim(i.pan_no)), '') ~ '^[A-Z]{5}[0-9]{4}[A-Z]$')
      AND NOT EXISTS (
          SELECT 1 FROM gold.client_folio f
           WHERE f.source = upper(btrim(i.source))
             AND f.folio_no = gold.normalise_folio(i.folio_no))
),
candidate AS (
    SELECT u.source, u.folio_no, c.id AS client_id,
           count(*) OVER (PARTITION BY u.source, u.folio_no) AS n
    FROM unlinked u
    JOIN gold.clients c
      ON c.pan IS NULL
     AND upper(btrim(c.guardian_pan)) = u.guardian_pan
     AND c.date_of_birth IS NOT DISTINCT FROM u.dob
     AND (
            u.tokens <@ string_to_array(gold.name_token_key(c.full_name), ' ')
         OR string_to_array(gold.name_token_key(c.full_name), ' ') <@ u.tokens
         )
)
INSERT INTO gold.client_folio (source, folio_no, client_id, resolved_by)
SELECT source, folio_no, client_id, 'GUARDIAN_NAME_SUBSET'
FROM candidate
WHERE n = 1
ON CONFLICT (source, folio_no) DO NOTHING;

COMMIT;

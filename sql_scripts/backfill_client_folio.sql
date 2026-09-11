-- Backfill gold.client_folio from the identity gold.clients
-- already holds. Three rules, applied in precedence order --
-- PAN first, because it is the only structural one.
BEGIN;

WITH folio AS (
    SELECT DISTINCT
        upper(btrim(i.source))                AS source,
        gold.normalise_folio(i.folio_no)      AS folio_no,
        CASE WHEN upper(btrim(i.pan_no)) ~ '^[A-Z]{5}[0-9]{4}[A-Z]$'
             THEN upper(btrim(i.pan_no)) END  AS pan,
        CASE WHEN upper(btrim(i.guardian_pan)) ~ '^[A-Z]{5}[0-9]{4}[A-Z]$'
             THEN upper(btrim(i.guardian_pan)) END AS guardian_pan,
        i.dob,
        gold.name_token_key(i.investor_name)  AS name_key
    FROM silver.investor_master i
    WHERE gold.normalise_folio(i.folio_no) IS NOT NULL
),
resolved AS (
    SELECT f.source, f.folio_no,
        COALESCE(by_pan.id, by_guardian.id, by_name.id) AS client_id,
        CASE WHEN by_pan.id      IS NOT NULL THEN 'PAN'
             WHEN by_guardian.id IS NOT NULL THEN 'GUARDIAN_NAME_DOB'
             WHEN by_name.id     IS NOT NULL THEN 'NAME_DOB'
        END AS resolved_by
    FROM folio f

    LEFT JOIN gold.clients by_pan
           ON by_pan.pan = f.pan

    LEFT JOIN gold.clients by_guardian
           ON f.pan IS NULL
          AND by_guardian.pan IS NULL
          AND upper(btrim(by_guardian.guardian_pan)) = f.guardian_pan
          AND by_guardian.date_of_birth IS NOT DISTINCT FROM f.dob
          AND gold.name_token_key(by_guardian.full_name) = f.name_key

    LEFT JOIN gold.clients by_name
           ON f.pan IS NULL
          AND f.guardian_pan IS NULL
          AND by_name.pan IS NULL
          AND by_name.guardian_pan IS NULL
          AND by_name.date_of_birth IS NOT DISTINCT FROM f.dob
          AND gold.name_token_key(by_name.full_name) = f.name_key
)
INSERT INTO gold.client_folio (source, folio_no, client_id, resolved_by)
SELECT source, folio_no, client_id, resolved_by
FROM resolved
WHERE client_id IS NOT NULL
ON CONFLICT (source, folio_no) DO NOTHING;

COMMIT;

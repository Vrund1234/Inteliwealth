-- ============================================================
-- Post-deployment verification for client identity
-- Run after the migrations + one full pipeline pass.
-- Every count in the second block must be 0.
-- ============================================================

\echo '--- client counts (expect 617 live / 3 superseded) ---'
SELECT count(*) FILTER (WHERE superseded_by IS NULL)     AS live_clients,
       count(*) FILTER (WHERE superseded_by IS NOT NULL) AS superseded,
       count(*)                                          AS total
FROM gold.clients;

\echo ''
\echo '--- integrity: every count must be 0 ---'
SELECT 'duplicate PANs' AS check, count(*) AS must_be_zero FROM (
    SELECT pan FROM gold.clients
    WHERE pan IS NOT NULL AND superseded_by IS NULL
    GROUP BY 1 HAVING count(*) > 1) a

UNION ALL SELECT 'folios owned by two clients', count(*) FROM (
    SELECT folio_no FROM gold.client_folio
    GROUP BY 1 HAVING count(DISTINCT client_id) > 1) b

UNION ALL SELECT 'folios in silver with no link', count(*) FROM (
    SELECT DISTINCT gold.normalise_folio(im.folio_no) AS f
    FROM silver.investor_master im
    WHERE NOT EXISTS (
        SELECT 1 FROM gold.client_folio cf
        WHERE cf.source   = upper(btrim(im.source))
          AND cf.folio_no = gold.normalise_folio(im.folio_no))) c

UNION ALL SELECT 'superseded still holding child rows', (
    SELECT count(*) FROM gold.clients g
    WHERE g.superseded_by IS NOT NULL
      AND (EXISTS (SELECT 1 FROM gold.client_folio   f WHERE f.client_id = g.id)
        OR EXISTS (SELECT 1 FROM gold.client_bank    b WHERE b.client_id = g.id)
        OR EXISTS (SELECT 1 FROM gold.client_address a WHERE a.client_id = g.id)))

UNION ALL SELECT 'live clients with no address', count(*)
    FROM gold.clients g WHERE g.superseded_by IS NULL
      AND NOT EXISTS (SELECT 1 FROM gold.client_address a WHERE a.client_id = g.id)

UNION ALL SELECT 'live clients with no folio', count(*)
    FROM gold.clients g WHERE g.superseded_by IS NULL
      AND NOT EXISTS (SELECT 1 FROM gold.client_folio f WHERE f.client_id = g.id)

UNION ALL SELECT 'individual_category_type NULL', count(*)
    FROM gold.clients WHERE superseded_by IS NULL
      AND individual_category_type IS NULL

UNION ALL SELECT 'entities carrying an age', count(*)
    FROM gold.clients WHERE superseded_by IS NULL
      AND individual_category_type <> 'INDIVIDUAL' AND age IS NOT NULL

UNION ALL SELECT 'aadhaar column holding a non-Aadhaar', count(*)
    FROM gold.clients WHERE aadhaar IS NOT NULL AND aadhaar !~ '^[0-9]{12}$';

\echo ''
\echo '--- what was merged, and on what evidence ---'
SELECT loser_name, winner_name, coalesce(winner_pan,'-') AS winner_pan,
       evidence, merged_at::date
FROM gold.client_merge_log ORDER BY merged_at;

\echo ''
\echo '--- folio map coverage by rule ---'
SELECT resolved_by, count(*) FROM gold.client_folio GROUP BY 1 ORDER BY 2 DESC;

\echo ''
\echo '--- adults still on a guardian PAN (document update required) ---'
SELECT full_name, age, guardian_pan, is_documentupdaterequired
FROM gold.clients
WHERE superseded_by IS NULL AND pan IS NULL AND age >= 18
ORDER BY age DESC;

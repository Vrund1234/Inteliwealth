-- =============================================================================
-- Near-duplicate (fuzzy) address detection for gold.client_address
-- =============================================================================
--
-- Companion to check_duplicate_client_address.sql.
--
--   check_duplicate_client_address.sql  -> EXACT duplicates after normalising
--                                          punctuation, case and pincode zeros.
--                                          Safe to auto-merge.
--
--   THIS FILE                           -> pairs that survive that normalising
--                                          but are still the same address,
--                                          because one feed abbreviates or
--                                          truncates a word the other spells
--                                          out. NOT safe to auto-merge --
--                                          route to a human via needs_review.
--
-- Examples this catches that exact matching cannot:
--
--   ...RETI BUNDER                  vs  ...RETI BUNDER ROAD
--   A/14 SWAGAT APPT OPP BHAVSARS   vs  ...OPP BHAVSAR SOCIETY
--   ...MOTERA SANSKRUT BUNGL        vs  ...MOTERA SANSKRUT BUNGLOWS
--
-- Requires the pg_trgm extension (already installed on this database).
--
-- The address key below is line-boundary agnostic: line1/line2/line3 are
-- concatenated with NO separator, because different RTAs split the same text
-- across the three columns at different points.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- Q1. The pairs, most similar first. Start here.
--
-- Tune the 0.75 threshold: higher = fewer, more certain pairs;
-- lower = wider net, more false positives to reject.
-- -----------------------------------------------------------------------------
WITH keyed AS (
    SELECT DISTINCT
        a.client_id,
        UPPER(REGEXP_REPLACE(
            COALESCE(a.line1,'') || COALESCE(a.line2,'') || COALESCE(a.line3,''),
            '[^A-Za-z0-9]', '', 'g'))
        || '#' ||
        COALESCE(NULLIF(LTRIM(REGEXP_REPLACE(
            COALESCE(a.pincode,''), '[^0-9]', '', 'g'), '0'), ''), '') AS address_key
    FROM gold.client_address a
    WHERE a.is_deleted = false
),
pairs AS (
    SELECT
        x.client_id,
        x.address_key AS key_a,
        y.address_key AS key_b,
        similarity(x.address_key, y.address_key) AS sim
    FROM keyed x
    JOIN keyed y
      ON y.client_id = x.client_id
     AND x.address_key < y.address_key          -- each pair once, never self
    WHERE similarity(x.address_key, y.address_key) > 0.75
)
SELECT
    c.pan,
    p.client_id,
    ROUND(p.sim::numeric, 3) AS similarity,
    a.id      AS id_a,
    a.seq     AS seq_a,
    a.is_main AS main_a,
    CONCAT_WS(' / ', a.line1, a.line2, a.line3) AS address_a,
    a.city    AS city_a,
    a.pincode AS pin_a,
    b.id      AS id_b,
    b.seq     AS seq_b,
    b.is_main AS main_b,
    CONCAT_WS(' / ', b.line1, b.line2, b.line3) AS address_b,
    b.city    AS city_b,
    b.pincode AS pin_b
FROM pairs p
JOIN LATERAL (
    SELECT * FROM gold.client_address z
    WHERE z.client_id = p.client_id AND z.is_deleted = false
      AND UPPER(REGEXP_REPLACE(COALESCE(z.line1,'')||COALESCE(z.line2,'')||COALESCE(z.line3,''),'[^A-Za-z0-9]','','g'))
          || '#' || COALESCE(NULLIF(LTRIM(REGEXP_REPLACE(COALESCE(z.pincode,''),'[^0-9]','','g'),'0'),''),'') = p.key_a
    ORDER BY z.seq LIMIT 1
) a ON true
JOIN LATERAL (
    SELECT * FROM gold.client_address z
    WHERE z.client_id = p.client_id AND z.is_deleted = false
      AND UPPER(REGEXP_REPLACE(COALESCE(z.line1,'')||COALESCE(z.line2,'')||COALESCE(z.line3,''),'[^A-Za-z0-9]','','g'))
          || '#' || COALESCE(NULLIF(LTRIM(REGEXP_REPLACE(COALESCE(z.pincode,''),'[^0-9]','','g'),'0'),''),'') = p.key_b
    ORDER BY z.seq LIMIT 1
) b ON true
LEFT JOIN gold.clients c ON c.id = p.client_id
ORDER BY p.sim DESC, c.pan;


-- -----------------------------------------------------------------------------
-- Q2. How many pairs at each threshold? Run this first to pick a cutoff.
-- -----------------------------------------------------------------------------
WITH keyed AS (
    SELECT DISTINCT
        a.client_id,
        UPPER(REGEXP_REPLACE(
            COALESCE(a.line1,'') || COALESCE(a.line2,'') || COALESCE(a.line3,''),
            '[^A-Za-z0-9]', '', 'g'))
        || '#' ||
        COALESCE(NULLIF(LTRIM(REGEXP_REPLACE(
            COALESCE(a.pincode,''), '[^0-9]', '', 'g'), '0'), ''), '') AS address_key
    FROM gold.client_address a
    WHERE a.is_deleted = false
),
sims AS (
    SELECT similarity(x.address_key, y.address_key) AS sim
    FROM keyed x
    JOIN keyed y ON y.client_id = x.client_id AND x.address_key < y.address_key
)
SELECT t.threshold, COUNT(*) FILTER (WHERE s.sim > t.threshold) AS pairs
FROM   (VALUES (0.60),(0.70),(0.75),(0.80),(0.85),(0.90),(0.95)) AS t(threshold)
CROSS JOIN sims s
GROUP BY t.threshold
ORDER BY t.threshold;

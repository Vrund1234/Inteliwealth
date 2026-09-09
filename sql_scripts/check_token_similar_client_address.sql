-- =============================================================================
-- Token-overlap near-duplicate detection for gold.client_address
-- =============================================================================
--
-- Third companion to the pair already in this folder:
--
--   check_duplicate_client_address.sql  -> EXACT duplicates after normalising
--                                          punctuation and case.
--   check_similar_client_address.sql    -> trigram-similar pairs (> 0.75).
--   THIS FILE                           -> pairs that BOTH of those miss.
--
-- Trigram similarity compares whole strings, so it fails when two feeds
-- describe one address using different LANDMARKS rather than different
-- spellings. PAN AMHPP6820E is the worked example -- three rows, one house:
--
--   38, ALOK BUNGLOWS / NERSAL HOSPITAL / THALTEJ,BODAKDEV
--   NO 38 ALOK BUNGLOWS / NEAR SUN N STEP CLUB / BODAKDEV
--   38, THALTEJ ALOK BUNGLOWS THALTEJ / NEAR SAL HOSPITAL
--
-- Those three score 0.194 - 0.491 against each other, so no workable trigram
-- threshold reaches them, and none is a prefix of another. What they DO share
-- is the rare token ALOK, plus one pincode.
--
-- THE RULE: two addresses of ONE client, with the SAME pincode, sharing at
-- least one RARE token. A token is rare when it appears in <= 3% of addresses
-- (20 of the 640 live on 2026-09-09) -- ALOK qualifies, BUNGLOWS does not. The
-- row's own city/state/country never count: both addresses carry them, so a
-- shared city name proves nothing, and taking them from the row rather than a
-- list means every city works, not only the ones someone listed.
--
-- Scoping to a single client is what makes so loose a rule safe: two addresses
-- filed under one PAN are far likelier to be one house than two arbitrary rows
-- are. It is still NOT safe to auto-merge -- 43/44 VRAJ VATIKA SOC and
-- B 44 VRAJVATIKA SOC share a rare token and may be two flats in one society.
-- Route to a human, never behind the unique index.
-- =============================================================================


-- Tokens that appear everywhere and therefore prove nothing.
-- Kept in sync with STOPWORDS in detect_client_address_duplicates.py.
CREATE TEMP VIEW _stopwords AS
SELECT UNNEST(ARRAY[
    'NEAR','OPPOSITE','BEHIND','ROAD','MARG','STREET','LANE','GALI','CROSS',
    'CHAR','RASTA','SOCIETY','NAGAR','PARK','APARTMENT','APARTMENTS','FLAT',
    'BLOCK','PLOT','HOUSE','HOME','BUNGLOW','BUNGLOWS','BUNGALOW','BUNGALOWS',
    'TOWER','TOWERS','COMPLEX','SCHEME','SECTOR','PHASE','FLOOR','WING','EAST',
    'WEST','NORTH','SOUTH','SHREE','SHRI','BAZAR','MAIN','CITY','VILLAGE','POST',
    'STATION','CHOWK','CIRCLE','HIGHWAY'
]) AS word;


CREATE TEMP VIEW _tok AS
WITH live AS (
    SELECT
        a.client_id,
        a.line1, a.line2, a.line3,
        COALESCE(a.city,'') || ' ' || COALESCE(a.state,'') || ' ' ||
        COALESCE(a.country,'') AS geography,
        NULLIF(REGEXP_REPLACE(COALESCE(a.pincode,''), '[^0-9]', '', 'g'), '') AS pin,
        UPPER(REGEXP_REPLACE(
            COALESCE(a.line1,'') || COALESCE(a.line2,'') || COALESCE(a.line3,''),
            '[^A-Za-z0-9]', '', 'g')) AS address_key
    FROM gold.client_address a
    WHERE a.is_deleted = false
      AND UPPER(REGEXP_REPLACE(
            COALESCE(a.line1,'') || COALESCE(a.line2,'') || COALESCE(a.line3,''),
            '[^A-Za-z0-9]', '', 'g')) <> ''
)
SELECT DISTINCT
    l.client_id, l.address_key, l.pin, UPPER(t.token) AS token
FROM live l
CROSS JOIN LATERAL regexp_split_to_table(
    COALESCE(l.line1,'') || ' ' || COALESCE(l.line2,'') || ' ' || COALESCE(l.line3,''),
    '[^A-Za-z0-9]+') AS t(token)
WHERE LENGTH(t.token) >= 4
  AND t.token !~ '^[0-9]+$'
  AND UPPER(t.token) NOT IN (SELECT word FROM _stopwords)
  -- The row's OWN city/state/country. Both addresses of one client carry them,
  -- so a shared city name is a free token that proves nothing. Taken from the
  -- row rather than a list, so every city works.
  AND UPPER(t.token) NOT IN (
      SELECT UPPER(g) FROM regexp_split_to_table(l.geography, '[^A-Za-z0-9]+') AS g
      WHERE g <> ''
  );


CREATE TEMP VIEW _pairs AS
WITH rare AS (
    SELECT token
    FROM _tok
    GROUP BY token
    HAVING COUNT(DISTINCT address_key) <= GREATEST(
        1, CEIL(0.03 * (SELECT COUNT(*) FROM gold.client_address WHERE is_deleted = false))
    )
)
SELECT
    ta.client_id,
    ta.address_key AS key_a,
    tb.address_key AS key_b,
    COUNT(*)                                   AS shared_rare_tokens,
    ARRAY_AGG(ta.token ORDER BY ta.token)      AS tokens
FROM _tok ta
JOIN _tok tb
  ON  tb.client_id   = ta.client_id
 AND  ta.address_key < tb.address_key    -- each pair once, never self
 AND  tb.token       = ta.token
JOIN rare r ON r.token = ta.token
WHERE ta.pin IS NOT NULL AND ta.pin = tb.pin
GROUP BY ta.client_id, ta.address_key, tb.address_key;


-- -----------------------------------------------------------------------------
-- Q1. Summary. Start here.
-- -----------------------------------------------------------------------------
SELECT
    (SELECT COUNT(*) FROM gold.client_address WHERE is_deleted = false) AS live_addresses,
    (SELECT COUNT(*) FROM _pairs)                                       AS flagged_pairs,
    (SELECT COUNT(DISTINCT client_id) FROM _pairs)                      AS clients_affected,
    (SELECT COUNT(*) FROM gold.client_address_review)                   AS already_queued_by_trigram;


-- -----------------------------------------------------------------------------
-- Q2. How many address rows would collapse, per client.
--     addresses_flagged - 1 is the rows this client would shed if every
--     flagged address under it is confirmed to be one place.
-- -----------------------------------------------------------------------------
WITH involved AS (
    SELECT client_id, key_a AS address_key FROM _pairs
    UNION
    SELECT client_id, key_b FROM _pairs
)
SELECT
    c.pan,
    i.client_id,
    COUNT(*)     AS addresses_flagged,
    COUNT(*) - 1 AS rows_that_would_collapse
FROM involved i
LEFT JOIN gold.clients c ON c.id = i.client_id
GROUP BY c.pan, i.client_id
ORDER BY addresses_flagged DESC, c.pan;


-- -----------------------------------------------------------------------------
-- Q3. The pairs themselves, with the addresses spelled out, to eyeball.
-- -----------------------------------------------------------------------------
SELECT
    c.pan,
    p.client_id,
    p.shared_rare_tokens,
    p.tokens,
    a.seq     AS seq_a,
    a.is_main AS main_a,
    CONCAT_WS(' / ', a.line1, a.line2, a.line3) AS address_a,
    b.seq     AS seq_b,
    b.is_main AS main_b,
    CONCAT_WS(' / ', b.line1, b.line2, b.line3) AS address_b,
    ROUND(similarity(p.key_a, p.key_b)::numeric, 3) AS trigram_sim
FROM _pairs p
JOIN LATERAL (
    SELECT z.* FROM gold.client_address z
    WHERE z.client_id = p.client_id AND z.is_deleted = false
      AND UPPER(REGEXP_REPLACE(COALESCE(z.line1,'')||COALESCE(z.line2,'')||COALESCE(z.line3,''),'[^A-Za-z0-9]','','g')) = p.key_a
    ORDER BY z.seq LIMIT 1) a ON true
JOIN LATERAL (
    SELECT z.* FROM gold.client_address z
    WHERE z.client_id = p.client_id AND z.is_deleted = false
      AND UPPER(REGEXP_REPLACE(COALESCE(z.line1,'')||COALESCE(z.line2,'')||COALESCE(z.line3,''),'[^A-Za-z0-9]','','g')) = p.key_b
    ORDER BY z.seq LIMIT 1) b ON true
LEFT JOIN gold.clients c ON c.id = p.client_id
ORDER BY p.shared_rare_tokens DESC, c.pan;


-- -----------------------------------------------------------------------------
-- Q4. What this rule adds over the trigram detector already in place.
-- -----------------------------------------------------------------------------
SELECT
    COUNT(*) FILTER (WHERE r.review_id IS NOT NULL) AS also_found_by_trigram,
    COUNT(*) FILTER (WHERE r.review_id IS NULL)     AS found_only_by_token_rule
FROM _pairs p
LEFT JOIN gold.client_address_review r
       ON  r.client_id     = p.client_id
       AND r.address_key_a = p.key_a
       AND r.address_key_b = p.key_b;

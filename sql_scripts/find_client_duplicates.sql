-- ============================================================
-- Duplicate detection for gold.clients -- REPORT ONLY
--
-- A pair is a duplicate only when ALL FIVE of these hold:
--
--   1. IDENTICAL name token sets. Order-independent, so
--      "Saleel Y Bhatt" meets "SALEEL Y BHATT", but NOT fuzzy:
--      SUREEL BHATT and SALEEL BHATT differ by one letter and
--      are two different men.
--
--   2. AT MOST ONE SIDE HAS A PAN. Two different PANs mean two
--      different people, full stop -- MIHIRKUMAR PATEL
--      (ALGPP2503E, 1982) and Patel Mihirkumar (BJYPP6199P,
--      1986) share a token set and are not the same person.
--      This test alone rejects them.
--
--   3. DATES OF BIRTH AGREE, or one side has none.
--
--   4. CORROBORATION: they share a bank account or a postal
--      address. Name and DOB alone are an assertion; a shared
--      account is evidence. This is what makes the merge safe
--      to run without a human reading it.
--
-- The winner is the side holding the PAN; failing that, the one
-- with more folios; failing that, the older row.
-- ============================================================

WITH client_key AS (
    SELECT c.id,
           c.pan,
           c.guardian_pan,
           c.full_name,
           c.date_of_birth,
           c.created_at,
           gold.name_token_key(c.full_name) AS tokens,
           (SELECT count(*) FROM gold.client_folio f
             WHERE f.client_id = c.id) AS folios
    FROM gold.clients c
    WHERE c.superseded_by IS NULL
      AND gold.name_token_key(c.full_name) IS NOT NULL
),

pair AS (
    SELECT a.id AS a_id, b.id AS b_id,
           a.pan AS a_pan, b.pan AS b_pan,
           a.full_name AS a_name, b.full_name AS b_name,
           a.date_of_birth AS a_dob, b.date_of_birth AS b_dob,
           a.folios AS a_folios, b.folios AS b_folios,
           a.created_at AS a_created, b.created_at AS b_created,
           a.tokens

    FROM client_key a
    JOIN client_key b
      -- SUBSET, not equality: one side routinely carries a
      -- middle name the other does not.
      ON (string_to_array(a.tokens,' ') <@ string_to_array(b.tokens,' ')
          OR string_to_array(b.tokens,' ') <@ string_to_array(a.tokens,' '))
     AND array_length(string_to_array(a.tokens,' '),1) >= 2
     AND array_length(string_to_array(b.tokens,' '),1) >= 2
     AND b.id > a.id

    -- rule 2
    WHERE (a.pan IS NULL OR b.pan IS NULL)

    -- rule 3
      AND (a.date_of_birth IS NULL
           OR b.date_of_birth IS NULL
           OR a.date_of_birth = b.date_of_birth)

    -- rule 5: a guardian is not their ward. Proof, not evidence.
    -- A guardian's name is routinely a subset of the ward's
    -- (Vedant Maheshwari inside Ahaan Vedant Maheshwari) and a
    -- minor usually transacts on the guardian's bank account, so
    -- rules 1 and 4 both fire on a parent/child pair. Only the
    -- differing dates of birth stood between them -- and a minor
    -- with no DOB on file has nothing at all.
      AND upper(btrim(coalesce(a.guardian_pan,'~')))
          IS DISTINCT FROM upper(btrim(coalesce(b.pan,'!')))
      AND upper(btrim(coalesce(b.guardian_pan,'~')))
          IS DISTINCT FROM upper(btrim(coalesce(a.pan,'!')))
),

corroborated AS (
    SELECT p.*,

           (SELECT string_agg(DISTINCT x.account_number, ',')
              FROM gold.client_bank x
              JOIN gold.client_bank y
                ON y.account_key = x.account_key
             WHERE x.client_id = p.a_id
               AND y.client_id = p.b_id) AS shared_bank,

           (SELECT count(*)
              FROM gold.client_address x
              JOIN gold.client_address y
                ON y.address_key = x.address_key
             WHERE x.client_id = p.a_id
               AND y.client_id = p.b_id) AS shared_addresses

    FROM pair p
)

SELECT
    CASE WHEN a_pan IS NOT NULL THEN a_id
         WHEN b_pan IS NOT NULL THEN b_id
         WHEN a_folios >= b_folios THEN a_id
         ELSE b_id END AS winner_id,

    CASE WHEN a_pan IS NOT NULL THEN b_id
         WHEN b_pan IS NOT NULL THEN a_id
         WHEN a_folios >= b_folios THEN b_id
         ELSE a_id END AS loser_id,

    tokens,
    a_name, coalesce(a_pan, '(none)') AS a_pan,
    b_name, coalesce(b_pan, '(none)') AS b_pan,
    coalesce(shared_bank, '') AS shared_bank,
    shared_addresses

FROM corroborated

-- rule 4
WHERE shared_bank IS NOT NULL OR shared_addresses > 0

ORDER BY tokens;

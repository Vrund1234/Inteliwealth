-- ============================================================
-- bronze.client_mapping_review
-- ============================================================
--
-- One row per folio, recording which client identity the mapper
-- chose and WHICH RULE chose it -- the same shape as
-- bronze.scheme_mapping_review.
--
-- Written by python_scripts/client_mapping.py. Four rules, in
-- order, first match wins:
--
--   1. own PAN present              PAN:<pan>
--   2. no PAN, guardian PAN present GPAN:<guard_pan>|<dob>|<first name>
--   3. no PAN and no guardian PAN   NAME:<name>|<dob>
--   4. no name either               FOLIO:<source>|<folio_no>
--
-- mapping_status is MAPPED when the rules are certain and
-- REVIEW when a human should look. reviewer_decision is the
-- human's answer; a re-run refreshes the mapping columns but
-- NEVER clears a decision already recorded.
-- ============================================================

CREATE TABLE IF NOT EXISTS bronze.client_mapping_review (
    review_id           UUID         PRIMARY KEY DEFAULT gen_random_uuid(),

    -- the folio the mapping was made for
    source              VARCHAR(20)  NOT NULL,
    folio_no            TEXT         NOT NULL,

    -- the evidence the rules had to work with, as received
    name                TEXT,
    pan_no              TEXT,
    guard_pan           TEXT,
    dob                 DATE,

    -- what the rules decided
    client_ref          TEXT,
    rule_name           VARCHAR(40)  NOT NULL,
    mapping_confidence  INTEGER,
    mapping_status      VARCHAR(20)  NOT NULL
        CHECK (mapping_status IN ('MAPPED', 'REVIEW')),

    -- why a human is being asked, when they are
    --   LOW_CONFIDENCE  decided on a name, not an identifier
    --   AMBIGUOUS       two candidates too close to separate
    --   STRUCTURAL      a fact disagrees (bad PAN, non-individual guardian)
    review_reason       VARCHAR(20),
    review_detail       TEXT,

    reviewer_decision   VARCHAR(20)
        CHECK (reviewer_decision IS NULL
               OR reviewer_decision IN ('APPROVED', 'REJECTED')),
    reviewed_by         VARCHAR(80),
    reviewed_at         TIMESTAMP,

    created_at          TIMESTAMP    NOT NULL DEFAULT now(),
    updated_at          TIMESTAMP
);

-- One mapping per folio. The script upserts on this key, so a
-- re-run refreshes the decision without disturbing a review.
CREATE UNIQUE INDEX IF NOT EXISTS uq_client_mapping_review_folio
    ON bronze.client_mapping_review (source, folio_no);

CREATE INDEX IF NOT EXISTS client_mapping_review_pending_idx
    ON bronze.client_mapping_review (review_reason)
    WHERE mapping_status = 'REVIEW' AND reviewer_decision IS NULL;

CREATE INDEX IF NOT EXISTS client_mapping_review_ref_idx
    ON bronze.client_mapping_review (client_ref);

CREATE INDEX IF NOT EXISTS client_mapping_review_rule_idx
    ON bronze.client_mapping_review (rule_name);


-- ============================================================
-- REVIEWING THE MAPPING
-- ============================================================

-- How every folio was mapped, and by which rule.
SELECT rule_name,
       mapping_confidence,
       mapping_status,
       COUNT(*)                  AS folios,
       COUNT(DISTINCT client_ref) AS clients
FROM bronze.client_mapping_review
GROUP BY 1, 2, 3
ORDER BY 2 DESC NULLS LAST;

-- The queue: everything awaiting a human decision.
SELECT source, folio_no, name, pan_no, guard_pan, dob,
       client_ref, rule_name, review_reason, review_detail
FROM bronze.client_mapping_review
WHERE mapping_status = 'REVIEW'
  AND reviewer_decision IS NULL
ORDER BY review_reason, source, folio_no;

-- Families: every folio sharing one guardian PAN.
SELECT guard_pan,
       COUNT(*)                   AS folios,
       COUNT(DISTINCT client_ref) AS children,
       STRING_AGG(DISTINCT name, ' | ') AS members
FROM bronze.client_mapping_review
WHERE guard_pan IS NOT NULL
  AND rule_name LIKE 'GUARDIAN_PAN%'
GROUP BY guard_pan
ORDER BY 3 DESC;

-- Clients built from more than one folio.
SELECT client_ref,
       COUNT(*) AS folios,
       STRING_AGG(DISTINCT name, ' | ') AS names_seen
FROM bronze.client_mapping_review
GROUP BY client_ref
HAVING COUNT(*) > 1
ORDER BY 2 DESC;

-- Approve a mapping (the reviewer's action).
-- UPDATE bronze.client_mapping_review
-- SET reviewer_decision = 'APPROVED',
--     reviewed_by       = 'your-name',
--     reviewed_at       = now()
-- WHERE mapping_status = 'REVIEW'
--   AND reviewer_decision IS NULL
--   AND (source, folio_no) IN (('CAMS', '1234567'));

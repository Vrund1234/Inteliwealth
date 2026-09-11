-- ============================================================
-- gold.clients.aadhaar holds a FLAG, not an Aadhaar number
--
-- WHAT IS WRONG
--   etl_gold_clients.py wrote the literal string 'Y' into
--   `aadhaar` whenever silver's holder_1_aadhaar_info was
--   non-null. Two separate faults:
--
--   1. `aadhaar` is VARCHAR(12), meant for a 12-digit Aadhaar
--      number. Every one of the 444 populated rows held 'Y' --
--      one distinct value, 0 matching ^[0-9]{12}$.
--
--   2. It wrote 'Y' for PRESENCE, not for the value. The source
--      column carries Y (1400), DELINKED (260), N (57),
--      AVAILABLE (12) and INVALID (2) -- so a client whose
--      Aadhaar is explicitly NOT seeded, or DELINKED, was still
--      recorded as 'Y'.
--
--   It also leaked: intelli-wealth-backend's map_gold_client
--   copies `aadhaar` 1:1, so 165 app clients carry
--   aadhaar = 'Y' in their own VARCHAR(12) column.
--
-- THE FIX
--   Keep the information, in a column that says what it is, at
--   the fidelity the source actually provides. There is no
--   Aadhaar NUMBER anywhere in this warehouse, so `aadhaar` is
--   cleared rather than reinterpreted -- a column named for an
--   identifier must never hold a status code.
-- ============================================================

BEGIN;

ALTER TABLE gold.clients
    ADD COLUMN IF NOT EXISTS aadhaar_seeding_status VARCHAR(20);

ALTER TABLE silver.investor_master
    ADD COLUMN IF NOT EXISTS aadhaar_seeding_status VARCHAR(20);


-- Carry the real source value through to silver.
UPDATE silver.investor_master
   SET aadhaar_seeding_status = upper(btrim(holder_1_aadhaar_info))
 WHERE nullif(btrim(holder_1_aadhaar_info), '') IS NOT NULL
   AND aadhaar_seeding_status IS DISTINCT FROM upper(btrim(holder_1_aadhaar_info));


-- Gold takes the most recent non-null status per client, the same
-- "newest statement wins" rule the rest of the client attributes use.
WITH resolved AS (
    SELECT upper(btrim(pan_no)) AS pan,
           (array_agg(upper(btrim(holder_1_aadhaar_info))
                      ORDER BY report_date DESC NULLS LAST)
            FILTER (WHERE nullif(btrim(holder_1_aadhaar_info), '') IS NOT NULL))[1] AS status
    FROM silver.investor_master
    WHERE upper(btrim(pan_no)) ~ '^[A-Z]{5}[0-9]{4}[A-Z]$'
    GROUP BY 1
)
UPDATE gold.clients c
   SET aadhaar_seeding_status = r.status
  FROM resolved r
 WHERE c.pan = r.pan
   AND c.aadhaar_seeding_status IS DISTINCT FROM r.status;


-- Clear the misfiled flag. This warehouse holds no Aadhaar numbers.
UPDATE gold.clients
   SET aadhaar = NULL
 WHERE aadhaar IS NOT NULL
   AND aadhaar !~ '^[0-9]{12}$';

COMMIT;

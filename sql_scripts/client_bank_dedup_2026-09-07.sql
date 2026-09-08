-- =============================================================================
-- gold.client_bank: make duplicate accounts impossible
-- 2026-09-07
-- =============================================================================
--
-- Companion to client_address_dedup_2026-09-07.sql. Same defect, far smaller:
-- 726 rows holding 722 distinct accounts, because the dedup key in
-- etl_gold_client_bank.py compares account_number as a RAW STRING and each RTA
-- pads it to a different width with leading zeros.
--
--     ACUPS2047M   00691060000052     vs  0691060000052
--     ALGPP2475B   0491010000421      vs  00491010000421
--     ALGPP2475B   00491060001464     vs  0491060001464
--     BFYPS0557N   30409457813        vs  00000030409457813
--
-- Coverage was verified clean before writing this: every distinct account in
-- bronze reaches silver (717 = 717, 0 missing), and every (client, account)
-- pair silver can resolve reaches gold (722 = 722, 0 missing, 0 extra). The
-- only defect is the four redundant rows.
--
-- This migration:
--   1. adds gold.client_bank.account_key, GENERATED from account_number and
--      matching utils/account_key.py exactly;
--   2. collapses the four duplicates into the most complete row of each pair;
--   3. re-asserts one is_main per client;
--   4. adds the UNIQUE index that makes step 2 unrepeatable.
--
-- ORDER MATTERS, and etl_gold_client_bank.py must be deployed WITH this file:
-- an index without the matching loader turns every re-run into a unique
-- violation and gold_loader.py reports client_bank FAILED.
--
-- Nothing hard-deletes. Losers are is_deleted = true, so this is reversible.
-- =============================================================================
--
-- TWO WAYS TO RUN THIS
--
-- (a) IN PLACE -- run as-is. Step 2 collapses 726 -> 722.
--
-- (b) TRUNCATE AND REBUILD -- uncomment the TRUNCATE, run, then:
--         cd python_scripts && venv/bin/python etl_gold_client_bank.py
--     Safe TODAY only because nothing consumes these ids yet: no foreign key
--     points at gold.client_bank, and the app backend does not sync the
--     entity (gold_sync/gold_models.py defines no GoldClientBank). Once it
--     does, (a) becomes the only correct option.
--
-- Either route ends at the same 722 rows.
-- =============================================================================

BEGIN;

-- Uncomment for route (b).
--
-- TRUNCATE gold.client_bank;


-- -----------------------------------------------------------------------------
-- 1. The generated key
-- -----------------------------------------------------------------------------
--
-- The account number alone: the bank's own identifier, never blank here
-- (0 of 726 rows), and unique enough scoped to one client.
--
-- ifsc / bank_branch / micr / bank_name / account_type / bank_city are all
-- ABSENT on purpose. They are enriched fields, not identity, and ifsc proves
-- why: it is blank on 22 rows and the ACUPS2047M pair is the same account with
-- the IFSC present on one side and missing on the other. A key containing a
-- field that arrives later inserts a duplicate instead of matching the row
-- already there. utils/account_key.py carries the full reasoning.
--
-- An all-zero number normalises to '' and is treated as absent, so a
-- placeholder can never merge two clients' rows.

ALTER TABLE gold.client_bank
    ADD COLUMN IF NOT EXISTS account_key text
    GENERATED ALWAYS AS (
        COALESCE(NULLIF(LTRIM(UPPER(REGEXP_REPLACE(
            COALESCE(account_number,''), '[^A-Za-z0-9]', '', 'g')), '0'), ''), '')
    ) STORED;


-- -----------------------------------------------------------------------------
-- 2. Collapse the duplicates
-- -----------------------------------------------------------------------------
--
-- Survivor by COMPLETENESS, not lowest seq -- the ACUPS2047M pair is exactly
-- why: seq 1 carries the IFSC and seq 3 does not, so ranking on seq alone
-- would keep the poorer row half the time. is_main then seq break ties, so the
-- choice is deterministic.

CREATE TEMP TABLE _bank_rank ON COMMIT DROP AS
SELECT
    id, client_id, account_key, seq, is_main,
    bank_name, bank_branch, bank_address, account_type,
    bank_city, pincode, micr, ifsc,
    ROW_NUMBER() OVER (
        PARTITION BY client_id, account_key
        ORDER BY
            ( (bank_name    IS NOT NULL)::int
            + (bank_branch  IS NOT NULL)::int
            + (bank_address IS NOT NULL)::int
            + (account_type IS NOT NULL)::int
            + (bank_city    IS NOT NULL)::int
            + (pincode      IS NOT NULL)::int
            + (micr         IS NOT NULL)::int
            + (ifsc         IS NOT NULL)::int ) DESC,
            is_main DESC,
            seq ASC,
            id ASC
    ) AS rn
FROM gold.client_bank
WHERE is_deleted = false
  AND account_key <> '';        -- a row with no usable account number is not a duplicate of anything

CREATE TEMP TABLE _bank_donor ON COMMIT DROP AS
SELECT
    client_id,
    account_key,
    (array_agg(bank_name    ORDER BY rn) FILTER (WHERE bank_name    IS NOT NULL))[1] AS bank_name,
    (array_agg(bank_branch  ORDER BY rn) FILTER (WHERE bank_branch  IS NOT NULL))[1] AS bank_branch,
    (array_agg(bank_address ORDER BY rn) FILTER (WHERE bank_address IS NOT NULL))[1] AS bank_address,
    (array_agg(account_type ORDER BY rn) FILTER (WHERE account_type IS NOT NULL))[1] AS account_type,
    (array_agg(bank_city    ORDER BY rn) FILTER (WHERE bank_city    IS NOT NULL))[1] AS bank_city,
    (array_agg(pincode      ORDER BY rn) FILTER (WHERE pincode      IS NOT NULL))[1] AS pincode,
    (array_agg(micr         ORDER BY rn) FILTER (WHERE micr         IS NOT NULL))[1] AS micr,
    (array_agg(ifsc         ORDER BY rn) FILTER (WHERE ifsc         IS NOT NULL))[1] AS ifsc,
    bool_or(is_main) AS any_main
FROM _bank_rank
WHERE rn > 1
GROUP BY client_id, account_key;

-- Fill the survivor's blanks from its losers. A value it already holds wins.
UPDATE gold.client_bank b
SET bank_name    = COALESCE(b.bank_name,    d.bank_name),
    bank_branch  = COALESCE(b.bank_branch,  d.bank_branch),
    bank_address = COALESCE(b.bank_address, d.bank_address),
    account_type = COALESCE(b.account_type, d.account_type),
    bank_city    = COALESCE(b.bank_city,    d.bank_city),
    pincode      = COALESCE(b.pincode,      d.pincode),
    micr         = COALESCE(b.micr,         d.micr),
    ifsc         = COALESCE(b.ifsc,         d.ifsc),
    is_main      = b.is_main OR d.any_main,
    updated_at   = now()
FROM _bank_rank r
JOIN _bank_donor d
  ON d.client_id = r.client_id AND d.account_key = r.account_key
WHERE b.id = r.id
  AND r.rn = 1;

UPDATE gold.client_bank
SET is_deleted = true,
    deleted_at = now(),
    updated_at = now()
WHERE id IN (SELECT id FROM _bank_rank WHERE rn > 1);


-- -----------------------------------------------------------------------------
-- 3. One main account per client
-- -----------------------------------------------------------------------------

WITH pick AS (
    SELECT DISTINCT ON (client_id) client_id, id
    FROM gold.client_bank
    WHERE is_deleted = false
    ORDER BY client_id, is_main DESC, seq ASC, id ASC
)
UPDATE gold.client_bank b
SET is_main = (b.id = pick.id),
    updated_at = now()
FROM pick
WHERE b.client_id = pick.client_id
  AND b.is_deleted = false
  AND b.is_main <> (b.id = pick.id);


-- -----------------------------------------------------------------------------
-- 4. The constraint
-- -----------------------------------------------------------------------------
--
-- Partial on is_deleted = false so a retired row never blocks the same account
-- coming back. load_client_bank() matches on the same predicate.
--
-- account_key <> '' keeps a row with no usable account number out of the index
-- entirely, rather than letting several such rows collide on ''.

CREATE UNIQUE INDEX IF NOT EXISTS uq_client_bank_natural
    ON gold.client_bank (client_id, account_key)
    WHERE is_deleted = false AND account_key <> '';

COMMIT;

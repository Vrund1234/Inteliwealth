-- =====================================================================
-- DRO / DIVIDEND_TRANSFER defect -- gold.transactions
-- =====================================================================
--
-- WHAT IS WRONG
--
--   classify_transaction_row() in python_scripts/etl_gold_transaction.py
--   checks the exact-code map first:
--
--     :835   if code in TRANSACTION_CODE_MAP:
--     :836       return TRANSACTION_CODE_MAP[code]
--
--   and "DRO" is a key in that map (:504), returning
--   ("DIVIDEND", "DIVIDEND_SWEEP_OUT", OUT).
--
--   The DRO description block that is supposed to separate a dividend
--   TRANSFER from a dividend SWEEP sits 130 lines later:
--
--     :967   if code == "DRO":
--     :969       if ("sweep out" in nature or "transferout" in nature
--                    or "transfer out" in nature
--                    or "paid & transferred" in nature
--                    or "reinvested in other scheme" in nature):
--     :977           return ("DIVIDEND", "DIVIDEND_TRANSFER", OUT)
--     :982       return ("DIVIDEND", "DIVIDEND_SWEEP_OUT", OUT)
--
--   It is unreachable. DIVIDEND_TRANSFER is emitted nowhere in the
--   table (Q3 below proves it), and every DRO row falls through to the
--   map's DIVIDEND_SWEEP_OUT.
--
--   transaction_direction is OUT on both paths, so DIRECTION IS NOT
--   AFFECTED -- this is a transaction_sub_type defect only, and it does
--   not touch the "direction follows transaction type" rule.
--
-- SECOND HALF OF THE DEFECT
--
--   The block's own conditions overlap its own fallback: "sweep out" is
--   listed in the TRANSFER branch, so even after making the block
--   reachable, the 13 "IDCW Sweep Out -" rows would be labelled
--   DIVIDEND_TRANSFER and the DIVIDEND_SWEEP_OUT fallback beneath it
--   would still be dead code.
--
--   The expected_sub_type below encodes the INTENDED split -- sweep
--   wording stays DIVIDEND_SWEEP_OUT, transfer wording becomes
--   DIVIDEND_TRANSFER. Fixing the ETL therefore needs BOTH:
--     1. remove "DRO" from TRANSACTION_CODE_MAP so :967 is reached, and
--     2. drop "sweep out" from the TRANSFER branch at :969 so it falls
--        through to the DIVIDEND_SWEEP_OUT fallback.
--   Doing only (1) relabels all 62 rows instead of 49.
--
-- SCOPE: 62 rows, all rta = 'CAMS', 4,637,816.11 total amount.
-- =====================================================================


-- =====================================================================
-- Q1. SUMMARY -- what each DRO description currently gets vs should get
-- =====================================================================

SELECT
    txn_desc,
    transaction_sub_type AS current_sub_type,
    CASE
        WHEN txn_desc ILIKE '%transferout%'
          OR txn_desc ILIKE '%transfer out%'
          OR txn_desc ILIKE '%paid & transferred%'
          OR txn_desc ILIKE '%reinvested in other scheme%'
            THEN 'DIVIDEND_TRANSFER'
        WHEN txn_desc ILIKE '%sweep out%'
            THEN 'DIVIDEND_SWEEP_OUT'
        ELSE 'DIVIDEND_SWEEP_OUT'
    END AS expected_sub_type,
    transaction_direction,
    COUNT(*)               AS n,
    SUM(units)             AS total_units,
    SUM(amount)            AS total_amount,
    MIN(txn_date)          AS first_txn_date,
    MAX(txn_date)          AS last_txn_date
FROM gold.transactions
WHERE txn_type_raw = 'DRO'
GROUP BY 1, 2, 3, 4
ORDER BY n DESC;


-- =====================================================================
-- Q2. FULL ROW-LEVEL LISTING -- all 62 rows.
--     is_wrong = true on the 49 that need relabelling.
-- =====================================================================

WITH dro AS (
    SELECT
        g.*,
        CASE
            WHEN g.txn_desc ILIKE '%transferout%'
              OR g.txn_desc ILIKE '%transfer out%'
              OR g.txn_desc ILIKE '%paid & transferred%'
              OR g.txn_desc ILIKE '%reinvested in other scheme%'
                THEN 'DIVIDEND_TRANSFER'
            ELSE 'DIVIDEND_SWEEP_OUT'
        END AS expected_sub_type
    FROM gold.transactions g
    WHERE g.txn_type_raw = 'DRO'
)
SELECT
    rta,
    rta_txn_no,
    folio_number,
    pan,
    txn_date,
    post_date,
    scheme_code,
    isin,
    txn_type,
    txn_type_raw,
    txn_desc,
    units,
    amount,
    nav,
    status,
    transaction_sub_type AS current_sub_type,
    expected_sub_type,
    (transaction_sub_type <> expected_sub_type) AS is_wrong,
    transaction_direction,
    client_id,
    scheme_id
FROM dro
ORDER BY is_wrong DESC, expected_sub_type, txn_date, rta_txn_no;


-- =====================================================================
-- Q3. PROOF the branch is dead.
--     dividend_transfer_rows = 0 today; should be 49 after the fix.
-- =====================================================================

SELECT
    COUNT(*) FILTER (WHERE transaction_sub_type = 'DIVIDEND_TRANSFER')
        AS dividend_transfer_rows,
    COUNT(*) FILTER (WHERE txn_type_raw = 'DRO')
        AS dro_rows,
    COUNT(*) FILTER (
        WHERE txn_type_raw = 'DRO'
          AND (txn_desc ILIKE '%transferout%'
            OR txn_desc ILIKE '%transfer out%'
            OR txn_desc ILIKE '%paid & transferred%'
            OR txn_desc ILIKE '%reinvested in other scheme%')
    ) AS dro_rows_that_should_be_transfer
FROM gold.transactions;


-- =====================================================================
-- Q4. RE-RUN THIS AFTER THE FIX -- all three must come back 0.
-- =====================================================================

SELECT 'dro_still_all_sweep_out' AS check_name, COUNT(*) AS bad_rows
FROM gold.transactions
WHERE txn_type_raw = 'DRO'
  AND transaction_sub_type = 'DIVIDEND_SWEEP_OUT'
  AND (txn_desc ILIKE '%transferout%'
    OR txn_desc ILIKE '%transfer out%'
    OR txn_desc ILIKE '%paid & transferred%'
    OR txn_desc ILIKE '%reinvested in other scheme%')

UNION ALL

SELECT 'sweep_wording_wrongly_moved_to_transfer', COUNT(*)
FROM gold.transactions
WHERE txn_type_raw = 'DRO'
  AND transaction_sub_type = 'DIVIDEND_TRANSFER'
  AND txn_desc ILIKE '%sweep out%'
  AND txn_desc NOT ILIKE '%transferout%'
  AND txn_desc NOT ILIKE '%transfer out%'
  AND txn_desc NOT ILIKE '%paid & transferred%'
  AND txn_desc NOT ILIKE '%reinvested in other scheme%'

UNION ALL

SELECT 'dro_direction_changed', COUNT(*)
FROM gold.transactions
WHERE txn_type_raw = 'DRO'
  AND transaction_direction <> 'OUT';


-- =====================================================================
-- Q5. OPTIONAL IN-PLACE PATCH -- commented out on purpose.
--
--     The real fix is in etl_gold_transaction.py (both changes listed
--     at the top), followed by:
--
--         cd python_scripts && venv/bin/python etl_gold_transaction.py --full
--
--     load_transactions upserts on
--     (rta, rta_txn_no, folio_number, amount, units) with DO UPDATE, so
--     the full re-run corrects these rows in place without duplicating.
--
--     If you patch the data directly WITHOUT the code change, the next
--     --full run silently reverts it. Uncomment only as a stopgap.
-- =====================================================================

-- BEGIN;
--
-- UPDATE gold.transactions
-- SET transaction_sub_type = 'DIVIDEND_TRANSFER'
-- WHERE txn_type_raw = 'DRO'
--   AND transaction_sub_type = 'DIVIDEND_SWEEP_OUT'
--   AND (txn_desc ILIKE '%transferout%'
--     OR txn_desc ILIKE '%transfer out%'
--     OR txn_desc ILIKE '%paid & transferred%'
--     OR txn_desc ILIKE '%reinvested in other scheme%');
-- -- expect: UPDATE 49
--
-- COMMIT;

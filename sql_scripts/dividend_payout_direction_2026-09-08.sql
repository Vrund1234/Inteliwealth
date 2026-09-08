-- =====================================================================
-- DIVIDEND_PAYOUT direction = 'NONE' -- open decision, gold.transactions
-- =====================================================================
--
-- NOT A BUG. The classifier does exactly what it is told: direction is
-- a function of the transaction type, and DIVIDEND_PAYOUT is declared
-- NONE in three places in python_scripts/etl_gold_transaction.py --
--
--   :486   "DIV"  -> ("DIVIDEND", "DIVIDEND_PAYOUT", NONE)   exact map
--   :750   "DP"   -> ("DIVIDEND", "DIVIDEND_PAYOUT", NONE)   prefix map
--   :1164  description fallback "dividend" (non-reinvest)     -> NONE
--
-- THE QUESTION
--
--   Three sub types have an IDENTICAL shape -- zero units, cash leaving
--   the scheme -- but do not agree on direction:
--
--     DIVIDEND_PAYOUT     NONE
--     REFUND              NONE
--     DIVIDEND_SWEEP_OUT  OUT
--
--   Under a type-based direction rule these three should not disagree.
--   Either NONE means "no UNIT movement" (then DIVIDEND_SWEEP_OUT is
--   the odd one out and should become NONE), or direction tracks value
--   leaving the scheme (then DIVIDEND_PAYOUT and REFUND should become
--   OUT). Q1 shows the disagreement; Q4 sizes each option.
--
--   Whichever way it goes, this is a TEAM DECISION on the meaning of
--   the column, not a defect to be fixed silently.
-- =====================================================================


-- =====================================================================
-- Q1. THE INCONSISTENCY -- three types, same shape, different direction
-- =====================================================================

SELECT
    transaction_sub_type,
    transaction_direction,
    COUNT(*)                                   AS n,
    SUM(units)                                 AS total_units,
    COUNT(*) FILTER (WHERE units <> 0)         AS rows_moving_units,
    ROUND(SUM(amount), 2)                      AS total_amount,
    COUNT(*) FILTER (WHERE amount <> 0)        AS rows_moving_cash
FROM gold.transactions
WHERE transaction_sub_type IN (
    'DIVIDEND_PAYOUT',
    'DIVIDEND_SWEEP_OUT',
    'REFUND'
)
GROUP BY 1, 2
ORDER BY total_amount DESC;


-- =====================================================================
-- Q2. WHICH RAW CODES LAND IN DIVIDEND_PAYOUT
--     Confirms the three classifier paths above are the only sources.
-- =====================================================================

SELECT
    rta,
    txn_type_raw,
    txn_desc,
    transaction_direction,
    COUNT(*)              AS n,
    ROUND(SUM(amount), 2) AS total_amount,
    MIN(txn_date)         AS first_txn_date,
    MAX(txn_date)         AS last_txn_date
FROM gold.transactions
WHERE transaction_sub_type = 'DIVIDEND_PAYOUT'
GROUP BY 1, 2, 3, 4
ORDER BY n DESC;


-- =====================================================================
-- Q3. SANITY -- does any DIVIDEND_PAYOUT row actually move units?
--     Expect 0. If this is ever non-zero the NONE reading is unsafe
--     regardless of which convention the team picks.
-- =====================================================================

SELECT
    COUNT(*)                                    AS payout_rows,
    COUNT(*) FILTER (WHERE units <> 0)          AS rows_with_units,
    COUNT(*) FILTER (WHERE units IS NULL)       AS rows_units_null,
    ROUND(SUM(amount), 2)                       AS total_amount,
    ROUND(MIN(amount), 2)                       AS min_amount,
    ROUND(MAX(amount), 2)                       AS max_amount
FROM gold.transactions
WHERE transaction_sub_type = 'DIVIDEND_PAYOUT';


-- =====================================================================
-- Q4. IMPACT OF EACH OPTION -- what moves between direction buckets
-- =====================================================================

-- 4a. Direction totals as they stand today.
SELECT
    'CURRENT' AS scenario,
    transaction_direction,
    COUNT(*)              AS n,
    ROUND(SUM(amount), 2) AS total_amount
FROM gold.transactions
GROUP BY 1, 2
ORDER BY 2;


-- 4b. OPTION A -- payout/refund become OUT (direction = value leaving).
--     Shows what the buckets would look like. Read-only, changes nothing.
SELECT
    'OPTION_A_payout_refund_to_OUT' AS scenario,
    CASE
        WHEN transaction_sub_type IN ('DIVIDEND_PAYOUT', 'REFUND')
            THEN 'OUT'
        ELSE transaction_direction
    END AS transaction_direction,
    COUNT(*)              AS n,
    ROUND(SUM(amount), 2) AS total_amount
FROM gold.transactions
GROUP BY 1, 2
ORDER BY 2;


-- 4c. OPTION B -- sweep-out becomes NONE (direction = unit movement).
SELECT
    'OPTION_B_sweep_out_to_NONE' AS scenario,
    CASE
        WHEN transaction_sub_type = 'DIVIDEND_SWEEP_OUT'
            THEN 'NONE'
        ELSE transaction_direction
    END AS transaction_direction,
    COUNT(*)              AS n,
    ROUND(SUM(amount), 2) AS total_amount
FROM gold.transactions
GROUP BY 1, 2
ORDER BY 2;


-- =====================================================================
-- Q5. THE WIDER TEST -- every sub type that moves cash but not units,
--     so the team can settle the convention once for all of them
--     rather than only for dividends.
-- =====================================================================

SELECT
    transaction_sub_type,
    transaction_direction,
    COUNT(*)              AS n,
    ROUND(SUM(amount), 2) AS total_amount
FROM gold.transactions
GROUP BY 1, 2
HAVING COUNT(*) FILTER (WHERE units <> 0) = 0
   AND COUNT(*) FILTER (WHERE amount <> 0) > 0
ORDER BY total_amount DESC;


-- =====================================================================
-- Q6. IF THE TEAM PICKS OPTION A -- the code change, not a data patch.
--
--     python_scripts/etl_gold_transaction.py, change NONE -> OUT at:
--       :486   "DIV" in TRANSACTION_CODE_MAP
--       :750   "DP"  in TRANSACTION_CODE_PREFIX_MAP
--       :1164  the description fallback for a non-reinvest dividend
--     and decide the same for "RFD" -> REFUND at :540.
--
--     Then re-run:
--       cd python_scripts && venv/bin/python etl_gold_transaction.py --full
--
--     Do NOT patch gold.transactions directly -- load_transactions
--     upserts with DO UPDATE and refreshes transaction_direction from
--     the classifier, so the next --full run reverts any manual UPDATE.
--
--     Guard to run afterwards: must return 0.
-- =====================================================================

SELECT 'payout_still_none' AS check_name, COUNT(*) AS rows_left
FROM gold.transactions
WHERE transaction_sub_type IN ('DIVIDEND_PAYOUT', 'REFUND')
  AND transaction_direction = 'NONE';

-- =====================================================================
-- Review queries for gold.transactions.transaction_sub_type /
-- transaction_direction (columns added + backfilled 2026-09-07).
--
-- Q1 is a CONFIRMED defect. Q2-Q5 are definitional calls to review.
-- gold.transactions.txn_desc is a straight copy of
-- silver.transaction_master_new.trxn_nature, so every query below runs
-- against gold alone -- no join needed.
-- =====================================================================


-- =====================================================================
-- Q1. CONFIRMED WRONG -- DRO rows that should be DIVIDEND_TRANSFER
--
-- classify_transaction_row() returns from TRANSACTION_CODE_MAP at
-- etl_gold_transaction.py:835 before reaching the DRO description block
-- at :967, and "DRO" is a key in that map. The block is unreachable, so
-- DIVIDEND_TRANSFER is never emitted anywhere in the table and these
-- rows fall through to the map's default DIVIDEND_SWEEP_OUT.
--
-- transaction_direction is OUT either way -- only the sub type is wrong.
-- Expect 62 rows, i.e. EVERY DRO row in the table. That is the second
-- half of the defect: the transfer branch's condition list includes
-- "sweep out", so if the block were reachable it would also swallow the
-- 13 "IDCW Sweep Out -" rows and the DIVIDEND_SWEEP_OUT fallback under
-- it would itself be dead. Decide which of the two labels those 13 rows
-- should carry before re-running the reclassify -- most likely "sweep
-- out" belongs in the fallback, not in the transfer branch.
-- =====================================================================

SELECT
    txn_type_raw,
    txn_desc,
    transaction_sub_type          AS current_sub_type,
    'DIVIDEND_TRANSFER'           AS expected_sub_type,
    transaction_direction,
    COUNT(*)                      AS n,
    SUM(amount)                   AS total_amount
FROM gold.transactions
WHERE txn_type_raw = 'DRO'
  AND (
        txn_desc ILIKE '%sweep out%'
     OR txn_desc ILIKE '%transferout%'
     OR txn_desc ILIKE '%transfer out%'
     OR txn_desc ILIKE '%paid & transferred%'
     OR txn_desc ILIKE '%reinvested in other scheme%'
      )
  AND transaction_sub_type <> 'DIVIDEND_TRANSFER'
GROUP BY 1, 2, 3, 4, 5
ORDER BY n DESC;


-- ---------------------------------------------------------------------
-- Q1b. Every DRO row broken out by description -- 47 "reinvested in
--      other scheme(s)", 13 "IDCW Sweep Out -", 2 "TransferOut".
-- ---------------------------------------------------------------------

SELECT
    txn_desc,
    transaction_sub_type,
    transaction_direction,
    COUNT(*)    AS n,
    SUM(amount) AS total_amount
FROM gold.transactions
WHERE txn_type_raw = 'DRO'
GROUP BY 1, 2, 3
ORDER BY n DESC;


-- ---------------------------------------------------------------------
-- Q1c. Proof the branch is dead: this returns 0 rows today, and should
--      return up to 62 after the fix, depending on Q1's decision.
-- ---------------------------------------------------------------------

SELECT COUNT(*) AS dividend_transfer_rows
FROM gold.transactions
WHERE transaction_sub_type = 'DIVIDEND_TRANSFER';


-- =====================================================================
-- Q2. REVIEW -- direction = 'NONE' on rows that DO move units
--
-- 'NONE' currently carries two opposite meanings. PLEDGE / UNPLEDGE are
-- correct (a lien does not change holdings). The *_REJECTION rows are
-- not: they reverse an earlier credit, so a balance computed as
-- SUM(IN) - SUM(OUT) silently skips them.
-- =====================================================================

SELECT
    transaction_sub_type,
    COUNT(*)    AS n,
    SUM(units)  AS total_units,
    SUM(amount) AS total_amount,
    CASE
        WHEN transaction_sub_type IN ('PLEDGE', 'UNPLEDGE')
            THEN 'OK - no holdings impact'
        ELSE 'REVIEW - reverses units but excluded from IN/OUT'
    END AS assessment
FROM gold.transactions
WHERE transaction_direction = 'NONE'
  AND units IS NOT NULL
  AND units <> 0
GROUP BY 1
ORDER BY n DESC;


-- ---------------------------------------------------------------------
-- Q2b. The individual rejection rows behind Q2.
-- ---------------------------------------------------------------------

SELECT
    rta,
    rta_txn_no,
    folio_number,
    txn_date,
    txn_type_raw,
    transaction_sub_type,
    transaction_direction,
    units,
    amount,
    txn_desc
FROM gold.transactions
WHERE transaction_direction = 'NONE'
  AND units < 0
ORDER BY transaction_sub_type, txn_date;


-- =====================================================================
-- Q3. REVIEW -- reversal rows keep the ORIGINAL transaction's direction
--
-- These carry negative units AND negative amount but are still tagged
-- IN / OUT (e.g. SIP rows with negative units). Self-consistent only if
-- consumers multiply signed units by direction; SUM(ABS(units)) grouped
-- by direction will be wrong. Expect ~6,745 rows, all rta = 'CAMS'.
-- =====================================================================

SELECT
    rta,
    transaction_sub_type,
    transaction_direction,
    COUNT(*)    AS n,
    SUM(units)  AS total_units,
    SUM(amount) AS total_amount
FROM gold.transactions
WHERE units < 0
  AND transaction_direction <> 'NONE'
GROUP BY 1, 2, 3
ORDER BY n DESC;


-- =====================================================================
-- Q4. REVIEW -- money moves but direction is 'NONE'
--
-- Correct if transaction_direction means "unit movement". Wrong if any
-- report reads it as cash-flow direction: DIVIDEND_PAYOUT alone is
-- ~32.8 crore that no direction-based cash flow will ever see.
-- =====================================================================

SELECT
    transaction_sub_type,
    COUNT(*)    AS n,
    SUM(amount) AS total_amount,
    SUM(units)  AS total_units
FROM gold.transactions
WHERE transaction_direction = 'NONE'
  AND amount IS NOT NULL
  AND amount <> 0
GROUP BY 1
ORDER BY total_amount DESC;


-- =====================================================================
-- Q5. FULL AUDIT -- every raw code -> sub type / direction, with a
--     sample description, for eyeballing the mapping end to end.
--     818 rows. Add the WHERE clause to narrow to one family.
-- =====================================================================

SELECT
    txn_type_raw,
    txn_type,
    transaction_sub_type,
    transaction_direction,
    COUNT(*)                      AS n,
    MIN(txn_desc)                 AS sample_desc_a,
    MAX(txn_desc)                 AS sample_desc_b
FROM gold.transactions
-- WHERE transaction_sub_type LIKE 'DIVIDEND%'
GROUP BY 1, 2, 3, 4
ORDER BY n DESC;


-- =====================================================================
-- Q6. GUARD -- these must all return 0 rows. Run after any reclassify.
-- =====================================================================

-- 6a. Unpopulated / unclassified
SELECT 'unpopulated' AS check_name, COUNT(*) AS bad_rows
FROM gold.transactions
WHERE transaction_sub_type IS NULL
   OR transaction_sub_type = ''
   OR transaction_sub_type = 'UNMAPPED'
   OR transaction_direction IS NULL
   OR transaction_direction = ''

UNION ALL

-- 6b. Direction outside the allowed domain
SELECT 'bad_direction_value', COUNT(*)
FROM gold.transactions
WHERE transaction_direction NOT IN ('IN', 'OUT', 'NONE')

UNION ALL

-- 6c. One raw code resolving to more than one direction
SELECT 'code_with_split_direction', COUNT(*)
FROM (
    SELECT txn_type_raw
    FROM gold.transactions
    GROUP BY 1
    HAVING COUNT(DISTINCT transaction_direction) > 1
) x

UNION ALL

-- 6d. sub type and direction disagreeing on _IN / _OUT
SELECT 'sub_type_vs_direction_mismatch', COUNT(*)
FROM gold.transactions
WHERE (transaction_sub_type LIKE '%\_IN'  AND transaction_direction <> 'IN')
   OR (transaction_sub_type LIKE '%\_OUT' AND transaction_direction <> 'OUT')

UNION ALL

-- 6e. txn_type and direction disagreeing
SELECT 'txn_type_vs_direction_mismatch', COUNT(*)
FROM gold.transactions
WHERE (txn_type = 'PURCHASE'   AND transaction_direction <> 'IN')
   OR (txn_type = 'REDEMPTION' AND transaction_direction <> 'OUT')
   OR (txn_type = 'OTHER'      AND transaction_direction <> 'NONE');

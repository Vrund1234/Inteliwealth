-- =============================================================================
-- Duplicate address detection for gold.client_address
-- =============================================================================
--
-- gold.client_address is meant to hold DISTINCT addresses per client, but the
-- dedup key in etl_gold_client_address.py:559-573 (and again at :693-703)
-- compares raw strings, so the same physical address arriving from two RTA
-- feeds survives as two rows whenever they differ by:
--
--   * mobile format      +919824013413  vs  9824013413
--   * punctuation        B/102          vs  B 102
--   * trailing commas    "DESAI VAGO,"  vs  "DESAI VAGO"
--   * NULL vs filled     state / country
--
-- These queries collapse those variants by stripping every non-alphanumeric
-- character from the address lines and case-folding, then grouping per client.
-- state / country / mobile_no are deliberately EXCLUDED from the key: they are
-- the fields that vary most between feeds and are derivable from city+pincode.
--
-- NOTE: this key is conservative. A city recorded as 'USA' in one feed and
-- 'USA, USA' in another still reads as two distinct addresses, so the real
-- duplicate count is a little higher than what Q1 reports.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- Q1. Summary: how bad is it?
-- -----------------------------------------------------------------------------
WITH normalised AS (
    SELECT
        a.id,
        a.client_id,
        UPPER(REGEXP_REPLACE(
            COALESCE(a.line1,'') || COALESCE(a.line2,'') || COALESCE(a.line3,'') ||
            COALESCE(a.area,'')  || COALESCE(a.city,'')  || COALESCE(a.pincode,''),
            '[^A-Za-z0-9]', '', 'g'
        )) AS address_key
    FROM gold.client_address a
    WHERE a.is_deleted = false
),
groups AS (
    SELECT client_id, address_key, COUNT(*) AS row_count
    FROM normalised
    WHERE address_key <> ''
    GROUP BY client_id, address_key
    HAVING COUNT(*) > 1
)
SELECT
    (SELECT COUNT(*) FROM normalised)                        AS total_rows,
    (SELECT COUNT(*) FROM groups)                            AS duplicate_groups,
    (SELECT COALESCE(SUM(row_count - 1), 0) FROM groups)     AS redundant_rows,
    (SELECT COUNT(*) FROM normalised)
      - (SELECT COALESCE(SUM(row_count - 1), 0) FROM groups) AS rows_after_dedup;


-- -----------------------------------------------------------------------------
-- Q2. Detail: every duplicate row, grouped, so you can eyeball what differs
-- -----------------------------------------------------------------------------
WITH normalised AS (
    SELECT
        a.*,
        UPPER(REGEXP_REPLACE(
            COALESCE(a.line1,'') || COALESCE(a.line2,'') || COALESCE(a.line3,'') ||
            COALESCE(a.area,'')  || COALESCE(a.city,'')  || COALESCE(a.pincode,''),
            '[^A-Za-z0-9]', '', 'g'
        )) AS address_key
    FROM gold.client_address a
    WHERE a.is_deleted = false
),
dupes AS (
    SELECT client_id, address_key
    FROM normalised
    WHERE address_key <> ''
    GROUP BY client_id, address_key
    HAVING COUNT(*) > 1
)
SELECT
    c.pan,
    n.client_id,
    DENSE_RANK() OVER (ORDER BY n.client_id, n.address_key) AS dup_group,
    n.id,
    n.seq,
    n.is_main,
    n.line1,
    n.line2,
    n.city,
    n.state,
    n.country,
    n.pincode,
    n.mobile_no
FROM normalised n
JOIN dupes d
  ON d.client_id = n.client_id
 AND d.address_key = n.address_key
LEFT JOIN gold.clients c ON c.id = n.client_id
ORDER BY dup_group, n.seq;


-- -----------------------------------------------------------------------------
-- Q3. Worst offenders: clients carrying the most redundant address rows
-- -----------------------------------------------------------------------------
WITH normalised AS (
    SELECT
        a.client_id,
        UPPER(REGEXP_REPLACE(
            COALESCE(a.line1,'') || COALESCE(a.line2,'') || COALESCE(a.line3,'') ||
            COALESCE(a.area,'')  || COALESCE(a.city,'')  || COALESCE(a.pincode,''),
            '[^A-Za-z0-9]', '', 'g'
        )) AS address_key
    FROM gold.client_address a
    WHERE a.is_deleted = false
)
SELECT
    c.pan,
    n.client_id,
    COUNT(*)                                 AS address_rows,
    COUNT(DISTINCT n.address_key)            AS distinct_addresses,
    COUNT(*) - COUNT(DISTINCT n.address_key) AS redundant_rows
FROM normalised n
LEFT JOIN gold.clients c ON c.id = n.client_id
GROUP BY c.pan, n.client_id
HAVING COUNT(*) > COUNT(DISTINCT n.address_key)
ORDER BY redundant_rows DESC, address_rows DESC;

-- ============================================================
-- Record counts -- 25_08_2025_intelliwealth_layer_db
-- Run after any restore / reload to confirm what landed.
-- ============================================================


-- ------------------------------------------------------------
-- 1. EVERY table in the medallion schemas, exact counts.
--    Dynamic: picks up new tables automatically, no edits needed.
-- ------------------------------------------------------------

SELECT
    t.schemaname                                   AS schema,
    t.tablename                                    AS table,
    (xpath(
        '/row/c/text()',
        query_to_xml(
            format('SELECT count(*) AS c FROM %I.%I', t.schemaname, t.tablename),
            false, true, ''
        )
    ))[1]::text::bigint                            AS rows,
    pg_size_pretty(
        pg_total_relation_size(
            format('%I.%I', t.schemaname, t.tablename)::regclass
        )
    )                                              AS size
FROM pg_tables t
WHERE t.schemaname IN ('bronze', 'silver', 'gold', 'pipeline')
  AND t.tablename NOT LIKE '\_test%'
ORDER BY t.schemaname, t.tablename;


-- ------------------------------------------------------------
-- 2. The tables that carry the load, checked against what the
--    six source files in old-files actually contain.
--
--    Expected values come from a full parse of those files:
--        CAMS R2   90,536 txns    KFIN MFSD201  38,230 txns
--        CAMS R9    2,098 inv     KFIN MFSD211   1,444 inv
--        CAMS R49     738 sip     KFIN MFSD243     658 sip
--
--    silver/gold transactions sit 1,512 BELOW bronze until
--    fix_dedup_constraints_2026-09-01.sql is applied -- that is
--    the known KFIN redemption-lot collapse, not a bad restore.
-- ------------------------------------------------------------

WITH actual (tbl, rows) AS (
    SELECT 'bronze.transaction_master_new', count(*) FROM bronze.transaction_master_new
    UNION ALL SELECT 'bronze.investor_master',        count(*) FROM bronze.investor_master
    UNION ALL SELECT 'bronze.sip_master_new',         count(*) FROM bronze.sip_master_new
    UNION ALL SELECT 'silver.transaction_master_new', count(*) FROM silver.transaction_master_new
    UNION ALL SELECT 'silver.investor_master',        count(*) FROM silver.investor_master
    UNION ALL SELECT 'silver.sip_master_new',         count(*) FROM silver.sip_master_new
    UNION ALL SELECT 'gold.transactions',             count(*) FROM gold.transactions
    UNION ALL SELECT 'gold.clients',                  count(*) FROM gold.clients
    UNION ALL SELECT 'gold.holdings',                 count(*) FROM gold.holdings
    UNION ALL SELECT 'gold.sip',                      count(*) FROM gold.sip
    UNION ALL SELECT 'gold.scheme',                   count(*) FROM gold.scheme
    UNION ALL SELECT 'gold.scheme_nav',               count(*) FROM gold.scheme_nav
    UNION ALL SELECT 'gold.amc',                      count(*) FROM gold.amc
    UNION ALL SELECT 'gold.folio_nominees',           count(*) FROM gold.folio_nominees
),
expected (tbl, rows) AS (
    VALUES ('bronze.transaction_master_new', 128766),
           ('bronze.investor_master',          3542),
           ('bronze.sip_master_new',           1396),
           ('silver.transaction_master_new', 127254),
           ('silver.investor_master',          3542),
           ('silver.sip_master_new',           1392),
           ('gold.transactions',             127254),
           ('gold.clients',                     593),
           ('gold.holdings',                   1875),
           ('gold.sip',                        1392),
           ('gold.scheme',                      515),
           ('gold.scheme_nav',                51061),
           ('gold.amc',                          29),
           ('gold.folio_nominees',             1699)
)
SELECT
    a.tbl                        AS table,
    a.rows                       AS actual,
    e.rows                       AS expected,
    a.rows - e.rows              AS diff,
    CASE
        WHEN a.rows = e.rows     THEN 'OK'
        WHEN a.rows > e.rows     THEN 'TOO MANY -- likely a repeated load'
        ELSE                          'TOO FEW -- rows missing'
    END                          AS verdict
FROM actual a
JOIN expected e USING (tbl)
ORDER BY (a.rows = e.rows), a.tbl;

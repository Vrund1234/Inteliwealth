"""Post-migration state check: every table from the dedup-constraints spec
has its unique constraint, and re-running the same duplicate census the
spec was built from comes back zero everywhere."""

from sqlalchemy import text

from utils.db import engine

EXPECTED_CONSTRAINTS = {
    "silver.transaction_master_new": "uq_silver_txn_natural_key",
    "silver.investor_master": "uq_silver_investor_natural_key",
    "gold.transactions": "uq_gold_txn_natural_key",
    "gold.holdings": "uq_gold_holdings",
    "gold.clients": "uq_gold_clients_pan",
    "gold.scheme": "uq_gold_scheme",
    "gold.scheme_nav": "uq_gold_scheme_nav",
    "gold.amc": "uq_gold_amc",
    "gold.folio_nominees": "uq_gold_folio_nominees",
}

# These two are deliberately NOT in EXPECTED_CONSTRAINTS: Postgres cannot
# promote an expression-based unique index (both are built on a
# COALESCE(...)-normalized SIP reg-no) to a table CONSTRAINT at all
# ("Cannot create a primary key or unique constraint using such an index" --
# confirmed live). They exist only as standalone unique INDEXes, which
# enforce uniqueness on their own -- verified live with a rollback-tested
# duplicate insert that correctly raised a "duplicate key value violates
# unique constraint" error even with no CONSTRAINT catalog entry backing it.
EXPECTED_EXPRESSION_INDEXES = {
    "silver.sip_master_new": "uq_silver_sip_natural_key",
    "gold.sip": "uq_gold_sip_natural_key",
}


def test_every_expected_constraint_exists():
    with engine.begin() as conn:
        existing = {
            row[0]
            for row in conn.execute(text(
                "SELECT conname FROM pg_constraint WHERE contype = 'u'"
            ))
        }
    missing = [name for name in EXPECTED_CONSTRAINTS.values() if name not in existing]
    assert not missing, f"missing constraints: {missing}"


def test_every_expected_expression_index_exists_and_is_valid():
    with engine.begin() as conn:
        rows = conn.execute(text("""
            SELECT c.relname, i.indisvalid
            FROM pg_class c
            JOIN pg_index i ON i.indexrelid = c.oid
            WHERE c.relname = ANY(:names)
        """), {"names": list(EXPECTED_EXPRESSION_INDEXES.values())}).fetchall()
    found = {r[0]: r[1] for r in rows}
    missing = [name for name in EXPECTED_EXPRESSION_INDEXES.values() if name not in found]
    invalid = [name for name, valid in found.items() if not valid]
    assert not missing, f"missing expression indexes: {missing}"
    assert not invalid, f"invalid expression indexes: {invalid}"


def test_no_nulls_not_distinct_gap():
    """Every constraint/index above must be backed by NULLS NOT DISTINCT --
    a plain unique index would silently let multiple all-NULL-key rows
    through."""
    all_names = list(EXPECTED_CONSTRAINTS.values()) + list(EXPECTED_EXPRESSION_INDEXES.values())
    with engine.begin() as conn:
        rows = conn.execute(text("""
            SELECT c.relname, i.indnullsnotdistinct
            FROM pg_class c
            JOIN pg_index i ON i.indexrelid = c.oid
            WHERE c.relname = ANY(:names)
        """), {"names": all_names}).fetchall()
    not_nulls_safe = [r[0] for r in rows if not r[1]]
    assert not not_nulls_safe, f"indexes missing NULLS NOT DISTINCT: {not_nulls_safe}"


def test_zero_duplicates_remain_anywhere():
    """Re-run the exact duplicate census the spec was built from -- every
    number here must be 0 now."""
    with engine.begin() as conn:
        rows = conn.execute(text("""
            SELECT 'silver.transaction_master_new' AS tbl,
                   COUNT(*) - COUNT(DISTINCT (source, trxnno, folio_no, amount, units)) AS dup_count
            FROM silver.transaction_master_new
            UNION ALL
            SELECT 'silver.investor_master',
                   COUNT(*) - COUNT(DISTINCT (source, folio_no, product_code))
            FROM silver.investor_master
            UNION ALL
            SELECT 'silver.sip_master_new',
                   COUNT(*) - COUNT(DISTINCT (
                       source, folio_no, scheme_code, reg_date, auto_amount,
                       COALESCE(NULLIF(NULLIF(BTRIM(ft_sip_regno), ''), '0'), NULLIF(BTRIM(request_ref_no), ''), '')
                   ))
            FROM silver.sip_master_new
            UNION ALL
            SELECT 'gold.transactions',
                   COUNT(*) - COUNT(DISTINCT (rta, rta_txn_no, folio_number, amount, units))
            FROM gold.transactions
            UNION ALL
            SELECT 'gold.sip',
                   COUNT(*) - COUNT(DISTINCT (
                       rta, folio_number, scheme_code, registered_date, amount,
                       COALESCE(NULLIF(sip_reg_no, ''), '')
                   ))
            FROM gold.sip
            UNION ALL
            SELECT 'gold.holdings', COUNT(*) - COUNT(DISTINCT (rta, pan, folio_number, scheme_id)) FROM gold.holdings
            UNION ALL
            SELECT 'gold.clients', COUNT(*) - COUNT(DISTINCT (pan)) FROM gold.clients
            UNION ALL
            SELECT 'gold.scheme', COUNT(*) - COUNT(DISTINCT (rta, scheme_code)) FROM gold.scheme
            UNION ALL
            SELECT 'gold.scheme_nav', COUNT(*) - COUNT(DISTINCT (scheme_id, nav_date)) FROM gold.scheme_nav
            UNION ALL
            SELECT 'gold.amc', COUNT(*) - COUNT(DISTINCT (rta, amc_code)) FROM gold.amc
            UNION ALL
            SELECT 'gold.folio_nominees', COUNT(*) - COUNT(DISTINCT (holding_id, seq)) FROM gold.folio_nominees
        """)).fetchall()
    nonzero = {r[0]: r[1] for r in rows if r[1] != 0}
    assert not nonzero, f"duplicates remain: {nonzero}"

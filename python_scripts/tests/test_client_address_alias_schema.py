"""gold.client_address_alias is what makes an approved merge survive a re-run.
The ETL rewrites keys through it on every pass, so the shape it relies on --
the key it looks up by, and the guards that keep the table sane -- is pinned
here rather than left to whoever last ran a migration.
"""

import pytest


def _catalog(query, **params):
    try:
        from sqlalchemy import text

        from utils.db import engine
    except Exception:
        pytest.skip("database not configured")

    try:
        with engine.connect() as conn:
            return conn.execute(text(query), params).fetchall()
    except Exception as exc:  # unreachable database is not a test failure
        if "could not connect" in str(exc).lower() or "connection" in str(exc).lower():
            pytest.skip(f"database unreachable: {exc}")
        raise


def test_alias_table_carries_the_columns_the_etl_reads():
    rows = _catalog(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'gold' AND table_name = 'client_address_alias'
        """
    )
    columns = {r[0] for r in rows}
    assert columns >= {
        "client_id",
        "address_key_alias",
        "address_key_canonical",
        "decision_source",
        "decided_by",
        "decided_at",
        "pan",
    }


def test_one_alias_key_resolves_to_exactly_one_canonical():
    """The rewrite is a lookup by (client_id, address_key). Two rows for one
    alias would make which canonical wins depend on row order."""
    rows = _catalog(
        """
        SELECT pg_get_constraintdef(oid)
        FROM pg_constraint
        WHERE conrelid = 'gold.client_address_alias'::regclass
          AND contype = 'p'
        """
    )
    assert rows, "no primary key on gold.client_address_alias"
    assert "client_id, address_key_alias" in rows[0][0]


def test_an_alias_cannot_point_at_itself():
    """alias = canonical would rewrite a key to itself forever and read as a
    merge that never happened."""
    rows = _catalog(
        """
        SELECT pg_get_constraintdef(oid)
        FROM pg_constraint
        WHERE conrelid = 'gold.client_address_alias'::regclass
          AND contype = 'c'
        """
    )
    assert any("<>" in r[0] for r in rows)


def test_decision_source_records_whether_a_person_approved_it():
    """An unattended merge must be findable afterwards -- it is the one kind
    that can be wrong without anyone having looked."""
    rows = _catalog(
        """
        SELECT pg_get_constraintdef(oid)
        FROM pg_constraint
        WHERE conrelid = 'gold.client_address_alias'::regclass
          AND contype = 'c'
        """
    )
    definitions = " ".join(r[0] for r in rows)
    assert "AUTO" in definitions and "USER" in definitions


def test_review_queue_accepts_the_token_match_type():
    """detect_client_address_duplicates writes TOKEN pairs alongside the
    PREFIX and FUZZY ones it already queued."""
    rows = _catalog(
        """
        SELECT pg_get_constraintdef(oid)
        FROM pg_constraint
        WHERE conrelid = 'gold.client_address_review'::regclass
          AND conname = 'ck_client_address_review_type'
        """
    )
    assert rows, "ck_client_address_review_type is missing"
    assert "TOKEN" in rows[0][0]

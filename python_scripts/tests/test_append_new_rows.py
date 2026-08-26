"""append_new_rows(): Bronze -> Silver loader for investor_master,
transaction_master_new, and sip_master_new.

Duplicate handling must be delegated entirely to upsert_dataframe's
INSERT ... ON CONFLICT ... DO UPDATE against each table's real unique
natural-key index -- append_new_rows() must NOT first read
`SELECT * FROM silver.<table>` and diff it against the incoming batch in
Python. That full-table read was an O(entire silver history) bottleneck,
the same class of problem already fixed on the Bronze side (see
docs/superpowers/plans/2026-08-26-bronze-dedup-performance.md): the
unique constraint + upsert already guarantees no duplicate row can ever
exist, so the pre-check bought nothing but an unbounded-growth read on
every single run.
"""

import uuid

import pandas as pd
import pytest
from sqlalchemy import text

from utils.db import engine
from transformations.transform import append_new_rows

SENTINEL = "__TEST_ANR__"  # <= 20 chars: silver.sip_master_new.source is varchar(20)

SILVER_TABLES = ("transaction_master_new", "investor_master", "sip_master_new")


@pytest.fixture(autouse=True)
def cleanup():
    def _delete():
        with engine.begin() as conn:
            for table in SILVER_TABLES:
                conn.execute(
                    text(f"DELETE FROM silver.{table} WHERE source = :s"),
                    {"s": SENTINEL},
                )

    _delete()
    yield
    _delete()


def _txn_row(**overrides):
    row = {
        "source": SENTINEL,
        "flag": 0,
        "trxnno": uuid.uuid4().hex[:10],
        "folio_no": "F1",
        "amount": 100,
        "units": 10,
        "trxnmode": "PHYSICAL",
    }
    row.update(overrides)
    return row


def test_inserts_a_new_row_via_upsert():
    df = pd.DataFrame([_txn_row()])
    trxnno = df.iloc[0]["trxnno"]

    append_new_rows(df, "transaction_master_new")

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT trxnmode FROM silver.transaction_master_new "
                "WHERE source = :s AND trxnno = :t"
            ),
            {"s": SENTINEL, "t": trxnno},
        ).fetchall()

    assert len(rows) == 1
    assert rows[0][0] == "PHYSICAL"


def test_conflicting_natural_key_updates_in_place_not_duplicated():
    trxnno = uuid.uuid4().hex[:10]

    append_new_rows(pd.DataFrame([_txn_row(trxnno=trxnno, trxnmode="PHYSICAL")]), "transaction_master_new")
    append_new_rows(pd.DataFrame([_txn_row(trxnno=trxnno, trxnmode="DEMAT")]), "transaction_master_new")

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT trxnmode, flag FROM silver.transaction_master_new "
                "WHERE source = :s AND trxnno = :t"
            ),
            {"s": SENTINEL, "t": trxnno},
        ).fetchall()

    assert len(rows) == 1  # no duplicate row created
    assert rows[0][0] == "DEMAT"  # value refreshed in place
    assert rows[0][1] == 0  # flag column left alone by the upsert, not nulled


def test_created_at_is_preserved_across_reprocessing():
    trxnno = uuid.uuid4().hex[:10]

    append_new_rows(pd.DataFrame([_txn_row(trxnno=trxnno)]), "transaction_master_new")

    with engine.begin() as conn:
        original_created_at = conn.execute(
            text(
                "SELECT created_at FROM silver.transaction_master_new "
                "WHERE source = :s AND trxnno = :t"
            ),
            {"s": SENTINEL, "t": trxnno},
        ).scalar()

    append_new_rows(pd.DataFrame([_txn_row(trxnno=trxnno, trxnmode="DEMAT")]), "transaction_master_new")

    with engine.begin() as conn:
        created_at = conn.execute(
            text(
                "SELECT created_at FROM silver.transaction_master_new "
                "WHERE source = :s AND trxnno = :t"
            ),
            {"s": SENTINEL, "t": trxnno},
        ).scalar()

    # NOTE: not asserting updated_at > created_at here -- silver's created_at/
    # updated_at are naive `timestamp` columns fed inconsistently (Python-side
    # local-clock Timestamp.now() on insert vs. Postgres-side now() on
    # conflict-update), a pre-existing timezone bug in upsert_dataframe/db.py
    # unrelated to this change. created_at surviving reprocessing is the only
    # contract append_new_rows itself is responsible for.
    assert created_at == original_created_at


def test_does_not_read_the_entire_existing_silver_table(monkeypatch):
    # append_new_rows()'s own `except Exception` around this call would
    # silently swallow an error raised from inside the spy (it did, the
    # first time this test was written -- AssertionError is an Exception
    # too), so this records calls instead and asserts on them afterward,
    # outside append_new_rows()'s reach entirely.
    import transformations.transform as transform_module

    real_read_sql = transform_module.pd.read_sql
    calls = []

    def spy(sql, *args, **kwargs):
        calls.append(str(sql))
        return real_read_sql(sql, *args, **kwargs)

    monkeypatch.setattr(transform_module.pd, "read_sql", spy)

    append_new_rows(pd.DataFrame([_txn_row()]), "transaction_master_new")

    bulk_reads = [
        c for c in calls
        if "silver.transaction_master_new" in c and "information_schema" not in c
    ]
    assert bulk_reads == [], (
        f"append_new_rows must not bulk-read the existing silver table, got: {bulk_reads!r}"
    )


def test_investor_master_insert_via_upsert():
    df = pd.DataFrame([{
        "source": SENTINEL,
        "flag": 0,
        "folio_no": "F1",
        "product_code": "P1",
        "investor_name": "Alice",
    }])

    append_new_rows(df, "investor_master")

    with engine.begin() as conn:
        name = conn.execute(
            text(
                "SELECT investor_name FROM silver.investor_master "
                "WHERE source = :s AND folio_no = 'F1' AND product_code = 'P1'"
            ),
            {"s": SENTINEL},
        ).scalar()

    assert name == "Alice"


def test_sip_master_insert_via_upsert():
    df = pd.DataFrame([{
        "source": SENTINEL,
        "flag": 0,
        "folio_no": "F1",
        "scheme_code": "S1",
        "reg_date": "2026-01-01",
        "auto_amount": 500,
        "ft_sip_regno": "REG1",
        "request_ref_no": None,
        "inv_name": "Alice",
    }])

    append_new_rows(df, "sip_master_new")

    with engine.begin() as conn:
        name = conn.execute(
            text(
                "SELECT inv_name FROM silver.sip_master_new "
                "WHERE source = :s AND folio_no = 'F1'"
            ),
            {"s": SENTINEL},
        ).scalar()

    assert name == "Alice"

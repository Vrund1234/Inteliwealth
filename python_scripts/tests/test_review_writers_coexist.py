"""bronze.scheme_mapping_review has two writers that must not clobber each other.

scheme_mapping.py replaces its own pending candidates on every run by deleting
everything still undecided. map_unmatched_nav_name.py writes into the same
table on its own schedule, so an unqualified delete silently discards a queue
of NAV_NAME_MATCH rows the reviewer had not reached yet — with no error and no
trace, since both writers legitimately produce reviewer_decision IS NULL rows.
"""

import uuid

import pytest
from sqlalchemy import text

from scheme_matching.reference import write_review
from utils.db import engine

SENTINEL_RTA = "__TEST_COEXIST__"


def _insert(rule_name, decision=None):
    row_id = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO bronze.scheme_mapping_review
                    (review_id, rta, rta_scheme_code, rta_scheme_name,
                     candidate_rank, candidate_amfi_code, candidate_amfi_name,
                     candidate_score, rule_name, reviewer_decision)
                VALUES (:id, :rta, :code, 'test scheme', 1, '999999',
                        'test candidate', 100.0, :rule, :decision)
                """
            ),
            {"id": row_id, "rta": SENTINEL_RTA, "code": f"C{row_id[:8]}",
             "rule": rule_name, "decision": decision},
        )
    return row_id


def _exists(row_id):
    with engine.begin() as conn:
        return conn.execute(
            text("SELECT 1 FROM bronze.scheme_mapping_review WHERE review_id = :id"),
            {"id": row_id},
        ).first() is not None


REVIEW_COLUMNS = (
    "review_id, rta, rta_scheme_code, rta_scheme_name, candidate_rank, "
    "candidate_amfi_code, candidate_amfi_name, candidate_score, rule_name, "
    "reviewer_decision, reviewed_by, reviewed_at, created_at"
)


@pytest.fixture(autouse=True)
def preserve_review_queue():
    """Snapshot and restore every pending row in the table.

    These tests exercise the real write_review() against the real database,
    which is the only way to prove the DELETE predicate behaves — but that
    call legitimately deletes the engine's pending candidates. Without this
    fixture, running the suite silently empties a live review queue that only
    a full scheme_mapping.py run can rebuild.
    """
    with engine.begin() as conn:
        saved = [
            dict(r._mapping)
            for r in conn.execute(
                text(f"SELECT {REVIEW_COLUMNS} FROM bronze.scheme_mapping_review")
            )
        ]

    yield

    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM bronze.scheme_mapping_review WHERE rta = :rta"),
            {"rta": SENTINEL_RTA},
        )
        for row in saved:
            conn.execute(
                text(
                    f"INSERT INTO bronze.scheme_mapping_review ({REVIEW_COLUMNS}) "
                    "VALUES (:review_id, :rta, :rta_scheme_code, :rta_scheme_name, "
                    ":candidate_rank, :candidate_amfi_code, :candidate_amfi_name, "
                    ":candidate_score, :rule_name, :reviewer_decision, :reviewed_by, "
                    ":reviewed_at, :created_at) "
                    "ON CONFLICT (review_id) DO NOTHING"
                ),
                row,
            )


def test_a_pending_nav_name_match_row_survives_a_scheme_mapping_run():
    pending = _insert("NAV_NAME_MATCH")

    write_review(engine, [])

    assert _exists(pending), (
        "scheme_mapping.py deleted a NAV_NAME_MATCH row awaiting review"
    )


def test_the_engines_own_pending_rows_are_still_replaced():
    """The delete must keep doing its job for the rows it does own."""
    stale = _insert("STRUCT_EXACT")

    write_review(engine, [])

    assert not _exists(stale)


def test_decided_rows_are_never_touched():
    approved = _insert("STRUCT_EXACT", decision="APPROVED")

    write_review(engine, [])

    assert _exists(approved)

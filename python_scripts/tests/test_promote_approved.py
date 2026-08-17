"""Promotion applies reviewer decisions to bronze.scheme_mapping.

This is the only step in the NAV_NAME_MATCH flow that writes a mapping, so the
guards matter more than the happy path: it must move nothing a reviewer has not
approved, and it must never overwrite a mapping some other rule has since
produced. The queue can sit for days between being written and being approved,
and scheme_mapping.py runs freely in that window.
"""

import uuid

import pytest
from sqlalchemy import text

from promote_approved_mappings import promote_approved
from utils.db import engine

SENTINEL_RTA = "__TEST_PROMOTE__"


def _seed(decision, existing_amfi_code=None, existing_status="UNMATCHED"):
    """One scheme_mapping row plus a review row pointing at AMFI code 999999."""
    code = f"S{uuid.uuid4().hex[:8]}"
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO bronze.scheme_mapping
                    (mapping_id, rta, rta_amc_code, rta_scheme_code,
                     rta_scheme_name, amfi_scheme_code, mapping_status)
                VALUES (gen_random_uuid(), :rta, 'B', :code, 'test scheme',
                        :amfi, :status)
                """
            ),
            {"rta": SENTINEL_RTA, "code": code,
             "amfi": existing_amfi_code, "status": existing_status},
        )
        conn.execute(
            text(
                """
                INSERT INTO bronze.scheme_mapping_review
                    (review_id, rta, rta_scheme_code, rta_scheme_name,
                     candidate_rank, candidate_amfi_code, candidate_amfi_name,
                     candidate_score, rule_name, reviewer_decision)
                VALUES (gen_random_uuid(), :rta, :code, 'test scheme', 1,
                        '999999', 'test candidate', 96.0, 'NAV_NAME_MATCH',
                        :decision)
                """
            ),
            {"rta": SENTINEL_RTA, "code": code, "decision": decision},
        )
    return code


def _mapping(code):
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT amfi_scheme_code, mapping_source, mapping_status, "
                "       mapping_confidence, scheme_id "
                "FROM bronze.scheme_mapping "
                "WHERE rta = :rta AND rta_scheme_code = :code"
            ),
            {"rta": SENTINEL_RTA, "code": code},
        ).first()
    return dict(row._mapping) if row else None


@pytest.fixture(autouse=True)
def cleanup():
    yield
    with engine.begin() as conn:
        for table in ("bronze.scheme_mapping_review", "bronze.scheme_mapping"):
            conn.execute(
                text(f"DELETE FROM {table} WHERE rta = :rta"), {"rta": SENTINEL_RTA}
            )


class TestApprovedRowsArePromoted:
    def test_an_approved_row_maps_the_scheme(self):
        code = _seed("APPROVED")

        promote_approved(engine)

        row = _mapping(code)
        assert row["amfi_scheme_code"] == "999999"
        assert row["mapping_status"] == "MATCHED"
        assert row["mapping_source"] == "NAV_NAME_MATCH"

    def test_the_promoted_row_carries_a_scheme_id(self):
        """scheme_id is amc_code + amfi code; downstream joins on it."""
        code = _seed("APPROVED")

        promote_approved(engine)

        assert _mapping(code)["scheme_id"] == "B999999"


class TestUnapprovedRowsAreLeftAlone:
    def test_a_pending_row_is_not_promoted(self):
        code = _seed(None)

        promote_approved(engine)

        assert _mapping(code)["amfi_scheme_code"] is None

    def test_a_rejected_row_is_not_promoted(self):
        code = _seed("REJECTED")

        promote_approved(engine)

        assert _mapping(code)["amfi_scheme_code"] is None


class TestExistingMappingsAreNeverOverwritten:
    def test_a_scheme_mapped_since_queueing_keeps_its_mapping(self):
        """The approval is stale: another rule resolved this scheme first, at
        higher confidence. Promotion must yield to it rather than clobber it."""
        code = _seed("APPROVED", existing_amfi_code="111111",
                     existing_status="MATCHED")

        result = promote_approved(engine)

        assert _mapping(code)["amfi_scheme_code"] == "111111"
        assert result["skipped_already_mapped"] >= 1


class TestTheEnginesOwnReviewQueue:
    """STRUCT_EXACT rows are the engine's ambiguity queue.

    When two AMFI schemes share a key the engine writes both as candidates and
    maps neither, which is right. But nothing applied the reviewer's answer:
    promotion only recognised the fallback rules, so a decision on one of these
    sat in the table with no effect and the scheme stayed PENDING_REVIEW
    forever.

    Approving several candidates for ONE scheme is not a decision -- it is the
    ambiguity restated -- so it must promote nothing rather than let whichever
    row happens to be applied last silently win.
    """

    def _seed_two_candidates(self, approve_ranks):
        code = f"A{uuid.uuid4().hex[:8]}"
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO bronze.scheme_mapping "
                    "(mapping_id, rta, rta_amc_code, rta_scheme_code, "
                    " rta_scheme_name, mapping_status) "
                    "VALUES (gen_random_uuid(), :rta, 'B', :code, 'test', "
                    "'PENDING_REVIEW')"
                ),
                {"rta": SENTINEL_RTA, "code": code},
            )
            for rank, amfi in ((1, "111111"), (2, "222222")):
                conn.execute(
                    text(
                        "INSERT INTO bronze.scheme_mapping_review "
                        "(review_id, rta, rta_scheme_code, rta_scheme_name, "
                        " candidate_rank, candidate_amfi_code, "
                        " candidate_amfi_name, candidate_score, rule_name, "
                        " reviewer_decision) "
                        "VALUES (gen_random_uuid(), :rta, :code, 'test', :rank, "
                        ":amfi, 'cand', 98, 'STRUCT_EXACT', :decision)"
                    ),
                    {"rta": SENTINEL_RTA, "code": code, "rank": rank,
                     "amfi": amfi,
                     "decision": "APPROVED" if rank in approve_ranks else None},
                )
        return code

    def test_approving_one_candidate_maps_the_scheme(self):
        code = self._seed_two_candidates(approve_ranks={2})

        promote_approved(engine)

        row = _mapping(code)
        assert row["amfi_scheme_code"] == "222222"
        assert row["mapping_status"] == "MATCHED"

    def test_approving_both_candidates_maps_nothing(self):
        code = self._seed_two_candidates(approve_ranks={1, 2})

        promote_approved(engine)

        assert _mapping(code)["amfi_scheme_code"] is None

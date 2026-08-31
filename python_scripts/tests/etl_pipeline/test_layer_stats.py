"""Every layer reports its counts in its own shape; layer_stats turns them all
into the same three log columns. The definition of "duplicate" lives here and
nowhere else."""

import pytest

from etl_pipeline.layer_stats import (
    bronze_counts,
    file_outcome,
    gold_counts,
    scheme_mapping_counts,
    silver_counts,
)


def test_bronze_counts_map_new_and_duplicate():
    result = {"total": 100, "new": 90, "duplicate": 10}

    assert bronze_counts(result) == {
        "total_records": 100, "total_processed": 90, "total_duplicate": 10,
    }


def test_bronze_counts_of_none_are_all_none():
    assert bronze_counts(None) == {
        "total_records": None, "total_processed": None, "total_duplicate": None,
    }


def test_silver_counts_treat_a_conflict_update_as_a_duplicate():
    # A row that took the ON CONFLICT DO UPDATE path was already in silver:
    # that is what "duplicate" means at this layer.
    result = {"table": "transaction_master_new", "status": "COMPLETED",
              "total": 500, "inserted": 400, "updated": 100, "error": None}

    assert silver_counts(result) == {
        "total_records": 500, "total_processed": 400, "total_duplicate": 100,
    }


def test_gold_counts_use_the_transform_output_as_the_total():
    result = {"entity": "transactions", "status": "COMPLETED",
              "total": 700, "inserted": 650, "updated": 50, "error": None}

    assert gold_counts(result) == {
        "total_records": 700, "total_processed": 650, "total_duplicate": 50,
    }


def test_a_failed_layer_still_reports_its_total():
    result = {"table": "investor_master", "status": "FAILED",
              "total": 42, "inserted": 0, "updated": 0, "error": "boom"}

    assert silver_counts(result)["total_records"] == 42
    assert silver_counts(result)["total_processed"] == 0


def test_scheme_mapping_counts_use_the_nine_key_summary():
    summary = {
        "approved_found": 5, "newly_mapped": 3, "already_mapped": 2,
        "still_unmatched": 7, "newly_queued": 4, "queued_tier1": 1,
        "queued_tier2": 2, "ambiguous": 0, "no_candidate": 3,
    }

    counts = scheme_mapping_counts(summary)

    assert counts["total_records"] == 5
    assert counts["total_processed"] == 3
    assert counts["total_duplicate"] == 2


def test_scheme_mapping_counts_of_none_are_all_none():
    assert scheme_mapping_counts(None)["total_records"] is None


# ---- per-file outcome ----------------------------------------------------

def test_a_clean_file_is_completed():
    assert file_outcome("transaction", True, set()) == ("COMPLETED", None)


def test_a_file_whose_dtype_failed_downstream_is_transform_failed():
    assert file_outcome("transaction", True, {"transaction"}) == (
        "FAILED", "TRANSFORM_FAILED",
    )


def test_a_file_whose_dtype_is_untouched_by_the_failure_is_completed():
    # A failed gold.folio_nominees (investor only) must not fail a
    # transactions file.
    assert file_outcome("transaction", True, {"investor"}) == ("COMPLETED", None)


def test_a_file_whose_bronze_load_failed_is_extract_failed():
    assert file_outcome("sip", False, set()) == ("FAILED", "EXTRACT_FAILED")


def test_a_bronze_failure_outranks_a_downstream_failure():
    # The bronze reason is the more specific and more actionable one.
    assert file_outcome("sip", False, {"sip"}) == ("FAILED", "EXTRACT_FAILED")


def test_an_unknown_dtype_is_completed_when_nothing_failed():
    assert file_outcome(None, True, set()) == ("COMPLETED", None)

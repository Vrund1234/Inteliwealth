"""dispatch.py is the single source of truth for which bronze loader a report
code feeds and which gold entities depend on it. A wrong entry here loads a
file into the wrong bronze table or reports the wrong files FAILED."""

import pytest

from etl_pipeline.dispatch import (
    DISPATCH,
    DTYPE_BY_SILVER_TABLE,
    GOLD_ENTITY_DEPENDENCIES,
    SILVER_TABLE_BY_DTYPE,
    failed_dtypes,
    failure_reason_for,
    resolve,
)


# ---- report code -> dtype ------------------------------------------------

@pytest.mark.parametrize("rta,report_code,dtype,vendor", [
    ("CAMS", "WBR2", "transaction", "cams"),
    ("CAMS", "WBR9", "investor", "cams"),
    ("CAMS", "WBR49", "sip", "cams"),
    ("KFIN", "MFSD201", "transaction", "kfin"),
    ("KFIN", "MFSD211", "investor", "kfin"),
    ("KFIN", "MFSD243", "sip", "kfin"),
    # Added 2026-08-31 alongside the raw_ingestion.py support for them.
    ("KFIN", "MFSD307", "transaction", "kfin"),
    ("KFIN", "MFSD311", "investor", "kfin"),
    ("KFIN", "MFSD313", "sip", "kfin"),
])
def test_every_in_scope_code_resolves(rta, report_code, dtype, vendor):
    assert resolve(rta, report_code) == (dtype, vendor)


def test_kfin_transaction_is_mfsd201_not_mfsd211():
    # raw_ingestion.py:722-728 is authoritative: "mfsd201" in the filename
    # appends to kfin_transaction and "mfsd211" to kfin_investor. The
    # abandoned 20_08_2026_testing_auto_pipeline branch had these swapped.
    assert resolve("KFIN", "MFSD201")[0] == "transaction"
    assert resolve("KFIN", "MFSD211")[0] == "investor"


def test_resolution_is_case_insensitive():
    assert resolve("cams", "wbr2") == ("transaction", "cams")
    assert resolve(" KFIN ", " mfsd243 ") == ("sip", "kfin")


def test_the_second_generation_kfin_codes_pair_with_the_first():
    # raw_ingestion.py routes each new code into the SAME bucket as its
    # first-generation sibling (raw_ingestion.py:1145-1172):
    #   mfsd201 or mfsd307 -> kfin_transaction
    #   mfsd211 or mfsd311 -> kfin_investor
    #   mfsd243 or mfsd313 -> kfin_sip
    # so each pair must resolve to an identical (dtype, vendor).
    assert resolve("KFIN", "MFSD307") == resolve("KFIN", "MFSD201")
    assert resolve("KFIN", "MFSD311") == resolve("KFIN", "MFSD211")
    assert resolve("KFIN", "MFSD313") == resolve("KFIN", "MFSD243")


def test_an_unknown_code_resolves_to_none():
    assert resolve("CAMS", "WBR99") is None
    assert resolve("KFIN", "MFSD999") is None
    assert resolve("SOMERTA", "WBR2") is None
    assert resolve(None, None) is None


def test_dispatch_covers_exactly_nine_codes():
    assert len(DISPATCH) == 9


# ---- silver tables -------------------------------------------------------

def test_silver_tables_map_one_to_one():
    assert SILVER_TABLE_BY_DTYPE == {
        "transaction": "transaction_master_new",
        "investor": "investor_master",
        "sip": "sip_master_new",
    }
    assert DTYPE_BY_SILVER_TABLE == {
        v: k for k, v in SILVER_TABLE_BY_DTYPE.items()
    }


# ---- gold dependencies ---------------------------------------------------

def test_gold_dependency_map_matches_the_spec():
    assert GOLD_ENTITY_DEPENDENCIES == {
        "amc": frozenset({"transaction"}),
        "scheme_nav": frozenset({"transaction"}),
        "transactions": frozenset({"transaction"}),
        "scheme": frozenset({"transaction", "investor"}),
        "holdings": frozenset({"transaction", "investor"}),
        "sip": frozenset({"sip", "transaction"}),
        "folio_nominees": frozenset({"investor"}),
        "clients": frozenset({"transaction", "investor", "sip"}),
    }


def test_every_gold_entity_depends_on_a_real_dtype():
    known = set(SILVER_TABLE_BY_DTYPE)
    for entity, dtypes in GOLD_ENTITY_DEPENDENCIES.items():
        assert dtypes <= known, entity


# ---- fan-out -------------------------------------------------------------

def _silver(status_by_table):
    return {
        table: {"table": table, "status": status, "total": 0,
                "inserted": 0, "updated": 0, "error": None}
        for table, status in status_by_table.items()
    }


def _gold(status_by_entity):
    return {
        entity: {"entity": entity, "status": status, "total": 0,
                 "inserted": 0, "updated": 0, "error": None}
        for entity, status in status_by_entity.items()
    }


def test_nothing_failed_means_no_failed_dtypes():
    result = failed_dtypes(
        _silver({"transaction_master_new": "COMPLETED", "investor_master": "SKIPPED"}),
        _gold({"amc": "COMPLETED", "clients": "SKIPPED"}),
    )

    assert result == set()


def test_a_failed_silver_table_fails_only_its_own_dtype():
    result = failed_dtypes(
        _silver({"investor_master": "FAILED", "transaction_master_new": "COMPLETED"}),
        _gold({}),
    )

    assert result == {"investor"}


def test_a_failed_gold_entity_fails_every_dtype_it_reads():
    result = failed_dtypes(_silver({}), _gold({"holdings": "FAILED"}))

    assert result == {"transaction", "investor"}


def test_a_failed_clients_entity_fails_all_three_dtypes():
    # The widest blast radius in the map, and deliberate: gold.clients reads
    # all three silver tables, so one failing clients load fails every file.
    result = failed_dtypes(_silver({}), _gold({"clients": "FAILED"}))

    assert result == {"transaction", "investor", "sip"}


def test_a_failed_amc_does_not_fail_an_investor_file():
    result = failed_dtypes(_silver({}), _gold({"amc": "FAILED"}))

    assert "investor" not in result
    assert result == {"transaction"}


def test_an_unknown_gold_entity_is_ignored_rather_than_raising():
    # A new gold entity added to gold_loader before dispatch.py knows about it
    # must not crash the reporting step.
    result = failed_dtypes(_silver({}), _gold({"brand_new_entity": "FAILED"}))

    assert result == set()


def test_empty_inputs_are_safe():
    assert failed_dtypes({}, {}) == set()
    assert failed_dtypes(None, None) == set()


# ---- failure reasons -----------------------------------------------------

def test_an_unsupported_format_valueerror_maps_to_unsupported_format():
    # raw_ingestion.read_file() raises exactly this at line 530.
    exc = ValueError("Unsupported file format: W0I7582.foo")

    assert failure_reason_for(exc) == "UNSUPPORTED_FORMAT"


def test_any_other_valueerror_maps_to_conversion_failed():
    assert failure_reason_for(ValueError("Unable to decode uploaded file.")) == (
        "CONVERSION_FAILED"
    )


def test_an_arbitrary_exception_maps_to_unknown():
    assert failure_reason_for(RuntimeError("something else")) == "UNKNOWN"

# python_scripts/tests/test_rta_date_formats.py
"""CAMS and KFinTech do NOT share a date format, and the pipeline used to
assume they did.

Decided from the source files themselves (audit 2026-09-01):

    CAMS  10072026104746_216882305R2.csv   TRADDATE
        90,536 values -- first component > 12 in 0, second in 55,812
        -> M/D/YYYY

    KFin  MFSD243_WSREG8131655_1159890_0.csv  RegistrationDate
        658 values -- first component > 12 in 364, second in 0
        -> D/M/YYYY

A first component that never once exceeds 12 across 90,536 values, while the
second exceeds it 55,812 times, can only be the month. Under the old global
DD-MM-YYYY rule every CAMS date was either coerced to None (day > 12, read as
an impossible month) or silently transposed -- 55,812 dropped and 34,724
swapped in bronze.transaction_master_new alone, with none left correct.
"""

import datetime

import pandas as pd

from raw_ingestion import format_dates, parse_source_date


# =====================================================
# CAMS -- MONTH FIRST
# =====================================================


def test_cams_unambiguous_month_first():
    """3/20/2019 -- 20 cannot be a month, so this can only be 20 March 2019.
    The old parser read day=3, month=20 and returned None."""
    assert parse_source_date("3/20/2019", "CAMS") == datetime.date(2019, 3, 20)


def test_cams_ambiguous_resolves_month_first():
    """12/10/2009 -- both components are <= 12. CAMS means 10 December, and the
    old parser stored it as 2009-10-12."""
    assert parse_source_date("12/10/2009", "CAMS") == datetime.date(2009, 12, 10)


def test_cams_strips_trailing_time():
    assert parse_source_date(
        "3/20/2019  12:00:00 AM", "CAMS"
    ) == datetime.date(2019, 3, 20)


def test_cams_rejects_impossible_month():
    """20/03/2019 is not a value CAMS emits; month=20 must not be salvaged by
    quietly falling back to day-first."""
    assert parse_source_date("20/03/2019", "CAMS") is None


# =====================================================
# KFIN -- DAY FIRST
# =====================================================


def test_kfin_unambiguous_day_first():
    assert parse_source_date("21/05/2019", "KFIN") == datetime.date(2019, 5, 21)


def test_kfin_ambiguous_resolves_day_first():
    """02/08/2024 -- KFIN SIP folio 91024506664, source RegistrationDate.
    2 August 2024, not 8 February."""
    assert parse_source_date("02/08/2024", "KFIN") == datetime.date(2024, 8, 2)


def test_kfin_rejects_impossible_month():
    assert parse_source_date("03/20/2019", "KFIN") is None


# =====================================================
# SHARED BEHAVIOUR
# =====================================================


def test_iso_passthrough_ignores_source():
    for source in ("CAMS", "KFIN", None):
        assert parse_source_date("2019-05-21", source) == datetime.date(2019, 5, 21)


def test_source_is_case_insensitive():
    assert parse_source_date("3/20/2019", "cams") == datetime.date(2019, 3, 20)


def test_blank_and_null_still_none():
    for value in ("", None, "nan", "NaT"):
        assert parse_source_date(value, "CAMS") is None


def test_unknown_source_keeps_day_first_default():
    """Callers not yet threaded through must not change behaviour."""
    assert parse_source_date("21/05/2019") == datetime.date(2019, 5, 21)


# =====================================================
# format_dates() THREADS THE SOURCE
# =====================================================


def test_format_dates_applies_cams_month_first():
    df = pd.DataFrame({"traddate": ["3/20/2019", "12/10/2009"]})
    result = format_dates(df, "CAMS")
    assert result["traddate"].tolist() == ["2019-03-20", "2009-12-10"]


def test_format_dates_applies_kfin_day_first():
    df = pd.DataFrame({"traddate": ["21/05/2019", "02/08/2024"]})
    result = format_dates(df, "KFIN")
    assert result["traddate"].tolist() == ["2019-05-21", "2024-08-02"]


def test_format_dates_leaves_unmapped_columns_alone():
    df = pd.DataFrame({"traddate": ["3/20/2019"], "amount": ["100"]})
    result = format_dates(df, "CAMS")
    assert result["amount"].tolist() == ["100"]

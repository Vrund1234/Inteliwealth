"""Smoke checks for column mapping. Run: python test_mapping.py"""

import pandas as pd

from etl_investor_master import apply_investor_mapping
from etl_sip import apply_sip_mapping
from mapping import INVESTOR_MASTER_MAPPING, SIP_MASTER_MAPPING
from utils.utils import clean_columns, format_dates, normalize_key


def test_clean_columns_agrees_with_normalize_key():
    """Column cleaning and alias cleaning must never diverge.

    If they do, an alias silently stops matching and the column loads as
    NULL with no error anywhere. This is why both go through normalize_key.
    """

    raw = ["Scheme Name", "CKYC NO", "Demat-Folio/flag", " 'quoted' ", '"Dbl"', "wt#"]

    cleaned = list(clean_columns(pd.DataFrame([range(len(raw))], columns=raw)).columns)

    assert cleaned == [normalize_key(c) for c in raw], cleaned
    assert cleaned == ["scheme_name", "ckyc_no", "demat_folio_flag",
                       "quoted", "dbl", "wt"], cleaned


def test_clean_columns_is_safe():
    """None-safe, non-mutating, and drops columns that collide once cleaned."""

    assert clean_columns(None) is None

    original = pd.DataFrame([[1]], columns=["A B"])
    clean_columns(original)
    assert list(original.columns) == ["A B"], "caller's frame was mutated"

    duped = pd.DataFrame([[1, 2]], columns=["Folio No", "folio_no"])
    out = clean_columns(duped)
    assert list(out.columns) == ["folio_no"], list(out.columns)
    assert out.iloc[0].tolist() == [1], "did not keep the first duplicate"


def test_sip_spaced_headers_map():
    """KFin ships SIP headers with spaces - they must reach their target column."""

    raw = pd.DataFrame([{
        "Scheme Name": "HDFC Flexi Cap",
        "No Of Installments": 12,
        "Start Date": "01-JAN-2026",
    }])

    out = apply_sip_mapping(raw, SIP_MASTER_MAPPING)

    assert out["scheme_name"].iloc[0] == "HDFC Flexi Cap", out["scheme_name"].iloc[0]
    assert out["no_of_installments"].iloc[0] == 12, out["no_of_installments"].iloc[0]
    assert out["from_date"].notna().iloc[0], "from_date did not map"


def test_investor_spaced_headers_map():
    """Same for the investor master aliases written in source spelling."""

    raw = pd.DataFrame([{
        "CKYC NO": "1234567890",
        "Report Date": "01-JAN-2026",
        "FOLIO": "F/001",
    }])

    out = apply_investor_mapping(raw, INVESTOR_MASTER_MAPPING, "CAMS")

    assert out["ckyc_no"].iloc[0] == "1234567890", out["ckyc_no"].iloc[0]
    assert out["report_date"].notna().iloc[0], "report_date did not map"
    assert out["folio_no"].iloc[0] == "F/001", out["folio_no"].iloc[0]
    assert out["source"].iloc[0] == "CAMS"


def test_dates_do_not_depend_on_row_order():
    """KFIN is D/M/YYYY regardless of what the first row happens to look like.

    pandas' format inference locks onto the first non-null value, so an
    ambiguous first row used to swap day/month for the whole column and null
    every day>12 value.
    """

    dates = ["05/07/2026", "15/07/2026", "03/11/2026", "21/05/2019"]
    expected = ["2026-07-05", "2026-07-15", "2026-11-03", "2019-05-21"]

    for order in (dates, list(reversed(dates))):
        out = format_dates(pd.DataFrame({"dob": order}), ["dob"], "KFIN")["dob"]
        assert [str(d) for d in out] == [
            expected[dates.index(v)] for v in order
        ], list(out)


def test_cams_is_month_first_with_time_suffix():
    """CAMS ships M/D/YYYY plus a time suffix, in two different shapes."""

    raw = ["3/14/2007  12:00:00 AM", "1/10/2010 12:00 AM", "12/20/2016  12:00:00 AM"]

    out = format_dates(pd.DataFrame({"dob": raw}), ["dob"], "CAMS")["dob"]

    assert [str(d) for d in out] == ["2007-03-14", "2010-01-10", "2016-12-20"], list(out)


def test_rows_read_back_from_the_db_still_parse():
    """The no-source call site feeds ISO strings straight out of Postgres."""

    out = format_dates(pd.DataFrame({"dob": ["2026-07-10", None]}), ["dob"])["dob"]

    assert str(out.iloc[0]) == "2026-07-10", out.iloc[0]
    assert out.iloc[1] is None


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)

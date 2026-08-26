# python_scripts/tests/test_sip_date_parsing.py
"""etl_sip.py's format_dates() parses reg_date/from_date/to_date/etc. with
plain pd.to_datetime(df[col], errors="coerce") -- no dayfirst, no
disambiguation. CAMS/KFin dates are DD-MM-YYYY, so an ambiguous value (both
components <= 12) gets silently guessed as MM-DD-YYYY by pandas' US-style
default instead of being flagged. etl_trans.py already solved this for
transaction dates with parse_source_date(), which commits to day-first only
when unambiguous (first token > 12) and otherwise refuses to guess. Verified
live: KFIN regno 232086 (folio 7776062333) is stored in bronze with
reg_date=2017-05-09, but re-parsing the same source file today with the
current code yields 2017-09-05 for the same row -- the exact ambiguous-date
class this test locks down."""

import pandas as pd

from etl_sip import format_dates, dedupe_compare_date


def test_ambiguous_date_is_not_silently_guessed():
    """Both day and month components <= 12: this must not be resolved either
    way by silent inference -- it must come back blank, exactly like
    etl_trans.parse_source_date already treats the same shape of value."""
    df = pd.DataFrame({"reg_date": ["05-09-2017"]})
    result = format_dates(df)
    assert result["reg_date"].iloc[0] is None


def test_unambiguous_day_first_date_parsed_as_day_first():
    """First component > 12: can only be the day. Must resolve to
    17 August 2026, matching the project's documented DD-MM-YYYY
    convention -- not get flipped to an invalid month=17."""
    df = pd.DataFrame({"reg_date": ["17-08-2026"]})
    result = format_dates(df)
    assert result["reg_date"].iloc[0] == "2026-08-17"


def test_iso_format_date_passthrough_unaffected():
    df = pd.DataFrame({"reg_date": ["2017-09-05"]})
    result = format_dates(df)
    assert result["reg_date"].iloc[0] == "2017-09-05"


def test_blank_date_becomes_none():
    df = pd.DataFrame({"reg_date": [""]})
    result = format_dates(df)
    assert result["reg_date"].iloc[0] is None


# =====================================================
# process_sip()'s own-batch-vs-bronze duplicate-flag comparison re-parses
# DATE_COLUMNS a second time (new_df/old_df in the "DUPLICATE FLAG" section)
# with the exact same unsafe pd.to_datetime call format_dates() had. That
# comparison must be just as deterministic, or the flag itself would still
# be ambiguous-date-dependent even after format_dates() is fixed. It uses ""
# rather than None for blank/unparseable, since it's joined straight into a
# string key (`new_df.astype(str).agg("|".join, ...)`).
# =====================================================


def test_dedupe_compare_date_refuses_ambiguous_date():
    assert dedupe_compare_date("05-09-2017") == ""


def test_dedupe_compare_date_parses_unambiguous_day_first_date():
    assert dedupe_compare_date("17-08-2026") == "2026-08-17"


def test_dedupe_compare_date_blank_becomes_empty_string():
    assert dedupe_compare_date("") == ""
    assert dedupe_compare_date(None) == ""

# python_scripts/tests/test_sip_date_parsing.py
"""etl_sip.py's format_dates() parses reg_date/from_date/to_date/etc. through
the shared parser in raw_ingestion.py, which resolves the component order from
the RTA the rows came from.

This module previously asserted the opposite policy: that BOTH RTAs emit
DD-MM-YYYY, and that an ambiguous value (both components <= 12) must be
refused rather than resolved. The audit of 2026-09-01 disproved the premise --
CAMS emits M/D/YYYY. In 10072026104746_216882305R2.csv the first component of
TRADDATE never exceeds 12 across 90,536 values while the second exceeds it
55,812 times; KFin's MFSD243 RegistrationDate is the mirror image. Under the
old global day-first rule every CAMS date was either coerced to None or
silently transposed.

So refusing ambiguous dates is no longer the right behaviour, and never was
the actual behaviour -- the two tests asserting it failed against the code
they shipped with. Once `source` fixes the component order there is nothing
left to be ambiguous about, and the tests below assert that resolution
instead. What must still never happen is a SILENT guess: with no source
supplied, the parser keeps its documented day-first default rather than
sniffing the data.

See tests/test_rta_date_formats.py for the per-RTA parser contract itself.
"""

import pandas as pd

from etl_sip import format_dates, dedupe_compare_date


def test_ambiguous_date_resolves_by_source():
    """05-09-2017 -- both components <= 12, so the value alone cannot say
    which is which. The RTA can: CAMS reads month-first, KFIN day-first."""
    df = pd.DataFrame({"reg_date": ["05-09-2017"]})

    assert format_dates(df, "CAMS")["reg_date"].iloc[0] == "2017-05-09"
    assert format_dates(df, "KFIN")["reg_date"].iloc[0] == "2017-09-05"


def test_unambiguous_day_first_date_parsed_as_day_first():
    """First component > 12: can only be the day. With no source given, the
    parser holds its day-first default -- it must not get flipped to an
    invalid month=17."""
    df = pd.DataFrame({"reg_date": ["17-08-2026"]})
    result = format_dates(df)
    assert result["reg_date"].iloc[0] == "2026-08-17"


def test_cams_date_with_day_above_twelve_is_not_dropped():
    """The regression this whole audit turned on: 3/20/2019 is 20 March in a
    CAMS file. Read day-first it becomes month=20 and is coerced to None --
    which is how 55,812 CAMS transaction dates were lost."""
    df = pd.DataFrame({"reg_date": ["3/20/2019"]})
    result = format_dates(df, "CAMS")
    assert result["reg_date"].iloc[0] == "2019-03-20"


def test_iso_format_date_passthrough_unaffected():
    """Bronze re-reads its own output; ISO must survive any source."""
    for source in ("CAMS", "KFIN", None):
        df = pd.DataFrame({"reg_date": ["2017-09-05"]})
        assert format_dates(df, source)["reg_date"].iloc[0] == "2017-09-05"


def test_blank_date_becomes_none():
    df = pd.DataFrame({"reg_date": [""]})
    result = format_dates(df)
    assert result["reg_date"].iloc[0] is None


# =====================================================
# process_sip()'s own-batch-vs-bronze duplicate-flag comparison re-parses
# DATE_COLUMNS a second time (new_df/old_df in the "DUPLICATE FLAG" section).
# It runs on the concatenated frame, after format_dates() has already
# normalized every value to YYYY-MM-DD, so it takes the parser's ISO branch
# and needs no source. It must stay deterministic either way -- a comparison
# that re-guessed the format would make the duplicate flag depend on row
# order. It uses "" rather than None for blank/unparseable, since it is
# joined straight into a string key (`new_df.astype(str).agg("|".join, ...)`).
# =====================================================


def test_dedupe_compare_date_passes_iso_through():
    assert dedupe_compare_date("2017-09-05") == "2017-09-05"


def test_dedupe_compare_date_resolves_ambiguous_date_by_source():
    assert dedupe_compare_date("05-09-2017", "CAMS") == "2017-05-09"
    assert dedupe_compare_date("05-09-2017", "KFIN") == "2017-09-05"


def test_dedupe_compare_date_parses_unambiguous_day_first_date():
    assert dedupe_compare_date("17-08-2026") == "2026-08-17"


def test_dedupe_compare_date_blank_becomes_empty_string():
    assert dedupe_compare_date("") == ""
    assert dedupe_compare_date(None) == ""

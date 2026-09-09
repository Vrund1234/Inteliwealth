"""clean_identifier treats an all-zero string as absent.

Both RTAs write "0" where they have no value. clean_string does not catch it,
so "0" was stored as a real CKYC number: 159 of the 355 clients gold reported
as having one carried "0", and in silver that same "0" spans 207 different
PANs. Anything filtering on `ckyc_no IS NOT NULL` got 45% false positives.
"""

import pandas as pd
import pytest

from etl_gold_clients import clean_identifier


@pytest.mark.parametrize("value", ["0", "00", "000000", " 0 ", "0000000000000"])
def test_all_zero_strings_are_absent(value):
    assert pd.isna(clean_identifier(pd.Series([value])).iloc[0])


@pytest.mark.parametrize("value", ["", None, "NULL", "NONE", "NAN", "NAT"])
def test_clean_string_behaviour_is_preserved(value):
    assert pd.isna(clean_identifier(pd.Series([value])).iloc[0])


@pytest.mark.parametrize("value,expected", [
    ("60055286922865", "60055286922865"),   # a real CKYC
    ("IN301549", "IN301549"),               # a real DP id
    ("0A", "0A"),                           # leading zero, NOT all zeros
    ("01", "01"),                           # ditto -- must survive
    ("100", "100"),                         # trailing zeros are data
    ("L10072824404828", "L10072824404828"),
])
def test_real_identifiers_survive(value, expected):
    assert clean_identifier(pd.Series([value])).iloc[0] == expected


def test_whitespace_is_stripped_before_the_zero_test():
    assert pd.isna(clean_identifier(pd.Series(["  000  "])).iloc[0])


def test_a_mixed_series_is_handled_row_by_row():
    out = list(clean_identifier(pd.Series(["0", "60055286922865", None, "IN301549"])))
    assert pd.isna(out[0])
    assert out[1] == "60055286922865"
    assert pd.isna(out[2])
    assert out[3] == "IN301549"


def test_the_lookup_uses_clean_identifier_not_clean_string():
    """Guards the call site: using clean_string here is the original bug."""
    import inspect

    import etl_gold_clients as mod

    source = inspect.getsource(mod.get_ckyc_dp_lookup)
    assert "clean_identifier(" in source
    assert "clean_string(\n        lookup[\"ckyc_no\"]" not in source

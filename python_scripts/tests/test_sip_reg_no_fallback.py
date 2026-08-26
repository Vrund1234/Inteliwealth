# python_scripts/tests/test_sip_reg_no_fallback.py
"""gold.sip.sip_reg_no must fall back to request_ref_no when ft_sip_regno is
blank or the literal placeholder '0' -- the architecture doc always claimed
this fallback existed; the code never actually did it, and it silently
merged two distinct real SIP registrations (folio 1019044785) into what
looked like one duplicate."""

import pandas as pd

from etl_gold_sip import transform_sip


def _base_row(**overrides):
    row = {
        "rta_clean": "CAMS",
        "folio_clean": "1019044785",
        "scheme_code_clean": "92",
        "ft_sip_regno": "",
        "request_ref_no": "",
        # transform_sip() reads df["scheme_id"] directly (not via
        # get_column()) for a diagnostic print; in production extract_sip()
        # always selects scheme_id from silver.sip_master_new, so this is
        # here purely so the minimal test DataFrame doesn't KeyError before
        # ever reaching the sip_reg_no logic under test.
        "scheme_id": "",
    }
    row.update(overrides)
    return row


def test_falls_back_to_request_ref_no_when_ft_sip_regno_blank():
    df = pd.DataFrame([_base_row(request_ref_no="CEO1343268")])
    gold_df = transform_sip(df)
    assert gold_df.iloc[0]["sip_reg_no"] == "CEO1343268"


def test_falls_back_to_request_ref_no_when_ft_sip_regno_is_placeholder_zero():
    df = pd.DataFrame([_base_row(ft_sip_regno="0", request_ref_no="ACH33462")])
    gold_df = transform_sip(df)
    assert gold_df.iloc[0]["sip_reg_no"] == "ACH33462"


def test_prefers_ft_sip_regno_when_present():
    df = pd.DataFrame([_base_row(ft_sip_regno="1600046", request_ref_no="SHOULD_NOT_WIN")])
    gold_df = transform_sip(df)
    assert gold_df.iloc[0]["sip_reg_no"] == "1600046"


def test_blank_when_both_are_blank():
    df = pd.DataFrame([_base_row()])
    gold_df = transform_sip(df)
    value = gold_df.iloc[0]["sip_reg_no"]
    # order matters: pd.NA's __eq__ returns pd.NA (not False), so
    # `value in ("", None)` raises "boolean value of NA is ambiguous" when
    # value is pd.NA. Checking pd.isna() first short-circuits the `or`
    # before the ambiguous `in` check ever runs.
    assert pd.isna(value) or value in ("", None)


def test_the_two_distinct_folio_1019044785_sips_stay_distinguishable():
    """The exact case found live: two real SIPs, same folio/scheme/date/amount,
    distinguished only by request_ref_no. Both must produce a non-blank,
    DIFFERENT sip_reg_no."""
    df = pd.DataFrame([
        _base_row(request_ref_no="CEO1343268"),
        _base_row(request_ref_no="CEO1336769"),
    ])
    gold_df = transform_sip(df)
    values = gold_df["sip_reg_no"].tolist()
    assert values[0] != values[1]
    assert "" not in values and None not in values

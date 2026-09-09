"""transform_clients must use the PAN-resolved ckyc_no / dp_id, not the value
that happened to be on one silver row.

extract_clients() selects both columns per row, so the lookup merge collides on
both names. With suffixes=("", "_lookup") the frame's own column keeps the plain
name and the lookup lands in *_lookup -- computed, merged, then never read.

That bug cost real data: PAN AMEPP9018M has 41 silver rows and exactly one
carries a dp_id, and load_clients() keeps one row per PAN with
drop_duplicates(keep="last"). The dp_id row was not last, so gold.clients.dp_id
was 0 of 593. CKYC lost 31 clients the same way (324 stored vs 355 resolvable).
"""

import pandas as pd
import pytest

import etl_gold_clients as mod


@pytest.fixture
def lookup(monkeypatch):
    """One PAN, resolved to a CKYC and a DP ID."""
    frame = pd.DataFrame([{
        "pan": "AMEPP9018M",
        "ckyc_no": "60055286922865",
        "dp_id": "IN301549",
    }])
    monkeypatch.setattr(mod, "get_ckyc_dp_lookup", lambda: frame)
    return frame


def _merge_block(df):
    """The merge exactly as transform_clients performs it."""
    ckyc_dp_lookup = mod.get_ckyc_dp_lookup()
    if not ckyc_dp_lookup.empty:
        df = df.merge(
            ckyc_dp_lookup, how="left",
            left_on="pan", right_on="pan",
            suffixes=("", "_lookup"),
        )
        df["ckyc_no"] = df["ckyc_no_lookup"]
        df["dp_id"] = df["dp_id_lookup"]
        df.drop(columns=["ckyc_no_lookup", "dp_id_lookup"],
                inplace=True, errors="ignore")
    else:
        df["ckyc_no"] = pd.NA
        df["dp_id"] = pd.NA
    return df


def test_lookup_value_reaches_every_row_of_the_pan(lookup):
    """The row carrying dp_id is one of many. Unless the value is broadcast to
    all of them, keep="last" decides at random whether the client keeps it."""
    df = pd.DataFrame([
        {"pan": "AMEPP9018M", "folio_no": "1016219093", "ckyc_no": None, "dp_id": None},
        {"pan": "AMEPP9018M", "folio_no": "432121379871",
         "ckyc_no": "60055286922865", "dp_id": "IN301549"},
        {"pan": "AMEPP9018M", "folio_no": "910124685515",
         "ckyc_no": "60055286922865", "dp_id": None},
    ])

    out = _merge_block(df)

    assert out["dp_id"].tolist() == ["IN301549"] * 3
    assert out["ckyc_no"].tolist() == ["60055286922865"] * 3


def test_the_last_row_still_carries_the_value(lookup):
    """load_clients() keeps the LAST row per PAN. That row must hold the
    resolved value even when the source row it came from sorted first."""
    df = pd.DataFrame([
        {"pan": "AMEPP9018M", "folio_no": "432121379871",
         "ckyc_no": "60055286922865", "dp_id": "IN301549"},
        {"pan": "AMEPP9018M", "folio_no": "999", "ckyc_no": None, "dp_id": None},
    ])

    kept = _merge_block(df).drop_duplicates(subset=["pan"], keep="last")

    assert kept.iloc[0]["dp_id"] == "IN301549"


def test_a_pan_absent_from_the_lookup_gets_nothing(lookup):
    """A left join must not invent values for an unmatched PAN, and must not
    leave the row's own stale value behind either."""
    df = pd.DataFrame([
        {"pan": "ZZZPZ0000Z", "folio_no": "1", "ckyc_no": "STALE", "dp_id": "STALE"},
    ])

    out = _merge_block(df)

    assert pd.isna(out.iloc[0]["ckyc_no"])
    assert pd.isna(out.iloc[0]["dp_id"])


def test_helper_columns_do_not_survive(lookup):
    """*_lookup must be dropped -- final_columns would otherwise carry them
    into the INSERT and fail on an unknown column."""
    df = pd.DataFrame([
        {"pan": "AMEPP9018M", "folio_no": "1", "ckyc_no": None, "dp_id": None},
    ])

    out = _merge_block(df)

    assert "ckyc_no_lookup" not in out.columns
    assert "dp_id_lookup" not in out.columns


def test_source_file_actually_assigns_the_lookup_columns():
    """Guards the real module, not just this file's copy of the merge."""
    import inspect

    source = inspect.getsource(mod.transform_clients)
    assert 'df["ckyc_no"] = df["ckyc_no_lookup"]' in source
    assert 'df["dp_id"] = df["dp_id_lookup"]' in source

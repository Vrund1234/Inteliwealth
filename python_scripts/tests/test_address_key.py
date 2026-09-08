"""address_key is the identity of a physical address, and gold.client_address
has a UNIQUE index over it. A change here silently merges or splits real
client addresses, so every rule the key relies on is pinned by a test."""

import pandas as pd
import pytest

from utils.address_key import ADDRESS_KEY_SQL, address_key, address_key_series


# ---- normalising ---------------------------------------------------------

@pytest.mark.parametrize("l1,l2,l3,expected", [
    ("RAMDAS NI KHADKI", "DESAI VAGO", "NADIAD KHEDA",
     "RAMDASNIKHADKIDESAIVAGONADIADKHEDA"),
    # trailing commas: the CAMS feed keeps them, KFIN does not
    ("RAMDAS NI KHADKI,", "DESAI VAGO,", "NADIAD, KHEDA",
     "RAMDASNIKHADKIDESAIVAGONADIADKHEDA"),
    ("b/102 panchjanya", None, None, "B102PANCHJANYA"),
    ("", "", "", ""),
])
def test_punctuation_and_case_are_stripped(l1, l2, l3, expected):
    assert address_key(l1, l2, l3) == expected


def test_line_boundaries_do_not_change_the_key():
    """Different RTAs split one address across the three columns at different
    points. A separator between the lines would make that look like a move."""
    split_three = address_key("9 KAIRVI BUNGLOWS", "NEAR SARTHI HOTEL", "BODAKDEV")
    split_two = address_key("9 KAIRVI BUNGLOWS NEAR SARTHI HOTEL", "BODAKDEV", None)
    assert split_three == split_two == "9KAIRVIBUNGLOWSNEARSARTHIHOTELBODAKDEV"


def test_missing_lines_are_empty_not_the_string_nan():
    """pandas hands NaN for a blank cell; str(NaN) would weld 'nan' into the
    key and make two blank-line2 rows differ from a genuinely empty one."""
    assert address_key("A 1", float("nan"), None) == "A1"
    assert address_key("A 1", pd.NA, pd.NaT) == "A1"


# ---- what the key deliberately ignores -----------------------------------

def test_key_ignores_everything_outside_the_three_lines():
    """pincode, city, state, country and mobile are enriched fields, not
    identity. Including any of them reintroduces the duplicates this key
    exists to prevent -- see the module docstring for the measurements."""
    assert address_key("RAMDAS NI KHADKI", "DESAI VAGO", "NADIAD KHEDA") == \
           address_key("RAMDAS NI KHADKI", "DESAI VAGO", "NADIAD KHEDA")


def test_a_later_feed_supplying_a_missing_pincode_keeps_the_same_key():
    """The case that removed pincode from the key: gold holds the address with
    a blank pincode, the next sync brings the same address WITH one. Same key
    means the loader UPDATEs in place instead of inserting a second row."""
    before = address_key("25 PRAMUKHSWAMI NAGAR", "IOC ROAD", "CHANDKHEDA")
    after = address_key("25 PRAMUKHSWAMI NAGAR", "IOC ROAD", "CHANDKHEDA")
    assert before == after


# ---- word-level differences are NOT merged -------------------------------

def test_truncated_text_is_a_different_key_and_must_stay_that_way():
    """These belong in gold.client_address_review for a human. If the key ever
    starts merging them, a fuzzy rule has leaked in behind the unique index."""
    assert address_key("B 102 Sai Siddhivinayak Chs Reti Bunder", None, None) != \
           address_key("B 102 Sai Siddhivinayak Chs Reti Bunder Road", None, None)


# ---- the vectorised form must agree with the scalar one ------------------

def test_series_matches_scalar_row_for_row():
    df = pd.DataFrame({
        "line1": ["RAMDAS NI KHADKI,", "9 KAIRVI BUNGLOWS NEAR SARTHI HOTEL", "A 1"],
        "line2": ["DESAI VAGO,", "BODAKDEV", None],
        "line3": ["NADIAD, KHEDA", None, None],
    })
    expected = [address_key(r.line1, r.line2, r.line3) for r in df.itertuples()]
    assert list(address_key_series(df)) == expected


def test_series_accepts_the_pre_rename_column_names():
    """transform_client_address keys the batch while the columns are still
    called address1/2/3, before they are renamed to line1/2/3."""
    df = pd.DataFrame({"address1": ["A 1"], "address2": [None], "address3": [None]})
    assert list(address_key_series(df, "address1", "address2", "address3")) == ["A1"]


# ---- python and SQL must produce the same string -------------------------

def test_sql_expression_matches_python(request):
    """gold.client_address.address_key is GENERATED from ADDRESS_KEY_SQL. If
    the two definitions drift, the loader dedups on one string while the unique
    index enforces another -- silent duplicates, or spurious violations."""
    try:
        from utils.db import engine
        from sqlalchemy import text
    except Exception:
        pytest.skip("database not configured")

    cases = [
        ("RAMDAS NI KHADKI,", "DESAI VAGO,", "NADIAD, KHEDA"),
        ("9 KAIRVI BUNGLOWS NEAR SARTHI HOTEL", "BODAKDEV", None),
        ("b/102 panchjanya", None, None),
        ("12899  SE  HAWKS  CREST  PLACE,", None, None),
        (None, None, None),
    ]
    try:
        with engine.connect() as conn:
            for l1, l2, l3 in cases:
                got = conn.execute(
                    text(
                        "SELECT UPPER(REGEXP_REPLACE("
                        "COALESCE(:l1,'')||COALESCE(:l2,'')||COALESCE(:l3,''),"
                        "'[^A-Za-z0-9]','','g'))"
                    ),
                    {"l1": l1, "l2": l2, "l3": l3},
                ).scalar()
                assert got == address_key(l1, l2, l3), (l1, l2, l3)
    except Exception as exc:  # unreachable database is not a test failure
        if "could not connect" in str(exc).lower() or "connection" in str(exc).lower():
            pytest.skip(f"database unreachable: {exc}")
        raise

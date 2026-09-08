"""account_key is the identity of a bank account, and gold.client_bank has a
UNIQUE index over it. A change here silently merges or splits real client
accounts, so every rule it relies on is pinned by a test."""

import pandas as pd
import pytest

from utils.account_key import ACCOUNT_KEY_SQL, account_key, account_key_series


# ---- the four real duplicates this function exists to collapse -----------

@pytest.mark.parametrize("a,b", [
    ("00691060000052", "0691060000052"),      # ACUPS2047M
    ("0491010000421", "00491010000421"),      # ALGPP2475B, NRO
    ("00491060001464", "0491060001464"),      # ALGPP2475B, NRE
    ("30409457813", "00000030409457813"),     # BFYPS0557N
])
def test_leading_zero_variants_are_one_account(a, b):
    assert account_key(a) == account_key(b)


def test_punctuation_and_case_are_stripped():
    assert account_key(" 0069-1060 000052 ") == "691060000052"
    assert account_key("abc123") == "ABC123"


def test_an_all_zero_number_is_absent_not_a_shared_key():
    """A placeholder account must never merge two clients' rows. '' is then
    excluded by the loader and by the detection query."""
    assert account_key("000000") == ""
    assert account_key("0") == ""


def test_missing_is_empty_not_the_string_nan():
    assert account_key(None) == ""
    assert account_key(float("nan")) == ""
    assert account_key(pd.NA) == ""


def test_interior_and_trailing_zeros_survive():
    """Only LEADING zeros are padding. Stripping others would merge genuinely
    different accounts."""
    assert account_key("102030") == "102030"
    assert account_key("500") == "500"
    assert account_key("0500") == account_key("500") == "500"


def test_two_different_accounts_stay_different():
    assert account_key("0491010000421") != account_key("0491060001464")


# ---- the vectorised form must agree with the scalar one -----------------

def test_series_matches_scalar_row_for_row():
    values = pd.Series(["00691060000052", "0691060000052", None,
                        "000000", " 30409457813 ", "abc-123"])
    expected = [account_key(v) for v in values]
    assert list(account_key_series(values)) == expected


# ---- python and SQL must produce the same string ------------------------

def test_sql_expression_matches_python():
    """gold.client_bank.account_key is GENERATED from ACCOUNT_KEY_SQL. Drift
    between the two means the loader dedups on one string while the unique
    index enforces another -- silent duplicates, or spurious violations."""
    try:
        from sqlalchemy import text

        from utils.db import engine
    except Exception:
        pytest.skip("database not configured")

    cases = ["00691060000052", "0691060000052", "00000030409457813",
             "000000", " 0069-1060 000052 ", "abc-123", None]

    expression = ACCOUNT_KEY_SQL.replace("account_number", ":acc")

    try:
        with engine.connect() as conn:
            for value in cases:
                got = conn.execute(
                    text(f"SELECT {expression}"), {"acc": value}
                ).scalar()
                assert got == account_key(value), value
    except Exception as exc:
        if "connect" in str(exc).lower():
            pytest.skip(f"database unreachable: {exc}")
        raise

# python_scripts/tests/test_silver_date_passthrough.py
"""Silver must CAST bronze dates, not re-interpret them.

Bronze stores every date column normalized to YYYY-MM-DD. The silver
transforms used to hand those strings back to
pd.to_datetime(..., dayfirst=True), which makes pandas infer %Y-%d-%m from the
first ambiguous value in the column and then apply that format to the whole
column. Reproduced on this project's pandas 3.0.5:

    pd.to_datetime(['2013-12-11','2019-05-21','2024-08-02'], dayfirst=True)
        -> [2013-11-12, NaT, 2024-02-08]
             swapped     ^ day 21 is not a month     swapped

Confirmed live before the fix: bronze held 38,230 KFIN transaction dates with
none blank; silver held 22,195 nulls, and gold.transactions had zero rows on
any day above the 12th. Because the inferred format depends on which value
pandas happens to see first, the corruption was also order-dependent.
"""

import datetime

import pandas as pd

from transformations.transform import parse_bronze_date_series


AMBIGUOUS_FIRST = ["2013-12-11", "2019-05-21", "2024-08-02"]


def test_ambiguous_leading_value_does_not_flip_the_column():
    """The exact series that pandas' dayfirst inference used to destroy."""
    result = parse_bronze_date_series(pd.Series(AMBIGUOUS_FIRST))
    assert result.tolist() == [
        datetime.date(2013, 12, 11),
        datetime.date(2019, 5, 21),
        datetime.date(2024, 8, 2),
    ]


def test_day_above_twelve_survives():
    """The 22,195-null case: a day > 12 must not become NaT."""
    result = parse_bronze_date_series(pd.Series(["2019-05-21", "2026-08-31"]))
    assert result.tolist() == [
        datetime.date(2019, 5, 21),
        datetime.date(2026, 8, 31),
    ]


def test_result_is_order_independent():
    forward = parse_bronze_date_series(pd.Series(AMBIGUOUS_FIRST))
    reversed_ = parse_bronze_date_series(pd.Series(AMBIGUOUS_FIRST[::-1]))
    assert forward.tolist() == reversed_.tolist()[::-1]


def test_timestamp_suffix_is_truncated_not_dropped():
    result = parse_bronze_date_series(pd.Series(["2019-05-21 00:00:00"]))
    assert result.tolist() == [datetime.date(2019, 5, 21)]


def test_date_objects_pass_through():
    """psycopg returns real date objects for bronze columns typed `date`."""
    result = parse_bronze_date_series(
        pd.Series([datetime.date(2019, 5, 21), datetime.date(2013, 12, 11)])
    )
    assert result.tolist() == [
        datetime.date(2019, 5, 21),
        datetime.date(2013, 12, 11),
    ]


def test_blank_and_null_become_none():
    result = parse_bronze_date_series(pd.Series(["", None, "not a date"]))
    assert result.isna().all()


def test_empty_series_is_handled():
    assert parse_bronze_date_series(pd.Series([], dtype="object")).empty

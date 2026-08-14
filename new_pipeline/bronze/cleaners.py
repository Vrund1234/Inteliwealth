"""Shared cleaning helpers.

Written ONCE and used by every entity. The existing pipeline copy-pastes
clean_columns / normalize / clean_identifier_columns / format_dates into all three
bronze writers, and they have already diverged: etl_sip upper-cases values for
comparison while the other two do not, and etl_investor_master's clean_columns omits
the duplicate-column drop the others have.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Iterable

import pandas as pd

from utils.logging import get_logger

log = get_logger(__name__)

# The single sentinel set for "this cell is empty". The existing pipeline converts
# between "", None, pd.NA, "nan", "None", "<NA>" and "NaT" at least four times per row.
#
# "/" is deliberately NOT here. WBR56 emits a bare "/" for an unknown compound
# code/label value, and the generated report must reproduce it verbatim. It is
# interpreted as "unknown" only inside split_compound, which is the one place where
# "/" carries meaning.
BLANKS = {"", "nan", "none", "<na>", "nat", "null", "-"}


def normalize_header(name: object) -> str:
    """Canonical form of a column name.

    THE function. Applied to both the config's declared source header and the header
    read from the file. The existing pipeline normalises headers with " " -> "_" but
    aliases with only .lower().strip(), so any alias containing a space can never
    match — which is how 45 KFin columns are silently lost.
    """
    text = str(name).strip().strip("'").strip('"').lower()
    for char in (" ", "-", "/", "\\", ".", "\t", "\n"):
        text = text.replace(char, "_")
    text = text.replace("#", "").replace("(", "").replace(")", "")
    while "__" in text:
        text = text.replace("__", "_")
    return text.strip("_")


def is_blank(value: object) -> bool:
    if value is None or value is pd.NA:
        return True
    if isinstance(value, float) and pd.isna(value):
        return True
    return str(value).strip().lower() in BLANKS


def blank_to_null(series: pd.Series) -> pd.Series:
    """One conversion, applied once, at a known point in the flow."""
    return series.map(lambda v: None if is_blank(v) else v)


def trim(series: pd.Series) -> pd.Series:
    """Strip surrounding whitespace.

    Needed on every WBR56 and WBR68 name column — the provider emits a trailing
    double space in every inv_name row.
    """
    return series.map(lambda v: v if v is None else str(v).strip())


def apply_case(series: pd.Series, case: str | None) -> pd.Series:
    if case is None:
        return series
    if case == "upper":
        return series.map(lambda v: v if v is None else str(v).upper())
    if case == "lower":
        return series.map(lambda v: v if v is None else str(v).lower())
    if case == "title":
        return series.map(lambda v: v if v is None else str(v).title())
    raise ValueError(f"unknown case transform {case!r}")


def strip_float_artifacts(series: pd.Series) -> pd.Series:
    """Remove the trailing '.0' Excel adds to numeric-looking identifiers.

    Driven by the config's `identifier` flag, not a hardcoded column list. Applies to
    folios, PANs, phone numbers, pincodes and transaction numbers — anything that
    looks numeric but is an identifier.

    Deliberately does NOT touch a value with a slash: WBR56 folios arrive as both
    '1049217049' and '42213157/43'.
    """
    def fix(value: object) -> object:
        if value is None:
            return None
        text = str(value).strip()
        if text.endswith(".0") and text[:-2].replace("-", "").isdigit():
            return text[:-2]
        return text

    return series.map(fix)


def parse_date_value(
    value: object,
    primary_format: str,
    fallbacks: Iterable[str] = (),
) -> tuple[date | None, bool]:
    """Parse one date. Returns (value, ok).

    Explicit formats only, tried in order. Never pandas inference — inference reads
    '03/04/2026' as March or April depending on the rest of the column, and WBR56
    carries '01-Jan-2025' and '7/16/2026' in the same file.

    ok=False means the caller must reject the row. It never quietly becomes NaT, which
    is what pd.to_datetime(errors="coerce") does throughout the existing pipeline.
    """
    if is_blank(value):
        return None, True

    text = str(value).strip()
    for fmt in (primary_format, *fallbacks):
        try:
            return datetime.strptime(text, fmt).date(), True
        except (ValueError, TypeError):
            continue
    return None, False


def parse_numeric_value(value: object) -> tuple[float | None, bool]:
    """Parse one number. Returns (value, ok).

    Strips thousands separators and currency-ish noise but refuses anything it cannot
    fully account for, rather than coercing to NaN.
    """
    if is_blank(value):
        return None, True

    text = str(value).strip().replace(",", "").replace("₹", "").replace(" ", "")
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]          # accounting negatives

    try:
        return float(text), True
    except (ValueError, TypeError):
        return None, False


def parse_integer_value(value: object) -> tuple[int | None, bool]:
    number, ok = parse_numeric_value(value)
    if not ok or number is None:
        return None, ok
    if float(number).is_integer():
        return int(number), True
    return None, False


def split_compound(series: pd.Series, sep: str = "/") -> tuple[pd.Series, pd.Series]:
    """Split a 'code/label' column into two.

    WBR56 and WBR68 both carry `location` as 'A1/Ahmedabad' and `state` as
    'GU/Gujarat'. A bare '/' means unknown and must become (None, None), not ('', '').
    """
    def left(value: object) -> object:
        # A bare separator means unknown. Handled here rather than by treating "/" as
        # a global blank, so the raw column keeps the provider's literal value.
        if is_blank(value) or str(value).strip() == sep:
            return None
        part = str(value).split(sep, 1)[0].strip()
        return part or None

    def right(value: object) -> object:
        if is_blank(value) or str(value).strip() == sep:
            return None
        pieces = str(value).split(sep, 1)
        if len(pieces) < 2:
            return None
        return pieces[1].strip() or None

    return series.map(left), series.map(right)


def normalize_msisdn(series: pd.Series, default_cc: str = "91") -> pd.Series:
    """Best-effort E.164 for Indian mobile numbers.

    WBR56 mixes '+919328266374' and '9825333209'. Applied in SILVER only — bronze
    keeps whatever the provider sent.
    """
    def fix(value: object) -> object:
        if value is None:
            return None
        digits = "".join(ch for ch in str(value) if ch.isdigit())
        if not digits:
            return None
        if len(digits) == 10:
            return f"+{default_cc}{digits}"
        if len(digits) == 12 and digits.startswith(default_cc):
            return f"+{digits}"
        return f"+{digits}"

    return series.map(fix)

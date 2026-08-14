"""Business rules as pure functions over a row.

Pattern copied from python_scripts/scheme_matching/rules.py, which is the
best-structured module in the existing codebase: each rule is an independently
testable function, they are held in an ordered registry, and every outcome is
recorded rather than inferred.

A rule returns None when the row passes, or a reason string when it fails. Failing
rows are rejected with the rule's name, never silently nulled.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable

PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
ARN_RE = re.compile(r"^ARN-\d{3,8}$")
EUIN_RE = re.compile(r"^E\d{5,8}$")

# Bounds for plausibility, not correctness. A date far outside this window is a
# parsing failure that slipped through, not real data.
MIN_PLAUSIBLE_DATE = date(1990, 1, 1)
MAX_PLAUSIBLE_DATE = date(2100, 1, 1)


def _as_date(value: object) -> date | None:
    """Coerce a date-like value to datetime.date, or None if it is not one.

    datetime (and therefore pd.Timestamp) must be tested BEFORE date: pd.Timestamp
    subclasses datetime.date, so an isinstance(value, date) check passes for it, but
    comparing a Timestamp to a date raises TypeError. Audit columns such as
    ingested_at come back from the database as tz-aware Timestamps and reach every
    row-level rule.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


@dataclass(frozen=True)
class Rule:
    name: str
    entities: tuple[str, ...]      # empty means "all entities"
    check: Callable[[dict], str | None]

    def applies_to(self, entity: str) -> bool:
        return not self.entities or entity in self.entities


REGISTRY: list[Rule] = []


def rule(name: str, *entities: str):
    def decorate(fn: Callable[[dict], str | None]) -> Callable[[dict], str | None]:
        REGISTRY.append(Rule(name=name, entities=tuple(entities), check=fn))
        return fn
    return decorate


# ---------------------------------------------------------------------
# Shared rules
# ---------------------------------------------------------------------

@rule("date_within_plausible_range")
def _dates_plausible(row: dict) -> str | None:
    for key, value in row.items():
        as_date = _as_date(value)
        if as_date is None:
            continue
        if not (MIN_PLAUSIBLE_DATE <= as_date <= MAX_PLAUSIBLE_DATE):
            return (
                f"{key}={as_date.isoformat()} outside "
                f"{MIN_PLAUSIBLE_DATE}..{MAX_PLAUSIBLE_DATE}"
            )
    return None


@rule("pan_format")
def _pan_format(row: dict) -> str | None:
    for key in ("tax_no", "inv_pan", "jointpan1", "jointpan2", "guardian_panno"):
        value = row.get(key)
        if value and not PAN_RE.match(str(value)):
            return f"{key}={value!r} is not a valid PAN"
    return None


# ---------------------------------------------------------------------
# WBR36 / WBR36H
# ---------------------------------------------------------------------

@rule("brokerage_measures_present", "wbr36_brokerage", "wbr36h_brokerage")
def _measures_present(row: dict) -> str | None:
    measures = ("upfront", "afe", "trailer_fee", "trxn_charges", "clawback", "incentives")
    if all(row.get(m) is None for m in measures):
        return "every brokerage measure is NULL — the row carries no information"
    return None


@rule("product_code_shape", "wbr36_brokerage", "wbr36h_brokerage")
def _product_code_shape(row: dict) -> str | None:
    code = row.get("product_code")
    if not code:
        return "product_code is NULL"
    if len(str(code)) > 30:
        return f"product_code {code!r} is implausibly long ({len(str(code))} chars)"
    return None


# ---------------------------------------------------------------------
# WBR56
# ---------------------------------------------------------------------

@rule("report_period_ordered", "wbr56_kyc")
def _period_ordered(row: dict) -> str | None:
    start, end = row.get("rep_from_date"), row.get("rep_to_date")
    if start and end and start > end:
        return f"rep_from_date {start} is after rep_to_date {end}"
    return None


@rule("kyc_status_recognised", "wbr56_kyc")
def _kyc_recognised(row: dict) -> str | None:
    """Flags the unmapped-lookup case that the existing pipeline hides.

    There, `.map(dict).fillna(original)` silently keeps an unrecognised code, so a new
    provider status looks like clean data forever.
    """
    unresolved = row.get("__unresolved_lookups__") or []
    if unresolved:
        return f"unrecognised lookup values: {unresolved}"
    return None


# ---------------------------------------------------------------------
# WBR68
# ---------------------------------------------------------------------

@rule("euin_is_actually_invalid", "wbr68_invalid_euin")
def _euin_invalid(row: dict) -> str | None:
    """The report is defined as invalid-EUIN rows, so a valid one does not belong.

    The sample carries BOTH 'N' and 'F' as invalid values with the same reason, which
    is why the predicate is `<> 'Y'` and not `= 'N'`.
    """
    flag = row.get("euin_valid")
    if flag is not None and str(flag).upper() == "Y":
        return f"euin_valid={flag!r} is valid, but this is the invalid-EUIN report"
    return None


@rule("amount_non_negative", "wbr68_invalid_euin")
def _amount_non_negative(row: dict) -> str | None:
    amount = row.get("amount")
    if amount is not None and float(amount) < 0:
        return f"amount={amount} is negative for a purchase transaction"
    return None


@rule("euin_format", "wbr68_invalid_euin")
def _euin_format(row: dict) -> str | None:
    euin = row.get("euin")
    if euin and not EUIN_RE.match(str(euin)):
        return f"euin={euin!r} does not match E<digits>"
    return None


@rule("folio_columns_agree", "wbr68_invalid_euin")
def _folio_agrees(row: dict) -> str | None:
    """folio_no and folio hold the same value in every sample row.

    A divergence means the provider changed the layout, and the report has two folio
    columns at positions 4 and 23 that would then disagree.
    """
    a, b = row.get("folio_no"), row.get("folio")
    if a and b and str(a) != str(b):
        return f"folio_no={a!r} disagrees with folio={b!r}"
    return None


def run_rules(row: dict, entity: str) -> list[tuple[str, str]]:
    """Every applicable rule runs. Returns [(rule_name, reason), ...]."""
    failures: list[tuple[str, str]] = []
    for item in REGISTRY:
        if not item.applies_to(entity):
            continue
        reason = item.check(row)
        if reason:
            failures.append((item.name, reason))
    return failures

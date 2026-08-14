"""Matching rules as pure functions over an ordered registry.

Every rule runs on every row, even after a confident match is found. Early exit
would save nothing at 515 rows and would make the audit table (which records
each rule's outcome) and the top-3 review candidates impossible to populate.
"""

import re
from dataclasses import dataclass, field

# Registry order. Also the tie-breaker when two rules return equal confidence.
RULE_ORDER = [
    "OVERRIDE",
    "ISIN_MATCH",
    "PRODUCT_MATCH",
    "STRUCT_EXACT",
    "NAV_MATCH",
    "STRUCT_TIEBREAK",
    "CORE_FUZZY",
]

CONFIDENCE = {
    "OVERRIDE": 100,
    "ISIN_MATCH": 100,
    "PRODUCT_MATCH": 100,
    "STRUCT_EXACT": 98,
    "NAV_MATCH": 97,
    "STRUCT_TIEBREAK": 95,
    "CORE_FUZZY": 90,
}


@dataclass(frozen=True)
class Candidate:
    amfi_scheme_code: str
    score: float
    rule_name: str
    confidence: int


@dataclass
class MatchContext:
    """Everything the rules read. Built once per run, never mutated by a rule."""

    amfi_by_key: dict = field(default_factory=dict)
    amfi_by_bucket: dict = field(default_factory=dict)
    amfi_names: dict = field(default_factory=dict)
    overrides: dict = field(default_factory=dict)
    nav_lookup: object = None


def arbitrate(candidates):
    """Pick the winner: highest confidence, then registry order, then score."""
    if not candidates:
        return None

    def sort_key(cand):
        try:
            order = RULE_ORDER.index(cand.rule_name)
        except ValueError:
            order = len(RULE_ORDER)
        return (-cand.confidence, order, -cand.score)

    return sorted(candidates, key=sort_key)[0]


# Rules are appended to this list by Tasks 7-11.
RULE_REGISTRY = []


def run_all(row, context):
    """Execute every registered rule and return the union of their candidates."""
    found = []
    for rule in RULE_REGISTRY:
        found.extend(rule(row, context) or [])
    return found


def rule_struct_exact(row, context):
    """Exact SchemeKey equality against the AMFI master.

    Emits every code sharing the key. A single candidate is a confident match;
    two or three are handed to rule_struct_tiebreak, which resolves them or
    routes them to review. Nothing is written from here on ambiguity.
    """
    key = row.get("scheme_key")
    if key is None:
        return []

    codes = context.amfi_by_key.get(key, [])
    return [
        Candidate(
            amfi_scheme_code=str(code),
            score=100.0,
            rule_name="STRUCT_EXACT",
            confidence=CONFIDENCE["STRUCT_EXACT"],
        )
        for code in codes
    ]


RULE_REGISTRY.append(rule_struct_exact)


class _NotInAmfi:
    """Sentinel: an override asserts this scheme has no AMFI counterpart."""

    def __repr__(self):
        return "NOT_IN_AMFI"

    def __bool__(self):
        return False


NOT_IN_AMFI = _NotInAmfi()


def rule_override(row, context):
    """Hand-curated mapping. Wins over every algorithmic rule.

    A key present with a NULL value is a positive assertion of absence, not a
    missing entry — it yields NOT_IN_AMFI so the scheme stops being re-examined.
    """
    key = (row.get("rta"), row.get("rta_scheme_code"))
    if key not in context.overrides:
        return []

    code = context.overrides[key]
    return [
        Candidate(
            amfi_scheme_code=NOT_IN_AMFI if code is None else str(code),
            score=100.0,
            rule_name="OVERRIDE",
            confidence=CONFIDENCE["OVERRIDE"],
        )
    ]


RULE_REGISTRY.insert(0, rule_override)


from rapidfuzz import fuzz

# Guards for the only inexact rule.
FUZZY_CUTOFF = 88
FUZZY_MARGIN = 5


def numbers_in(core_name):
    """Integers carried by a core name, e.g. "SERIES 113 1228" -> {113, 1228}."""
    return set(re.findall(r"\d+", core_name or ""))


def numbers_conflict(left, right):
    """True when two core names carry numbers that cannot be the same fund.

    A series number is identity, not description: "Series 48" and "Series 113"
    are different closed-end plans with different maturities and different NAV
    series, but differ by one short token that a similarity ratio barely
    registers.

    Subset rather than equality, because the RTA routinely omits a day-count
    the AMFI name carries ("Series 135" vs "Series 135 1117 Days"). Extra
    numbers on one side are additional detail; numbers on BOTH sides that the
    other lacks are a genuine contradiction.
    """
    a, b = numbers_in(left), numbers_in(right)
    return bool(a - b) and bool(b - a)


def rule_core_fuzzy(row, context):
    """Fuzzy match on core_name only, inside an identical-attribute bucket.

    Three guards, all required:
      1. Candidates share the row's exact bucket, so plan/option/frequency/
         qualifiers are never fuzzy-matched.
      2. token_sort_ratio >= FUZZY_CUTOFF. token_sort_ratio rather than ratio
         because word order differs between RTA and AMFI.
      3. The top score beats the runner-up by >= FUZZY_MARGIN. A near-tie means
         the name does not distinguish the funds, so returning nothing and
         routing to review is better than guessing.
    """
    key = row.get("scheme_key")
    if key is None:
        return []

    entries = context.amfi_by_bucket.get(key.bucket(), [])
    if not entries:
        return []

    # Numbers are filtered before scoring, not after: a candidate whose series
    # number contradicts the row's is not a weaker match, it is a different
    # fund. Leaving it in the pool would also let it suppress a correct match
    # through the margin guard.
    scored = sorted(
        (
            (fuzz.token_sort_ratio(key.core_name, core), code)
            for core, code in entries
            if not numbers_conflict(key.core_name, core)
        ),
        key=lambda pair: pair[0],
        reverse=True,
    )
    if not scored:
        return []

    top_score, top_code = scored[0]
    if top_score < FUZZY_CUTOFF:
        return []

    if len(scored) > 1 and top_score - scored[1][0] < FUZZY_MARGIN:
        return []

    return [
        Candidate(
            amfi_scheme_code=str(top_code),
            score=float(top_score),
            rule_name="CORE_FUZZY",
            confidence=CONFIDENCE["CORE_FUZZY"],
        )
    ]


RULE_REGISTRY.append(rule_core_fuzzy)


# KFIN product code suffixes carrying the option. CAMS codes have no comparable
# convention, so CAMS rows return None here and fall back to NAV verification.
_PRODCODE_GROWTH_SUFFIXES = ("GP", "GR", "SG", "G")
_PRODCODE_IDCW_SUFFIXES = ("DP", "ID", "RD", "RW", "MR", "DD", "D")


def option_from_prodcode(code):
    """Read the option from a KFIN product code suffix, or None if unreadable."""
    if not code:
        return None

    upper = str(code).upper()
    for suffix in _PRODCODE_IDCW_SUFFIXES:
        if upper.endswith(suffix):
            return "IDCW"
    for suffix in _PRODCODE_GROWTH_SUFFIXES:
        if upper.endswith(suffix):
            return "GROWTH"
    return None


def rule_struct_tiebreak(row, context):
    """Resolve a key that matched 2-3 AMFI rows, using the product code suffix.

    Fires only on genuine ambiguity. Requires the code signal and the name
    signal to agree; a contradiction or a missing signal returns nothing, which
    routes the scheme to review rather than guessing between real funds.
    """
    key = row.get("scheme_key")
    if key is None:
        return []

    codes = context.amfi_by_key.get(key, [])
    if len(codes) < 2:
        return []

    code_option = option_from_prodcode(row.get("rta_scheme_code"))
    if code_option is None or code_option != key.option:
        return []

    matching = [
        code
        for code in codes
        if context.amfi_names.get(str(code))
        and parse_option_of(context.amfi_names[str(code)]) == code_option
    ]

    if len(matching) != 1:
        return []

    return [
        Candidate(
            amfi_scheme_code=str(matching[0]),
            score=100.0,
            rule_name="STRUCT_TIEBREAK",
            confidence=CONFIDENCE["STRUCT_TIEBREAK"],
        )
    ]


def parse_option_of(amfi_name):
    """Option carried by an AMFI name, via the shared attribute extractor."""
    from scheme_matching.scheme_key import extract_attributes

    _, attrs = extract_attributes(amfi_name)
    return attrs["option"]


RULE_REGISTRY.append(rule_struct_tiebreak)

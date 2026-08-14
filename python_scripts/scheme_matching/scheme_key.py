"""Parse RTA and AMFI scheme names into a comparable structured key.

The critical ordering rule: attributes are EXTRACTED into fields before
structural filler is DELETED. Deleting plan/option/frequency tokens as noise
would collapse a fund's Growth and IDCW variants onto one key and produce
confidently wrong mappings.
"""

import re
from dataclasses import dataclass

PLAN_DIRECT = "DIRECT"
PLAN_REGULAR = "REGULAR"
OPTION_GROWTH = "GROWTH"
OPTION_IDCW = "IDCW"

# Ordered longest-first so "HALF YEARLY" wins over "YEARLY".
_FREQUENCIES = [
    # Hyphen included deliberately: "Half-Yearly" would otherwise fall through
    # to the bare YEARLY pattern below and be recorded as ANNUAL.
    ("HALF[\\s\\-]*YEARLY", "HALF_YEARLY"),
    ("FORTNIGHTLY", "FORTNIGHTLY"),
    ("QUARTERLY", "QUARTERLY"),
    ("MONTHLY", "MONTHLY"),
    ("WEEKLY", "WEEKLY"),
    ("ANNUALLY", "ANNUAL"),
    ("ANNUAL", "ANNUAL"),
    ("YEARLY", "ANNUAL"),
    ("DAILY", "DAILY"),
]

# DISCONTINUED is a qualifier, not noise: a discontinued share class keeps
# its own NAV series, so it must never share a key with the live plan.
_QUALIFIERS = ["SEGREGATED", "RETAIL", "INSTITUTIONAL", "DISCONTINUED"]

# "DIVIDEND YIELD" is a fund category, not a payout option — 52 AMFI schemes
# are named that way and most of them are Growth. The lookahead stops the
# category name being read as an IDCW signal.
_IDCW_TOKENS = r"\b(IDCW|DIVIDEND|DIV)\b(?!\s+YIELD)"

# Deleted only after attribute extraction.
_FILLER = {
    "FUND", "FUNDS", "SCHEME", "PLAN", "PLANS", "OPTION", "OPTIONS",
    "THE", "OF", "AN", "A", "MUTUAL",
    "REGULAR", "DIRECT", "GROWTH", "IDCW", "DIVIDEND", "DIV",
    "APPRECIATION",
    "PAYOUT", "REINVESTMENT", "REINVEST", "REINVESTED",
    "SEGREGATED", "RETAIL", "INSTITUTIONAL", "DISCONTINUED",
    "DAILY", "WEEKLY", "FORTNIGHTLY", "MONTHLY", "QUARTERLY",
    "HALF", "YEARLY", "ANNUAL", "ANNUALLY",
    "DAYS", "DAY",
}

_PARENTHETICAL_NOISE = [
    r"\([^)]*FORMERLY[^)]*\)",
    r"\([^)]*ERSTWHILE[^)]*\)",
    r"\([^)]*ELSS[^)]*\)",
    r"\([^)]*MATURITY\s*DATE[^)]*\)",
]

_BARE_NOISE = [
    r"FORMERLY\s+KNOWN\s+AS.*$",
    r"FORMERLY\s+.*$",
    r"ERSTWHILE\s+.*$",
    r"MATURITY\s*DATE\s*[-–]?\s*\d{1,2}[-/][A-Z]{3}[-/]\d{2,4}",
    r"U\s*/?\s*S\s*80\s*C(\s+OF\s+IT\s+ACT)?",
    r"CLOSED\s+FOR\s+FV\s+CHANGE",
]


@dataclass(frozen=True)
class SchemeKey:
    amc_code: str | None
    core_name: str
    plan: str
    option: str
    frequency: str | None
    qualifiers: frozenset

    def bucket(self):
        """Everything except core_name. Fuzzy matching happens within a bucket."""
        return (self.amc_code, self.plan, self.option, self.frequency, self.qualifiers)


def strip_parentheticals(text):
    """Remove rename annotations, regulatory boilerplate and maturity dates."""
    out = text.upper()
    for pattern in _PARENTHETICAL_NOISE:
        out = re.sub(pattern, " ", out)
    for pattern in _BARE_NOISE:
        out = re.sub(pattern, " ", out)
    return re.sub(r"\s+", " ", out).strip()


def extract_attributes(text):
    """Pull plan/option/frequency/qualifiers out of `text`.

    Returns (text_unchanged, attrs). The text is returned as-is; filler removal
    happens later in parse_scheme_key so callers can inspect attributes without
    losing words.
    """
    upper = text.upper()

    plan = PLAN_DIRECT if re.search(r"\bDIRECT\b", upper) else PLAN_REGULAR
    option = OPTION_IDCW if re.search(_IDCW_TOKENS, upper) else OPTION_GROWTH

    frequency = None
    for pattern, value in _FREQUENCIES:
        if re.search(r"\b" + pattern + r"\b", upper):
            frequency = value
            break

    qualifiers = frozenset(
        q for q in _QUALIFIERS if re.search(r"\b" + q + r"\b", upper)
    )

    return text, {
        "plan": plan,
        "option": option,
        "frequency": frequency,
        "qualifiers": qualifiers,
    }


def _to_core_name(text):
    """Delete structural filler and punctuation, leaving the distinguishing words."""
    out = text.upper()
    out = out.replace("&", " AND ")
    out = re.sub(r"[^A-Z0-9\s]", " ", out)
    out = re.sub(r"\s+", " ", out).strip()
    words = [w for w in out.split() if w not in _FILLER]
    return " ".join(words)


def parse_scheme_key(name, amc_code=None, alias_fn=None):
    """Parse a raw RTA or AMFI scheme name into a SchemeKey.

    `alias_fn`, when supplied, is called as alias_fn(text, amc_code) between
    parenthetical stripping and attribute extraction. Task 5 supplies it.
    Returns None when the name is empty.
    """
    if name is None:
        return None

    text = str(name).strip()
    if not text:
        return None

    text = strip_parentheticals(text)

    if alias_fn is not None:
        text = alias_fn(text, amc_code)

    _, attrs = extract_attributes(text)
    core = _to_core_name(text)

    return SchemeKey(
        amc_code=amc_code,
        core_name=core,
        plan=attrs["plan"],
        option=attrs["option"],
        frequency=attrs["frequency"],
        qualifiers=attrs["qualifiers"],
    )

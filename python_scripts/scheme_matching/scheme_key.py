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

# A fund-of-funds is a different scheme from the ETF it holds, with its own NAV
# (DSP Gold ETF trades at 147.85, DSP Gold ETF Fund of Fund at 23.84). FUND and
# OF are both filler, so without this the two reduce to the same core name.
# The FOF -> FUND OF FUNDS token alias means the bare abbreviation lands here too.
_FOF_PATTERN = r"\bFUND\s+OF\s+FUNDS?\b"

# SEBI's 2021 circular renamed the dividend option to "Income Distribution cum
# Capital Withdrawal". 654 live AMFI names use it and 350 of those carry no
# IDCW/DIVIDEND token at all, so without the phrase they read as Growth.
# PAYOUT and REINVESTMENT name the same share class and never co-occur with
# GROWTH in the AMFI master (0 of 14,268 names).
#
# "DIVIDEND YIELD" is a fund category, not a payout option — 52 AMFI schemes
# are named that way and most of them are Growth. The lookahead stops the
# category name being read as an IDCW signal.
_IDCW_TOKENS = (
    r"\b(IDCW|DIVIDEND|DIV)\b(?!\s+YIELD)"
    r"|INCOME\s+DISTRIBUTION"
    r"|\bPAYOUT\b"
    r"|\bREINVEST"
)

# Removed as a phrase, never word by word: CAPITAL appears in 397 live names
# outside this phrase ("HDFC Capital Builder"), so dropping it as filler would
# erase part of those fund names.
_IDCW_PHRASE = r"\bINCOME\s+DISTRIBUTION\s+CUM\s+CAPITAL\s+WITHDRAWAL\b"

# Deleted only after attribute extraction.
_FILLER = {
    "FUND", "FUNDS", "SCHEME", "PLAN", "PLANS", "OPTION", "OPTIONS",
    "THE", "OF", "AN", "A", "MUTUAL",
    "REGULAR", "DIRECT", "GROWTH", "IDCW", "DIVIDEND", "DIV",
    "APPRECIATION",
    # ABSL writes its growth option as "Growth / Payment". Only 6 live names
    # use the word at all, none of them as part of a fund name.
    "PAYMENT",
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
    # "Segregated Portfolio - 1 with 3.69%". CAMS reports the segregated
    # portion as a percentage; AMFI names the same share class without it. The
    # figure describes how much of the fund was side-pocketed, not which share
    # class this is, and it differs between files for one scheme -- so it
    # cannot take part in identity. Removed here, while the % is still present
    # to anchor the match: by the time the core name is built the punctuation
    # is gone and "with 3.69%" reads as the words "WITH 3 69", which no longer
    # distinguishes itself from a fund legitimately named "... With ...".
    # The portfolio NUMBER is deliberately left alone -- Portfolio 1 and
    # Portfolio 2 are different pools of assets.
    r"\bWITH\s+\d+(?:\.\d+)?\s*%",
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

    found = {q for q in _QUALIFIERS if re.search(r"\b" + q + r"\b", upper)}
    if re.search(_FOF_PATTERN, upper):
        found.add("FOF")
    qualifiers = frozenset(found)

    return text, {
        "plan": plan,
        "option": option,
        "frequency": frequency,
        "qualifiers": qualifiers,
    }


# Strict roman-numeral grammar. Anchored, so only a whole token matches, and
# subtractive forms are the standard ones -- "IIII" and "VV" are rejected.
_ROMAN_RE = re.compile(
    r"^M{0,3}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$"
)
_ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}

# Above this, a "numeral" is a series LABEL rather than a number. Measured over
# all 39,640 master names: every genuine series numeral falls between II (2) and
# XLVI (46), while every false positive is 51 or more -- LI, LV, LX, CC, CD, CI,
# CL, CM, CV, CX, DC, DI, DL, DV, DX, MD, MX. Those are ABSL's two-letter fixed
# term plan codes, the same alphabet as its ED, FF and JJ series, and
# "Principal Bank CD Fund" is a certificate of deposit. The gap between 46 and
# 51 is what this cap sits in; it is drawn from observed data, so a house that
# one day numbers a series L or beyond in roman would need it revisited.
_ROMAN_MAX = 49


def roman_to_arabic(token):
    """"XIV" -> "14". Returns None when the token is not a roman numeral.

    NOT applied when building core names, deliberately. rules.numbers_conflict
    keeps roman markers in a namespace of their own (romans_in) so that a
    series GROUP written in roman cannot be confused with a series NUMBER
    written in arabic. Converting here collapses the two: for "Reliance Fixed
    Horizon Fund - XXII - Series 11" the RTA numbers become {11, 22} and the
    candidate "XXII - Series 22" becomes {22}, so the subset test reads 22 as
    extra detail rather than a contradiction and Series 11 matches Series 22.
    That mapping was produced and caught in review.

    Used instead by nav_name_match.match_nav_anchored, where the comparison is
    local to one scheme's NAV candidates and cannot disturb the engine.

    SINGLE letters are refused outright, and that restriction is the whole
    reason this is safe. Closed-ended funds label their series with letters,
    and the master holds Series C x170, V x134, X x116, D x20, M x15 and L x7 --
    reading those as 100, 5, 10, 500, 1000 and 50 would collapse hundreds of
    unrelated schemes onto shared keys. A multi-letter token that parses as a
    valid numeral is unambiguous by comparison: two-letter series labels in use
    (ED, FF, JJ, CH, DD) contain letters outside the roman alphabet or break
    the grammar above.
    """
    if not token or len(token) < 2:
        return None
    token = token.upper()
    if not _ROMAN_RE.match(token):
        return None

    total, previous = 0, 0
    for char in reversed(token):
        value = _ROMAN_VALUES[char]
        total = total - value if value < previous else total + value
        previous = max(previous, value)
    if total > _ROMAN_MAX:
        return None
    return str(total)


def _to_core_name(text):
    """Delete structural filler and punctuation, leaving the distinguishing words."""
    out = text.upper()
    out = out.replace("&", " AND ")
    out = re.sub(r"[^A-Z0-9\s]", " ", out)
    out = re.sub(r"\s+", " ", out).strip()
    # After punctuation is flattened, so "Withdrawal(IDCW)" and "Withdrawal
    # (IDCW)" reduce alike; before word filler, so the phrase goes as a unit.
    out = re.sub(_IDCW_PHRASE, " ", out)
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

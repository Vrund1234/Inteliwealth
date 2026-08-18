"""Structured-key matching restricted to a NAV-derived candidate set.

Exists for the closed and matured schemes that live only in
public.scheme_master. The structured engine builds its index from
public.amfi_scheme_master, so those schemes are invisible to it no matter how
well their names match. NAV lookup does reach scheme_master, so a NAV
fingerprint is what narrows 37,764 historical names down to a handful; the
name comparison then runs only inside that handful.

The comparison is SchemeKey equality, never a similarity score. On the real
data a fuzzy scorer ranked the 385-day SBI Debt Series C-30 above the true
1228-day one, because AMC renames (Nippon/Reliance, Bandhan/IDFC) depress a
correct match to ~84 while a wrong sibling with the right AMC name reaches 94.
No threshold separates those two populations. SchemeKey does, because it keeps
the duration and option as fields instead of blending them into a ratio.
"""

import re
from dataclasses import dataclass

from rapidfuzz import fuzz

from scheme_matching.scheme_key import parse_scheme_key, roman_to_arabic

RESOLVED = "RESOLVED"
AMBIGUOUS = "AMBIGUOUS"
NO_MATCH = "NO_MATCH"


@dataclass(frozen=True)
class MatchResult:
    """Outcome for one RTA scheme.

    amfi_scheme_code is populated only for RESOLVED. AMBIGUOUS keeps its codes
    in `matches` so the caller can offer them for review, but deliberately
    carries no code: picking one would be a guess, and this rule exists
    precisely because guessing is what produced the wrong mappings.
    """

    status: str
    amfi_scheme_code: str | None
    matches: tuple


def nav_candidate_union(samples, nav_lookup):
    """Every scheme whose NAV equals this scheme's price on ANY sampled date.

    Rule 3.5 intersects these per-date sets instead, which is why it leaves 33
    of the 56 unresolved: a closed fund's sampled prices mix its Rs.10 launch
    NAV, shared with thousands of schemes, and its real NAV on a later date,
    shared with none. The true scheme sits in one set and not the other, so the
    intersection is empty exactly when the evidence is strongest.

    The union is permissive on purpose. It is a filter, not a decision — the
    key comparison downstream is what rejects the wrong ones.
    """
    union = set()
    for sample in samples:
        union |= nav_lookup.get(sample, set())
    return union


def match_within_candidates(rta_scheme_name, candidates, alias_fn=None):
    """Find the one candidate whose parsed key equals the RTA scheme's.

    `candidates` maps amfi/scheme_master code -> name. A candidate with no
    name is skipped rather than treated as a blank key, which would otherwise
    match any other unparseable name.

    amc_code is not passed to parse_scheme_key: these candidates come from
    scheme_master, which has no amc_code column, so scoping the alias
    application to an AMC would apply it to one side of the comparison only.
    """
    rta_key = parse_scheme_key(rta_scheme_name, amc_code=None, alias_fn=alias_fn)
    if rta_key is None:
        return MatchResult(NO_MATCH, None, ())

    matches = []
    for code, name in candidates.items():
        if not name:
            continue
        candidate_key = parse_scheme_key(name, amc_code=None, alias_fn=alias_fn)
        if candidate_key is not None and candidate_key == rta_key:
            matches.append(str(code))

    if len(matches) == 1:
        return MatchResult(RESOLVED, matches[0], tuple(matches))
    if len(matches) > 1:
        return MatchResult(AMBIGUOUS, None, tuple(matches))
    return MatchResult(NO_MATCH, None, ())


def match_in_key_index(rta_scheme_name, key_index, alias_fn=None):
    """Exact key match against the whole universe, with no NAV filter.

    For the schemes whose NAV evidence is missing or unusable, which the
    NAV-filtered path cannot reach at all: if the sampled dates are absent from
    nav_master, the candidate set is empty and the right answer was never on
    the table.

    Dropping the NAV filter widens the search from a handful of names to
    39,640, so the only thing standing between this and a wrong mapping is the
    test itself -- full SchemeKey equality plus uniqueness. Both are kept:
    a key held by two schemes is ambiguous and resolves to nothing, exactly as
    in the NAV-filtered path.

    This is the weaker of the two signals and should be recorded as such. On
    the live residual the two paths agreed on all 26 schemes where both fired,
    with no disagreements, which is the evidence that the wider search is not
    buying reach at the cost of accuracy.
    """
    key = parse_scheme_key(rta_scheme_name, amc_code=None, alias_fn=alias_fn)
    if key is None:
        return MatchResult(NO_MATCH, None, ())

    matches = sorted(str(code) for code in key_index.get(key, ()))
    if len(matches) == 1:
        return MatchResult(RESOLVED, matches[0], tuple(matches))
    if len(matches) > 1:
        return MatchResult(AMBIGUOUS, None, tuple(matches))
    return MatchResult(NO_MATCH, None, ())


# A unique NAV fingerprint is strong evidence on its own: one scheme in the
# whole master priced identically on a sampled date. There the name only has to
# corroborate, so a lower bar is defensible. Once several schemes share that
# price the name is what discriminates between them, and it has to be strict.
SOLE_CANDIDATE_THRESHOLD = 70
CONTESTED_THRESHOLD = 90


def _arabicised(core_name):
    """Core name with roman series numerals rewritten as digits.

    Applied only within this rule. scheme_key deliberately leaves romans alone
    so that rules.numbers_conflict can keep a roman series GROUP apart from an
    arabic series NUMBER; doing it there mapped Series 11 onto Series 22. Here
    the comparison never leaves one scheme's own NAV candidates, so the
    normalisation cannot reach the engine.
    """
    return " ".join(
        roman_to_arabic(token) or token for token in core_name.split()
    )


def _numeric_tokens(core_name):
    """The numbers in a core name, sorted, romans included.

    These carry the series, duration and portfolio number -- the tokens that
    separate a scheme from its siblings. Everything else in the name can vary
    with house style; these cannot.
    """
    return sorted(
        token for token in _arabicised(core_name).split() if token.isdigit()
    )


def _squashed(core_name):
    """Core name with separators removed and romans in digits, so that
    MID CAP == MIDCAP and SERIES II == SERIES 2."""
    return re.sub(r"[^A-Z0-9]", "", _arabicised(core_name).upper())


def match_nav_anchored(rta_scheme_name, candidates, alias_fn=None):
    """Resolve on a close name match inside the NAV-derived candidate set.

    The looser sibling of match_within_candidates, for names that differ only
    in house style: "Mid Cap" against "Midcap", where an exact key comparison
    refuses a match the NAV evidence has already made.

    Three things are NOT relaxed, and they are what keep it safe:

      * plan, option, frequency and qualifiers must be equal, so a Growth
        share class can never absorb its IDCW sibling
      * the multiset of numeric tokens must be equal -- the guard that rejects
        the 385-day SBI fund for the 1228-day one despite 87% similarity, and
        Series 1 for Series 140
      * exactly one candidate may pass; a tie resolves to nothing

    The similarity thresholds are calibrated on a residual of 14 schemes, which
    is few enough that they should be treated as provisional. The three guards
    above are structural and do not depend on that calibration; the thresholds
    are the part most likely to need revisiting on new data, which is why this
    rule queues for review rather than writing a mapping.
    """
    rta_key = parse_scheme_key(rta_scheme_name, amc_code=None, alias_fn=alias_fn)
    if rta_key is None or not candidates:
        return MatchResult(NO_MATCH, None, ())

    threshold = (
        SOLE_CANDIDATE_THRESHOLD if len(candidates) == 1 else CONTESTED_THRESHOLD
    )
    rta_numbers = _numeric_tokens(rta_key.core_name)
    rta_squashed = _squashed(rta_key.core_name)

    passing = []
    for code, name in candidates.items():
        if not name:
            continue
        candidate_key = parse_scheme_key(name, amc_code=None, alias_fn=alias_fn)
        if candidate_key is None:
            continue
        if candidate_key.bucket()[1:] != rta_key.bucket()[1:]:
            # bucket() leads with amc_code, which scheme_master does not carry;
            # everything after it is plan/option/frequency/qualifiers.
            continue
        if _numeric_tokens(candidate_key.core_name) != rta_numbers:
            continue
        if fuzz.ratio(rta_squashed, _squashed(candidate_key.core_name)) >= threshold:
            passing.append(str(code))

    if len(passing) == 1:
        return MatchResult(RESOLVED, passing[0], tuple(passing))
    if len(passing) > 1:
        return MatchResult(AMBIGUOUS, None, tuple(sorted(passing)))
    return MatchResult(NO_MATCH, None, ())

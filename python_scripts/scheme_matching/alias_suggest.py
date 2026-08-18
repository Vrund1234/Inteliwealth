"""Derive candidate alias rows from near-miss scheme pairs.

bronze.scheme_name_alias is the compounding asset in this pipeline: one row
resolves a whole family of schemes and keeps resolving them for every file that
arrives afterwards. NIPPON INDIA -> RELIANCE alone moved 12 schemes across two
different rules. Authoring those rows by reading unmatched names one at a time
is the manual cost this module exists to remove.

The miner is intentionally loose and the guards do the work, because the input
is unreliable by construction: an unmatched scheme's NAV candidates include
every fund that happened to share a price on a sampled date, most of which are
unrelated. Nothing here writes an alias. It produces a ranked, evidenced
shortlist for a human to accept or reject.
"""

import difflib

from scheme_matching.scheme_key import parse_scheme_key

# A proposal needs enough unchanged context to be evidence that the two names
# describe the same fund. Without this, two unrelated funds that happen to
# share a launch NAV read as a single contiguous "replacement" of the whole
# core and are proposed as a rename of one house's fund into another's.
MIN_SHARED_TOKENS = 2
MIN_SHARED_RATIO = 0.3


def derive_term_pair(rta_core, candidate_core):
    """The one run of tokens that differs between two core names.

    Returns (raw_term, normalized_term), or None when the difference is not a
    single localised edit backed by enough shared context.
    """
    a, b = rta_core.split(), candidate_core.split()
    if not a or not b or a == b:
        return None

    matcher = difflib.SequenceMatcher(None, a, b)
    edits = [op for op in matcher.get_opcodes() if op[0] != "equal"]
    if len(edits) != 1:
        # Two or more separate edits describe two or more rules. Proposing
        # either one alone would not resolve the scheme.
        return None

    shared = sum(op[2] - op[1] for op in matcher.get_opcodes() if op[0] == "equal")
    if shared < MIN_SHARED_TOKENS or shared / len(a) < MIN_SHARED_RATIO:
        return None

    _, i1, i2, j1, j2 = edits[0]
    raw, normalized = " ".join(a[i1:i2]), " ".join(b[j1:j2])
    if not raw or not normalized:
        # A pure insertion or deletion. Filler words are already handled by
        # scheme_key; anything else here is noise rather than a rename.
        return None
    return raw, normalized


def is_structurally_safe(raw, normalized):
    """False for proposals no amount of corroborating evidence can redeem.

    Numeric-to-numeric is the one that matters. In these names numbers are the
    series and duration -- the only tokens separating sibling schemes -- so
    aliasing one to another silently maps a scheme onto its sibling. The
    collision report cannot catch it either: when the correct counterpart does
    not exist in the master, nothing collapses and the wrong mapping simply
    looks clean.
    """
    if not raw or not normalized:
        return False

    def all_numeric(term):
        parts = term.split()
        return bool(parts) and all(p.isdigit() for p in parts)

    if all_numeric(raw) and all_numeric(normalized):
        return False
    return True


def build_key_index(names_by_code, alias_fn):
    """{SchemeKey: {code, ...}} over a scheme-name universe."""
    index = {}
    for code, name in names_by_code.items():
        if not name:
            continue
        key = parse_scheme_key(name, amc_code=None, alias_fn=alias_fn)
        if key is None:
            continue
        index.setdefault(key, set()).add(str(code))
    return index


def new_collisions(names_by_code, base_index, alias_fn, raw_term):
    """Schemes an alias would newly collapse onto a shared key.

    Only names containing raw_term can change key, so only those are re-parsed
    and compared against the baseline index. Re-keying the whole universe per
    candidate would be correct too, and far too slow to run over a shortlist.

    Returns [(key, {codes}), ...] for keys that would hold more than one scheme
    and did not before.
    """
    needle = raw_term.upper()
    found = []

    for code, name in names_by_code.items():
        if not name or needle not in str(name).upper():
            continue
        new_key = parse_scheme_key(str(name), amc_code=None, alias_fn=alias_fn)
        if new_key is None:
            continue

        # Whoever already owns this key, minus this scheme itself: rewriting a
        # term that appears in only one name changes its key but collides with
        # nothing, and a scheme never collides with itself.
        occupants = set(base_index.get(new_key, set())) - {str(code)}
        if occupants:
            found.append((new_key, occupants | {str(code)}))

    return found

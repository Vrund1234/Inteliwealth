"""Client identity mapping.

Decides which client each folio in silver.investor_master belongs
to, and records the decision -- with the rule that made it -- in
bronze.client_mapping_review.

Four rules, in order. First match wins.

    1. own PAN present               PAN:<pan>
    2. no PAN, guardian PAN present  GPAN:<guard_pan>|<dob>|<first>
    3. no PAN and no guardian PAN    NAME:<name>|<dob>
    4. no name either                FOLIO:<source>|<folio_no>

Structured like scheme_matching.rules: an ordered registry, each
rule returning Candidates carrying a confidence, and arbitrate()
picking the winner. Every rule runs on every row, so a folio's
runners-up are known and can be shown to a reviewer.

Run:
    python client_mapping.py            map and write the review table
    python client_mapping.py --dry-run  map and report, write nothing
"""

import sys
import traceback

import pandas as pd
from sqlalchemy import bindparam, text

from utils.db import engine


# ============================================================
# RULE REGISTRY
# ============================================================

# Registry order. Also the tie-breaker when two rules return
# equal confidence.
RULE_ORDER = [
    "PAN_EXACT",
    "GUARDIAN_PAN",
    "GUARDIAN_PAN_NO_DOB",
    "NAME_CLUSTER",
    "FOLIO_FALLBACK",
]

CONFIDENCE = {
    "PAN_EXACT": 100,
    "GUARDIAN_PAN": 98,
    "GUARDIAN_PAN_NO_DOB": 92,
    "NAME_CLUSTER": 85,
    "FOLIO_FALLBACK": 60,
}

# A winner below this goes to review even when unambiguous: the
# evidence behind it is a name rather than a registry identifier.
REVIEW_CONFIDENCE = 95

# Rule 3. A client identified this way has no PAN and no
# guardian PAN -- only a name -- so the mapping is always put in
# front of a reviewer, unique or not.
NAME_BASED_RULES = frozenset({
    "NAME_CLUSTER",
})

# Two candidates closer than this do not distinguish the person.
# Same idea as scheme_matching.rules.FUZZY_MARGIN.
MATCH_MARGIN = 5

PAN_REGEX = r"^[A-Z]{5}[0-9]{4}[A-Z]$"

NAME_MATCH_EXACT = 100

NAME_MATCH_SUBSUME = 95

# Rule 3 merges at or above this.
NAME_MATCH_MERGE = 95

# A pair below the merge threshold but still this similar is not
# merged -- both sides go to the review table instead.
NAME_REVIEW_CUTOFF = 85

# The fuzzy ratio is capped BELOW the merge threshold, always.
# It is a similarity number, not evidence of identity: it scores
# the Shah twins NIVAA / NIVAAN at 97 and the Desai spouses at
# 85. Only exact set equality (100) and guarded subsumption (95)
# may merge; a ratio can never do it on its own.
NAME_RATIO_CAP = NAME_MATCH_MERGE - 1

NAME_TITLE_REGEX = (
    r"^(MASTER|MSTR|MISS|BABY|KUM|KUMARI|MR|MRS|SMT|SHRI)[ .]+"
)

# PAN character 4 encodes the entity. A guardian must be 'P'.
PAN_ENTITY = {
    "P": "Individual",
    "H": "HUF",
    "C": "Company",
    "T": "Trust",
    "F": "Firm",
    "A": "AOP",
    "B": "BOI",
    "J": "Artificial Juridical",
    "G": "Government",
    "L": "Local Authority",
}


class Candidate:
    """One rule's answer for one folio."""

    __slots__ = ("client_ref", "score", "rule_name", "confidence")

    def __init__(self, client_ref, score, rule_name):

        self.client_ref = client_ref

        self.score = float(score)

        self.rule_name = rule_name

        self.confidence = CONFIDENCE[rule_name]

    def __repr__(self):

        return "Candidate({}, {}, {})".format(
            self.rule_name,
            self.client_ref,
            self.score
        )


def arbitrate(candidates):
    """Highest confidence, then registry order, then score."""

    if not candidates:

        return None

    def sort_key(candidate):

        try:
            order = RULE_ORDER.index(candidate.rule_name)
        except ValueError:
            order = len(RULE_ORDER)

        return (-candidate.confidence, order, -candidate.score)

    return sorted(candidates, key=sort_key)[0]


# ============================================================
# NORMALISATION
# ============================================================

def clean_string(series):

    return (
        series
        .fillna("")
        .astype(str)
        .str.strip()
        .replace(["", "NAN", "NONE", "NULL", "NAT"], pd.NA)
    )


def valid_pan(series):
    """A PAN is AAAAA9999A. Anything else is treated as absent.

    Live junk this rejects: guard_pan = '0', a KFIN placeholder,
    and pan_no = 'NON RESIDENT'. Left in, a placeholder becomes a
    family key and files unrelated investors under one guardian.
    """

    cleaned = (
        clean_string(series)
        .str.upper()
        .str.strip()
        .str[:10]
    )

    return cleaned.where(
        cleaned.str.match(PAN_REGEX, na=False),
        pd.NA
    )


def pan_entity(value):

    if value is None or pd.isna(value):

        return None

    return PAN_ENTITY.get(str(value)[3], "Unknown")


def norm_name(series):
    """Upper-cased, stripped of titles, punctuation and the KFIN
    guardian suffix.

    KFIN stores a minor as "DHYANI PATEL REP BY NIKESH PATEL".
    Left in place, the guardian's name is carried into the
    child's key.
    """

    name = clean_string(series).fillna("").astype(str).str.upper()

    name = name.str.replace(
        r"\s+REP\s+BY\s+.*$", "", regex=True
    )

    name = name.str.replace(NAME_TITLE_REGEX, "", regex=True)

    name = name.str.replace(r"[^A-Z0-9 ]", "", regex=True)

    name = (
        name
        .str.replace(r"\s+", " ", regex=True)
        .str.strip()
    )

    return name.replace("", pd.NA)


def name_tokens(value):

    if value is None or pd.isna(value):

        return frozenset()

    return frozenset(t for t in str(value).split(" ") if t)


def _token_matches(candidate, token, allow_initial=True):
    """Do two name tokens refer to the same word?

    `allow_initial` gates the K -> KANUBHAI expansion. It is a
    powerful rule and a dangerous one: a single letter otherwise
    matches ANY token starting with it, so DWITI P PATEL matched
    D P TRADING CO -- D took DWITI, P took PATEL -- and a child
    was merged into an HUF's trade name. The caller only enables
    it when the two names share a surname.
    """

    if candidate == token:

        return True

    if not allow_initial:

        return False

    if len(token) == 1 and candidate.startswith(token):

        return True

    if len(candidate) == 1 and token.startswith(candidate):

        return True

    return False


def tokens_subsume(bigger, smaller, allow_initial=True):
    """Every token of `smaller` appears in `bigger`.

    Sets, not sequences -- which is what makes
    PATEL TUSHAR JITENDRABHAI match TUSHAR PATEL.
    """

    if not bigger or not smaller:

        return False

    return all(
        any(
            _token_matches(c, t, allow_initial)
            for c in bigger
        )
        for t in smaller
    )


def _leading_token(value):

    parts = str(value).split(" ") if value is not None else []

    return parts[0] if parts else ""


def _last_token(value):

    parts = str(value).split(" ") if value is not None else []

    return parts[-1] if parts else ""


def _subsumes_safely(
    longer_value,
    longer_tokens,
    shorter_tokens,
    allow_initial=True,
):
    """`shorter` fits inside `longer` without losing a leading name.

    Subsumption is only safe when the longer name ADDS a middle
    or trailing token:

        NILESH PATEL   / NILESH KANUBHAI PATEL      same person
        TUSHAR PATEL   / PATEL TUSHAR JITENDRABHAI  same person
        JITENDRA DESAI / MINA JITENDRA DESAI        husband, wife

    The third is a subsumption too, and merged a wife into her
    husband until this guard was added: the longer name's leading
    token must itself appear in the shorter name.
    """

    if not tokens_subsume(
        longer_tokens, shorter_tokens, allow_initial
    ):

        return False

    lead = _leading_token(longer_value)

    if not lead:

        return False

    return any(
        _token_matches(token, lead, allow_initial)
        for token in shorter_tokens
    )


def name_match_score(left, right):
    """100 exact, 95 guarded subsumption, else informational.

    WHY NOT A RATIO. Measured on live pairs:

      token_set_ratio  handles short forms (NILESH PATEL vs
                       NILESH KANUBHAI PATEL = 100) but scores
                       the Bhandari siblings 93 and the Shah
                       twins 97.
      token_sort_ratio separates siblings (76) but fails short
                       forms (73) and still scores the twins 97.

    No single ratio separates them, so the ratio is kept only to
    route a close pair to review and is capped below the merge
    threshold. A merge is reachable only through set equality or
    guarded subsumption.
    """

    left_tokens = name_tokens(left)

    right_tokens = name_tokens(right)

    if not left_tokens or not right_tokens:

        return 0.0

    if left_tokens == right_tokens:

        return float(NAME_MATCH_EXACT)

    # Initial expansion is only allowed between two names that
    # share a surname. NILESH K PATEL and NILESH KANUBHAI PATEL
    # do; DWITI P PATEL and D P TRADING CO do not.
    allow_initial = (
        _last_token(left) == _last_token(right)
        and _last_token(left) != ""
    )

    if (
        _subsumes_safely(
            left, left_tokens, right_tokens, allow_initial
        )
        or _subsumes_safely(
            right, right_tokens, left_tokens, allow_initial
        )
    ):

        return float(NAME_MATCH_SUBSUME)

    from rapidfuzz import fuzz

    ratio = fuzz.token_sort_ratio(str(left), str(right))

    return float(min(ratio, NAME_RATIO_CAP))


def dob_conflicts(left, right):
    """Two known and different dates of birth veto any merge."""

    if left is None or right is None:

        return False

    if pd.isna(left) or pd.isna(right):

        return False

    return left != right


# ============================================================
# THE FOUR RULES
# ============================================================

def rule_pan_exact(row, context):
    """RULE 1 -- the investor's own PAN."""

    if row["own_pan"] is None:

        return []

    return [Candidate("PAN:" + row["own_pan"], 100.0, "PAN_EXACT")]


def rule_guardian_pan(row, context):
    """RULE 2 -- guardian PAN + DOB + first name.

    Fires only on a folio with NO PAN OF ITS OWN that carries a
    guardian PAN. Absence, never PAN equality -- a rule of "this
    PAN is somebody's guardian PAN" misclassifies the guardians
    who are also clients in their own right.

    First name, not full name: inside one family the key must
    survive middle-name drift (DHYANI N PATEL / DHYANI PATEL)
    while still separating twins (NIVAA / NIVAAN).
    """

    if row["own_pan"] is not None or row["guard_pan"] is None:

        return []

    if row["dob"] is not None and row["first_name"] is not None:

        return [
            Candidate(
                "GPAN:{}|{}|{}".format(
                    row["guard_pan"], row["dob"], row["first_name"]
                ),
                100.0,
                "GUARDIAN_PAN",
            )
        ]

    if row["name"] is not None:

        return [
            Candidate(
                "GPAN:{}|?|{}".format(
                    row["guard_pan"],
                    str(row["name"]).replace(" ", "-")
                ),
                100.0,
                "GUARDIAN_PAN_NO_DOB",
            )
        ]

    return []


# RULE 3a -- NAME_ATTACH -- REMOVED DELIBERATELY.
#
# It attached a PAN-less folio to a client who already had a
# PAN, on the strength of a name match. A different PAN means a
# different person, and a folio carrying no PAN is not evidence
# that it belongs to someone who has one.
#
# The DOB veto was not enough of a guard, because a null DOB
# cannot conflict with anything: three of the four merges it made
# here (SUREEL YOGENDRA BHATT, ANIMESH J MEHTA, PRITIPAL SHAH)
# had no DOB at all and rested on the name alone. Names in this
# data differ by a single letter between different people --
# SUREEL BHATT (AQEPB6066F, 1977) and SALEEL BHATT (AAYPB0139M,
# 1971) are two men -- so a name is not an identifier.
#
# A PAN-less folio now falls through to rule 3b (full name + DOB
# clustered with its PAN-less peers) and then to rule 4
# (source + folio), never onto somebody else's PAN.
#
# The consequence is accepted and intended: a person whose folio
# genuinely lost its PAN is stored as a second, PAN-less client
# rather than being silently merged into the first.


def rule_name_cluster(row, context):
    """RULE 3b -- full name + DOB, clustered with its peers.

    One entity may hold several folios: Pavanendra Bhatt Heritage
    Fund holds two, and keying on folio would split it in two.
    """

    if row["own_pan"] is not None or row["guard_pan"] is not None:

        return []

    ref = context.get("cluster_ref", {}).get(row["index"])

    if ref is None:

        return []

    return [Candidate(ref, 100.0, "NAME_CLUSTER")]


def rule_folio_fallback(row, context):
    """RULE 4 -- source + folio. The safety net.

    Nothing reaches it on the current data; it exists so a folio
    can never fall off the end without an identity.
    """

    if row["folio_key"] is None:

        return []

    return [
        Candidate("FOLIO:" + row["folio_key"], 100.0, "FOLIO_FALLBACK")
    ]


RULE_REGISTRY = [
    rule_pan_exact,
    rule_guardian_pan,
    rule_name_cluster,
    rule_folio_fallback,
]


# ============================================================
# STRUCTURAL CORROBORATION
#
# Facts that must hold regardless of any score. A mismatch never
# changes the winner -- it sends the folio to review.
#
# NOTE a folio whose only PAN is the guardian's is deliberately
# NOT flagged. That is the normal rule 2 path for every CAMS
# minor, and flagging it buries the real problems under routine
# noise.
# ============================================================

def structural_mismatch(row):

    problems = []

    if row["raw_guard_pan"] and row["guard_pan"] is None:

        problems.append(
            "guard_pan '{}' is not a valid PAN".format(
                row["raw_guard_pan"]
            )
        )

    if (
        row["raw_pan_no"]
        and row["own_pan"] is None
        and not row["borrowed"]
    ):

        problems.append(
            "pan_no '{}' is not a valid PAN".format(row["raw_pan_no"])
        )

    if row["guard_pan"] and row["guard_entity"] != "Individual":

        problems.append(
            "guardian PAN is a {}, not an individual".format(
                row["guard_entity"]
            )
        )

    return "; ".join(problems) or None


# ============================================================
# CLUSTERING FOR RULE 3b
# ============================================================

def cluster_by_name(names, dobs):
    """Union rows whose names subsume one another.

    A pair in the review band is NOT merged -- it is reported.
    """

    count = len(names)

    parent = list(range(count))

    def find(node):

        while parent[node] != node:

            parent[node] = parent[parent[node]]

            node = parent[node]

        return node

    def union(left, right):

        left_root, right_root = find(left), find(right)

        if left_root != right_root:

            parent[right_root] = left_root

    near_misses = {}

    for i in range(count):

        for j in range(i + 1, count):

            if dob_conflicts(dobs[i], dobs[j]):

                continue

            score = name_match_score(names[i], names[j])

            if score >= NAME_MATCH_MERGE:

                union(i, j)

            elif score >= NAME_REVIEW_CUTOFF:

                near_misses.setdefault(i, []).append(
                    (names[j], score)
                )

                near_misses.setdefault(j, []).append(
                    (names[i], score)
                )

    return [find(i) for i in range(count)], near_misses


def build_cluster_refs(indexes, names, dobs):
    """NAME:<canonical name>|<dob> per PAN-less folio."""

    labels, near_misses = cluster_by_name(names, dobs)

    best_name = {}

    best_dob = {}

    for position, label in enumerate(labels):

        name = names[position]

        if name is None:

            continue

        current = best_name.get(label)

        if (
            current is None
            or len(name_tokens(name)) > len(name_tokens(current))
            or (
                len(name_tokens(name)) == len(name_tokens(current))
                and len(str(name)) > len(str(current))
            )
        ):

            best_name[label] = name

        if best_dob.get(label) is None and dobs[position] is not None:

            best_dob[label] = dobs[position]

    refs = {}

    misses = {}

    for position, label in enumerate(labels):

        name = best_name.get(label)

        if name is None:

            continue

        dob = best_dob.get(label)

        # Spaces become '-', not nothing. Stripping them lets two
        # DIFFERENT clusters collide on one ref: RAMESH BHAI SHAH
        # and RAMESHBHAI SHAH both flatten to RAMESHBHAISHAH and
        # silently become one client, even though the matcher
        # scored them below the merge threshold.
        refs[indexes[position]] = "NAME:{}|{}".format(
            str(name).replace(" ", "-"),
            dob if dob is not None else "?",
        )

        if position in near_misses:

            misses[indexes[position]] = near_misses[position]

    return refs, misses


# ============================================================
# EXTRACT
# ============================================================

def extract_folios():

    print("=" * 80)
    print("READING silver.investor_master")
    print("=" * 80)

    df = pd.read_sql(
        """
        SELECT
            source,
            folio_no,
            investor_name,
            pan_no,
            guardian_pan,
            dob
        FROM silver.investor_master
        """,
        engine,
    )

    print("Rows :", len(df))

    return df


# ============================================================
# MAP
# ============================================================

def map_clients(df):

    print()
    print("=" * 80)
    print("MAPPING CLIENTS")
    print("=" * 80)

    raw_pan_no = clean_string(df["pan_no"])

    raw_guard_pan = clean_string(df["guardian_pan"])

    own_pan = valid_pan(df["pan_no"])

    guard_pan = valid_pan(df["guardian_pan"])

    # A candidate equal to THIS folio's guardian PAN is not the
    # investor's own. CAMS puts the guardian's PAN in the
    # ordinary pan field with no flag at all.
    borrowed = (
        own_pan.notna()
        & guard_pan.notna()
        & (own_pan == guard_pan)
    )

    own_pan = own_pan.where(~borrowed, pd.NA)

    name = norm_name(df["investor_name"])

    first = (
        name.fillna("").str.split(" ").str[0].replace("", pd.NA)
    )

    dob = pd.to_datetime(df["dob"], errors="coerce").dt.date

    folio_no = (
        clean_string(df["folio_no"])
        .fillna("")
        .astype(str)
        .str.replace(r"\.0$", "", regex=True)
    )

    source = clean_string(df["source"]).fillna("").str.upper()

    folio_key = source + "|" + folio_no

    def cell(series, index):

        value = series.at[index]

        return None if pd.isna(value) else value

    rows = {
        index: {
            "index": index,
            "own_pan": cell(own_pan, index),
            "guard_pan": cell(guard_pan, index),
            "raw_pan_no": cell(raw_pan_no, index),
            "raw_guard_pan": cell(raw_guard_pan, index),
            "guard_entity": pan_entity(cell(guard_pan, index)),
            "borrowed": bool(borrowed.at[index]),
            "name": cell(name, index),
            "first_name": cell(first, index),
            "dob": cell(dob, index),
            "folio_key": cell(folio_key, index),
            "source": cell(source, index),
            "folio_no": cell(folio_no, index),
        }
        for index in df.index
    }

    # ---- pass 1: the deterministic rules ----

    context = {"known": [], "cluster_ref": {}}

    known = []

    settled = set()

    for index, row in rows.items():

        candidates = []

        for rule in (rule_pan_exact, rule_guardian_pan):

            candidates.extend(rule(row, context))

        winner = arbitrate(candidates)

        if winner is not None:

            settled.add(index)

            if row["name"] is not None:

                known.append(
                    (row["name"], row["dob"], winner.client_ref)
                )

    # dob may be None, so sort on a stringified key rather than
    # letting tuple comparison reach None vs date
    context["known"] = sorted(
        set(known),
        key=lambda item: (item[0], str(item[1]), item[2]),
    )

    # ---- pass 2: cluster whatever is left ----

    unsettled = [
        index
        for index in rows
        if index not in settled and rows[index]["name"] is not None
    ]

    cluster_refs, near_misses = build_cluster_refs(
        unsettled,
        [rows[i]["name"] for i in unsettled],
        [rows[i]["dob"] for i in unsettled],
    )

    context["cluster_ref"] = cluster_refs

    # ---- run the full registry ----

    mapped = []

    data_quality = []

    for index, row in rows.items():

        candidates = []

        for rule in RULE_REGISTRY:

            candidates.extend(rule(row, context))

        winner = arbitrate(candidates)

        if winner is None:

            continue

        mismatch = structural_mismatch(row)

        peers = sorted(
            {
                (c.client_ref, c.score)
                for c in candidates
                if c.confidence == winner.confidence
            },
            key=lambda item: -item[1],
        )

        ambiguous = (
            len(peers) > 1
            and peers[0][1] - peers[1][1] < MATCH_MARGIN
        )

        detail = None

        near = near_misses.get(index)

        # A structural problem only sends a mapping to review when
        # the winning rule actually USED the broken field. A junk
        # guard_pan on a folio mapped by its own PAN does not put
        # that mapping in doubt -- it is a data-quality issue, and
        # it is reported below rather than queued for a decision.
        mapping_affected = bool(
            mismatch
            and winner.rule_name.startswith("GUARDIAN_PAN")
        )

        if ambiguous:

            # two candidates the evidence cannot separate
            reason = "AMBIGUOUS"

            detail = "also matched " + ", ".join(
                ref for ref, _ in peers[1:]
            )

        elif near:

            # Rule 3 compared these two and did NOT clear the
            # threshold, so they are kept apart -- but they are
            # similar enough that a human should confirm the
            # split. Both sides of the comparison are queued.
            reason = "NAME_NO_MATCH"

            detail = "did not match " + ", ".join(
                "{} (score {:.0f}, needs {})".format(
                    other, score, NAME_MATCH_MERGE
                )
                for other, score in near
            )

        elif mapping_affected:

            reason = "STRUCTURAL"

            detail = mismatch

        elif winner.rule_name == "FOLIO_FALLBACK":

            # Rule 4 -- nothing identified this folio: no PAN, no
            # guardian PAN, and no name to match on. It is keyed
            # on source + folio_no so it cannot be lost, but that
            # is a placeholder, not an identity.
            reason = "FOLIO_ONLY"

            detail = "no PAN, no guardian PAN and no usable name"

        elif winner.rule_name in NAME_BASED_RULES:

            # Rule 3 cleared the threshold, so the two are the
            # same client and nothing needs deciding. Only the
            # merges that FAILED are queued -- handled by the
            # near_misses branch above, which puts BOTH sides of
            # the failed comparison in the review table.
            reason = None

        else:

            # unique, uncontested match on a registry identifier
            # -- nothing to decide
            reason = None

        if mismatch and not mapping_affected:

            data_quality.append(
                (row["source"], row["folio_no"], row["name"], mismatch)
            )

        mapped.append(
            {
                "source": row["source"],
                "folio_no": row["folio_no"],
                "name": row["name"],
                "pan_no": row["raw_pan_no"],
                "guard_pan": row["raw_guard_pan"],
                "dob": row["dob"],
                "client_ref": winner.client_ref,
                "rule_name": winner.rule_name,
                "mapping_confidence": winner.confidence,
                "mapping_status": "REVIEW" if reason else "MAPPED",
                "review_reason": reason,
                "review_detail": detail,
            }
        )

    result = pd.DataFrame(mapped)

    # ---- report ----

    print()
    print("Mapping by rule")
    print("-" * 80)

    for rule_name in RULE_ORDER:

        selected = result["rule_name"] == rule_name

        if not selected.any():

            continue

        print(
            "  {:22s} conf {:3d} : {:6d} folios {:6d} clients".format(
                rule_name,
                CONFIDENCE[rule_name],
                int(selected.sum()),
                result.loc[selected, "client_ref"].nunique(),
            )
        )

    print("-" * 80)

    print(
        "  {:22s}          : {:6d} folios {:6d} clients".format(
            "TOTAL",
            len(result),
            result["client_ref"].nunique(),
        )
    )

    review = result[result["mapping_status"] == "REVIEW"]

    print()
    print("Needing review :", len(review))

    if not review.empty:

        for reason, count in review["review_reason"].value_counts().items():

            print("  {:16s} {}".format(reason, count))

    # Bad values that did NOT affect any mapping. Worth fixing at
    # source, but there is no decision for a reviewer to make, so
    # they are reported here rather than queued.
    if data_quality:

        print()
        print("Data quality (mapping unaffected) :", len(data_quality))

        seen = set()

        for source, folio, name, problem in data_quality:

            if problem in seen:

                continue

            seen.add(problem)

            print(
                "  {} {} {} -- {}".format(source, folio, name, problem)
            )

    return result


# ============================================================
# ONE ROW PER CLIENT
#
# The rules run per folio, but a client is not a folio: 3,559
# folios resolve to a few hundred people, and one person can
# hold a dozen. The review table records the CLIENT, so each
# client_ref appears exactly once.
#
# The folio kept as the representative is the one with the best
# evidence -- highest confidence, then a known DOB, then the
# most complete name -- so the reviewer sees the fullest version
# of the person rather than whichever folio happened to sort
# first. folio_count says how many folios were behind it.
#
# A client needs review if ANY of its folios did, and it carries
# that folio's reason.
# ============================================================

def collapse_to_clients(result):

    result = result.copy()

    result["_has_dob"] = result["dob"].notna()

    result["_name_len"] = (
        result["name"].fillna("").astype(str).str.len()
    )

    folio_count = result.groupby("client_ref").size()

    # the folio that best represents each client
    best = (
        result
        .sort_values(
            [
                "client_ref",
                "mapping_confidence",
                "_has_dob",
                "_name_len",
            ],
            ascending=[True, False, False, False],
        )
        .drop_duplicates(subset=["client_ref"], keep="first")
        .copy()
    )

    # a client inherits the review flag of any folio that raised one
    flagged = (
        result[result["mapping_status"] == "REVIEW"]
        .sort_values(["client_ref", "mapping_confidence"])
        .drop_duplicates(subset=["client_ref"], keep="first")
        .set_index("client_ref")
    )

    # For a client under review, the representative must be the
    # folio that RAISED the flag, not the best-looking one.
    # Otherwise the row shows a valid PAN next to the reason
    # "pan_no 'NON RESIDENT' is not a valid PAN", which is the
    # evidence from a different folio and reads as nonsense.
    flagged_rows = (
        result[result["mapping_status"] == "REVIEW"]
        .sort_values(["client_ref", "mapping_confidence"])
        .drop_duplicates(subset=["client_ref"], keep="first")
        .copy()
    )

    best = best[~best["client_ref"].isin(flagged_rows["client_ref"])]

    best["mapping_status"] = "MAPPED"

    best["review_reason"] = None

    best["review_detail"] = None

    best = pd.concat([best, flagged_rows], ignore_index=True)

    best["folio_count"] = (
        best["client_ref"].map(folio_count).astype(int)
    )

    best = best.drop(columns=["_has_dob", "_name_len"])

    # A NAME_CLUSTER client standing on ONE folio was never
    # matched against anything -- the "cluster" has a single
    # member. It got a name key only because it has no PAN, and
    # there is no decision for a reviewer to confirm.
    #
    # Contrast the ones that stay: NAME_ATTACH_* folded a
    # PAN-less folio into an existing client, and a multi-folio
    # NAME_CLUSTER merged folios together. Those are judgements.
    solo = (
        (best["rule_name"] == "NAME_CLUSTER")
        & (best["folio_count"] == 1)
        & (best["review_reason"] == "NAME_BASED")
    )

    if solo.any():

        print(
            "  single-folio name clients (no match made):",
            int(solo.sum())
        )

        best.loc[solo, "mapping_status"] = "MAPPED"

        best.loc[solo, "review_reason"] = None

        best.loc[solo, "review_detail"] = None

    print()
    print("Collapsing folios to clients")
    print("-" * 80)

    print("  folios mapped :", len(result))

    print("  clients       :", len(best))

    print(
        "  needing review:",
        int((best["mapping_status"] == "REVIEW").sum())
    )

    return best.reset_index(drop=True)


# ============================================================
# LOAD
# ============================================================

# The mapper owns this DDL so a fresh database needs nothing run
# by hand. Keyed on client_ref because that is the grain
# collapse_to_clients() emits -- one row per CLIENT, carrying its
# representative folio and folio_count. The older
# sql_scripts/client_mapping_review_2026-09-08.sql keyed on
# (source, folio_no) and had no folio_count column, so the upsert
# below could never have targeted it.
#
# pan_no and guard_pan are deliberately SEPARATE columns: a
# guardian's PAN is never folded into the investor's own. That
# separation is what lets the GPAN: rules and this queue tell a
# minor apart from their guardian.
REVIEW_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS bronze.client_mapping_review (
    review_id           UUID         PRIMARY KEY
                                     DEFAULT gen_random_uuid(),

    source              VARCHAR(20)  NOT NULL,
    folio_no            TEXT         NOT NULL,

    name                TEXT,
    pan_no              TEXT,
    guard_pan           TEXT,
    dob                 DATE,

    client_ref          TEXT         NOT NULL,
    rule_name           VARCHAR(40)  NOT NULL,
    mapping_confidence  INTEGER,
    mapping_status      VARCHAR(20)  NOT NULL
        CHECK (mapping_status IN ('MAPPED', 'REVIEW')),
    folio_count         INTEGER,

    review_reason       VARCHAR(20),
    review_detail       TEXT,

    reviewer_decision   VARCHAR(20)
        CHECK (reviewer_decision IS NULL
               OR reviewer_decision IN ('APPROVED', 'REJECTED')),
    reviewed_by         VARCHAR(80),
    reviewed_at         TIMESTAMP,

    created_at          TIMESTAMP    NOT NULL DEFAULT now(),
    updated_at          TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_client_mapping_review_client
    ON bronze.client_mapping_review (client_ref);

CREATE INDEX IF NOT EXISTS client_mapping_review_pending_idx
    ON bronze.client_mapping_review (review_reason)
    WHERE mapping_status = 'REVIEW' AND reviewer_decision IS NULL;

CREATE INDEX IF NOT EXISTS client_mapping_review_rule_idx
    ON bronze.client_mapping_review (rule_name);

CREATE INDEX IF NOT EXISTS client_mapping_review_folio_idx
    ON bronze.client_mapping_review (source, folio_no);
"""


def ensure_review_table():

    """Create the review table if it is not there yet."""

    with engine.begin() as connection:

        connection.execute(
            text("CREATE SCHEMA IF NOT EXISTS bronze")
        )

        for statement in REVIEW_TABLE_DDL.split(";"):

            if statement.strip():

                connection.execute(text(statement))


def load_review(result):

    print()
    print("=" * 80)
    print("WRITING bronze.client_mapping_review")
    print("=" * 80)

    ensure_review_table()

    # EVERY mapping is recorded, not just the review queue. The
    # rules decide which client each of 3,567 folios belongs to,
    # and that answer IS the mapping -- keeping only the handful
    # a human still has to look at discarded all 616 client
    # identities and left the table empty precisely when the
    # rules did well.
    #
    # mapping_status separates the two populations: MAPPED for
    # the settled ones, REVIEW for the queue, which the partial
    # index client_mapping_review_pending_idx still serves.
    pending = result[result["mapping_status"] == "REVIEW"]

    print("Clients mapped        :", len(result))

    print("Needing review        :", len(pending))

    # Drop only clients the rules no longer produce at all -- and
    # never a row somebody has already decided on.
    keep = tuple(result["client_ref"].tolist())

    with engine.begin() as connection:

        if keep:

            removed = connection.execute(
                text(
                    """
                    DELETE FROM bronze.client_mapping_review
                    WHERE reviewer_decision IS NULL
                      AND client_ref NOT IN :keep
                    """
                ).bindparams(bindparam("keep", expanding=True)),
                {"keep": list(keep)},
            ).rowcount

        else:

            removed = connection.execute(
                text(
                    """
                    DELETE FROM bronze.client_mapping_review
                    WHERE reviewer_decision IS NULL
                    """
                )
            ).rowcount

    print("Stale clients removed :", removed)

    result = result.copy()

    result["updated_at"] = pd.Timestamp.now()

    from utils.db import upsert_dataframe

    # Upsert on the client, so a re-run refreshes the mapping but
    # never clears a reviewer_decision already recorded.
    outcome = upsert_dataframe(
        result,
        schema="bronze",
        table="client_mapping_review",
        conflict_columns=["client_ref"],
        chunksize=200,
        updated_at_column=None,
    )

    print("Rows written :", outcome)

    return True


# ============================================================
# MAIN
# ============================================================

def main():

    dry_run = "--dry-run" in sys.argv

    try:

        df = extract_folios()

        if df.empty:

            print("No folios found in silver.investor_master.")

            return

        result = map_clients(df)

        result = collapse_to_clients(result)

        if dry_run:

            print()
            print("--dry-run : nothing written.")

            return

        load_review(result)

    except Exception as error:

        print("CLIENT MAPPING FAILED")
        print(error)
        traceback.print_exc()


if __name__ == "__main__":

    main()

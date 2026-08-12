"""NAV fingerprint matching and verification.

gold.scheme_nav currently holds NAV history for CAMS only — 68,424 rows across
332 scheme codes, zero for KFIN. Every KFIN scheme is therefore unverifiable by
NAV and must rely on name signals plus human review.
"""

import pandas as pd
from sqlalchemy import text

RTAS_WITH_NAV = {"CAMS"}

FINGERPRINT_SIZE = 3
NAV_PRECISION = 4


def decimal_places(value):
    """Decimal places actually carried by a NAV, ignoring trailing zeros.

    10.06 -> 2, 10.0600 -> 2, 10.0564 -> 4. Used to avoid comparing an RTA
    price beyond the precision it was supplied with.
    """
    text = format(float(value), "f").rstrip("0")
    return len(text.split(".")[1]) if "." in text else 0


def load_rta_fingerprints(engine, master_engine, rta_codes):
    """Most recent NAVs per RTA scheme, restricted to dates AMFI also publishes.

    Non-positive NAVs (nav <= 0) are excluded before the most-recent-3 selection —
    gold.scheme_nav carries placeholder 0.0 rows (1,499 of 68,424) that are not
    real prices and must not enter a fingerprint.

    Returns {(rta, scheme_code): [(nav_date, nav_round), ...]}. Schemes with
    fewer than FINGERPRINT_SIZE usable dates are omitted — a partial fingerprint
    is not strong enough to identify a fund.
    """
    codes = [c for c in rta_codes if c]
    if not codes:
        return {}

    rta_nav = pd.read_sql(
        text(
            """
            SELECT s.rta, s.scheme_code AS rta_scheme_code, sn.nav_date, sn.nav
            FROM gold.scheme_nav sn
            JOIN gold.scheme s ON sn.scheme_id = s.id
            WHERE sn.nav_date IS NOT NULL
              AND sn.nav IS NOT NULL
              AND sn.nav > 0
              AND s.scheme_code = ANY(:codes)
            """
        ),
        engine,
        params={"codes": codes},
    )
    if rta_nav.empty:
        return {}

    amfi_dates = set(
        pd.read_sql("SELECT DISTINCT nav_date FROM public.nav_master", master_engine)[
            "nav_date"
        ]
    )
    rta_nav = rta_nav[rta_nav.nav_date.isin(amfi_dates)]
    if rta_nav.empty:
        return {}

    rta_nav = (
        rta_nav.sort_values(["rta", "rta_scheme_code", "nav_date", "nav"])
        .drop_duplicates(subset=["rta", "rta_scheme_code", "nav_date"], keep="last")
        .sort_values("nav_date", ascending=False)
    )
    rta_nav["nav_round"] = rta_nav["nav"].round(NAV_PRECISION)

    top = rta_nav.groupby(["rta", "rta_scheme_code"]).head(FINGERPRINT_SIZE)

    out = {}
    for (rta, code), grp in top.groupby(["rta", "rta_scheme_code"]):
        if len(grp) < FINGERPRINT_SIZE:
            continue
        out[(rta, code)] = list(zip(grp.nav_date, grp.nav_round))
    return out


def load_amfi_navs(master_engine, dates):
    """AMFI NAVs for the given dates, rounded to NAV_PRECISION."""
    # public.nav_master.nav_date is a PostgreSQL `date` column. Pass real
    # date objects through the parameter (psycopg2 adapts a list of
    # datetime.date to a `date[]` array literal automatically) rather than
    # stringifying them — comparing `date = text` raises
    # psycopg2.errors.UndefinedFunction, and casting the column instead of
    # the parameter would defeat any index on nav_date.
    date_list = list(set(dates))
    if not date_list:
        return pd.DataFrame(columns=["scheme_code", "nav_date", "nav_round"])

    return pd.read_sql(
        text(
            f"""
            SELECT scheme_code, nav_date, ROUND(nav, {NAV_PRECISION}) AS nav_round
            FROM public.nav_master
            WHERE nav_date = ANY(:dates)
            """
        ),
        master_engine,
        params={"dates": date_list},
    )


def verify(fingerprint, amfi_code, amfi_navs):
    """True only when every (date, nav) in the fingerprint agrees with AMFI.

    An empty fingerprint returns False: absence of evidence is not verification.
    """
    if not fingerprint:
        return False

    subset = amfi_navs[amfi_navs.scheme_code.astype(str) == str(amfi_code)]
    if subset.empty:
        return False

    by_date = dict(zip(subset.nav_date.astype(str), subset.nav_round))

    for nav_date, nav_round in fingerprint:
        published = by_date.get(str(nav_date))
        if published is None:
            return False
        if round(float(published), NAV_PRECISION) != round(
            float(nav_round), NAV_PRECISION
        ):
            return False

    return True


def classify(fingerprint, amfi_code, amfi_navs):
    """Per-date breakdown of a fingerprint against AMFI, with a graded verdict.

    Unlike verify() — which collapses "matched", "missing" (AMFI has no row
    for that date) and "differed" (both sides have a value but disagree) into
    a single boolean — this keeps the three buckets separate so a caller can
    tell "no evidence either way" apart from "evidence of a real conflict".

    Verdicts:
      VERIFIED     — at least one date matched, none differed.
      VERIFIED_LOW_PRECISION
                   — as VERIFIED, but at least one date agreed only after
                     dropping to the precision the RTA supplied (2dp against
                     AMFI's 4dp, say). Real evidence, but weaker: funds whose
                     NAVs cluster — weekly IDCW near 10, liquid near 1000 —
                     can share a 2dp price without being the same fund.
      NO_EVIDENCE  — no dates matched and none differed (AMFI had nothing to
                     compare, e.g. missing history or dates outside AMFI's
                     coverage window). Absence of evidence is not contradiction.
      WEAK         — at least one date matched AND at least one date differed.
                     Typically a single stale/off-by-a-day RTA row riding
                     alongside otherwise-exact dates — not proof of a wrong
                     mapping.
      CONTRADICTED — zero dates matched and at least one date differed. The
                     genuine red flag: AMFI has an opinion for these dates and
                     it disagrees every time.

    Returns {"matched": int, "missing": int, "differed": int, "verdict": str,
    "max_pct_diff": float}. max_pct_diff is the largest absolute percentage
    difference among the differed dates (0.0 when nothing differed).
    """
    if not fingerprint:
        return {
            "matched": 0,
            "missing": 0,
            "differed": 0,
            "verdict": "NO_EVIDENCE",
            "max_pct_diff": 0.0,
        }

    subset = amfi_navs[amfi_navs.scheme_code.astype(str) == str(amfi_code)]
    by_date = (
        dict(zip(subset.nav_date.astype(str), subset.nav_round))
        if not subset.empty
        else {}
    )

    matched = 0
    missing = 0
    differed = 0
    max_pct_diff = 0.0
    reduced_precision = False

    for nav_date, nav_round in fingerprint:
        published = by_date.get(str(nav_date))
        if published is None:
            missing += 1
            continue

        rta_val = round(float(nav_round), NAV_PRECISION)
        amfi_val = round(float(published), NAV_PRECISION)
        if amfi_val == rta_val:
            matched += 1
            continue

        # The RTA sometimes supplies fewer decimals than AMFI publishes for
        # the same date. Comparing at NAV_PRECISION then reports a difference
        # that is an artefact of the feed, not a disagreement about the price.
        # Compare at the precision actually supplied — never beyond it.
        precision = min(decimal_places(nav_round), NAV_PRECISION)
        if round(amfi_val, precision) == round(rta_val, precision):
            matched += 1
            reduced_precision = True
            continue

        differed += 1
        if rta_val:
            pct_diff = abs(amfi_val - rta_val) / abs(rta_val) * 100
        else:
            pct_diff = float("inf")
        max_pct_diff = max(max_pct_diff, pct_diff)

    if differed == 0 and matched > 0:
        # Agreement only at the coarser precision is real evidence but weaker:
        # distinct funds can share a 2dp price. Keep it distinguishable rather
        # than folding it into VERIFIED.
        verdict = "VERIFIED_LOW_PRECISION" if reduced_precision else "VERIFIED"
    elif differed == 0 and matched == 0:
        verdict = "NO_EVIDENCE"
    elif differed > 0 and matched > 0:
        verdict = "WEAK"
    else:
        verdict = "CONTRADICTED"

    return {
        "matched": matched,
        "missing": missing,
        "differed": differed,
        "verdict": verdict,
        "max_pct_diff": max_pct_diff,
    }


# Verdicts for a group of RTA schemes that resolved to one AMFI code.
COLLISION_SAME_FUND = "SAME_FUND"
COLLISION_WRONG = "WRONG"
COLLISION_UNVERIFIED = "UNVERIFIED"


def classify_collision(verdicts):
    """Grade an AMFI code reached by several RTA schemes.

    Sharing a code is not itself an error. Two share classes of one fund — a
    lock-in and a non-lock-in class, say — are one portfolio with one NAV
    series, and AMFI publishes one code for them. Reporting that as a
    collision alongside genuine errors trains the reader to ignore the
    warning, which is how a real one gets missed.

    NAV decides:
      SAME_FUND   — every member's NAV series matches the code. They are the
                    same fund; the shared code is correct.
      WRONG       — at least one member is CONTRADICTED. That member's prices
                    are not this code's, so its mapping is wrong.
      UNVERIFIED  — no contradiction, but not every member is verified either
                    (KFIN has no NAV history at all). Needs a human; absence
                    of evidence must not read as a clean bill of health.
    """
    verdicts = list(verdicts)
    if any(v == "CONTRADICTED" for v in verdicts):
        return COLLISION_WRONG
    if verdicts and all(v == "VERIFIED" for v in verdicts):
        return COLLISION_SAME_FUND
    return COLLISION_UNVERIFIED

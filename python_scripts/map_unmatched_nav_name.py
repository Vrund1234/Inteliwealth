"""Second-pass mapping for the schemes scheme_mapping.py leaves UNMATCHED.

Two tiers, both testing exact SchemeKey equality and both refusing to guess on
ambiguity; they differ only in how wide they search and are recorded under
separate rule names so a reviewer can weigh them differently.

Run AFTER scheme_mapping.py. Reads bronze.scheme_mapping for rows whose status
is UNMATCHED, and for each one narrows the historical scheme universe to the
schemes whose published NAV equals this scheme's own price on a sampled date,
then looks for a candidate whose parsed SchemeKey is equal to the RTA scheme's.

Why this catches what the main engine cannot: the structured engine indexes
public.amfi_scheme_master (16,345 active schemes), so a closed or matured
scheme that survives only in public.scheme_master (37,764 rows) is invisible to
it however well its name matches. NAV lookup already reaches scheme_master, so
NAV is used here as the filter that makes searching that larger universe safe.

Nothing here writes to bronze.scheme_mapping. Matches are queued in
bronze.scheme_mapping_review for a human to approve; promote_approved_mappings.py
applies the approved ones.

    venv/bin/python map_unmatched_nav_name.py [--csv PATH]
"""

import argparse
import uuid

import pandas as pd
from sqlalchemy import text

from scheme_matching.alias_suggest import build_key_index
from scheme_matching.aliases import build_alias_fn, load_aliases
from scheme_matching.nav_name_match import (
    AMBIGUOUS,
    NO_MATCH,
    RESOLVED,
    match_in_key_index,
    match_nav_anchored,
    match_within_candidates,
    nav_candidate_union,
)
from utils.db import engine, master_engine

# Tier 1: key equality inside the NAV-derived candidate set. Two independent
# signals agree, so this is the stronger evidence.
RULE_NAME = "NAV_NAME_MATCH"
CONFIDENCE = 96

# Tier 2: key equality over the whole universe, for schemes whose NAV evidence
# is missing or unusable and which tier 1 therefore cannot reach at all. Same
# test, wider search, one signal instead of two -- recorded separately and at
# lower confidence so a reviewer can weigh it differently.
RULE_NAME_TIER2 = "NAME_KEY_MATCH"
CONFIDENCE_TIER2 = 94

# Tier 3: a close-but-not-exact name match inside the NAV candidate set, for
# house-style differences like "Mid Cap" against "Midcap". Lowest confidence of
# the three: it is the only tier that accepts anything other than an exact key,
# and its similarity thresholds are calibrated on a small residual.
RULE_NAME_TIER3 = "NAV_FUZZY_MATCH"
CONFIDENCE_TIER3 = 92

# Same sample size as Rule 3.5. Three recent dates is enough to pin a scheme
# once names are also compared, and keeps the candidate union small.
NAV_SAMPLE_DATES = 3


def load_unmatched(conn_engine):
    return pd.read_sql(
        """
        SELECT rta, rta_amc_code, rta_scheme_code, rta_scheme_name
        FROM bronze.scheme_mapping
        WHERE mapping_status = 'UNMATCHED'
        ORDER BY rta, rta_scheme_code
        """,
        conn_engine,
    )


def load_rta_nav(conn_engine, rta_scheme_codes):
    """Sampled NAV prices per RTA scheme, newest first.

    Filters match Rule 3.5 exactly: purprice > 0 drops the zero-price dividend
    and placeholder rows that would otherwise forge candidate matches.
    """
    if not rta_scheme_codes:
        return pd.DataFrame(
            columns=["rta", "rta_scheme_code", "nav_date", "nav", "nav_round"]
        )

    df = pd.read_sql(
        text(
            """
            SELECT source   AS rta,
                   prodcode AS rta_scheme_code,
                   traddate::date   AS nav_date,
                   purprice::numeric AS nav
            FROM bronze.transaction_master_new
            WHERE prodcode = ANY(:codes)
              AND purprice IS NOT NULL
              AND TRIM(purprice) != ''
              AND purprice::numeric > 0
              AND traddate IS NOT NULL
            """
        ),
        conn_engine,
        params={"codes": list(rta_scheme_codes)},
    )
    if df.empty:
        df["nav_round"] = []
        return df

    # One price per scheme per date before sampling, so three "dates" are three
    # distinct dates rather than three rows from the same busy day.
    df = df.sort_values(["rta", "rta_scheme_code", "nav_date", "nav"]).drop_duplicates(
        subset=["rta", "rta_scheme_code", "nav_date"], keep="last"
    )
    df["nav_round"] = df["nav"].round(4)
    return df.sort_values("nav_date", ascending=False)


def load_nav_lookup(conn_engine, nav_dates):
    """{(nav_date, nav): {scheme_code, ...}} for the sampled dates only."""
    if not nav_dates:
        return {}
    df = pd.read_sql(
        text(
            """
            SELECT scheme_code, nav_date, ROUND(nav, 4) AS nav_round
            FROM public.nav_master
            WHERE nav_date = ANY(:dates)
            """
        ),
        conn_engine,
        params={"dates": list(nav_dates)},
    )
    df["scheme_code"] = df["scheme_code"].astype(str)
    return df.groupby(["nav_date", "nav_round"])["scheme_code"].apply(set).to_dict()


def load_scheme_names(conn_engine):
    """code -> name across the historical and active masters.

    scheme_master is loaded first and amfi_scheme_master second so that where a
    code exists in both, the AMFI spelling wins — it is the one the rest of the
    pipeline compares against.
    """
    names = {}
    for query, code_col, name_col in (
        ("SELECT scheme_code::text AS code, name FROM public.scheme_master "
         "WHERE is_deleted = false", "code", "name"),
        ("SELECT amfi_scheme_code::text AS code, scheme_nav_name AS name "
         "FROM public.amfi_scheme_master", "code", "name"),
    ):
        df = pd.read_sql(query, conn_engine)
        names.update(dict(zip(df[code_col], df[name_col])))
    return names


def write_review_rows(conn_engine, rows):
    """Queue matches for approval, replacing this rule's own pending rows.

    Scoped to RULE_NAME so it cannot disturb the structured engine's pending
    candidates, mirroring the exclusion reference.write_review() applies in the
    other direction.
    """
    with conn_engine.begin() as conn:
        conn.execute(
            text(
                "DELETE FROM bronze.scheme_mapping_review "
                "WHERE reviewer_decision IS NULL AND rule_name = ANY(:rules)"
            ),
            {"rules": [RULE_NAME, RULE_NAME_TIER2, RULE_NAME_TIER3]},
        )
        if not rows:
            return
        for row in rows:
            row.setdefault("review_id", str(uuid.uuid4()))
        conn.execute(
            text(
                """
                INSERT INTO bronze.scheme_mapping_review
                    (review_id, rta, rta_scheme_code, rta_scheme_name,
                     candidate_rank, candidate_amfi_code, candidate_amfi_name,
                     candidate_score, rule_name)
                VALUES
                    (:review_id, :rta, :rta_scheme_code, :rta_scheme_name,
                     :candidate_rank, :candidate_amfi_code, :candidate_amfi_name,
                     :candidate_score, :rule_name)
                ON CONFLICT (rta, rta_scheme_code, candidate_rank) DO NOTHING
                """
            ),
            rows,
        )


def run_fallback_mapping(verbose=True, csv_path=None):
    """Queue fallback matches for review. Returns a summary dict.

    Callable from app.py as well as the CLI. Writes only to
    bronze.scheme_mapping_review -- never to bronze.scheme_mapping -- so it is
    safe to run unattended at the end of an ingestion.
    """
    def say(message):
        if verbose:
            print(message)

    say("=" * 80)
    say("NAV-FILTERED NAME MATCH for UNMATCHED schemes")
    say("=" * 80)

    unmatched = load_unmatched(engine)
    say(f"UNMATCHED schemes to process : {len(unmatched)}")
    if unmatched.empty:
        say("Nothing to do.")
        return {"unmatched": 0, "queued": 0, "tier1": 0, "tier2": 0,
                "ambiguous": 0, "unresolved": 0, "rows": []}

    alias_fn = build_alias_fn(load_aliases(engine))
    say(f"Active aliases loaded        : {len(load_aliases(engine))}")

    rta_nav = load_rta_nav(engine, unmatched["rta_scheme_code"].dropna().unique())
    say(f"RTA NAV rows sampled from    : {rta_nav['rta_scheme_code'].nunique()} schemes")

    sampled = rta_nav.groupby(["rta", "rta_scheme_code"]).head(NAV_SAMPLE_DATES)
    nav_dates = list(pd.to_datetime(sampled["nav_date"]).dt.date.unique())
    nav_lookup = load_nav_lookup(master_engine, nav_dates)
    say(f"nav_master dates looked up   : {len(nav_dates)}")

    names = load_scheme_names(master_engine)
    say(f"Scheme names in universe     : {len(names)}")

    review_rows, report, counts = [], [], {RESOLVED: 0, AMBIGUOUS: 0, NO_MATCH: 0}
    tier_counts = {"NAV+NAME": 0, "NAME_ONLY": 0, "NAV+FUZZY": 0}
    no_nav = 0

    # Built once: tier 2 tests every unmatched scheme against the same index.
    key_index = build_key_index(names, alias_fn)
    say(f"Distinct keys in universe    : {len(key_index)}")
    say("-" * 80)

    sampled_by_scheme = dict(list(sampled.groupby(["rta", "rta_scheme_code"])))

    for row in unmatched.itertuples():
        key = (row.rta, row.rta_scheme_code)
        group = sampled_by_scheme.get(key)

        if group is None or group.empty:
            # No NAV evidence at all, so tier 1 is impossible. Tier 2 is the
            # only route left for these.
            no_nav += 1
            fallback = match_in_key_index(
                row.rta_scheme_name, key_index, alias_fn=alias_fn
            )
            counts[fallback.status] += 1
            report.append({
                "rta": row.rta, "rta_scheme_code": row.rta_scheme_code,
                "rta_scheme_name": row.rta_scheme_name,
                "outcome": fallback.status if fallback.status != NO_MATCH
                           else "NO_NAV_DATA",
                "candidates_considered": 0,
                "evidence": "NAME_ONLY" if fallback.status == RESOLVED else None,
                "amfi_scheme_code": fallback.amfi_scheme_code,
                "amfi_scheme_name": names.get(fallback.amfi_scheme_code)
                                    if fallback.amfi_scheme_code else None,
            })
            if fallback.status == RESOLVED:
                review_rows.append({
                    "rta": row.rta,
                    "rta_scheme_code": row.rta_scheme_code,
                    "rta_scheme_name": row.rta_scheme_name,
                    "candidate_rank": 1,
                    "candidate_amfi_code": fallback.amfi_scheme_code,
                    "candidate_amfi_name": names.get(fallback.amfi_scheme_code),
                    "candidate_score": float(CONFIDENCE_TIER2),
                    "rule_name": RULE_NAME_TIER2,
                })
                tier_counts["NAME_ONLY"] += 1
                say(f"  {'NAME_ONLY':9s} {row.rta}/{row.rta_scheme_code:9s} -> "
                    f"{fallback.amfi_scheme_code:>7s}  "
                    f"{str(names.get(fallback.amfi_scheme_code))[:48]}")
            continue

        samples = [(s.nav_date, s.nav_round) for s in group.itertuples()]
        union = nav_candidate_union(samples, nav_lookup)
        candidates = {code: names.get(code) for code in union}

        result = match_within_candidates(
            row.rta_scheme_name, candidates, alias_fn=alias_fn
        )
        rule, confidence, tier = RULE_NAME, CONFIDENCE, "NAV+NAME"

        # Tier 2 only where tier 1 found nothing at all. An AMBIGUOUS tier-1
        # outcome is a real ambiguity, not a gap, and widening the search
        # cannot resolve it -- it can only bury it.
        if result.status == NO_MATCH:
            fallback = match_in_key_index(
                row.rta_scheme_name, key_index, alias_fn=alias_fn
            )
            if fallback.status != NO_MATCH:
                result = fallback
                rule, confidence, tier = (
                    RULE_NAME_TIER2, CONFIDENCE_TIER2, "NAME_ONLY"
                )

        # Tier 3 last: only where both exact-key tiers found nothing at all.
        if result.status == NO_MATCH:
            loose = match_nav_anchored(
                row.rta_scheme_name, candidates, alias_fn=alias_fn
            )
            if loose.status != NO_MATCH:
                result = loose
                rule, confidence, tier = (
                    RULE_NAME_TIER3, CONFIDENCE_TIER3, "NAV+FUZZY"
                )

        counts[result.status] += 1

        report.append({
            "rta": row.rta, "rta_scheme_code": row.rta_scheme_code,
            "rta_scheme_name": row.rta_scheme_name, "outcome": result.status,
            "candidates_considered": len(union),
            "evidence": tier if result.status == RESOLVED else None,
            "amfi_scheme_code": result.amfi_scheme_code,
            "amfi_scheme_name": names.get(result.amfi_scheme_code)
                                if result.amfi_scheme_code else None,
        })

        if result.status == RESOLVED:
            review_rows.append({
                "rta": row.rta,
                "rta_scheme_code": row.rta_scheme_code,
                "rta_scheme_name": row.rta_scheme_name,
                "candidate_rank": 1,
                "candidate_amfi_code": result.amfi_scheme_code,
                "candidate_amfi_name": names.get(result.amfi_scheme_code),
                "candidate_score": float(confidence),
                "rule_name": rule,
            })
            tier_counts[tier] += 1
            say(f"  {tier:9s} {row.rta}/{row.rta_scheme_code:9s} -> "
                f"{result.amfi_scheme_code:>7s}  "
                f"{str(names.get(result.amfi_scheme_code))[:48]}")
        elif result.status == AMBIGUOUS:
            say(f"  AMBIG  {row.rta}/{row.rta_scheme_code:9s} -> "
                f"{len(result.matches)} candidates share the key: "
                f"{', '.join(result.matches)}")

    write_review_rows(engine, review_rows)

    say("-" * 80)
    say(f"Resolved (queued for review) : {counts[RESOLVED]}")
    say(f"   tier 1  NAV + name key    : {tier_counts['NAV+NAME']}")
    say(f"   tier 2  name key only     : {tier_counts['NAME_ONLY']}")
    say(f"   tier 3  NAV + fuzzy name  : {tier_counts['NAV+FUZZY']}")
    say(f"Ambiguous (not queued)       : {counts[AMBIGUOUS]}")
    say(f"No key match                 : {counts[NO_MATCH]}")
    say(f"No NAV data to sample        : {no_nav}")
    say(f"Total                        : {len(unmatched)}")
    say("=" * 80)
    say(f"{len(review_rows)} row(s) written to bronze.scheme_mapping_review "
        f"as rule_name IN ('{RULE_NAME}', '{RULE_NAME_TIER2}').")
    say("bronze.scheme_mapping was NOT modified.")

    if csv_path:
        pd.DataFrame(report).to_csv(csv_path, index=False)
        say(f"\nFull outcome table written to {csv_path}")

    return {
        "unmatched": len(unmatched),
        "queued": len(review_rows),
        "tier1": tier_counts["NAV+NAME"],
        "tier2": tier_counts["NAME_ONLY"],
        "tier3": tier_counts["NAV+FUZZY"],
        "ambiguous": counts[AMBIGUOUS],
        "unresolved": counts[NO_MATCH],
        "rows": review_rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", help="also write the full outcome table here")
    args = parser.parse_args()
    result = run_fallback_mapping(verbose=True, csv_path=args.csv)
    if result["queued"]:
        print("Approve rows with:")
        print("  UPDATE bronze.scheme_mapping_review SET reviewer_decision='APPROVED', "
              "reviewed_by='<you>', reviewed_at=now()")
        print(f"   WHERE rule_name IN ('{RULE_NAME}','{RULE_NAME_TIER2}') "
              "AND reviewer_decision IS NULL;")
        print("then run: venv/bin/python promote_approved_mappings.py")

if __name__ == "__main__":
    main()

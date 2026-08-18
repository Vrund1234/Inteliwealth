"""Propose bronze.scheme_name_alias rows that would resolve UNMATCHED schemes.

Writes nothing. Produces a ranked shortlist so a reviewer approves VOCABULARY
(one row, fixes a family of schemes, permanently) rather than MAPPINGS (one
decision, fixes one scheme). Each proposal is scored two ways:

    fixes      UNMATCHED schemes it would resolve unambiguously
    collides   schemes distinct today that it would collapse onto one key

Neither number decides on its own. MID CAP -> MIDCAP is correct English and
still collapses schemes in the historical master; a reviewer weighing 'fixes'
against the named colliding schemes is the intended final step.

    venv/bin/python suggest_aliases.py [--csv PATH] [--min-fixes N]
"""

import argparse
from collections import defaultdict

import pandas as pd
from sqlalchemy import text

from scheme_matching.alias_suggest import (
    build_key_index,
    derive_term_pair,
    is_structurally_safe,
    new_collisions,
)
from scheme_matching.aliases import build_alias_fn, load_aliases
from scheme_matching.nav_name_match import nav_candidate_union
from scheme_matching.scheme_key import parse_scheme_key
from utils.db import engine, master_engine

NAV_SAMPLE_DATES = 3


def load_universe(conn_engine):
    """code -> name across the historical and active masters."""
    names = {}
    for query in (
        "SELECT scheme_code::text AS code, name FROM public.scheme_master "
        "WHERE is_deleted = false AND name IS NOT NULL",
        "SELECT amfi_scheme_code::text AS code, scheme_nav_name AS name "
        "FROM public.amfi_scheme_master WHERE scheme_nav_name IS NOT NULL",
    ):
        df = pd.read_sql(query, conn_engine)
        names.update(dict(zip(df["code"], df["name"])))
    return names


def load_nav_candidates(conn_engine, master, unmatched):
    """(rta, rta_scheme_code) -> set of NAV-plausible scheme codes."""
    codes = list(unmatched["rta_scheme_code"].dropna().unique())
    if not codes:
        return {}

    nav = pd.read_sql(
        text(
            """
            SELECT source AS rta, prodcode AS rta_scheme_code,
                   traddate::date AS nav_date, purprice::numeric AS nav
            FROM bronze.transaction_master_new
            WHERE prodcode = ANY(:codes) AND purprice IS NOT NULL
              AND TRIM(purprice) != '' AND purprice::numeric > 0
              AND traddate IS NOT NULL
            """
        ),
        conn_engine,
        params={"codes": codes},
    )
    if nav.empty:
        return {}

    nav = nav.sort_values(["rta", "rta_scheme_code", "nav_date", "nav"]).drop_duplicates(
        subset=["rta", "rta_scheme_code", "nav_date"], keep="last"
    )
    nav["nav_round"] = nav["nav"].round(4)
    nav = nav.sort_values("nav_date", ascending=False)
    sampled = nav.groupby(["rta", "rta_scheme_code"]).head(NAV_SAMPLE_DATES)

    dates = list(pd.to_datetime(sampled["nav_date"]).dt.date.unique())
    amfi_nav = pd.read_sql(
        text(
            "SELECT scheme_code, nav_date, ROUND(nav, 4) AS nav_round "
            "FROM public.nav_master WHERE nav_date = ANY(:dates)"
        ),
        master,
        params={"dates": dates},
    )
    amfi_nav["scheme_code"] = amfi_nav["scheme_code"].astype(str)
    lookup = (
        amfi_nav.groupby(["nav_date", "nav_round"])["scheme_code"].apply(set).to_dict()
    )

    out = {}
    for key, group in sampled.groupby(["rta", "rta_scheme_code"]):
        samples = [(s.nav_date, s.nav_round) for s in group.itertuples()]
        out[key] = nav_candidate_union(samples, lookup)
    return out


def mine(unmatched, nav_candidates, names, alias_fn):
    """Collect term pairs that survive the structural guards."""
    proposals = defaultdict(list)

    for row in unmatched.itertuples():
        candidates = nav_candidates.get((row.rta, row.rta_scheme_code))
        if not candidates:
            continue
        rta_key = parse_scheme_key(row.rta_scheme_name, alias_fn=alias_fn)
        if rta_key is None:
            continue

        for code in candidates:
            name = names.get(code)
            if not name:
                continue
            cand_key = parse_scheme_key(name, alias_fn=alias_fn)
            if cand_key is None:
                continue
            # Attributes must already agree. Without this a proposal could
            # merge a Growth share class into its IDCW sibling, which have
            # separate NAV series and must never share a key.
            if (cand_key.plan, cand_key.option, cand_key.frequency,
                    cand_key.qualifiers) != (
                    rta_key.plan, rta_key.option, rta_key.frequency,
                    rta_key.qualifiers):
                continue

            pair = derive_term_pair(rta_key.core_name, cand_key.core_name)
            if pair is None or not is_structurally_safe(*pair):
                continue
            proposals[pair].append((row.rta, row.rta_scheme_code,
                                    row.rta_scheme_name, code, name))
    return proposals


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", help="write the ranked shortlist here")
    parser.add_argument("--min-fixes", type=int, default=1,
                        help="drop proposals resolving fewer than N schemes")
    args = parser.parse_args()

    print("=" * 84)
    print("ALIAS SUGGESTIONS for UNMATCHED schemes")
    print("=" * 84)

    base_rows = load_aliases(engine)
    alias_fn = build_alias_fn(base_rows)

    unmatched = pd.read_sql(
        "SELECT rta, rta_amc_code, rta_scheme_code, rta_scheme_name "
        "FROM bronze.scheme_mapping WHERE mapping_status = 'UNMATCHED' "
        "ORDER BY rta, rta_scheme_code",
        engine,
    )
    print(f"UNMATCHED schemes      : {len(unmatched)}")
    if unmatched.empty:
        print("Nothing to do.")
        return

    names = load_universe(master_engine)
    print(f"Scheme-name universe   : {len(names)}")

    nav_candidates = load_nav_candidates(engine, master_engine, unmatched)
    print(f"Schemes with candidates: {len(nav_candidates)}")

    proposals = mine(unmatched, nav_candidates, names, alias_fn)
    proposals = {k: v for k, v in proposals.items() if len(v) >= args.min_fixes}
    print(f"Proposals after guards : {len(proposals)}")
    print("Scoring against the full universe (this is the slow part)...")

    base_index = build_key_index(names, alias_fn)

    scored = []
    for (raw, normalized), hits in proposals.items():
        trial_fn = build_alias_fn(
            base_rows
            + [{"raw_term": raw, "normalized_term": normalized,
                "alias_type": "FUND_RENAME", "amc_code": None}]
        )
        collided = new_collisions(names, base_index, trial_fn, raw)
        collided_codes = sorted({c for _, codes in collided for c in codes})
        scored.append({
            "raw_term": raw,
            "normalized_term": normalized,
            "fixes": len({(h[0], h[1]) for h in hits}),
            "collides": len(collided_codes),
            "example_rta_code": hits[0][1],
            "example_rta_name": hits[0][2],
            "example_target_code": hits[0][3],
            "example_target_name": hits[0][4],
            "colliding_examples": "; ".join(
                f"{c}={str(names.get(c))[:40]}" for c in collided_codes[:3]
            ),
        })

    scored.sort(key=lambda r: (-r["fixes"], r["collides"]))

    print()
    print(f"{'raw term':34s} {'-> normalized':26s} {'fixes':>5s} {'collides':>9s}")
    print("-" * 84)
    for row in scored:
        flag = "" if row["collides"] == 0 else "  <-- review collisions"
        print(f"{row['raw_term'][:34]:34s} -> {row['normalized_term'][:24]:24s} "
              f"{row['fixes']:>5d} {row['collides']:>9d}{flag}")

    clean = [r for r in scored if r["collides"] == 0]
    print("-" * 84)
    print(f"Proposals with zero collisions : {len(clean)}")
    print(f"Total proposals                : {len(scored)}")
    print()
    print("Nothing was written. To adopt a row, add it to")
    print("sql_scripts/scheme_name_alias_seed.sql, apply it, then re-run")
    print("scheme_mapping.py and diff against a snapshot before trusting it.")

    if args.csv:
        pd.DataFrame(scored).to_csv(args.csv, index=False)
        print(f"\nShortlist written to {args.csv}")


if __name__ == "__main__":
    main()

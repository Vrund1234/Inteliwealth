import re
import uuid
from dataclasses import replace

import pandas as pd
from sqlalchemy import text
from rapidfuzz import fuzz, process

from utils.db import engine, master_engine

from scheme_matching import rules as rule_mod
from scheme_matching.aliases import build_alias_fn, load_aliases
from scheme_matching.reference import (
    load_amc_map,
    load_overrides,
    write_audit,
    write_review,
)
from scheme_matching.rules import NOT_IN_AMFI, MatchContext, arbitrate, run_all
from scheme_matching.scheme_key import parse_scheme_key


# =====================================================
# NORMALIZE SCHEME NAME
# =====================================================

def normalize_scheme_name(name):
    """
    Normalize scheme name:
    - Convert to uppercase
    - Remove special characters
    - Remove extra spaces
    """

    if pd.isna(name):
        return None

    name = str(name).upper()
    name = re.sub(r"[^A-Z0-9\s]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()

    return name


# =====================================================
# BUILD STRUCTURED MATCH CONTEXT
# =====================================================

def derive_scheme_id(amc_code, amfi_scheme_code):
    """Identifier for the AMFI scheme a mapping resolved to, or None.

    An unmapped row resolved to nothing, so it has no scheme_id. Concatenating
    two blanks gave every unmapped row the same "" — indistinguishable from a
    real shared identifier. None says "unknown", which is what it is.
    """
    amc = "" if amc_code is None or pd.isna(amc_code) else str(amc_code).strip()
    code = (
        ""
        if amfi_scheme_code is None or pd.isna(amfi_scheme_code)
        else str(amfi_scheme_code).strip()
    )
    if not amc or not code:
        return None
    return amc + code


def build_scheme_id_column(df):
    """scheme_id for every row, as an object-dtype Series.

    dtype=object is required, not cosmetic: pandas coerces None to nan in a
    plain list assignment, the pipeline's later df.where(pd.notna(df), None)
    does not restore it, and psycopg2 then sends a float nan that Postgres
    stores as the literal string 'NaN' in this varchar column.
    """
    return pd.Series(
        [derive_scheme_id(r.amc_code, r.amfi_scheme_code) for r in df.itertuples()],
        index=df.index,
        dtype=object,
    )


def build_context(df, amfi_df, alias_fn, overrides):
    """Index the AMFI master by SchemeKey and by bucket, once per run."""
    amfi_by_key = {}
    amfi_by_bucket = {}
    amfi_names = {}

    for amfi_row in amfi_df.itertuples():
        key = parse_scheme_key(
            amfi_row.name_norm, amc_code=amfi_row.amc_code, alias_fn=alias_fn
        )
        if key is None:
            continue
        code = str(amfi_row.amfi_scheme_code)
        amfi_by_key.setdefault(key, []).append(code)
        amfi_by_bucket.setdefault(key.bucket(), []).append((key.core_name, code))
        amfi_names[code] = amfi_row.name_norm

        # normalized_nav_name is the same name with punctuation flattened, so
        # it renders some funds differently ("Large & Midcap" -> "LARGE
        # MIDCAP" where scheme_nav_name gives "LARGE AND MIDCAP"). Index that
        # spelling too, so an RTA name written either way still resolves.
        #
        # Only the core_name is taken from it. Flattening destroys the
        # parentheses that strip_parentheticals() relies on, so a bare
        # "FORMERLY KNOWN AS ..." matches the delete-to-end-of-string rule and
        # silently carries off the plan and option — 222 schemes in the master
        # are named that way. Attributes therefore always come from `key`.
        alt_name = getattr(amfi_row, "normalized_nav_name", None)
        if alt_name is None or not str(alt_name).strip():
            continue
        alt = parse_scheme_key(alt_name, amc_code=amfi_row.amc_code, alias_fn=alias_fn)
        if alt is None or alt.core_name == key.core_name:
            continue
        alt_key = replace(key, core_name=alt.core_name)
        amfi_by_key.setdefault(alt_key, []).append(code)
        amfi_by_bucket.setdefault(alt_key.bucket(), []).append(
            (alt_key.core_name, code)
        )

    return MatchContext(
        amfi_by_key=amfi_by_key,
        amfi_by_bucket=amfi_by_bucket,
        amfi_names=amfi_names,
        overrides=overrides,
    )


# =====================================================
# MAIN FUNCTION
# =====================================================

def load_scheme_mapping():

    print("=" * 80)
    print("STARTING SCHEME MAPPING")
    print("=" * 80)


    # =================================================
    # LOAD DISTINCT RTA SCHEMES
    # =================================================

    query = """
        SELECT DISTINCT ON (source, prodcode)
            source,
            amc_code,
            prodcode,
            scheme
        FROM silver.transaction_master_new
        WHERE source IS NOT NULL
          AND scheme IS NOT NULL
          AND NULLIF(TRIM(prodcode), '') IS NOT NULL
        ORDER BY source, prodcode;
    """

    df = pd.read_sql(query, engine)

    print(f"Distinct Schemes Found : {len(df)}")


    # =================================================
    # RENAME RTA COLUMNS
    # =================================================

    df.rename(
        columns={
            "source": "rta",
            "amc_code": "rta_amc_code",
            "prodcode": "rta_scheme_code",
            "scheme": "rta_scheme_name",
        },
        inplace=True,
    )


    # =================================================
    # GENERATE STABLE MAPPING ID
    # =================================================

    # df["mapping_id"] = df.apply(
    #     lambda x: str(
    #         uuid.uuid5(
    #             uuid.NAMESPACE_DNS,
    #             f"{x['rta']}|{x['rta_scheme_code']}|{x['amfi_scheme_code']}"
    #         )
    #         if ("amfi_scheme_code" in x and pd.notna(x["amfi_scheme_code"]))
    #         else uuid.uuid5(
    #             uuid.NAMESPACE_DNS,
    #             f"{x['rta']}|{x['rta_scheme_code']}"
    #         )
    #     ),
    #     axis=1
    # )

    # =================================================
    # SHORT NAME NORMALIZATION
    # =================================================

    def normalize_short_name(name):

        if pd.isna(name):
            return None

        name = normalize_scheme_name(name)

        remove_words = {
            "FUND",
            "SCHEME",
            "PLAN"
        }

        words = [
            w
            for w in name.split()
            if w not in remove_words
        ]

        return " ".join(words)


    # =================================================
    # NORMALIZE RTA SCHEME NAMES
    # =================================================

    df["normalized_scheme_name"] = (
        df["rta_scheme_name"]
        .apply(normalize_scheme_name)
    )

    df["short_scheme_name"] = (
        df["rta_scheme_name"]
        .apply(normalize_short_name)
    )


    # =================================================
    # RTA ISIN
    # =================================================

    # Placeholder until RTA starts providing ISIN
    df["rta_isin"] = None


    # =================================================
    # LOAD AMFI SCHEME MASTER
    # =================================================

    # AMFI master was restructured on 2026-08-12: name_norm was replaced by
    # normalized_scheme_name, and plan/option moved into plan_type/option_type.
    #
    # scheme_nav_name — NOT normalized_scheme_name — is the successor to the old
    # name_norm. normalized_scheme_name is the bare fund name with plan, option
    # and frequency stripped: only 3,710 distinct values across 16,345 schemes,
    # so every Growth/IDCW and Daily/Weekly variant of a fund collapses onto one
    # string. scheme_nav_name keeps the full detail (16,308 distinct) and is what
    # parse_scheme_key needs in order to tell those variants apart.
    #
    # Aliased to name_norm because this module already uses
    # normalized_scheme_name for the RTA-side name; keeping the two distinct
    # avoids collisions in the merges further down.
    amfi_query = """
        SELECT
            amfi_scheme_code,
            amc_code,
            scheme_nav_name AS name_norm,
            normalized_nav_name,
            plan_type,
            option_type,
            isin_growth,
            isin_idcw,
            status
        FROM public.amfi_scheme_master;
    """

    print("START: Loading AMFI master")

    amfi_df = pd.read_sql(
        amfi_query,
        master_engine
    )

    print(
        f"DONE: AMFI master loaded: "
        f"{len(amfi_df)}"
    )


    # =================================================
    # NORMALIZE AMFI NAMES
    # =================================================

    amfi_df["name_norm"] = (
        amfi_df["name_norm"]
        .apply(normalize_scheme_name)
    )

    amfi_df["short_name"] = (
        amfi_df["name_norm"]
        .apply(normalize_short_name)
    )


    # =================================================
    # IDENTIFY DUPLICATE AMFI NORMALIZED NAMES
    # =================================================

    amfi_name_counts = (
        amfi_df[
            amfi_df["name_norm"].notna()
        ]
        .groupby("name_norm")
        .size()
        .reset_index(name="amfi_count")
    )

    duplicate_amfi_names = set(
        amfi_name_counts[
            amfi_name_counts["amfi_count"] > 1
        ]["name_norm"]
    )

    print(
        f"AMFI duplicate normalized names found: "
        f"{len(duplicate_amfi_names)}"
    )


    # =================================================
    # LOAD RTA -> AMC SLUG MAPPING
    # =================================================

    amc_mapping_query = """
        SELECT
            rta,
            amc_code AS rta_amc_code,
            amc_slug
        FROM public.rta_amc_code;
    """

    print("START: Loading AMC mapping")

    amc_mapping_df = pd.read_sql(
        amc_mapping_query,
        master_engine
    )

    print(
        f"DONE: AMC mapping loaded: "
        f"{len(amc_mapping_df)}"
    )


    df = df.merge(
        amc_mapping_df,
        on=[
            "rta",
            "rta_amc_code"
        ],
        how="left"
    )


    # =================================================
    # BEST MATCH COLUMNS
    # =================================================

    df["best_amfi_scheme_code"] = None
    df["best_mapping_source"] = None
    df["best_mapping_confidence"] = None


    # =================================================
    # HELPER: UPDATE BEST MATCH
    # =================================================

    def update_best_match(
        df,
        idx,
        amfi_code,
        source,
        confidence
    ):

        if (
            amfi_code is None
            or pd.isna(amfi_code)
        ):
            return

        current_confidence = df.at[
            idx,
            "best_mapping_confidence"
        ]

        if (
            pd.isna(current_confidence)
            or confidence > current_confidence
        ):

            df.at[
                idx,
                "best_amfi_scheme_code"
            ] = amfi_code

            df.at[
                idx,
                "best_mapping_source"
            ] = source

            df.at[
                idx,
                "best_mapping_confidence"
            ] = confidence


    # =================================================
    # RULE 0 : ISIN MATCH (100)
    # =================================================

    isin_pattern = re.compile(
        r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$"
    )

    for idx, row in df.iterrows():

        if pd.isna(row["rta_isin"]) or not str(row["rta_isin"]).strip():
            continue

        if not isin_pattern.match(
            row["rta_isin"]
        ):
            continue

        matches = amfi_df[
            (
                amfi_df["isin_growth"]
                == row["rta_isin"]
            )
            |
            (
                amfi_df["isin_idcw"]
                == row["rta_isin"]
            )
        ]

        if len(matches) == 1:

            update_best_match(
                df,
                idx,
                matches.iloc[0][
                    "amfi_scheme_code"
                ],
                "ISIN_MATCH",
                100
            )


    # =================================================
    # RULE 2 : PRODUCT MATCH (100)
    # =================================================

    print("=" * 80)
    print("RULE 2 : PRODUCT MATCH")
    print("=" * 80)

    for idx, row in df.iterrows():

        matches = amfi_df[
            (
                amfi_df["amc_code"]
                == row["rta_amc_code"]
            )
            &
            (
                amfi_df["name_norm"]
                == row["normalized_scheme_name"]
            )
        ]

        if len(matches) == 1:

            update_best_match(
                df,
                idx,
                matches.iloc[0][
                    "amfi_scheme_code"
                ],
                "PRODUCT_MATCH",
                100
            )

    # Fix 3: Diagnostic — Rule 2 contribution
    rule2_matched = df["best_mapping_source"].eq("PRODUCT_MATCH").sum()
    print(
        f"[DIAG] Rule 2 (PRODUCT_MATCH) total matched: {rule2_matched}"
    )

    # =================================================
    # RULE 3.5 : NAV MATCH (97)
    # =================================================

    print("=" * 80)
    print("RULE 3.5 : NAV MATCH")
    print("=" * 80)

    unmatched_nav_df = df[df["best_amfi_scheme_code"].isna()].copy()
    
    if not unmatched_nav_df.empty:
        rta_codes = tuple(unmatched_nav_df["rta_scheme_code"].dropna().unique())
        if rta_codes:
            rta_codes_str = f"('{rta_codes[0]}')" if len(rta_codes) == 1 else str(rta_codes)
            
            scheme_nav_query = f"""
                SELECT s.rta, s.scheme_code AS rta_scheme_code, sn.nav_date, sn.nav
                FROM gold.scheme_nav sn
                JOIN gold.scheme s ON sn.scheme_id = s.id
                WHERE sn.nav_date IS NOT NULL AND sn.nav IS NOT NULL AND s.scheme_code IN {rta_codes_str}
            """
            rta_nav_df = pd.read_sql(scheme_nav_query, engine)
            
            if not rta_nav_df.empty:
                rta_nav_df = rta_nav_df.sort_values(
                    ['rta', 'rta_scheme_code', 'nav_date', 'nav']
                ).drop_duplicates(
                    subset=['rta', 'rta_scheme_code', 'nav_date'], keep='last'
                )
                rta_nav_df['nav_round'] = rta_nav_df['nav'].round(4)
                
                amfi_dates_df = pd.read_sql("SELECT DISTINCT nav_date FROM public.nav_master", master_engine)
                amfi_dates = set(amfi_dates_df['nav_date'])
                
                rta_nav_df = rta_nav_df[rta_nav_df['nav_date'].isin(amfi_dates)]
                rta_nav_df = rta_nav_df.sort_values('nav_date', ascending=False)
                top3_navs = rta_nav_df.groupby(['rta', 'rta_scheme_code']).head(3)
                
                counts = top3_navs.groupby(['rta', 'rta_scheme_code']).size()
                valid_rta = counts[counts == 3].index
                
                if not valid_rta.empty:
                    top3_navs = top3_navs.set_index(['rta', 'rta_scheme_code']).loc[valid_rta].reset_index()
                    required_dates = tuple(top3_navs['nav_date'].astype(str).unique())
                    req_dates_str = f"('{required_dates[0]}')" if len(required_dates) == 1 else str(required_dates)
                    
                    nav_master_query = f"""
                        SELECT nm.scheme_code, nm.nav_date, ROUND(nm.nav, 4) as nav_round
                        FROM public.nav_master nm
                        WHERE nm.nav_date IN {req_dates_str}
                    """
                    amfi_nav_df = pd.read_sql(nav_master_query, master_engine)
                    
                    for idx, row in unmatched_nav_df.iterrows():
                        rta = row['rta']
                        rta_code = row['rta_scheme_code']
                        
                        if (rta, rta_code) not in valid_rta:
                            continue
                            
                        sample_navs = top3_navs[(top3_navs['rta'] == rta) & (top3_navs['rta_scheme_code'] == rta_code)]
                        
                        matched_codes = []
                        for _, sample in sample_navs.iterrows():
                            amfi_matches = amfi_nav_df[
                                (amfi_nav_df['nav_date'] == sample['nav_date']) &
                                (amfi_nav_df['nav_round'] == sample['nav_round'])
                            ]
                            matched_codes.append(set(amfi_matches['scheme_code'].astype(str)))
                            
                        if any(not codes for codes in matched_codes):
                            continue

                        common_codes = set.intersection(*matched_codes)

                        if len(common_codes) == 1:
                            # Fix 1: nav_master.scheme_code may be string;
                            # validate it against amfi_df to ensure type
                            # consistency before calling update_best_match.
                            raw_code = list(common_codes)[0]

                            # Try integer lookup first, then string fallback
                            amfi_lookup = amfi_df[
                                amfi_df["amfi_scheme_code"].astype(str)
                                == str(raw_code)
                            ]

                            if len(amfi_lookup) != 1:
                                print(
                                    f"[NAV_MATCH SKIP] nav_master code {raw_code} "
                                    f"did not resolve uniquely in amfi_df "
                                    f"({len(amfi_lookup)} rows) for "
                                    f"{rta}/{rta_code}"
                                )
                                continue

                            matched_amfi = amfi_lookup.iloc[0]["amfi_scheme_code"]
                            update_best_match(
                                df, idx, matched_amfi, "NAV_MATCH", 97
                            )

    # Fix 3: Diagnostic — Rule 3.5 contribution
    rule35_matched = df["best_mapping_source"].eq("NAV_MATCH").sum()
    print(
        f"[DIAG] Rule 3.5 (NAV_MATCH) total matched: {rule35_matched}"
    )

    # -------------------------------------------------
    # STRUCTURED MATCHING ENGINE
    # -------------------------------------------------

    alias_fn = build_alias_fn(load_aliases(engine))
    overrides = load_overrides(engine)

    amc_map = load_amc_map(master_engine)
    df = df.merge(
        amc_map[["rta", "rta_amc_code", "amfi_amc_code"]],
        on=["rta", "rta_amc_code"],
        how="left",
    )

    context = build_context(df, amfi_df, alias_fn, overrides)

    df["mapping_status"] = None
    df["scheme_key"] = [
        parse_scheme_key(
            r.rta_scheme_name, amc_code=r.amfi_amc_code, alias_fn=alias_fn
        )
        for r in df.itertuples()
    ]

    audit_rows = []
    review_rows = []

    for idx, row in df.iterrows():
        record = row.to_dict()
        candidates = run_all(record, context)

        for cand in candidates:
            audit_rows.append({
                "rta": record["rta"],
                "rta_scheme_code": record["rta_scheme_code"],
                "rule_name": cand.rule_name,
                "execution_outcome": "CANDIDATE",
                "confidence_score": cand.confidence,
                "candidate_scheme_id": (
                    None if cand.amfi_scheme_code is NOT_IN_AMFI
                    else str(cand.amfi_scheme_code)
                ),
            })

        winner = arbitrate(candidates)

        not_in_amfi = winner is not None and winner.amfi_scheme_code is NOT_IN_AMFI

        pending_candidates = None

        if winner is not None and not not_in_amfi:
            # Ambiguity that no rule resolved: write nothing, offer for review.
            ambiguous = (
                winner.rule_name == "STRUCT_EXACT"
                and len(context.amfi_by_key.get(record["scheme_key"], [])) > 1
            )
            if ambiguous:
                pending_candidates = sorted(candidates, key=lambda x: -x.score)[:3]
            else:
                update_best_match(
                    df,
                    idx,
                    winner.amfi_scheme_code,
                    winner.rule_name,
                    winner.confidence,
                )

        # mapping_status is DERIVED from the row's final state after every
        # rule (inline ISIN/PRODUCT/NAV_MATCH as well as the engine) has had
        # a chance to write to best_amfi_scheme_code — never written
        # unconditionally from the engine's own outcome alone, so an inline
        # rule's earlier, higher-confidence code can never be clobbered by a
        # status that contradicts it.
        final_code = df.at[idx, "best_amfi_scheme_code"]
        has_code = not (final_code is None or pd.isna(final_code))

        if not_in_amfi:
            df.at[idx, "mapping_status"] = "NOT_IN_AMFI"
        elif has_code:
            df.at[idx, "mapping_status"] = "MATCHED"
        elif candidates:
            df.at[idx, "mapping_status"] = "PENDING_REVIEW"
        else:
            df.at[idx, "mapping_status"] = "UNMATCHED"

        # Only send genuinely-unresolved rows to human review: a row that
        # already carries a written amfi_scheme_code (from this engine pass
        # or an earlier inline rule) needs no review candidates.
        if pending_candidates is not None and not has_code:
            for rank, cand in enumerate(pending_candidates, start=1):
                review_rows.append({
                    "rta": record["rta"],
                    "rta_scheme_code": record["rta_scheme_code"],
                    "rta_scheme_name": record["rta_scheme_name"],
                    "candidate_rank": rank,
                    "candidate_amfi_code": str(cand.amfi_scheme_code),
                    "candidate_amfi_name": context.amfi_names.get(
                        str(cand.amfi_scheme_code)
                    ),
                    "candidate_score": cand.score,
                    "rule_name": cand.rule_name,
                })

    write_audit(engine, audit_rows)
    write_review(engine, review_rows)
    df.drop(columns=["scheme_key"], inplace=True)

    # =================================================
    # FINALIZE NORMAL MATCH RESULTS
    # =================================================

    df["amfi_scheme_code"] = (
        df["best_amfi_scheme_code"]
    )

    df["mapping_source"] = (
        df["best_mapping_source"]
    )

    df["mapping_confidence"] = (
        df["best_mapping_confidence"]
    )


    # =================================================
    # GENERATE INTERNAL SCHEME ID
    # =================================================

    matched_amfi = (
        amfi_df[
            [
                "amfi_scheme_code",
                "amc_code"
            ]
        ]
        .drop_duplicates()
    )


    df = df.merge(
        matched_amfi,
        on="amfi_scheme_code",
        how="left",
        suffixes=("", "_master")
    )


    df["scheme_id"] = build_scheme_id_column(df)



    # =================================================
    # GENERATE STABLE MAPPING ID
    # =================================================

    df["mapping_id"] = df.apply(
        lambda x: uuid.uuid5(
            uuid.NAMESPACE_DNS,
            (
                f"{x['rta']}|"
                f"{x['rta_scheme_code']}|"
                f"{x['amfi_scheme_code']}"
            )
        )
        if pd.notna(x["amfi_scheme_code"])
        else uuid.uuid5(
            uuid.NAMESPACE_DNS,
            (
                f"{x['rta']}|"
                f"{x['rta_scheme_code']}"
            )
        ),
        axis=1
    )


    # =================================================
    # CLEAN DATA
    # =================================================

    df.drop(
        columns=[
            "name_norm",
            "match_count"
        ],
        errors="ignore",
        inplace=True
    )


    df = df.where(
        pd.notna(df),
        None
    )


    df["mapping_confidence"] = (
        df["mapping_confidence"]
        .astype("Int64")
    )




    # =================================================
    # DEDUPLICATE NORMAL RTA SCHEMES
    # =================================================

    print(
        f"Before dedup: {len(df)}"
    )

    df = df.drop_duplicates(
        subset=[
            "rta",
            "rta_scheme_code",
            "amfi_scheme_code"
        ],
        keep="first"
    )

    print(
        f"After dedup: {len(df)}"
    )





    # =================================================
    # INSERT NORMAL MAPPINGS
    # =================================================

    insert_query = text("""
        INSERT INTO bronze.scheme_mapping (
            mapping_id,
            scheme_id,
            rta,
            rta_amc_code,
            rta_scheme_code,
            rta_scheme_name,
            normalized_scheme_name,
            amfi_scheme_code,
            mapping_source,
            mapping_confidence,
            mapping_status
        )
        VALUES (
            :mapping_id,
            :scheme_id,
            :rta,
            :rta_amc_code,
            :rta_scheme_code,
            :rta_scheme_name,
            :normalized_scheme_name,
            :amfi_scheme_code,
            :mapping_source,
            :mapping_confidence,
            :mapping_status
        )
        ON CONFLICT (
            rta,
            rta_scheme_code
        )
        DO UPDATE SET
            scheme_id = EXCLUDED.scheme_id,
            rta_amc_code = EXCLUDED.rta_amc_code,
            rta_scheme_name = EXCLUDED.rta_scheme_name,
            normalized_scheme_name = EXCLUDED.normalized_scheme_name,
            -- Fix 1: always overwrite amfi_scheme_code on re-runs so that
            -- a previously-unmatched row gets the code found by later rules.
            amfi_scheme_code = EXCLUDED.amfi_scheme_code,
            mapping_source = EXCLUDED.mapping_source,
            mapping_confidence = EXCLUDED.mapping_confidence,
            mapping_status = EXCLUDED.mapping_status;
    """)


    with engine.begin() as conn:

        conn.execute(
            insert_query,
            df.to_dict(
                orient="records"
            )
        )


    print(
        f"Normal mappings processed: "
        f"{len(df)}"
    )

    # Fix 3: Final summary of mapping sources
    mapping_summary = (
        df["mapping_source"]
        .fillna("UNMATCHED")
        .value_counts()
        .reset_index()
    )
    mapping_summary.columns = ["mapping_source", "count"]
    print("\n" + "=" * 50)
    print("MAPPING SOURCE SUMMARY (this run)")
    print("=" * 50)
    print(mapping_summary.to_string(index=False))
    total = len(df)
    matched = df["amfi_scheme_code"].notna().sum()
    print(f"Matched   : {matched}/{total} ({matched*100//total}%)")
    print(f"Unmatched : {total - matched}/{total}")
    print("=" * 50 + "\n")

    # Fix 5: Detect and log AMFI code collisions
    # (multiple distinct RTA scheme codes -> same AMFI code)
    collision_df = (
        df[df["amfi_scheme_code"].notna()]
        .groupby("amfi_scheme_code")["rta_scheme_code"]
        .nunique()
        .reset_index(name="distinct_rta_codes")
    )
    collisions = collision_df[
        collision_df["distinct_rta_codes"] > 1
    ]
    if not collisions.empty:
        print("[WARNING] AMFI code collisions detected (multiple RTA codes -> same AMFI code):")
        for _, c_row in collisions.iterrows():
            acode = c_row["amfi_scheme_code"]
            dupes = df[
                df["amfi_scheme_code"] == acode
            ][["rta", "rta_scheme_code", "rta_scheme_name", "mapping_source"]]
            print(f"  AMFI {acode}:")
            for _, d in dupes.iterrows():
                print(
                    f"    [{d['rta']}] {d['rta_scheme_code']} "
                    f"-> {d['rta_scheme_name']} "
                    f"(source={d['mapping_source']})"
                )
    else:
        print("[OK] No AMFI code collisions detected.")


    # =================================================
    # DUPLICATE AMFI NAME EXPANSION
    # =================================================

    print("=" * 80)
    print("START: DUPLICATE AMFI NAME EXPANSION")
    print("=" * 80)


    # -------------------------------------------------
    # LOAD EXISTING SCHEME MAPPINGS
    # -------------------------------------------------

    existing_mapping_query = """
        SELECT
            mapping_id,
            scheme_id,
            rta,
            rta_amc_code,
            rta_scheme_code,
            rta_scheme_name,
            normalized_scheme_name,
            amfi_scheme_code,
            mapping_source,
            mapping_confidence
        FROM bronze.scheme_mapping
        WHERE normalized_scheme_name IS NOT NULL;
    """


    existing_mapping_df = pd.read_sql(
        existing_mapping_query,
        engine
    )


    print(
        f"Existing scheme mappings loaded: "
        f"{len(existing_mapping_df)}"
    )


    # -------------------------------------------------
    # COUNT MAPPINGS PER NORMALIZED NAME
    # -------------------------------------------------

    mapping_counts = (
        existing_mapping_df
        .groupby(
            "normalized_scheme_name"
        )
        .size()
        .reset_index(
            name="mapping_count"
        )
    )


    # -------------------------------------------------
    # FIND TARGET NAMES
    #
    # AMFI count > 1
    # AND
    # scheme_mapping count == 1
    # -------------------------------------------------

    target_names = (
        amfi_name_counts
        .merge(
            mapping_counts,
            left_on="name_norm",
            right_on="normalized_scheme_name",
            how="inner"
        )
    )


    target_names = target_names[
        (
            target_names["amfi_count"]
            > 1
        )
        &
        (
            target_names["mapping_count"]
            == 1
        )
    ]


    print(
        "Names requiring duplicate expansion: "
        f"{len(target_names)}"
    )


    # -------------------------------------------------
    # NO TARGETS
    # -------------------------------------------------

    if target_names.empty:

        print(
            "No duplicate AMFI mappings "
            "require expansion."
        )

    else:

        target_name_list = (
            target_names[
                "name_norm"
            ]
            .tolist()
        )


        # ---------------------------------------------
        # GET SOURCE MAPPING
        # ---------------------------------------------

        source_mappings = (
            existing_mapping_df[
                existing_mapping_df[
                    "normalized_scheme_name"
                ].isin(
                    target_name_list
                )
            ]
            .copy()
        )


        # ---------------------------------------------
        # GET ALL AMFI RECORDS
        # ---------------------------------------------

        duplicate_amfi_df = (
            amfi_df[
                amfi_df["name_norm"].isin(
                    target_name_list
                )
            ]
            .copy()
        )


        print(
            "AMFI records to expand: "
            f"{len(duplicate_amfi_df)}"
        )


        # ---------------------------------------------
        # CREATE EXPANDED ROWS
        # ---------------------------------------------

        expanded_rows = []


        for _, mapping in (
            source_mappings.iterrows()
        ):

            matching_amfi = (
                duplicate_amfi_df[
                    duplicate_amfi_df[
                        "name_norm"
                    ]
                    ==
                    mapping[
                        "normalized_scheme_name"
                    ]
                ]
            )


            for _, amfi in (
                matching_amfi.iterrows()
            ):

                expanded_rows.append({

                    "mapping_id": str(
                        uuid.uuid5(
                            uuid.NAMESPACE_DNS,
                            (
                                f"{mapping['rta']}|"
                                f"{mapping['rta_scheme_code']}|"
                                f"{amfi['amfi_scheme_code']}"
                            )
                        )
                    ),

                    "scheme_id": derive_scheme_id(
                        amfi["amc_code"],
                        amfi["amfi_scheme_code"],
                    ),

                    "rta": mapping["rta"],

                    "rta_amc_code": (
                        mapping[
                            "rta_amc_code"
                        ]
                    ),

                    "rta_scheme_code": (
                        mapping[
                            "rta_scheme_code"
                        ]
                    ),

                    "rta_scheme_name": (
                        mapping[
                            "rta_scheme_name"
                        ]
                    ),

                    "normalized_scheme_name": (
                        mapping[
                            "normalized_scheme_name"
                        ]
                    ),

                    "amfi_scheme_code": (
                        amfi[
                            "amfi_scheme_code"
                        ]
                    ),

                    "mapping_source": (
                        "NAME_EXACT"
                    ),

                    # Ambiguous by name,
                    # therefore no confidence.
                    "mapping_confidence": 99
                })


        expanded_df = pd.DataFrame(
            expanded_rows
        )


        # ---------------------------------------------
        # INSERT EXPANDED MAPPINGS
        # ---------------------------------------------

        if not expanded_df.empty:

            print(
                "Duplicate mapping rows generated: "
                f"{len(expanded_df)}"
            )


            expanded_df = (
                expanded_df.where(
                    pd.notna(
                        expanded_df
                    ),
                    None
                )
            )


            insert_duplicate_query = text("""
                INSERT INTO bronze.scheme_mapping (
                    mapping_id,
                    scheme_id,
                    rta,
                    rta_amc_code,
                    rta_scheme_code,
                    rta_scheme_name,
                    normalized_scheme_name,
                    amfi_scheme_code,
                    mapping_source,
                    mapping_confidence
                )
                VALUES (
                    :mapping_id,
                    :scheme_id,
                    :rta,
                    :rta_amc_code,
                    :rta_scheme_code,
                    :rta_scheme_name,
                    :normalized_scheme_name,
                    :amfi_scheme_code,
                    :mapping_source,
                    :mapping_confidence
                )
                ON CONFLICT (
                    rta,
                    rta_scheme_code,
                    amfi_scheme_code
                )
                DO UPDATE SET
                    scheme_id = EXCLUDED.scheme_id,
                    mapping_source =
                        EXCLUDED.mapping_source,
                    mapping_confidence =
                        EXCLUDED.mapping_confidence;
            """)


            with engine.begin() as conn:

                conn.execute(
                    insert_duplicate_query,
                    expanded_df.to_dict(
                        orient="records"
                    )
                )


            print(
                "DONE: Duplicate AMFI "
                "Name Expansion"
            )


        else:

            print(
                "No duplicate rows generated."
            )


    # =================================================
    # FINAL SUMMARY
    # =================================================

    print("=" * 80)
    print("SCHEME MAPPING COMPLETED")
    print("=" * 80)

    print(
        f"Processed RTA schemes : {len(df)}"
    )

    print(
        "Normal matching rules completed."
    )

    print(
        "Duplicate AMFI name expansion completed."
    )


# =====================================================
# ENTRY POINT
# =====================================================

if __name__ == "__main__":
    load_scheme_mapping()
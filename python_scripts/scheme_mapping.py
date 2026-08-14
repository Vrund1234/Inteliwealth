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
from scheme_matching.rules import (
    AUTHORITATIVE_RULES,
    NOT_IN_AMFI,
    MatchContext,
    arbitrate,
    run_all,
)
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


def update_best_match(df, idx, amfi_code, source, confidence, force=False):
    """Record a rule's answer, if it beats whatever is already there.

    The single write-path for every rule, inline and registry alike. Because
    the guard compares confidence rather than arrival order, a rule that runs
    later can only displace an earlier one by being strictly more confident.

    `force` exists because a curator's OVERRIDE is authority, not a score.
    ISIN_MATCH and PRODUCT_MATCH run inline and write 100 before the engine
    ever proposes OVERRIDE — also 100 — so the strict `>` guard would drop it,
    silently disabling the one mechanism for correcting a confident-but-wrong
    automatic match. Raising OVERRIDE's confidence past 100 instead would leak
    an off-scale number into the stored mapping_confidence.
    """

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
        force
        or pd.isna(current_confidence)
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


def dedupe_mappings(df):
    """One row per (rta, rta_scheme_code).

    An RTA scheme code is a single share class with a single NAV, so it resolves
    to exactly one AMFI scheme. bronze.scheme_mapping enforces that with
    uq_scheme_mapping, and the upsert conflicts on the same two columns.
    Deduplicating on the code as well would let a merge fan-out reach the insert
    as two competing rows, where DO UPDATE silently keeps whichever landed last.
    """

    return df.drop_duplicates(subset=["rta", "rta_scheme_code"], keep="first")


def clear_best_match(df, idx):
    """Drop any match already recorded for a row.

    Used when an override asserts the scheme has no AMFI counterpart at all.
    Without it a row that an inline rule matched earlier keeps that code and is
    written with mapping_status='NOT_IN_AMFI' alongside a populated
    amfi_scheme_code — a contradiction no downstream consumer can resolve.
    """

    df.at[idx, "best_amfi_scheme_code"] = None
    df.at[idx, "best_mapping_source"] = None
    df.at[idx, "best_mapping_confidence"] = None


def build_context(df, amfi_df, alias_fn, overrides):
    """Index the AMFI master by SchemeKey and by bucket, once per run."""
    amfi_by_key = {}
    amfi_by_bucket = {}
    amfi_names = {}

    for amfi_row in amfi_df.itertuples():
        # name_raw is scheme_nav_name verbatim; name_norm is the same name with
        # punctuation already flattened. Parse the raw one: the flattened
        # spelling has lost the parentheses that strip_parentheticals() keys
        # on, so "(Formerly Known as X)" degrades into the bare
        # "FORMERLY KNOWN AS ...$" rule and carries off the plan and option —
        # 222 of 237 such names collapse onto 27 shared keys. It also spells
        # "&" as a space rather than AND, which the RTA side does not.
        raw_name = getattr(amfi_row, "name_raw", None)
        if raw_name is None or not str(raw_name).strip():
            raw_name = amfi_row.name_norm
        key = parse_scheme_key(
            raw_name, amc_code=amfi_row.amc_code, alias_fn=alias_fn
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
        FROM bronze.transaction_master_new
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

    # Keep scheme_nav_name verbatim before it is flattened. build_context parses
    # this one, because normalize_scheme_name() strips the parentheses that
    # strip_parentheticals() depends on. name_norm stays normalized: Rule 2
    # compares it against the equally-normalized RTA name.
    amfi_df["name_raw"] = amfi_df["name_norm"]

    amfi_df["name_norm"] = (
        amfi_df["name_norm"]
        .apply(normalize_scheme_name)
    )

    amfi_df["short_name"] = (
        amfi_df["name_norm"]
        .apply(normalize_short_name)
    )


    # Names shared by several AMFI schemes used to be counted here to drive a
    # duplicate-expansion phase, which wrote one mapping row per sharing code.
    # That contradicted uq_scheme_mapping (one row per rta + rta_scheme_code)
    # and guessed at confidence 99 where every other rule refuses. The
    # structured engine already handles the same situation correctly, by
    # routing the scheme to PENDING_REVIEW with all candidates listed.


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
    # Uses bronze.transaction_master_new purprice as the
    # NAV source — covers both CAMS and KFIN.
    # =================================================

    print("=" * 80)
    print("RULE 3.5 : NAV MATCH (from bronze.transaction_master_new)")
    print("=" * 80)

    unmatched_nav_df = df[df["best_amfi_scheme_code"].isna()].copy()

    if not unmatched_nav_df.empty:
        rta_codes = tuple(unmatched_nav_df["rta_scheme_code"].dropna().unique())
        if rta_codes:
            # Pull NAV (purprice) from bronze.transaction_master_new.
            # purprice > 0 filters out zero-NAV rows (dividend payouts /
            # placeholders) that would create false fingerprint matches.
            #
            # Codes are bound, not interpolated: the previous f-string relied on
            # a Python tuple's repr happening to emit single quotes, needed a
            # special case for the 1-element tuple repr, and would break on a
            # prodcode containing an apostrophe. nav_verify.py binds the same
            # way.
            # Dividend-reinvestment rows are excluded because they do not price
            # at the published NAV. Measured over 24,545 observations on
            # name-exact mappings: purprice equals AMFI's NAV for the same date
            # 98.3% of the time on reinvest_flag='Z' and 97.5% on NULL, but only
            # 30.4% on 'Y'; by type, DRED 0.0%, DRY1 0.0%, PSNIL 3.7%, DR1 7.6%.
            #
            # Those prices are also stale — CAMS/B44N carries 152 distinct
            # purprices across 297 dates — so they repeat, and a repeated wrong
            # price is exactly what can coincide with another fund's NAV and
            # forge a three-date fingerprint. Redemption types (R1 81.9%, FUL
            # 89.7%) are left in: they are noisy rather than systematically
            # wrong, so they cost a match rather than inventing one.
            txn_nav_query = text("""
                SELECT source AS rta,
                       prodcode AS rta_scheme_code,
                       traddate::date AS nav_date,
                       purprice::numeric AS nav
                FROM bronze.transaction_master_new
                WHERE prodcode = ANY(:codes)
                  AND purprice IS NOT NULL
                  AND TRIM(purprice) != ''
                  AND purprice::numeric > 0
                  AND traddate IS NOT NULL
            """)
            rta_nav_df = pd.read_sql(
                txn_nav_query, engine, params={"codes": list(rta_codes)}
            )

            print(
                f"  RTA NAV rows from bronze.transaction_master_new: "
                f"{len(rta_nav_df)} across "
                f"{rta_nav_df['rta_scheme_code'].nunique()} schemes"
            )

            if not rta_nav_df.empty:
                # De-duplicate: keep the last NAV per scheme per date
                rta_nav_df = rta_nav_df.sort_values(
                    ['rta', 'rta_scheme_code', 'nav_date', 'nav']
                ).drop_duplicates(
                    subset=['rta', 'rta_scheme_code', 'nav_date'],
                    keep='last'
                )
                rta_nav_df['nav_round'] = rta_nav_df['nav'].round(4)

                # Only keep dates that AMFI also publishes
                amfi_dates_df = pd.read_sql(
                    "SELECT DISTINCT nav_date FROM public.nav_master",
                    master_engine
                )
                amfi_dates = set(amfi_dates_df['nav_date'])

                rta_nav_df = rta_nav_df[
                    rta_nav_df['nav_date'].isin(amfi_dates)
                ]
                rta_nav_df = rta_nav_df.sort_values(
                    'nav_date', ascending=False
                )

                # Take top 3 most recent NAVs per scheme
                top3_navs = rta_nav_df.groupby(
                    ['rta', 'rta_scheme_code']
                ).head(3)

                counts = top3_navs.groupby(
                    ['rta', 'rta_scheme_code']
                ).size()
                valid_rta = counts[counts == 3].index

                print(
                    f"  Schemes with 3+ NAV dates on AMFI calendar: "
                    f"{len(valid_rta)}"
                )

                if not valid_rta.empty:
                    top3_navs = (
                        top3_navs
                        .set_index(['rta', 'rta_scheme_code'])
                        .loc[valid_rta]
                        .reset_index()
                    )
                    # Real date objects, bound rather than interpolated.
                    # nav_master.nav_date is a `date` column: comparing it
                    # against text raises UndefinedFunction, and casting the
                    # column instead of the parameter would defeat its index.
                    required_dates = list(
                        pd.to_datetime(top3_navs['nav_date']).dt.date.unique()
                    )

                    nav_master_query = text("""
                        SELECT nm.scheme_code,
                               nm.nav_date,
                               ROUND(nm.nav, 4) as nav_round
                        FROM public.nav_master nm
                        WHERE nm.nav_date = ANY(:dates)
                    """)
                    amfi_nav_df = pd.read_sql(
                        nav_master_query,
                        master_engine,
                        params={"dates": required_dates},
                    )

                    for idx, row in unmatched_nav_df.iterrows():
                        rta = row['rta']
                        rta_code = row['rta_scheme_code']

                        if (rta, rta_code) not in valid_rta:
                            continue

                        sample_navs = top3_navs[
                            (top3_navs['rta'] == rta)
                            & (top3_navs['rta_scheme_code'] == rta_code)
                        ]

                        matched_codes = []
                        for _, sample in sample_navs.iterrows():
                            amfi_matches = amfi_nav_df[
                                (amfi_nav_df['nav_date']
                                 == sample['nav_date'])
                                & (amfi_nav_df['nav_round']
                                   == sample['nav_round'])
                            ]
                            matched_codes.append(
                                set(amfi_matches['scheme_code'].astype(str))
                            )

                        if any(not codes for codes in matched_codes):
                            continue

                        common_codes = set.intersection(*matched_codes)

                        if len(common_codes) == 1:
                            raw_code = list(common_codes)[0]

                            # Validate against amfi_df for type consistency
                            amfi_lookup = amfi_df[
                                amfi_df["amfi_scheme_code"].astype(str)
                                == str(raw_code)
                            ]

                            if len(amfi_lookup) != 1:
                                print(
                                    f"[NAV_MATCH SKIP] nav_master code "
                                    f"{raw_code} did not resolve uniquely "
                                    f"in amfi_df ({len(amfi_lookup)} rows) "
                                    f"for {rta}/{rta_code}"
                                )
                                continue

                            matched_amfi = (
                                amfi_lookup.iloc[0]["amfi_scheme_code"]
                            )
                            update_best_match(
                                df, idx, matched_amfi, "NAV_MATCH", 97
                            )

    # Diagnostic — Rule 3.5 contribution
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
                # Every STRUCT_EXACT candidate scores 100.0, so a top-3 slice
                # cuts arbitrarily: for KFIN/176LDGP the correct scheme was not
                # among the three offered, leaving the row unresolvable by the
                # reviewer it was raised for. Offer them all.
                pending_candidates = sorted(candidates, key=lambda x: -x.score)
            else:
                update_best_match(
                    df,
                    idx,
                    winner.amfi_scheme_code,
                    winner.rule_name,
                    winner.confidence,
                    force=winner.rule_name in AUTHORITATIVE_RULES,
                )

        # mapping_status is DERIVED from the row's final state after every
        # rule (inline ISIN/PRODUCT/NAV_MATCH as well as the engine) has had
        # a chance to write to best_amfi_scheme_code — never written
        # unconditionally from the engine's own outcome alone, so an inline
        # rule's earlier, higher-confidence code can never be clobbered by a
        # status that contradicts it.
        if not_in_amfi:
            # An override asserting absence outranks whatever an inline rule
            # matched earlier; leaving that code in place would contradict the
            # status about to be written.
            clear_best_match(df, idx)

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

    df = dedupe_mappings(df)

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


# =====================================================
# ENTRY POINT
# =====================================================

if __name__ == "__main__":
    load_scheme_mapping()
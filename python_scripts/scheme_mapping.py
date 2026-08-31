from sqlalchemy import inspect
import difflib
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
from scheme_matching.scheme_id import (
    build_scheme_id_column,
    derive_scheme_id,
)
from scheme_matching.scheme_key import parse_scheme_key
from scheme_matching.structured_signals import plan_option_mismatch


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
# LOAD SCHEME_MASTER
# =====================================================

def load_scheme_master(conn_engine):
    """scheme_code -> name/category/ISINs/plan_type/option_type, public.scheme_master.

    Covers historical and active schemes (37,764 rows), unlike
    public.amfi_scheme_master's 16,345 currently-active-only rows -- this is
    the only master Rule 2.5 (legacy/historical name matching) can reach.

    plan_type/option_type were added to this table in 2026-08; as of
    2026-08-21 only ~13% of rows have them populated. A blank value here means
    "no structured signal yet," never a specific plan/option -- see
    scheme_matching.structured_signals.plan_option_mismatch.
    """
    plan_col = "plan_type"
    option_col = "option_type"
    if conn_engine is not None:
        try:
            cols = [c["name"] for c in inspect(conn_engine).get_columns("scheme_master", schema="public")]
            if "plan_type" not in cols:
                plan_col = "NULL::text AS plan_type"
            if "option_type" not in cols:
                option_col = "NULL::text AS option_type"
        except Exception:
            pass

    sm_query = f"""
        SELECT
            scheme_code::text AS scheme_code,
            name              AS scheme_name,
            name_norm,
            category,
            isin_growth,
            isin_reinvest,
            {plan_col},
            {option_col}
        FROM public.scheme_master
        WHERE is_deleted = false;
    """
    sm_df = pd.read_sql(sm_query, conn_engine)
    sm_df["scheme_code"] = sm_df["scheme_code"].astype(str).str.strip()
    return sm_df


def resolve_scheme_master_exact(
    rta_scheme_name, alias_fn, sm_name_to_code, sm_attrs_by_code
):
    """Rule 2.5's decision for one RTA scheme name.

    sm_name_to_code: {normalized_name: scheme_code}, built once per run from
        scheme_master rows whose normalized name is unique (see Rule 2.5).
    sm_attrs_by_code: {scheme_code: {"plan_type": ..., "option_type": ...}}.

    Returns (scheme_code, mismatch_reason). scheme_code is None when there is
    no exact normalized-name match. When a name match exists but
    scheme_master's own plan_type/option_type disagree with the RTA name's
    parsed plan/option, scheme_code is None and mismatch_reason explains why
    -- Rule 2.5 must not accept a match its own structured evidence
    contradicts; downstream rules (NAV_MATCH, Phase-3 tiers) get the chance
    instead.
    """
    norm_name = normalize_scheme_name(rta_scheme_name)
    matched_code = sm_name_to_code.get(norm_name)
    if matched_code is None:
        return None, None

    attrs = sm_attrs_by_code.get(matched_code, {})
    rta_key = parse_scheme_key(rta_scheme_name, amc_code=None, alias_fn=alias_fn)
    reason = plan_option_mismatch(
        rta_key, attrs.get("plan_type"), attrs.get("option_type")
    )
    if reason:
        return None, reason
    return matched_code, None


# =====================================================
# BUILD STRUCTURED MATCH CONTEXT
# =====================================================

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
# MAPPING UPSERT
# =====================================================

# amfi_scheme_code is normally overwritten on every run, so a scheme left
# unmatched last time picks up a code a later rule now finds.
#
# The exception is a mapping a reviewer approved out of
# bronze.scheme_mapping_review. Those schemes reach the queue precisely BECAUSE
# the engine cannot resolve them, so it contributes NULL for them on every
# later run; overwriting unconditionally erased the approval silently, leaving
# no error and nothing in the log. verified_at marks such a row.
#
# A verified mapping yields only to a STRICTLY more confident engine result.
# The first version of this guard yielded to any result at all, on the
# assumption that every rule producing one outranks the fallbacks. CORE_FUZZY
# does not: it sits at 90, below NAV_NAME_MATCH's 96. Live, that let
# CORE_FUZZY re-point RMF7GGP from Series 11 to Series 22 -- a different fund --
# over a mapping a reviewer had approved. Ties go to the reviewer, who looked
# at the scheme.
UPSERT_MAPPING_SQL = text("""
    INSERT INTO bronze.scheme_mapping (
        mapping_id, scheme_id, rta, rta_amc_code, rta_scheme_code,
        rta_scheme_name, normalized_scheme_name, amfi_scheme_code,
        mapping_source, mapping_confidence, mapping_status, rta_isin
    )
    VALUES (
        :mapping_id, :scheme_id, :rta, :rta_amc_code, :rta_scheme_code,
        :rta_scheme_name, :normalized_scheme_name, :amfi_scheme_code,
        :mapping_source, :mapping_confidence, :mapping_status, :rta_isin
    )
    ON CONFLICT (rta, rta_scheme_code)
    DO UPDATE SET
        rta_amc_code           = EXCLUDED.rta_amc_code,
        rta_scheme_name        = EXCLUDED.rta_scheme_name,
        normalized_scheme_name = EXCLUDED.normalized_scheme_name,
        rta_isin                = EXCLUDED.rta_isin,
        scheme_id = CASE
            -- A scheme that is ALREADY MAPPED is frozen: no later run may
            -- blank it, re-point it, or downgrade it, whatever confidence the
            -- new answer claims. Previously only verified_at rows were
            -- protected, so any automatically-mapped scheme could be
            -- overwritten -- live, CAMS/K205G and CAMS/TRFPG were both mapped
            -- in the baseline and were later wiped to NULL by a run that
            -- simply failed to resolve them, and CAMS/TRFPG could also be
            -- re-pointed between two AMFI candidates tied at score 100.
            -- To re-point a mapped scheme now, clear amfi_scheme_code first;
            -- that is a deliberate act rather than a side effect of a run.
            WHEN bronze.scheme_mapping.amfi_scheme_code IS NOT NULL
            THEN bronze.scheme_mapping.scheme_id
            ELSE EXCLUDED.scheme_id END,
        amfi_scheme_code = CASE
            -- A scheme that is ALREADY MAPPED is frozen: no later run may
            -- blank it, re-point it, or downgrade it, whatever confidence the
            -- new answer claims. Previously only verified_at rows were
            -- protected, so any automatically-mapped scheme could be
            -- overwritten -- live, CAMS/K205G and CAMS/TRFPG were both mapped
            -- in the baseline and were later wiped to NULL by a run that
            -- simply failed to resolve them, and CAMS/TRFPG could also be
            -- re-pointed between two AMFI candidates tied at score 100.
            -- To re-point a mapped scheme now, clear amfi_scheme_code first;
            -- that is a deliberate act rather than a side effect of a run.
            WHEN bronze.scheme_mapping.amfi_scheme_code IS NOT NULL
            THEN bronze.scheme_mapping.amfi_scheme_code
            ELSE EXCLUDED.amfi_scheme_code END,
        mapping_source = CASE
            -- A scheme that is ALREADY MAPPED is frozen: no later run may
            -- blank it, re-point it, or downgrade it, whatever confidence the
            -- new answer claims. Previously only verified_at rows were
            -- protected, so any automatically-mapped scheme could be
            -- overwritten -- live, CAMS/K205G and CAMS/TRFPG were both mapped
            -- in the baseline and were later wiped to NULL by a run that
            -- simply failed to resolve them, and CAMS/TRFPG could also be
            -- re-pointed between two AMFI candidates tied at score 100.
            -- To re-point a mapped scheme now, clear amfi_scheme_code first;
            -- that is a deliberate act rather than a side effect of a run.
            WHEN bronze.scheme_mapping.amfi_scheme_code IS NOT NULL
            THEN bronze.scheme_mapping.mapping_source
            ELSE EXCLUDED.mapping_source END,
        mapping_confidence = CASE
            -- A scheme that is ALREADY MAPPED is frozen: no later run may
            -- blank it, re-point it, or downgrade it, whatever confidence the
            -- new answer claims. Previously only verified_at rows were
            -- protected, so any automatically-mapped scheme could be
            -- overwritten -- live, CAMS/K205G and CAMS/TRFPG were both mapped
            -- in the baseline and were later wiped to NULL by a run that
            -- simply failed to resolve them, and CAMS/TRFPG could also be
            -- re-pointed between two AMFI candidates tied at score 100.
            -- To re-point a mapped scheme now, clear amfi_scheme_code first;
            -- that is a deliberate act rather than a side effect of a run.
            WHEN bronze.scheme_mapping.amfi_scheme_code IS NOT NULL
            THEN bronze.scheme_mapping.mapping_confidence
            ELSE EXCLUDED.mapping_confidence END,
        mapping_status = CASE
            -- A scheme that is ALREADY MAPPED is frozen: no later run may
            -- blank it, re-point it, or downgrade it, whatever confidence the
            -- new answer claims. Previously only verified_at rows were
            -- protected, so any automatically-mapped scheme could be
            -- overwritten -- live, CAMS/K205G and CAMS/TRFPG were both mapped
            -- in the baseline and were later wiped to NULL by a run that
            -- simply failed to resolve them, and CAMS/TRFPG could also be
            -- re-pointed between two AMFI candidates tied at score 100.
            -- To re-point a mapped scheme now, clear amfi_scheme_code first;
            -- that is a deliberate act rather than a side effect of a run.
            WHEN bronze.scheme_mapping.amfi_scheme_code IS NOT NULL
            THEN bronze.scheme_mapping.mapping_status
            ELSE EXCLUDED.mapping_status END;
""")


# =====================================================
# LOAD DISTINCT RTA SCHEMES
# =====================================================

def load_rta_scheme_candidates():
    """Distinct (source, prodcode) scheme candidates from
    bronze.transaction_master_new, one row per RTA scheme code.

    A given RTA scheme code can appear on many transaction rows, and only
    some of them may carry an ISIN (not every source file has one -- e.g.
    today's CAMS files don't, KFIN's do). `ORDER BY ... isin NULLS LAST`
    makes DISTINCT ON prefer an ISIN-bearing row over a null one for the
    same code, so the scheme resolves to its ISIN whenever the data exists
    anywhere, not just on whichever row happened to sort first.
    """

    query = """
        SELECT DISTINCT ON (source, prodcode)
            source,
            amc_code,
            prodcode,
            scheme,
            isin
        FROM bronze.transaction_master_new
        WHERE source IS NOT NULL
          AND scheme IS NOT NULL
          AND NULLIF(TRIM(prodcode), '') IS NOT NULL
        ORDER BY source, prodcode, isin NULLS LAST;
    """

    df = pd.read_sql(query, engine)

    df.rename(
        columns={
            "source": "rta",
            "amc_code": "rta_amc_code",
            "prodcode": "rta_scheme_code",
            "scheme": "rta_scheme_name",
            "isin": "rta_isin",
        },
        inplace=True,
    )

    df["rta_isin"] = (
        df["rta_isin"]
        .astype("string")
        .str.strip()
        .str.upper()
        .replace("", pd.NA)
    )

    return df


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

    df = load_rta_scheme_candidates()

    print(f"Distinct Schemes Found : {len(df)}")


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


    # rta_isin is already populated by load_rta_scheme_candidates() above.


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
    # LOAD SCHEME_MASTER (HISTORICAL AMFI MASTER)
    # =================================================
    print("START: Loading scheme_master")

    sm_df = load_scheme_master(master_engine)

    sm_codes = set(sm_df["scheme_code"].dropna().unique())
    sm_names = dict(zip(sm_df["scheme_code"], sm_df["scheme_name"]))

    print(
        f"DONE: scheme_master loaded: "
        f"{len(sm_df)} schemes ({len(sm_codes)} unique codes)"
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
    # RULE 2.5 : SCHEME_MASTER EXACT NAME MATCH (99)
    # Matches RTA normalized scheme name against public.scheme_master
    # for legacy / historical schemes not present in amfi_scheme_master.
    # =================================================

    print("=" * 80)
    print("RULE 2.5 : SCHEME_MASTER EXACT NAME MATCH")
    print("=" * 80)

    sm_df["name_norm_calc"] = sm_df["scheme_name"].apply(normalize_scheme_name)
    sm_name_counts = sm_df.groupby("name_norm_calc")["scheme_code"].nunique()
    sm_unique_names = set(sm_name_counts[sm_name_counts == 1].index)
    sm_name_to_code = (
        sm_df[sm_df["name_norm_calc"].isin(sm_unique_names)]
        .drop_duplicates(subset=["name_norm_calc"])
        .set_index("name_norm_calc")["scheme_code"]
        .to_dict()
    )
    sm_attrs_by_code = sm_df.set_index("scheme_code")[
        ["plan_type", "option_type"]
    ].to_dict(orient="index")

    rule25_mismatches = 0

    for idx, row in df.iterrows():
        if pd.notna(df.at[idx, "best_amfi_scheme_code"]):
            continue

        matched_code, mismatch_reason = resolve_scheme_master_exact(
            row["rta_scheme_name"],
            None,
            sm_name_to_code,
            sm_attrs_by_code,
        )
        if mismatch_reason:
            rule25_mismatches += 1
            continue
        if matched_code:
            update_best_match(
                df,
                idx,
                matched_code,
                "SCHEME_MASTER_EXACT",
                99,
            )

    rule25_matched = df["best_mapping_source"].eq("SCHEME_MASTER_EXACT").sum()
    print(
        f"[DIAG] Rule 2.5 (SCHEME_MASTER_EXACT) total matched: {rule25_matched}"
    )
    print(
        f"[DIAG] Rule 2.5 (SCHEME_MASTER_EXACT) structured mismatches skipped: "
        f"{rule25_mismatches}"
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
                valid_rta = counts[counts >= 2].index

                print(
                    f"  Schemes with 2+ NAV dates on AMFI calendar: "
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

                    amfi_nav_df['scheme_code_str'] = amfi_nav_df['scheme_code'].astype(str)
                    nav_lookup_dict = (
                        amfi_nav_df.groupby(['nav_date', 'nav_round'])['scheme_code_str']
                        .apply(set)
                        .to_dict()
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

                        matched_codes = [
                            nav_lookup_dict.get((sample['nav_date'], sample['nav_round']), set())
                            for _, sample in sample_navs.iterrows()
                        ]

                        if any(not codes for codes in matched_codes):
                            continue

                        common_codes = set.intersection(*matched_codes)

                        raw_code = None
                        if len(common_codes) == 1:
                            raw_code = str(list(common_codes)[0])
                        elif len(common_codes) > 1:
                            # Disambiguate multiple candidate schemes (e.g. Growth vs IDCW, Regular vs Direct/Institutional)
                            rta_name = str(row['rta_scheme_name']).lower()
                            is_direct = 'direct' in rta_name
                            is_growth = 'growth' in rta_name or ' gr' in rta_name
                            is_idcw = 'idcw' in rta_name or 'div' in rta_name
                            is_retail = 'retail' in rta_name
                            is_inst = 'institutional' in rta_name or ' inst' in rta_name

                            cand_scores = []
                            for c in common_codes:
                                c_code = str(c)
                                c_name = sm_names.get(c_code, '').lower()
                                score = difflib.SequenceMatcher(None, rta_name, c_name).ratio()
                                if is_direct != ('direct' in c_name):
                                    score -= 0.3
                                c_is_growth = 'growth' in c_name or 'cumulative' in c_name
                                c_is_idcw = 'dividend' in c_name or 'idcw' in c_name or 'payout' in c_name or 'reinvestment' in c_name
                                if is_growth and c_is_growth:
                                    score += 0.2
                                elif is_growth and not c_is_growth:
                                    score -= 0.2
                                if is_idcw and c_is_idcw:
                                    score += 0.2
                                elif is_idcw and not c_is_idcw:
                                    score -= 0.2
                                if is_retail and 'retail' in c_name:
                                    score += 0.2
                                if is_inst and 'institutional' in c_name:
                                    score += 0.2
                                cand_scores.append((score, c_code))

                            cand_scores.sort(reverse=True)
                            if cand_scores:
                                raw_code = cand_scores[0][1]

                        if raw_code:
                            # Validate against amfi_df (active AMFI schemes)
                            amfi_lookup = amfi_df[
                                amfi_df["amfi_scheme_code"].astype(str)
                                == raw_code
                            ]

                            if len(amfi_lookup) == 1:
                                matched_amfi = (
                                    amfi_lookup.iloc[0]["amfi_scheme_code"]
                                )
                                update_best_match(
                                    df, idx, matched_amfi, "NAV_MATCH", 97
                                )
                            elif raw_code in sm_codes:
                                # Historical AMFI scheme in scheme_master
                                update_best_match(
                                    df,
                                    idx,
                                    raw_code,
                                    "NAV_MATCH_SCHEME_MASTER",
                                    97,
                                )
                            else:
                                print(
                                    f"[NAV_MATCH SKIP] nav_master code "
                                    f"{raw_code} not found in amfi_df "
                                    f"or scheme_master for {rta}/{rta_code}"
                                )
                                continue

    # Diagnostic — Rule 3.5 contribution (direct amfi_df + scheme_master)
    rule35_amfi = df["best_mapping_source"].eq("NAV_MATCH").sum()
    rule35_sm = df["best_mapping_source"].eq("NAV_MATCH_SCHEME_MASTER").sum()
    print(
        f"[DIAG] Rule 3.5 (NAV_MATCH amfi_master) total matched: {rule35_amfi}"
    )
    print(
        f"[DIAG] Rule 3.5 (NAV_MATCH scheme_master) total matched: {rule35_sm}"
    )
    print(
        f"[DIAG] Rule 3.5 (NAV_MATCH total): {rule35_amfi + rule35_sm}"
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

    df["amc_code"] = (
        df["amc_code"]
        .combine_first(df["amfi_amc_code"] if "amfi_amc_code" in df.columns else None)
        .combine_first(df["rta_amc_code"] if "rta_amc_code" in df.columns else None)
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

    insert_query = UPSERT_MAPPING_SQL


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

    return apply_review_decisions()


# =====================================================
# REVIEW DECISIONS
# =====================================================

def apply_review_decisions():
    """Apply approvals, then queue whatever is still unresolved.

    Runs at the end of every pipeline run so that nothing a reviewer approved
    is ever left sitting unapplied. Approvals are made directly in
    bronze.scheme_mapping_review and picked up here on the next run, which is
    what keeps silver.scheme_id from going stale: transform.py reads
    bronze.scheme_mapping, so a mapping that exists only as an unapplied
    approval would leave those transactions with a NULL scheme_id.

    Order matters. Approvals are applied BEFORE the fallback matcher looks for
    gaps, so a scheme that was approved but not yet mapped is resolved rather
    than re-queued as though it were still an open question. Both steps come
    after the engine has written its own results, and promotion only fills
    rows where amfi_scheme_code IS NULL, so a rule that resolves a scheme
    itself always outranks a stale approval.

    Imported here rather than at module scope: promote_approved_mappings reads
    scheme_matching.scheme_id, and a module-level import in both directions
    would still be a cycle through this module's own name.
    """
    from map_unmatched_nav_name import run_fallback_mapping
    from promote_approved_mappings import promote_approved

    print("=" * 80)
    print("APPLYING REVIEW DECISIONS")
    print("=" * 80)

    promoted = promote_approved(engine)
    print(f"Approved rows found      : {promoted['approved']}")
    print(f"Newly mapped             : {promoted['promoted']}")
    print(f"Skipped (already mapped) : {promoted['skipped_already_mapped']}")

    print("-" * 80)
    queued = run_fallback_mapping(verbose=False)
    print(f"Still unmatched          : {queued['unmatched']}")
    print(f"Newly queued for review  : {queued['queued']} "
          f"(NAV+name {queued['tier1']}, name-only {queued['tier2']}, "
          f"NAV+fuzzy {queued['tier3']})")
    if queued["ambiguous"]:
        print(f"Ambiguous, left unmapped : {queued['ambiguous']}")
    print(f"No candidate found       : {queued['unresolved']}")

    if queued["queued"]:
        print()
        print("To map these, approve them and re-run the pipeline:")
        print("  UPDATE bronze.scheme_mapping_review")
        print("     SET reviewer_decision='APPROVED', reviewed_by='<you>', "
              "reviewed_at=now()")
        print("   WHERE reviewer_decision IS NULL;")

    print("=" * 80)

    return {
        "approved_found": promoted["approved"],
        "newly_mapped": promoted["promoted"],
        "already_mapped": promoted["skipped_already_mapped"],
        "still_unmatched": queued["unmatched"],
        "newly_queued": queued["queued"],
        "queued_tier1": queued["tier1"],
        "queued_tier2": queued["tier2"],
        "ambiguous": queued["ambiguous"],
        "no_candidate": queued["unresolved"],
    }


# =====================================================
# ENTRY POINT
# =====================================================

if __name__ == "__main__":
    load_scheme_mapping()
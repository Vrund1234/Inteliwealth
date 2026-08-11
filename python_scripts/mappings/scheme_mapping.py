import re
import uuid
import pandas as pd
from sqlalchemy import text
from rapidfuzz import fuzz, process
 
from utils.db import engine, restore_engine
 
 
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
 
 
# Rule 0 uses this to reject anything that is not a well-formed ISIN.
ISIN_PATTERN = r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$"


def unique_match_lookup(frame, key_column, value_column):
    """key -> value Series holding ONLY keys that appear on exactly one row.

    This is the vectorised form of the old ``if len(matches) == 1`` guard.
    A plain left merge cannot express it: a key present on two or more AMFI
    rows would fan the left row out into duplicates, whereas the rule
    requires an ambiguous key to match *nothing* and fall through to the
    next rule.  Counting first and dropping every non-unique key makes the
    subsequent join safe — one row in, at most one row out.

    Null keys are dropped (``value_counts`` skips them), which matches the
    old ``amfi_df[col] == value`` filter: a null never compares equal, so a
    null-named AMFI row could never be the single match.
    """
    keys = frame[key_column]

    counts = keys.value_counts(dropna=True)

    unique_keys = counts.index[counts == 1]

    return (
        frame[keys.isin(unique_keys)]
        .set_index(key_column)[value_column]
    )


def apply_isin_match(df, amfi_df):
    """Rule 0 : ISIN Match (100) — vectorised.

    Replaces a per-row loop that re-filtered the whole of ``amfi_df`` on
    every iteration (O(rows x amfi)) with a single lookup build plus one
    ``isin``/``map`` pass (O(rows + amfi)).

    Only rows whose ``mapping_confidence`` is still null are considered, so
    this stays composable with the rules that follow.
    """
    pending = df["mapping_confidence"].isna()

    rta_isin = df["rta_isin"].astype("string")

    # Equivalent to the old `if not row["rta_isin"]: continue` followed by
    # `if not isin_pattern.match(...): continue` — nulls and blanks become
    # NA / "" and fail the anchored pattern anyway.
    eligible = pending & rta_isin.str.match(ISIN_PATTERN, na=False)

    if not eligible.any():
        return

    # An AMFI row is ONE match even when its growth and IDCW ISIN are the
    # same value, so pairs are de-duplicated per (AMFI row, isin) before
    # counting — otherwise such a row would look ambiguous and be skipped.
    isin_pairs = (
        pd.concat(
            [
                amfi_df[["amfi_scheme_code", column]]
                .rename(columns={column: "isin"})
                for column in ("isin_growth", "isin_idcw")
            ]
        )
        .dropna(subset=["isin"])
        .reset_index(names="amfi_row")
        .drop_duplicates(subset=["amfi_row", "isin"])
    )

    lookup = unique_match_lookup(
        isin_pairs,
        "isin",
        "amfi_scheme_code"
    )

    matched = eligible & df["rta_isin"].isin(lookup.index)

    if not matched.any():
        return

    df.loc[matched, "amfi_scheme_code"] = (
        df.loc[matched, "rta_isin"].map(lookup)
    )
    df.loc[matched, "mapping_source"] = "ISIN_MATCH"
    df.loc[matched, "mapping_confidence"] = 100


def apply_exact_name_match(df, amfi_df):
    """Rule 1 : Exact Name Match (99) — vectorised.

    Same shape as :func:`apply_isin_match`: build the unique-key lookup once
    instead of re-filtering ``amfi_df`` inside a per-row loop, and apply it
    only to rows Rule 0 left unmatched.
    """
    pending = df["mapping_confidence"].isna()

    if not pending.any():
        return

    lookup = unique_match_lookup(
        amfi_df,
        "name_norm",
        "amfi_scheme_code"
    )

    matched = pending & df["normalized_scheme_name"].isin(lookup.index)

    if not matched.any():
        return

    df.loc[matched, "amfi_scheme_code"] = (
        df.loc[matched, "normalized_scheme_name"].map(lookup)
    )
    df.loc[matched, "mapping_source"] = "EXACT_NAME"
    df.loc[matched, "mapping_confidence"] = 99


def load_scheme_mapping():

    print("=" * 80)
    print("STARTING SCHEME MAPPING")
    print("=" * 80)
 
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
 
    df.rename(
        columns={
            "source": "rta",
            "amc_code": "rta_amc_code",
            "prodcode": "rta_scheme_code",
            "scheme": "rta_scheme_name",
        },
        inplace=True,
    )
 
    existing_mapping_query = """
    SELECT
        rta,
        rta_scheme_code,
        normalized_scheme_name
    FROM bronze.scheme_mapping
    WHERE amfi_scheme_code IS NOT NULL;
    """
 
    existing_mapping_df = pd.read_sql(
        existing_mapping_query,
        engine
    )
 
 
    df = df.merge(
        existing_mapping_df,
        on=[
            "rta",
            "rta_scheme_code"
        ],
        how="left",
        indicator=True
    )
 
 
    df = df[
        df["_merge"] == "left_only"
    ].drop(
        columns=["_merge"]
    )
 
    # Generate stable mapping_id
    df["mapping_id"] = df.apply(
        lambda x: str(
            uuid.uuid5(
                uuid.NAMESPACE_DNS,
                f"{x['rta']}|{x['rta_scheme_code']}"
            )
        ),
        axis=1
    )
 
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
            w for w in name.split()
            if w not in remove_words
        ]
 
        return " ".join(words)
 
    df["normalized_scheme_name"] = df["rta_scheme_name"].apply(
        normalize_scheme_name
    )
 
    df["short_scheme_name"] = df["rta_scheme_name"].apply(
        normalize_short_name
    )
 
    # Placeholder until RTA starts providing ISIN
    df["rta_isin"] = None
 
    # Load AMFI Scheme Master
    amfi_query = """
    SELECT
        amfi_scheme_code,
        amc_code,
        name_norm,
        isin_growth,
        isin_idcw
    FROM public.amfi_scheme_master
    WHERE status = 'ACTIVE';
    """
 
    amfi_df = pd.read_sql(
        amfi_query,
        restore_engine()  # call to get a fresh engine instance
    )
 
    # Normalize AMFI scheme name first
    amfi_df["name_norm"] = amfi_df["name_norm"].apply(
        normalize_scheme_name
    )
 
    # Create short name after normalization
    amfi_df["short_name"] = amfi_df["name_norm"].apply(
        normalize_short_name
    )
 
    # nav_query = """
    # SELECT
    #     scheme_code,
    #     nav_date,
    #     nav
    # FROM public.nav_master;
    # """
 
    # nav_df = pd.read_sql(
    #     nav_query,
    #     restore_engine
    # )
 
    # Load RTA -> AMC Slug mapping
    amc_mapping_query = """
    SELECT
        rta,
        amc_code AS rta_amc_code,
        amc_slug
    FROM public.rta_amc_code;
    """
 
    amc_mapping_df = pd.read_sql(
        amc_mapping_query,
        restore_engine()  # call to get a fresh engine instance
    )
 
    df = df.merge(
        amc_mapping_df,
        on=["rta", "rta_amc_code"],
        how="left"
    )
 
    # Match by normalized scheme name
    # Match by normalized scheme name
    # df = df.merge(
    #     amfi_df[
    #         [
    #             "amfi_scheme_code",
    #             "amc_code",
    #             "name_norm",
    #             "isin_growth",
    #             "isin_idcw"
    #         ]
    #     ],
    #     left_on="normalized_scheme_name",
    #     right_on="name_norm",
    #     how="left",
    #     suffixes=("", "_amfi")
    # )
 
    df["mapping_source"] = None
    df["mapping_confidence"] = None

    # Created up front rather than by `.at` enlargement inside the rule
    # loops, so the column exists (all-null) even when no rule matches a
    # single row — the merge on amfi_scheme_code further down would
    # otherwise raise KeyError on a run with zero matches.
    df["amfi_scheme_code"] = None

    # Rule 0 : ISIN Match (100)
    # Currently no RTA file contains ISIN, so this rule will not match any rows.

    apply_isin_match(df, amfi_df)

    # ===============================
    # Rule 1 : Exact Name Match (99)
    # ===============================

    apply_exact_name_match(df, amfi_df)


    # Rule 2 : Product Match (100)
    # Still row-wise, but now iterates only what Rules 0/1 left unmatched
    # instead of walking every row and skipping most of them.

    unmatched_product_df = df[
        df["mapping_confidence"].isna()
    ].copy()

    for idx, row in unmatched_product_df.iterrows():

        matches = amfi_df[
            (amfi_df["amc_code"] == row["rta_amc_code"])
            &
            (amfi_df["name_norm"] == row["normalized_scheme_name"])
        ]
 
        if len(matches) == 1:
            df.at[idx, "amfi_scheme_code"] = matches.iloc[0]["amfi_scheme_code"]
            df.at[idx, "mapping_source"] = "PRODUCT_MATCH"
            df.at[idx, "mapping_confidence"] = 100
 
    # Rule 3 : AMC + Scheme Name Match (97)
 
    unmatched_amc_df = df[
        df["mapping_confidence"].isna()
    ].copy()
 
 
    for idx, row in unmatched_amc_df.iterrows():
 
        if pd.isna(row["amc_slug"]):
            continue
 
 
        # First try exact normalized name
        matches = amfi_df[
            (amfi_df["amc_code"] == row["amc_slug"])
            &
            (amfi_df["name_norm"] == row["normalized_scheme_name"])
        ]
 
        # If no unique match, try short name
        if len(matches) != 1:
            matches = amfi_df[
                (amfi_df["amc_code"] == row["amc_slug"])
                &
                (amfi_df["short_name"] == row["short_scheme_name"])
            ]
 
        if len(matches) == 1:
 
            df.at[idx, "amfi_scheme_code"] = matches.iloc[0]["amfi_scheme_code"]
            df.at[idx, "mapping_source"] = "AMC_NAME"
            df.at[idx, "mapping_confidence"] = 97
 
    # Rule 4 : Fuzzy Name Match (95)
 
    unmatched_df = df[
        df["mapping_confidence"].isna()
    ].copy()
 
 
    amfi_names = amfi_df[
        [
            "amfi_scheme_code",
            "name_norm"
        ]
    ].dropna()
 
 
    amfi_name_list = amfi_names["name_norm"].tolist()
 
 
    amfi_lookup = dict(
        zip(
            amfi_names["name_norm"],
            amfi_names["amfi_scheme_code"]
        )
    )
 
 
    def fuzzy_match(row):
 
        match = process.extractOne(
            row["normalized_scheme_name"],
            amfi_name_list,
            scorer=fuzz.ratio,
            score_cutoff=98
        )
 
 
        if match:
 
            matched_name = match[0]
 
            return pd.Series(
                [
                    amfi_lookup[matched_name],
                    "NAME_FUZZY",
                    95
                ]
            )
 
 
        return pd.Series(
            [
                None,
                None,
                None
            ]
        )
 
 
    df.loc[
        df["mapping_confidence"].isna(),
        [
            "amfi_scheme_code",
            "mapping_source",
            "mapping_confidence"
        ]
    ] = unmatched_df.apply(
        fuzzy_match,
        axis=1
    ).values
 
 
    # =====================================================
    # Generate Internal Scheme ID
    # =====================================================
 
    matched_amfi = amfi_df[
        ["amfi_scheme_code", "amc_code"]
    ].drop_duplicates()
 
    df = df.merge(
        matched_amfi,
        on="amfi_scheme_code",
        how="left",
        suffixes=("", "_master")
    )
 
    df["scheme_id"] = (
        df["amc_code"].fillna("").astype(str)
        + df["amfi_scheme_code"].fillna("").astype(str)
    )
   
    # # Rule 3 : AMC + Scheme Name Match (97)
 
    # unmatched_amc_df = df[
    #     df["mapping_confidence"].isna()
    # ].copy()
 
 
    # for idx, row in unmatched_amc_df.iterrows():
 
    #     if pd.isna(row["amc_slug"]):
    #         continue
 
 
    #     matches = amfi_df[
    #         (amfi_df["amc_code"] == row["amc_slug"])
    #         &
    #         (amfi_df["name_norm"] == row["normalized_scheme_name"])
    #     ]
 
 
    #     # Exact name + AMC match
    #     if len(matches) == 1:
 
    #         df.at[idx, "amfi_scheme_code"] = (
    #             matches.iloc[0]["amfi_scheme_code"]
    #         )
 
    #         df.at[idx, "mapping_source"] = "AMC_NAME"
 
    #         df.at[idx, "mapping_confidence"] = 97
 
    df.drop(
        columns=[
            "name_norm",
            "match_count"
        ],
        errors="ignore",
        inplace=True
    )
 
    # Convert pandas NaN to SQL NULL
    df = df.where(pd.notna(df), None)
 
    # Ensure mapping_confidence is integer or NULL
    df["mapping_confidence"] = df["mapping_confidence"].astype("Int64")
 
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
                ON CONFLICT (rta, rta_scheme_code)
        DO NOTHING;
    """)
 
    with engine.begin() as conn:
        conn.execute(
            insert_query,
            df.to_dict(orient="records")
        )
 
    print(f"Processed : {len(df)}")
    print("Duplicates skipped automatically.")
    print("=" * 80)
    print("SCHEME MAPPING COMPLETED")
    print("=" * 80)
 
 
if __name__ == "__main__":
    load_scheme_mapping()
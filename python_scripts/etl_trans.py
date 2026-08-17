import pandas as pd
import numpy as np
# from sqlalchemy import create_engine
from mapping import TRANSACTION_MASTER_MAPPING
from datetime import datetime
from zoneinfo import ZoneInfo
from utils.db import engine

# =====================================================
# CLEAN COLUMN NAMES
# =====================================================

def clean_columns(df):

    if df is None:
        return df

    df = df.copy()

    df.columns = (
        df.columns.astype(str)
        .str.strip("'")
        .str.strip('"')
        .str.strip()
        .str.lower()
        .str.replace(" ", "_", regex=False)
        .str.replace("-", "_", regex=False)
        .str.replace("/", "_", regex=False)
        .str.replace("#", "", regex=False)
    )

    # Keep first duplicate column
    df = df.loc[:, ~df.columns.duplicated(keep="first")]

    return df

# =====================================================
# DATE COLUMNS
# =====================================================

DATE_COLUMNS = [
    "traddate",
    "postdate",
    "rep_date",
    "ticob_posted_date",
    "sys_regn_date",
    "ca_initiated_date"
]


# =====================================================
# IDENTIFIER COLUMNS
# =====================================================

IDENTIFIER_COLUMNS = [

    "folio_no",
    "trxnno",
    "usrtrxno",
    "application_no",

    "scheme_folio_number",
    "folio_old",
    "old_folio",

    "micr_no",
    "ac_no",

    "dp_id",
    "client_id",                # NEW
    "common_account_number",    # NEW
    "ft_accno",                 # NEW
    "rejtrnoor2",               # NEW
    "to_product_code",  

    "ticob_trno",
    "siptrxnno",

    "amc_ref_no",
    "request_ref_no"
]


# =====================================================
# NORMALIZE
# =====================================================

def normalize(df):

    if df is None:
        return df

    df = df.copy()

    for col in df.columns:

        if col in DATE_COLUMNS:
            continue

        if pd.api.types.is_datetime64_any_dtype(df[col]):
            continue

        df[col] = (
            df[col]
            .fillna("")
            .astype(str)
            .str.replace("'", "", regex=False)
            .str.replace('"', "", regex=False)
            .str.strip()
            .replace({
                "nan": "",
                "None": "",
                "<NA>": "",
                "NaT": ""
            })
        )

    return df


# =====================================================
# CLEAN IDENTIFIER COLUMNS
# =====================================================

def clean_identifier_columns(df):

    if df is None:
        return df

    df = df.copy()

    for col in IDENTIFIER_COLUMNS:

        if col not in df.columns:
            continue

        df[col] = (
            df[col]
            .fillna("")
            .astype(str)
            .str.strip()
            .str.replace(r"\.0$", "", regex=True)
            .replace({
                "": None,
                "nan": None,
                "None": None,
                "<NA>": None
            })
        )

    return df


# =====================================================
# CLEAN VALUE
# =====================================================

def clean_value(value):

    if pd.isna(value):
        return None

    value = str(value).strip()

    if value.lower() in [
        "",
        "nan",
        "none",
        "<na>",
        "nat"
    ]:
        return None

    return value


# =====================================================
# FORMAT DATE COLUMNS
# =====================================================

def format_dates(df):
    """
    Normalizes every column in DATE_COLUMNS to a python `date` object.

    IMPORTANT: `postdate` is already converted to a proper datetime64
    column upstream in apply_transaction_mapping() for BOTH CAMS and
    KFIN. If a column is already datetime64, we must NOT stringify it
    and re-parse with a strict format - doing so turns values like
    the Timestamp for 2019-03-20 into the string "2019-03-20 00:00:00",
    which does not match "%m/%d/%Y" and silently becomes NaT. That was
    wiping out postdate on every run, for every source.
    """

    if df is None:
        return df

    df = df.copy()

    for col in DATE_COLUMNS:

        if col not in df.columns:
            continue

        # -------------------------------------------------
        # Already a real datetime column (e.g. postdate,
        # which apply_transaction_mapping() already parsed)
        # -> use it as-is, do not stringify/re-parse it.
        # -------------------------------------------------
        if pd.api.types.is_datetime64_any_dtype(df[col]):

            values = df[col]

        else:

            # Clean whitespace including non-breaking space (\xa0)
            # and stray quote characters before parsing.
            values = (
                df[col]
                .astype("string")
                .str.replace("\xa0", " ", regex=False)
                .str.replace("'", "", regex=False)
                .str.replace('"', "", regex=False)
                .str.strip()
            )

            # -------------------------------------------------
            # POSTDATE
            # -------------------------------------------------
            if col == "postdate":

                # CAMS format: M/D/YYYY
                values = pd.to_datetime(
                    values,
                    errors="coerce",
                    format="%m/%d/%Y"
                )

            else:

                values = pd.to_datetime(
                    values,
                    errors="coerce"
                )

        df[col] = values.dt.date

        df[col] = df[col].where(
            pd.notnull(df[col]),
            None
        )

    return df

# =====================================================
# APPLY TRANSACTION MAPPING
# =====================================================

def apply_transaction_mapping(raw_df, mapping, source):

    raw_df = clean_columns(raw_df)
    print("SOURCE:", source)
    debug_cols = [c for c in ["divper", "guardpanno"] if c in raw_df.columns]

    if debug_cols:
        print(raw_df[debug_cols].head(20))
    else:
        print("Columns not present:", ["divper", "guardpanno"])
    print("=" * 80)
    print(f"Processing {source}")
    print(f"Rows : {len(raw_df)}")
    print(f"Columns : {len(raw_df.columns)}")
    print(raw_df.columns.tolist())
    print("=" * 80)

    mapped_df = pd.DataFrame(index=raw_df.index)

    for target_col, source_cols in mapping.items():

        if target_col in ("flag", "created_at", "updated_at"):
            continue

        if target_col == "source":
            mapped_df[target_col] = source
            continue

        mapped_df[target_col] = None

        if target_col == "postdate":

            # =====================================================
            # KFIN: the RTA sends the date only in td_prdt (KFIN
            # files do NOT have a "postdate" column at all - that
            # name only exists on the CAMS side). td_prdt values can
            # still arrive quoted / with \xa0 / with a trailing
            # time-of-day suffix (e.g. "6/6/2023  12:00:00 AM"), so
            # clean them the same way before parsing. td_prdt is in
            # D/M/Y order.
            # =====================================================
            if source == "KFIN":

                if "td_prdt" in raw_df.columns:

                    cleaned = (
                        raw_df["td_prdt"]
                        .astype("string")
                        .str.replace("\xa0", " ", regex=False)
                        .str.replace("'", "", regex=False)
                        .str.replace('"', "", regex=False)
                        .str.strip()
                    )

                    # Keep only the D/M/YYYY (or D/M/YY) portion,
                    # drop any trailing " 12:00:00 AM" style text.
                    cleaned = cleaned.str.extract(
                        r"(\d{1,2}/\d{1,2}/\d{2,4})"
                    )[0]

                    mapped_df["postdate"] = pd.to_datetime(
                        cleaned,
                        errors="coerce",
                        format="%d/%m/%Y"
                    )

                continue

            # =====================================================
            # CAMS: postdate arrives the same way KFIN's does, e.g.
            #   '3/20/2019  12:00:00 AM'
            # quoted, with \xa0/whitespace, and a trailing time-of-day
            # component. Stripping quotes alone is NOT enough because
            # the leftover "  12:00:00 AM" still fails to match the
            # strict format "%m/%d/%Y" and silently becomes NaT.
            # Extract just the M/D/YYYY portion before parsing.
            # =====================================================
            if source == "CAMS" and "postdate" in raw_df.columns:

                cleaned = (
                    raw_df["postdate"]
                    .astype("string")
                    .str.replace("\xa0", " ", regex=False)
                    .str.replace("'", "", regex=False)
                    .str.replace('"', "", regex=False)
                    .str.strip()
                )

                # Keep only the M/D/YYYY portion, drop any trailing
                # " 12:00:00 AM" style time text.
                cleaned = cleaned.str.extract(
                    r"(\d{1,2}/\d{1,2}/\d{4})"
                )[0]

                mapped_df["postdate"] = pd.to_datetime(
                    cleaned,
                    errors="coerce",
                    format="%m/%d/%Y"
                )

                continue

        for src in source_cols:
            src = src.lower().strip()

            if src in raw_df.columns:

                source_values = raw_df[src].copy()

                # Treat blank values as missing
                source_values = source_values.replace(
                    ["", "nan", "None", "<NA>", "NaT"],
                    np.nan
                )

                # Fill only rows where target is still missing
                mapped_df[target_col] = mapped_df[target_col].fillna(
                    source_values
                )

    return mapped_df
    

# =====================================================
# PROCESS TRANSACTIONS
# =====================================================

def process_transactions(cams=None, kfin=None):

    dfs = []

    # =====================================================
    # CAMS
    # =====================================================

    if cams is not None and not cams.empty:

        cams_df = apply_transaction_mapping(
            cams,
            TRANSACTION_MASTER_MAPPING,
            "CAMS"
        )

        print("=" * 80)
        print("CAMS POSTDATE AFTER MAPPING")

        print(cams_df["postdate"].head(20))

        print(
            "CAMS POSTDATE NON-NULL:",
            cams_df["postdate"].notna().sum(),
            "/",
            len(cams_df)
        )

        print(
            "CAMS POSTDATE NULL:",
            cams_df["postdate"].isna().sum()
        )

        print("=" * 80)

        cams_df = normalize(cams_df)

        cams_df = clean_identifier_columns(cams_df)

        cams_df = format_dates(cams_df)

        print("=" * 80)
        print("CAMS POSTDATE AFTER FORMAT_DATES")

        print(cams_df["postdate"].head(20))

        print(
            "CAMS POSTDATE NON-NULL:",
            cams_df["postdate"].notna().sum(),
            "/",
            len(cams_df)
        )

        print(
            "CAMS POSTDATE NULL:",
            cams_df["postdate"].isna().sum()
        )

        print("=" * 80)

        dfs.append(cams_df)
        print(cams_df.head())

    # =====================================================
    # KFIN
    # =====================================================

    if kfin is not None and not kfin.empty:

        kfin_df = apply_transaction_mapping(
            kfin,
            TRANSACTION_MASTER_MAPPING,
            "KFIN"
        )

        print("=" * 80)
        print("KFIN POSTDATE AFTER MAPPING")

        print(kfin_df["postdate"].head(20))

        print(
            "KFIN POSTDATE NON-NULL:",
            kfin_df["postdate"].notna().sum(),
            "/",
            len(kfin_df)
        )

        print(
            "KFIN POSTDATE NULL:",
            kfin_df["postdate"].isna().sum()
        )

        print("=" * 80)

        kfin_df = normalize(kfin_df)

        kfin_df = clean_identifier_columns(kfin_df)

        kfin_df = format_dates(kfin_df)

        dfs.append(kfin_df)
        print(kfin_df.head())
        print("="*80)
        print("KFIN dataframe")
        print(kfin.head())
        print(kfin.columns.tolist())
    

    # =====================================================
    # NO FILES
    # =====================================================

    if not dfs:

        print("No Transaction file found.")
        return

    # =====================================================
    # MERGE
    # =====================================================

    df = pd.concat(
        dfs,
        ignore_index=True
    )

    now = pd.Timestamp.now(tz="Asia/Kolkata")

    df["created_at"] = now
    df["updated_at"] = now

    # =====================================================
    # READ EXISTING BRONZE TABLE
    # =====================================================

    try:

        existing = pd.read_sql(
            "SELECT * FROM bronze.transaction_master_new",
            engine
        )

        existing = normalize(existing)

        existing = clean_identifier_columns(existing)

        existing = format_dates(existing)

    except Exception:

        existing = pd.DataFrame()

    # =====================================================
    # DUPLICATE FLAG
    # =====================================================

    ignore_cols = {
        "flag",
        "created_at",
        "updated_at",
        "source"
    }

    if existing.empty:

        df["flag"] = 0

    else:

        compare_cols = [

            c

            for c in df.columns

            if c in existing.columns

            and c not in ignore_cols

        ]

        new_df = df[compare_cols].copy()

        old_df = existing[compare_cols].copy()

        # =====================================================
        # CLEAN IDENTIFIER COLUMNS BEFORE COMPARISON
        # =====================================================

        new_df = clean_identifier_columns(new_df)

        old_df = clean_identifier_columns(old_df)

        # =====================================================
        # NORMALIZE VALUES
        # =====================================================

        for col in compare_cols:

            if col in DATE_COLUMNS:

                new_df[col] = (
                    pd.to_datetime(
                        new_df[col],
                        errors="coerce"
                    )
                    .dt.strftime("%Y-%m-%d")
                    .fillna("")
                )

                old_df[col] = (
                    pd.to_datetime(
                        old_df[col],
                        errors="coerce"
                    )
                    .dt.strftime("%Y-%m-%d")
                    .fillna("")
                )

            else:

                new_df[col] = (
                    new_df[col]
                    .fillna("")
                    .astype(str)
                    .str.strip()
                )

                old_df[col] = (
                    old_df[col]
                    .fillna("")
                    .astype(str)
                    .str.strip()
                )

        # =====================================================
        # COMPARE COMPLETE ROW
        # =====================================================

        new_keys = (
            new_df
            .astype(str)
            .agg("|".join, axis=1)
        )

        old_keys = set(
            old_df
            .astype(str)
            .agg("|".join, axis=1)
        )

        df["flag"] = (
            new_keys
            .isin(old_keys)
            .astype(int)
        )

    # =====================================================
    # CLEAN IDENTIFIER COLUMNS AGAIN
    # =====================================================

    #df = clean_identifier_columns(df)

    # =====================================================
    # GET DATABASE COLUMN ORDER
    # =====================================================

    db_columns = pd.read_sql(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'bronze'
        AND table_name = 'transaction_master_new'
        ORDER BY ordinal_position
        """,
        engine
    )["column_name"].tolist()

    # =====================================================
    # ADD MISSING COLUMNS
    # =====================================================

    for col in db_columns:

        if col not in df.columns:

            df[col] = None

    # =====================================================
    # KEEP ONLY DATABASE COLUMNS
    # =====================================================

    df = df[db_columns]

        # =====================================================
    # FINAL DATE CLEANING
    # =====================================================

    for col in DATE_COLUMNS:

        if col in df.columns:

            df[col] = (
                pd.to_datetime(
                    df[col],
                    errors="coerce"
                )
                .dt.date
            )

            df[col] = df[col].where(
                pd.notnull(df[col]),
                None
            )

    # =====================================================
    # CLEAN NON-DATE COLUMNS
    # =====================================================

    for col in df.columns:

        if col not in DATE_COLUMNS:

            df[col] = (
                df[col]
                .replace({
                    "": None,
                    "nan": None,
                    "None": None,
                    "<NA>": None,
                    "NaT": None
                })
            )

    # =====================================================
    # FINAL CLEAN IDENTIFIER COLUMNS
    # =====================================================

    #df = clean_identifier_columns(df)

    # =====================================================
    # DEBUG DATE COLUMNS
    # =====================================================

    print("=" * 80)
    print("Checking DATE columns before insert")

    for col in DATE_COLUMNS:

        if col in df.columns:

            print(f"{col} : {df[col].dtype}")

            bad = df[df[col].astype(str) == ""]

            if len(bad):

                print(f"{col} has {len(bad)} empty strings")

            print(df[col].head())

    print("=" * 80)

    # =====================================================
    # REPLACE REMAINING NaN
    # =====================================================

    df = df.where(
        pd.notnull(df),
        None
    )

    # =====================================================
    # FINAL SAFETY CHECK FOR DATE COLUMNS
    # =====================================================

    for col in DATE_COLUMNS:

        if col in df.columns:

            df.loc[
                df[col].astype(str).isin(
                    ["", "NaT", "nan", "None"]
                ),
                col
            ] = None

    # =====================================================
    # FINAL CLEAN IDENTIFIER COLUMNS
    # =====================================================

    df = clean_identifier_columns(df)

    # =====================================================
    # REMOVE EXACT DUPLICATE ROWS
    # =====================================================

  # before = len(df)

   # df = (
   #     df
    #    .drop_duplicates(keep="first")
    #    .reset_index(drop=True)
   # )

    #print(
    #    f"Removed {before - len(df)} exact duplicate rows"
   # )

    # =====================================================
    # FINAL COLUMN ORDER CHECK
    # =====================================================

    df = df[db_columns]

        # =====================================================
    # INSERT INTO POSTGRES
    # =====================================================

    print("=" * 80)
    print("Loading Transaction Master...")
    print(f"Rows to insert : {len(df)}")
    print("=" * 80)

    df.to_sql(
        "transaction_master_new",
        engine,
        schema="bronze",
        if_exists="append",
        index=False,
        method="multi",
        chunksize=50000
    )

    print("=" * 80)
    print("Transaction Master Loaded Successfully")
    print(f"Inserted {len(df)} rows")
    print("=" * 80)

    return len(df)
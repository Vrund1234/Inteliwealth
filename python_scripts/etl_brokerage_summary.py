# =====================================================
# BRONZE LOADER : BROKERAGE SUMMARY BY SCHEME
#
# Feeds bronze.brokerage_summary from the RTA brokerage
# reports:
#
#   CAMS WBR36   - current period
#   CAMS WBR36H  - historic / adjustments
#   KFINTECH     - added later, no code change needed
#
# The loader is source-agnostic. Everything RTA-specific
# lives in mapping_wbr.py:
#
#   BROKERAGE_FILE_PATTERNS      which file is which
#   BROKERAGE_SUMMARY_MAPPING    header aliases
#   BROKERAGE_SOURCE_OVERRIDES   per-RTA alias overrides
#
# Same house rules as the other bronze loaders:
#   - every value stored as TEXT (dates as DATE)
#   - nothing updated, nothing deleted
#   - repeat rows appended with flag = 1 and never
#     travel further, because Silver reads flag = 0
# =====================================================

import pandas as pd

from utils.db import engine

from mapping_wbr import (
    BROKERAGE_SUMMARY_MAPPING,
    BROKERAGE_SOURCE_OVERRIDES,
    BROKERAGE_AMOUNT_COLUMNS,
    BROKERAGE_DATE_COLUMNS,
    BROKERAGE_IDENTIFIER_COLUMNS
)


BRONZE_TABLE = "brokerage_summary"


# =====================================================
# COLUMNS NOT READ FROM THE FILE
# =====================================================

STAMPED_COLUMNS = [
    "source",
    "report_type",
    "flag",
    "created_at",
    "updated_at"
]


# =====================================================
# DATE FORMATS PER RTA
#
# CAMS WBR36 / WBR36H carry no date column, so nothing
# uses the CAMS entry today. It is here so a CAMS report
# that does carry a period parses with the same rule the
# rest of the CAMS pipeline uses.
# =====================================================

DATE_FORMATS = {
    "CAMS": "%m/%d/%Y",
    "KFIN": "%d/%m/%Y"
}


# =====================================================
# CLEAN COLUMN NAMES
#
# Identical rule to the other loaders, so the aliases in
# mapping_wbr.py are written in the same form.
# =====================================================

def clean_columns(df):

    if df is None or df.empty:
        return df

    df = df.copy()

    df.columns = (
        df.columns.astype(str)
        .str.strip()
        .str.strip("'")
        .str.strip('"')
        .str.lower()
        .str.replace(" ", "_", regex=False)
        .str.replace("-", "_", regex=False)
        .str.replace("/", "_", regex=False)
        .str.replace("#", "", regex=False)
    )

    df = df.loc[:, ~df.columns.duplicated(keep="first")]

    return df


# =====================================================
# NORMALIZE VALUES
# =====================================================

def normalize(df):

    if df is None or df.empty:
        return df

    df = df.copy()

    for col in df.columns:

        if col in BROKERAGE_DATE_COLUMNS:
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
# REMOVE .0 FROM IDENTIFIER COLUMNS
# =====================================================

def clean_identifier_columns(df):

    if df is None or df.empty:
        return df

    df = df.copy()

    for col in BROKERAGE_IDENTIFIER_COLUMNS:

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
# CLEAN AMOUNT COLUMNS
#
# Bronze keeps them as TEXT, but the thousands separators
# and brackets the RTAs put in Excel are stripped here so
# Silver can cast without guessing.
#
#   "1,234.50"  -> "1234.50"
#   "(500.00)"  -> "-500.00"
#   ""          -> None
# =====================================================

def clean_amount_columns(df):

    if df is None or df.empty:
        return df

    df = df.copy()

    for col in BROKERAGE_AMOUNT_COLUMNS:

        if col not in df.columns:
            continue

        cleaned = (
            df[col]
            .fillna("")
            .astype(str)
            .str.strip()
            .str.replace(",", "", regex=False)
            .str.replace("₹", "", regex=False)
            .str.replace(r"^\((.*)\)$", r"-\1", regex=True)
        )

        # Keep only values that are really numeric.
        numeric = pd.to_numeric(
            cleaned,
            errors="coerce"
        )

        bad = cleaned.ne("") & numeric.isna()

        if bad.any():

            print(
                f"{col} : {int(bad.sum())} non-numeric "
                "value(s) set to NULL"
            )

            print(
                "Examples :",
                cleaned[bad].unique()[:5].tolist()
            )

        df[col] = (
            numeric
            .astype(object)
            .where(numeric.notna(), None)
        )

        df[col] = df[col].apply(
            lambda x: None if x is None else str(x)
        )

    return df


# =====================================================
# FORMAT DATE COLUMNS
#
# The RTA format is tried first; anything it cannot read
# falls back to pandas inference, so a file that switches
# format does not silently lose its period.
# =====================================================

def format_dates(df, source=None):

    if df is None or df.empty:
        return df

    df = df.copy()

    source = str(source).upper()

    date_format = DATE_FORMATS.get(source)

    for col in BROKERAGE_DATE_COLUMNS:

        if col not in df.columns:
            continue

        raw = df[col]

        if date_format:

            parsed = pd.to_datetime(
                raw,
                format=date_format,
                errors="coerce"
            )

        else:

            parsed = pd.to_datetime(
                raw,
                errors="coerce"
            )

        # Fallback for the rows the strict format rejected
        missing = parsed.isna() & raw.notna()

        if missing.any():

            fallback = pd.to_datetime(
                raw[missing],
                errors="coerce",
                dayfirst=(source != "CAMS")
            )

            parsed.loc[missing] = fallback

        df[col] = parsed.dt.date

        df[col] = df[col].where(
            pd.notnull(df[col]),
            None
        )

    return df


# =====================================================
# APPLY BROKERAGE MAPPING
#
# First alias present in the file wins.
# =====================================================

def apply_brokerage_mapping(raw_df, source):

    if raw_df is None or raw_df.empty:
        return pd.DataFrame()

    raw_df = clean_columns(raw_df)

    print("=" * 80)
    print("Applying Brokerage Summary Mapping")
    print(f"Source  : {source}")
    print(f"Rows    : {len(raw_df)}")
    print(f"Columns : {len(raw_df.columns)}")
    print(raw_df.columns.tolist())
    print("=" * 80)

    overrides = BROKERAGE_SOURCE_OVERRIDES.get(
        str(source).upper(),
        {}
    )

    mapped_df = pd.DataFrame(index=raw_df.index)

    unmapped_targets = []

    for target_col, source_cols in (
        BROKERAGE_SUMMARY_MAPPING.items()
    ):

        # source / report_type / flag / created_at /
        # updated_at are stamped by the loader.
        if target_col in STAMPED_COLUMNS:
            continue

        source_cols = overrides.get(
            target_col,
            source_cols
        )

        mapped_series = None

        for src_col in source_cols:

            if src_col in raw_df.columns:

                mapped_series = raw_df[src_col]

                break

        if mapped_series is None:

            unmapped_targets.append(target_col)

            mapped_series = pd.Series(
                [None] * len(raw_df),
                index=raw_df.index,
                dtype="object"
            )

        mapped_df[target_col] = mapped_series

    # =================================================
    # HEADERS PRESENT IN THE FILE BUT NOT MAPPED
    #
    # Printed, never dropped silently, so a new RTA
    # column is noticed the first time it appears.
    # =================================================

    known_aliases = set()

    for target_col, source_cols in (
        BROKERAGE_SUMMARY_MAPPING.items()
    ):

        known_aliases.update(
            overrides.get(target_col, source_cols)
        )

    unknown_headers = [
        col
        for col in raw_df.columns
        if col not in known_aliases
    ]

    if unknown_headers:

        print(
            "File headers with no mapping :",
            unknown_headers
        )

    if unmapped_targets:

        print(
            "Target columns not present in file :",
            unmapped_targets
        )

    # =================================================
    # CLEANING
    # =================================================

    mapped_df = normalize(mapped_df)

    mapped_df = clean_identifier_columns(mapped_df)

    mapped_df = clean_amount_columns(mapped_df)

    mapped_df = mapped_df.where(
        pd.notnull(mapped_df),
        None
    )

    print("Mapped Columns :", len(mapped_df.columns))

    return mapped_df


# =====================================================
# PROCESS BROKERAGE SUMMARY
#
# files : list of dicts, one per uploaded report
#
#     {
#         "df"          : DataFrame,
#         "source"      : "CAMS",
#         "report_type" : "WBR36"
#     }
#
# The shape is a list rather than the cams=/kfin= pair the
# other loaders use, because one upload can hold several
# report types per RTA (WBR36 and WBR36H together) and
# more RTAs are coming.
# =====================================================

def process_brokerage_summary(files):

    if not files:

        print("No brokerage summary file found.")

        return 0

    dfs = []

    for entry in files:

        raw_df = entry.get("df")

        source = entry.get("source")

        report_type = entry.get("report_type")

        if raw_df is None or raw_df.empty:

            print(
                f"Skipping empty {source} "
                f"{report_type} file."
            )

            continue

        print(
            f"\nProcessing {source} "
            f"{report_type} brokerage file..."
        )

        mapped_df = apply_brokerage_mapping(
            raw_df,
            source
        )

        if mapped_df.empty:
            continue

        mapped_df = format_dates(
            mapped_df,
            source
        )

        mapped_df["source"] = source

        mapped_df["report_type"] = report_type

        dfs.append(mapped_df)

        print(
            f"{source} {report_type} Rows : "
            f"{len(mapped_df)}"
        )

    if not dfs:

        print("No brokerage summary rows to load.")

        return 0

    # =================================================
    # MERGE ALL REPORTS
    # =================================================

    df = pd.concat(
        dfs,
        ignore_index=True
    )

    print("=" * 80)
    print("Merged Brokerage Summary Data")
    print(f"Total Rows : {len(df)}")
    print("=" * 80)

    # =================================================
    # REMOVE DUPLICATES INSIDE CURRENT LOAD
    # =================================================

    before = len(df)

    df = (
        df
        .drop_duplicates()
        .reset_index(drop=True)
    )

    print(
        f"Duplicate Rows Removed : {before - len(df)}"
    )

    # =================================================
    # DROP ROWS WITHOUT A PRODUCT CODE
    #
    # RTA reports end with a total line that has no
    # product code. It must not become a scheme row.
    # =================================================

    if "product_code" in df.columns:

        before = len(df)

        df = df[
            df["product_code"].notna()
        ].reset_index(drop=True)

        if before != len(df):

            print(
                "Rows without product_code dropped :",
                before - len(df)
            )

    if df.empty:

        print("Nothing left to load after cleaning.")

        return 0

    # =================================================
    # AUDIT COLUMNS
    # =================================================

    now = pd.Timestamp.now(tz="Asia/Kolkata")

    df["created_at"] = now
    df["updated_at"] = now

    # =================================================
    # READ EXISTING BRONZE TABLE
    # =================================================

    try:

        existing = pd.read_sql(
            f"SELECT * FROM bronze.{BRONZE_TABLE}",
            engine
        )

        print(f"Existing Bronze Rows : {len(existing)}")

    except Exception:

        existing = pd.DataFrame()

        print("Bronze table not found. Initial Load.")

    # =================================================
    # DUPLICATE FLAG
    #
    # Compare every business column. source and
    # report_type are part of the comparison here (unlike
    # the other loaders, which ignore source), because the
    # same product code with the same amounts is a
    # different fact in WBR36 than in WBR36H.
    # =================================================

    ignore_cols = {
        "flag",
        "created_at",
        "updated_at"
    }

    if existing.empty:

        df["flag"] = 0

    else:

        existing = clean_columns(existing)
        existing = normalize(existing)
        existing = clean_identifier_columns(existing)
        existing = clean_amount_columns(existing)

        compare_cols = [
            col
            for col in df.columns
            if col in existing.columns
            and col not in ignore_cols
        ]

        print("Columns used for duplicate check:")
        print(compare_cols)

        new_df = df[compare_cols].copy()
        old_df = existing[compare_cols].copy()

        for col in compare_cols:

            if col in BROKERAGE_DATE_COLUMNS:

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

            elif col in BROKERAGE_AMOUNT_COLUMNS:

                # Compare on value, not on spelling, so
                # "0" and "0.0" are one row.
                new_df[col] = (
                    pd.to_numeric(
                        new_df[col],
                        errors="coerce"
                    )
                    .round(6)
                    .astype(str)
                )

                old_df[col] = (
                    pd.to_numeric(
                        old_df[col],
                        errors="coerce"
                    )
                    .round(6)
                    .astype(str)
                )

            else:

                new_df[col] = (
                    new_df[col]
                    .fillna("")
                    .astype(str)
                    .str.strip()
                    .str.upper()
                )

                old_df[col] = (
                    old_df[col]
                    .fillna("")
                    .astype(str)
                    .str.strip()
                    .str.upper()
                )

        new_keys = new_df.astype(str).agg("|".join, axis=1)

        old_keys = set(
            old_df.astype(str).agg("|".join, axis=1)
        )

        df["flag"] = (
            new_keys.isin(old_keys)
            .astype(int)
        )

        print("=" * 80)
        print("Duplicate Check Result")
        print(df["flag"].value_counts(dropna=False))
        print("=" * 80)

    # =================================================
    # MATCH DATABASE COLUMN ORDER
    # =================================================

    db_columns = pd.read_sql(
        f"""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'bronze'
        AND table_name = '{BRONZE_TABLE}'
        ORDER BY ordinal_position
        """,
        engine
    )["column_name"].tolist()

    if not db_columns:

        raise RuntimeError(
            f"bronze.{BRONZE_TABLE} does not exist. "
            "Run sql_scripts/brokerage_summary.sql first."
        )

    for col in db_columns:

        if col not in df.columns:
            df[col] = None

    df = df[db_columns]

    # =================================================
    # FINAL CLEANING
    # =================================================

    df["flag"] = pd.to_numeric(
        df["flag"],
        errors="coerce"
    )

    for col in df.columns:

        if col in BROKERAGE_DATE_COLUMNS:
            continue

        if col in ("flag", "created_at", "updated_at"):
            continue

        df[col] = df[col].replace({
            "": None,
            "nan": None,
            "None": None,
            "<NA>": None
        })

    df = df.where(
        pd.notnull(df),
        None
    )

    # =================================================
    # INSERT INTO BRONZE
    # =================================================

    df.to_sql(
        BRONZE_TABLE,
        engine,
        schema="bronze",
        if_exists="append",
        index=False,
        method="multi",
        chunksize=5000
    )

    print("=" * 80)
    print("Brokerage Summary Loaded Successfully")
    print(f"Inserted {len(df)} rows")
    print("=" * 80)

    return len(df)

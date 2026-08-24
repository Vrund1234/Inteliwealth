# =====================================================
# BRONZE LOADER : WBR REPORTS
#
# One loader for every WBR report the pipeline knows:
#
#   WBR36 / WBR36H  - brokerage summary by scheme
#   WBR56           - KYC status of investor
#   WBR68           - invalid EUIN report
#
# The loader is report-agnostic AND source-agnostic.
# Everything report-specific lives in mapping_wbr.py:
#
#   WBR_REPORTS          the per-report spec
#   WBR_FILE_PATTERNS    which uploaded file is which
#   <REPORT>_MAPPING     header aliases
#   <REPORT>_OVERRIDES   per-RTA alias overrides
#
# So a NEW REPORT, or a KFINTECH equivalent of an existing
# one, is a data change in mapping_wbr.py plus its DDL -
# no change in this file.
#
# Same house rules as the other bronze loaders:
#   - every value stored as TEXT (dates as DATE)
#   - nothing updated, nothing deleted
#   - repeat rows appended with flag = 1 and never
#     travel further, because Silver reads flag = 0
# =====================================================

import pandas as pd

from utils.db import engine

from mapping_wbr import get_report_spec


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
# Tried first; anything the strict format cannot read
# falls back to pandas inference, so a file that switches
# format does not silently lose its dates.
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

def normalize(df, date_columns):

    if df is None or df.empty:
        return df

    df = df.copy()

    for col in df.columns:

        if col in date_columns:
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

def clean_identifier_columns(df, identifier_columns):

    if df is None or df.empty:
        return df

    df = df.copy()

    for col in identifier_columns:

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

def clean_amount_columns(df, amount_columns):

    if df is None or df.empty:
        return df

    df = df.copy()

    for col in amount_columns:

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
# format does not silently lose its dates.
# =====================================================

def format_dates(df, date_columns, source=None):

    if df is None or df.empty:
        return df

    df = df.copy()

    source = str(source).upper()

    date_format = DATE_FORMATS.get(source)

    for col in date_columns:

        if col not in df.columns:
            continue

        raw = df[col]

        # Excel hands back real timestamps for some
        # reports (WBR68 trade_date) and strings for
        # others (WBR56 "01-Jan-2025"). A strict format
        # only applies to the string case.
        if pd.api.types.is_datetime64_any_dtype(raw):

            parsed = pd.to_datetime(
                raw,
                errors="coerce"
            )

        elif date_format:

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
# APPLY REPORT MAPPING
#
# First alias present in the file wins.
# =====================================================

def apply_mapping(raw_df, spec, source, report_type):

    if raw_df is None or raw_df.empty:
        return pd.DataFrame()

    raw_df = clean_columns(raw_df)

    mapping = spec["mapping"]

    print("=" * 80)
    print(f"Applying {report_type} Mapping")
    print(f"Source  : {source}")
    print(f"Rows    : {len(raw_df)}")
    print(f"Columns : {len(raw_df.columns)}")
    print(raw_df.columns.tolist())
    print("=" * 80)

    overrides = spec["overrides"].get(
        str(source).upper(),
        {}
    )

    mapped_df = pd.DataFrame(index=raw_df.index)

    unmapped_targets = []

    for target_col, source_cols in mapping.items():

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

    for target_col, source_cols in mapping.items():

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

    mapped_df = normalize(
        mapped_df,
        spec["date_columns"]
    )

    mapped_df = clean_identifier_columns(
        mapped_df,
        spec["identifier_columns"]
    )

    mapped_df = clean_amount_columns(
        mapped_df,
        spec["amount_columns"]
    )

    mapped_df = mapped_df.where(
        pd.notnull(mapped_df),
        None
    )

    print("Mapped Columns :", len(mapped_df.columns))

    return mapped_df


# =====================================================
# PROCESS ONE REPORT
#
# report_key : key into mapping_wbr.WBR_REPORTS
# files      : list of dicts, one per uploaded file of
#              THAT report
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

def process_report(report_key, files):

    spec = get_report_spec(report_key)

    bronze_table = spec["table"]

    date_columns = spec["date_columns"]

    amount_columns = spec["amount_columns"]

    if not files:

        print(f"No {report_key} file found.")

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
            f"{report_type} file..."
        )

        mapped_df = apply_mapping(
            raw_df,
            spec,
            source,
            report_type
        )

        if mapped_df.empty:
            continue

        mapped_df = format_dates(
            mapped_df,
            date_columns,
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

        print(f"No {report_key} rows to load.")

        return 0

    # =================================================
    # MERGE ALL FILES OF THIS REPORT
    # =================================================

    df = pd.concat(
        dfs,
        ignore_index=True
    )

    print("=" * 80)
    print(f"Merged {report_key} Data")
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
    # DROP ROWS WITHOUT THE REPORT'S KEY
    #
    # RTA reports end with a total line that has no key.
    # It must not become a business row.
    # =================================================

    for col in spec["required_columns"]:

        if col not in df.columns:
            continue

        before = len(df)

        df = df[
            df[col].notna()
        ].reset_index(drop=True)

        if before != len(df):

            print(
                f"Rows without {col} dropped :",
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
            f"SELECT * FROM bronze.{bronze_table}",
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
    # same key with the same values is a different fact in
    # WBR36 than in WBR36H.
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

        existing = normalize(
            existing,
            date_columns
        )

        existing = clean_identifier_columns(
            existing,
            spec["identifier_columns"]
        )

        existing = clean_amount_columns(
            existing,
            amount_columns
        )

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

            if col in date_columns:

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

            elif col in amount_columns:

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
        AND table_name = '{bronze_table}'
        ORDER BY ordinal_position
        """,
        engine
    )["column_name"].tolist()

    if not db_columns:

        raise RuntimeError(
            f"bronze.{bronze_table} does not exist. "
            f"Run {spec['sql_script']} first."
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

        if col in date_columns:
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
        bronze_table,
        engine,
        schema="bronze",
        if_exists="append",
        index=False,
        method="multi",
        chunksize=5000
    )

    print("=" * 80)
    print(f"{report_key} Loaded Successfully")
    print(f"Inserted {len(df)} rows")
    print("=" * 80)

    return len(df)


# =====================================================
# PROCESS EVERY WBR REPORT IN ONE UPLOAD
#
# files : list of dicts as above, each one also carrying
#         "report_key". Files are grouped by report_key
#         and each group is loaded into its own table.
#
# Returns {report_key: rows_inserted}.
# =====================================================

def process_wbr_reports(files):

    if not files:

        print("No WBR report file found.")

        return {}

    grouped = {}

    for entry in files:

        report_key = entry.get("report_key")

        if report_key is None:

            print(
                "Skipping WBR file with no report_key :",
                entry.get("report_type")
            )

            continue

        grouped.setdefault(report_key, []).append(entry)

    results = {}

    for report_key, group in grouped.items():

        print()
        print("=" * 80)
        print(f"BRONZE LOAD : {report_key}")
        print("=" * 80)

        try:

            results[report_key] = process_report(
                report_key,
                group
            )

        except Exception as e:

            print(f"{report_key} bronze load FAILED")
            print(e)

            results[report_key] = 0

    return results

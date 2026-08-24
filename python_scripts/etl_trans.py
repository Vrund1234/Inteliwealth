import pandas as pd
import numpy as np

from mapping import TRANSACTION_MASTER_MAPPING
from datetime import datetime, date
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

    # Keep first occurrence if duplicate column names exist.
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
    "ca_initiated_date",
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
    "client_id",
    "common_account_number",
    "ft_accno",
    "rejtrnoor2",
    "to_product_code",
    "ticob_trno",
    "siptrxnno",
    "amc_ref_no",
    "request_ref_no",
]


# =====================================================
# DATE PARSER
# =====================================================

def parse_source_date(value):
    """
    Parse a source date WITHOUT pandas date inference.

    Supported deterministic formats:

        DD-MM-YYYY
        DD/MM/YYYY
        DD-MM-YYYY HH:MM:SS
        DD/MM/YYYY HH:MM:SS
        DD-MM-YYYY HH:MM:SS AM/PM
        DD/MM/YYYY HH:MM:SS AM/PM

        YYYY-MM-DD
        YYYY-MM-DD HH:MM:SS
        YYYY-MM-DDTHH:MM:SS
        YYYY-MM-DDTHH:MM:SS.sss

    Also supports Python datetime/date and pandas Timestamp objects.

    IMPORTANT:
    The source value 17-08-2026 is ALWAYS interpreted as:
        17 August 2026
    and stored as:
        2026-08-17

    We deliberately do NOT use pd.to_datetime(..., dayfirst=True)
    or automatic format inference because ambiguous dates can otherwise
    be interpreted incorrectly.

    For ambiguous numeric dates such as 03/04/2026, the function does
    NOT guess. It returns None and logs the value.
    """

    if value is None:
        return None

    if isinstance(value, pd.Timestamp):
        if pd.isna(value):
            return None
        return value.to_pydatetime().date()

    if isinstance(value, datetime):
        return value.date()

    if isinstance(value, date):
        return value

    if pd.isna(value):
        return None

    value = (
        str(value)
        .replace("\xa0", " ")
        .replace("\r", " ")
        .replace("\n", " ")
        .replace("'", "")
        .replace('"', "")
        .strip()
    )

    if value.lower() in {
        "",
        "nan",
        "none",
        "<na>",
        "nat",
        "null",
    }:
        return None

    # -------------------------------------------------
    # ISO format: YYYY-MM-DD...
    # -------------------------------------------------
    # If the source is already ISO, preserve its exact date
    # instead of allowing pandas to reinterpret it.
    iso_date = value[:10]

    if (
        len(value) >= 10
        and iso_date[4] == "-"
        and iso_date[7] == "-"
        and iso_date[:4].isdigit()
        and iso_date[5:7].isdigit()
        and iso_date[8:10].isdigit()
    ):
        try:
            return datetime.strptime(iso_date, "%Y-%m-%d").date()
        except ValueError:
            return None

    # -------------------------------------------------
    # Extract DD-MM-YYYY / DD/MM/YYYY date portion
    # -------------------------------------------------
    import re

    match = re.search(
        r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})",
        value,
    )

    if not match:
        return None

    first = int(match.group(1))
    second = int(match.group(2))
    year = int(match.group(3))

    # -------------------------------------------------
    # Determine separator
    # -------------------------------------------------
    separator = "-" if "-" in match.group(0) else "/"

    # -------------------------------------------------
    # Explicit DD-MM-YYYY / DD/MM/YYYY
    # -------------------------------------------------
    #
    # If first > 12, it can only be the day.
    # If second > 12, it can only be the day in MM-DD format.
    # If both <= 12, the input is ambiguous.
    #
    # For this project, source files such as 17-08-2026 are
    # explicitly DD-MM-YYYY / DD/MM/YYYY.
    # -------------------------------------------------

    if first > 12 and second <= 12:
        day = first
        month = second

    elif second > 12 and first <= 12:
        # Unambiguous MM-DD-YYYY / MM/DD/YYYY.
        # Support it only when the source itself makes the order
        # unambiguous.
        month = first
        day = second

    elif first <= 12 and second <= 12:
        # Ambiguous input. Do not guess.
        print(
            f"WARNING: Ambiguous date '{value}'. "
            f"Expected DD-MM-YYYY/DD-MM-YYYY style. "
            f"Value was not converted."
        )
        return None

    else:
        return None

    try:
        return date(year, month, day)
    except ValueError:
        return None


def format_dates(df):
    """
    Apply exactly the same date logic to every column in DATE_COLUMNS.

    Example:

        Source:
            17-08-2026

        Python:
            date(2026, 8, 17)

        PostgreSQL DATE:
            2026-08-17
    """

    if df is None:
        return df

    df = df.copy()

    for col in DATE_COLUMNS:
        if col not in df.columns:
            continue

        original = df[col].copy()

        df[col] = original.apply(parse_source_date)

        invalid_mask = original.notna() & df[col].isna()

        if invalid_mask.any():
            print(
                f"WARNING: {col} has "
                f"{invalid_mask.sum()} date values that could not be parsed."
            )
            print(
                "Unparsed values:",
                original.loc[invalid_mask].head(10).tolist(),
            )

    return df


# =====================================================
# NORMALIZE
# =====================================================

def normalize(df):
    if df is None:
        return df

    df = df.copy()

    for col in df.columns:

        # Date columns must NEVER be converted to strings here.
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
            .replace(
                {
                    "nan": "",
                    "None": "",
                    "<NA>": "",
                    "NaT": "",
                }
            )
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
            .replace(
                {
                    "": None,
                    "nan": None,
                    "None": None,
                    "<NA>": None,
                    "NaT": None,
                }
            )
        )

    return df


# =====================================================
# CLEAN VALUE
# =====================================================

def clean_value(value):
    if pd.isna(value):
        return None

    value = str(value).strip()

    if value.lower() in {
        "",
        "nan",
        "none",
        "<na>",
        "nat",
        "null",
    }:
        return None

    return value


# =====================================================
# APPLY TRANSACTION MAPPING
# =====================================================

def apply_transaction_mapping(raw_df, mapping, source):

    raw_df = clean_columns(raw_df)

    print("=" * 80)
    print(f"Processing {source}")
    print(f"Rows    : {len(raw_df)}")
    print(f"Columns : {len(raw_df.columns)}")
    print("=" * 80)

    debug_cols = [
        c
        for c in ["divper", "guardpanno", "traddate", "postdate"]
        if c in raw_df.columns
    ]

    if debug_cols:
        print("Source date/debug columns:")
        print(raw_df[debug_cols].head(20))
    else:
        print("No date/debug columns found in source.")

    print("=" * 80)

    mapped_df = pd.DataFrame(index=raw_df.index)

    for target_col, source_cols in mapping.items():

        if target_col in ("flag", "created_at", "updated_at"):
            continue

        if target_col == "source":
            mapped_df[target_col] = source
            continue

        mapped_df[target_col] = None

        for src in source_cols:

            src = src.lower().strip()

            if src not in raw_df.columns:
                continue

            source_values = raw_df[src].copy()

            source_values = source_values.replace(
                ["", "nan", "None", "<NA>", "NaT"],
                np.nan,
            )

            mapped_df[target_col] = mapped_df[target_col].fillna(
                source_values
            )

    return mapped_df


# =====================================================
# VALIDATE DATE COLUMNS
# =====================================================

def validate_date_columns(df, stage):
    """
    Validate that every populated DATE_COLUMN contains a Python date.

    This prevents a malformed date/string from silently reaching
    PostgreSQL.
    """

    print("=" * 80)
    print(f"DATE VALIDATION - {stage}")

    errors = []

    for col in DATE_COLUMNS:

        if col not in df.columns:
            continue

        non_null = df[col].notna()

        invalid = df.loc[
            non_null
            & ~df[col].apply(lambda x: isinstance(x, date))
        ]

        if len(invalid) > 0:
            errors.append(
                f"{col}: {len(invalid)} invalid date values"
            )

        print(
            f"{col}: "
            f"non-null={non_null.sum()}, "
            f"null={df[col].isna().sum()}"
        )

        if non_null.any():
            print(df.loc[non_null, col].head(5).tolist())

    if errors:
        print("DATE VALIDATION ERRORS:")
        for error in errors:
            print(" -", error)

        raise ValueError(
            f"Date validation failed during {stage}: "
            + "; ".join(errors)
        )

    print("Date validation passed.")
    print("=" * 80)


# =====================================================
# NORMALIZE VALUE FOR DUPLICATE COMPARISON
# =====================================================

def normalize_compare_value(value, col):
    """
    Convert a value to a deterministic comparison string.

    Date columns are converted ONLY from Python date/datetime/Timestamp
    to YYYY-MM-DD. No pandas date inference is used.
    """

    if pd.isna(value):
        return ""

    if col in DATE_COLUMNS:

        parsed = parse_source_date(value)

        if parsed is None:
            return ""

        return parsed.strftime("%Y-%m-%d")

    if isinstance(value, float) and value.is_integer():
        return str(int(value))

    return str(value).strip()


# =====================================================
# NORMALIZE DATAFRAME FOR DUPLICATE COMPARISON
# =====================================================

def prepare_for_comparison(df, compare_cols):

    result = df[compare_cols].copy()

    for col in compare_cols:
        result[col] = result[col].apply(
            lambda value: normalize_compare_value(value, col)
        )

    return result


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
            "CAMS",
        )

        # Normalize ordinary columns first.
        # Date columns are deliberately left untouched.
        cams_df = normalize(cams_df)

        # Clean identifiers.
        cams_df = clean_identifier_columns(cams_df)

        # Parse ALL date columns using the same parser.
        cams_df = format_dates(cams_df)

        validate_date_columns(
            cams_df,
            "CAMS AFTER MAPPING AND DATE FORMATTING",
        )

        if "postdate" in cams_df.columns:
            print("CAMS POSTDATE:")
            print(cams_df["postdate"].head(20))

        if "traddate" in cams_df.columns:
            print("CAMS TRADDATE:")
            print(cams_df["traddate"].head(20))

        dfs.append(cams_df)

    # =====================================================
    # KFIN
    # =====================================================

    if kfin is not None and not kfin.empty:

        kfin_df = apply_transaction_mapping(
            kfin,
            TRANSACTION_MASTER_MAPPING,
            "KFIN",
        )

        kfin_df = normalize(kfin_df)

        kfin_df = clean_identifier_columns(kfin_df)

        # SAME date parser used for CAMS.
        kfin_df = format_dates(kfin_df)

        validate_date_columns(
            kfin_df,
            "KFIN AFTER MAPPING AND DATE FORMATTING",
        )

        if "postdate" in kfin_df.columns:
            print("KFIN POSTDATE:")
            print(kfin_df["postdate"].head(20))

        if "traddate" in kfin_df.columns:
            print("KFIN TRADDATE:")
            print(kfin_df["traddate"].head(20))

        dfs.append(kfin_df)

    # =====================================================
    # NO FILES
    # =====================================================

    if not dfs:
        print("No Transaction file found.")
        return 0

    # =====================================================
    # MERGE
    # =====================================================

    df = pd.concat(
        dfs,
        ignore_index=True,
    )

    # =====================================================
    # CREATED / UPDATED TIMESTAMP
    # =====================================================

    now = pd.Timestamp.now(tz="Asia/Kolkata")

    df["created_at"] = now
    df["updated_at"] = now

    # =====================================================
    # READ EXISTING BRONZE TABLE
    # =====================================================

    try:

        existing = pd.read_sql(
            "SELECT * FROM bronze.transaction_master_new",
            engine,
        )

        existing = clean_columns(existing)

        existing = normalize(existing)

        existing = clean_identifier_columns(existing)

        # Existing database DATE values are converted using the
        # same deterministic parser.
        existing = format_dates(existing)

        validate_date_columns(
            existing,
            "EXISTING BRONZE DATA",
        )

    except Exception as exc:

        print(
            "Could not read existing bronze.transaction_master_new."
        )
        print("Reason:", exc)

        existing = pd.DataFrame()

    # =====================================================
    # DUPLICATE FLAG
    # =====================================================

    ignore_cols = {
        "flag",
        "created_at",
        "updated_at",
        "source",
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

        if not compare_cols:
            raise ValueError(
                "No columns available for duplicate comparison."
            )

        # Prepare both sides using deterministic normalization.
        new_df = prepare_for_comparison(
            df,
            compare_cols,
        )

        old_df = prepare_for_comparison(
            existing,
            compare_cols,
        )

        # Complete-row comparison.
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

        print("=" * 80)
        print("DUPLICATE CHECK")
        print(f"Rows checked : {len(df)}")
        print(f"Already seen : {(df['flag'] == 1).sum()}")
        print(f"New rows     : {(df['flag'] == 0).sum()}")
        print("=" * 80)

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
        engine,
    )["column_name"].tolist()

    if not db_columns:
        raise ValueError(
            "Could not find bronze.transaction_master_new columns."
        )

    # =====================================================
    # ADD MISSING DATABASE COLUMNS
    # =====================================================

    for col in db_columns:

        if col not in df.columns:
            df[col] = None

    # =====================================================
    # KEEP ONLY DATABASE COLUMNS
    # =====================================================

    df = df[db_columns]

    # =====================================================
    # FINAL DATE VALIDATION
    # =====================================================

    validate_date_columns(
        df,
        "FINAL DATA BEFORE INSERT",
    )

    # =====================================================
    # CLEAN NON-DATE COLUMNS
    # =====================================================

    for col in df.columns:

        if col in DATE_COLUMNS:
            continue

        df[col] = (
            df[col]
            .replace(
                {
                    "": None,
                    "nan": None,
                    "None": None,
                    "<NA>": None,
                    "NaT": None,
                }
            )
        )

    # =====================================================
    # FINAL IDENTIFIER CLEANING
    # =====================================================

    df = clean_identifier_columns(df)

    # =====================================================
    # FINAL COLUMN ORDER CHECK
    # =====================================================

    df = df[db_columns]

    # =====================================================
    # FINAL DATE TYPE SAFETY CHECK
    # =====================================================

    for col in DATE_COLUMNS:

        if col not in df.columns:
            continue

        invalid = df[
            df[col].notna()
            & ~df[col].apply(lambda x: isinstance(x, date))
        ]

        if not invalid.empty:
            raise ValueError(
                f"FINAL SAFETY CHECK FAILED for {col}. "
                f"Found {len(invalid)} non-date values."
            )

    # =====================================================
    # FINAL DATE SAMPLE
    # =====================================================

    print("=" * 80)
    print("FINAL DATE VALUES BEFORE POSTGRES INSERT")

    for col in DATE_COLUMNS:

        if col in df.columns:

            print(f"\n{col}")
            print(df[col].head(10).tolist())

            non_null = df[col].dropna()

            if not non_null.empty:
                print(
                    "Database representation:",
                    non_null.iloc[0].strftime("%Y-%m-%d"),
                )

    print("=" * 80)

    # =====================================================
    # FINAL NULL CLEANING
    # =====================================================

    df = df.where(
        pd.notnull(df),
        None,
    )

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
        chunksize=50000,
    )

    print("=" * 80)
    print("Transaction Master Loaded Successfully")
    print(f"Inserted {len(df)} rows")
    print("=" * 80)

    return len(df)
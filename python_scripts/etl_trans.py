import pandas as pd
import numpy as np
import re

from mapping import TRANSACTION_MASTER_MAPPING
from datetime import date
from utils.db import engine
from utils.dedupe_hash import compute_flag_via_row_hash

# =====================================================
# IMPORT SHARED DATE LOGIC FROM RAW INGESTION
# =====================================================

from raw_ingestion import (
    DATE_COLUMNS,
    parse_source_date,
    format_dates,
)


# =====================================================
# CLEAN COLUMN NAMES
# =====================================================

def clean_column_name(name):
    """
    Normalize a single source column name.

    This must use exactly the same rules as clean_columns()
    so mapping names and dataframe names always match.
    """
    return (
        str(name)
        .strip("'")
        .strip('"')
        .strip()
        .lower()
        .replace(" ", "_")
        .replace("-", "_")
        .replace("/", "_")
        .replace("#", "")
    )


def clean_columns(df):

    if df is None:
        return df

    df = df.copy()

    df.columns = [
        clean_column_name(col)
        for col in df.columns
    ]

    # Keep first occurrence if duplicate column names exist.
    df = df.loc[
        :,
        ~df.columns.duplicated(keep="first")
    ]

    return df


# =====================================================
# IDENTIFIER COLUMNS
# =====================================================

IDENTIFIER_COLUMNS = [
    "folio_no",
    "altfolio",
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
# NORMALIZE
# =====================================================

def normalize(df):

    if df is None:
        return df

    df = df.copy()

    for col in df.columns:

        # Date columns are handled by the shared
        # date parser in raw_ingestion.py.
        if col in DATE_COLUMNS:
            continue

        if pd.api.types.is_datetime64_any_dtype(
            df[col]
        ):
            continue

        df[col] = (
            df[col]
            .fillna("")
            .astype(str)
            .str.replace(
                "'",
                "",
                regex=False
            )
            .str.replace(
                '"',
                "",
                regex=False
            )
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

        def clean_identifier(value):

            if pd.isna(value):
                return None

            value = str(value).strip()

            # Common null values
            if value.lower() in {
                "",
                "nan",
                "none",
                "<na>",
                "nat",
                "null",
            }:
                return None

            # Remove artificial .0 only when the complete
            # value is an integer represented as float.
            #
            # 12345.0  -> 12345
            # 00123.0  -> 00123
            #
            # ABC.0   -> ABC.0
            # 123.50  -> 123.50

            if re.fullmatch(
                r"\d+\.0",
                value
            ):
                value = value[:-2]

            return value

        df[col] = df[col].apply(
            clean_identifier
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
# DETERMINE TRANSACTION MAPPING
# =====================================================

def get_transaction_mapping(
    df,
    source
):
    """
    Select the correct mapping.

    CAMS:
        CAMS CSV + CAMS DBF
            -> CAMS mapping

    KFIN:
        KFIN 201
            -> KFIN_201 mapping

        KFIN 307
            -> KFIN_307 mapping
    """

    if df is None or df.empty:

        raise ValueError(
            f"Cannot determine transaction mapping "
            f"for {source}: source dataframe is empty."
        )

    columns = {
        clean_column_name(col)
        for col in df.columns
    }

    source_upper = (
        str(source)
        .upper()
        .strip()
    )

    # =================================================
    # CAMS
    # =================================================

    if source_upper == "CAMS":

        print(
            "Selected transaction mapping: CAMS"
        )

        return TRANSACTION_MASTER_MAPPING[
            "CAMS"
        ]

    # =================================================
    # KFIN
    # =================================================

    if source_upper == "KFIN":

        # -------------------------------------------------
        # KFIN 201 identifying columns
        # -------------------------------------------------

        kfin_201_required = {
            "td_fund",
            "td_acno",
            "fmcode",
            "funddesc",
            "invname",
            "td_trtype",
            "td_trno",
        }

        # -------------------------------------------------
        # KFIN 307 identifying columns
        # -------------------------------------------------

        kfin_307_required = {
            "product_code",
            "fund",
            "folio_number",
            "fund_description",
            "investor_name",
            "transaction_type",
            "transaction_number",
        }

        kfin_201_score = len(
            kfin_201_required.intersection(
                columns
            )
        )

        kfin_307_score = len(
            kfin_307_required.intersection(
                columns
            )
        )

        print("=" * 80)
        print("KFIN MAPPING DETECTION")
        print(
            f"KFIN 201 score : {kfin_201_score}"
        )
        print(
            f"KFIN 307 score : {kfin_307_score}"
        )
        print("=" * 80)

        # -------------------------------------------------
        # KFIN 201
        # -------------------------------------------------

        if kfin_201_score > kfin_307_score:

            print(
                "Selected transaction mapping: KFIN_201"
            )

            return TRANSACTION_MASTER_MAPPING[
                "KFIN_201"
            ]

        # -------------------------------------------------
        # KFIN 307
        # -------------------------------------------------

        if kfin_307_score > kfin_201_score:

            print(
                "Selected transaction mapping: KFIN_307"
            )

            return TRANSACTION_MASTER_MAPPING[
                "KFIN_307"
            ]

        # -------------------------------------------------
        # Could not identify
        # -------------------------------------------------

        raise ValueError(
            "Unable to determine KFIN transaction format. "
            "The file does not contain enough recognizable "
            "KFIN 201 or KFIN 307 columns."
        )

    # =================================================
    # Unsupported source
    # =================================================

    raise ValueError(
        f"Unsupported transaction source: {source}"
    )


# =====================================================
# APPLY TRANSACTION MAPPING
# =====================================================

def apply_transaction_mapping(
    raw_df,
    mapping,
    source
):

    raw_df = clean_columns(raw_df)

    print("=" * 80)
    print(f"Processing {source}")
    print(f"Rows    : {len(raw_df)}")
    print(f"Columns : {len(raw_df.columns)}")
    print("=" * 80)

    debug_cols = [
        c
        for c in [
            "divper",
            "guardpanno",
            "traddate",
            "postdate",
        ]
        if c in raw_df.columns
    ]

    if debug_cols:

        print(
            "Source date/debug columns:"
        )

        print(
            raw_df[
                debug_cols
            ].head(20)
        )

    else:

        print(
            "No date/debug columns found in source."
        )

    print("=" * 80)

    mapped_df = pd.DataFrame(
        index=raw_df.index
    )

    # =================================================
    # APPLY MAPPING
    # =================================================

    for target_col, source_cols in mapping.items():

        # -------------------------------------------------
        # ETL/system fields are not mapped from source.
        # -------------------------------------------------

        if target_col in {
            "flag",
            "created_at",
            "updated_at",
            "row_hash",
        }:
            continue

        # -------------------------------------------------
        # SOURCE
        # -------------------------------------------------

        if target_col == "source":

            mapped_df[target_col] = source

            continue

        # -------------------------------------------------
        # Default target value
        # -------------------------------------------------

        mapped_df[target_col] = None

        # -------------------------------------------------
        # Source aliases
        # -------------------------------------------------

        for src in source_cols:

            src = clean_column_name(src)

            if src not in raw_df.columns:
                continue

            source_values = (
                raw_df[src].copy()
            )

            source_values = (
                source_values.replace(
                    [
                        "",
                        "nan",
                        "None",
                        "<NA>",
                        "NaT",
                    ],
                    np.nan,
                )
            )

            # Fill target only where it is missing.
            mapped_df[target_col] = (
                mapped_df[target_col].fillna(
                    source_values
                )
            )

    return mapped_df


# =====================================================
# VALIDATE DATE COLUMNS
# =====================================================

def validate_date_columns(
    df,
    stage
):
    """
    Validate that every populated DATE_COLUMNS
    field contains either:

        - Python date
        - YYYY-MM-DD string

    The actual parsing is handled centrally by
    raw_ingestion.py.
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
            & ~df[col].apply(
                lambda x:
                isinstance(x, date)
                or (
                    isinstance(x, str)
                    and bool(
                        re.match(
                            r"^\d{4}-\d{2}-\d{2}$",
                            x
                        )
                    )
                )
            )
        ]

        if len(invalid) > 0:

            errors.append(
                f"{col}: "
                f"{len(invalid)} invalid date values"
            )

        print(
            f"{col}: "
            f"non-null={non_null.sum()}, "
            f"null={df[col].isna().sum()}"
        )

        if non_null.any():

            print(
                df.loc[
                    non_null,
                    col
                ].head(5).tolist()
            )

    if errors:

        print(
            "DATE VALIDATION ERRORS:"
        )

        for error in errors:

            print(
                " -",
                error
            )

        raise ValueError(
            f"Date validation failed during "
            f"{stage}: "
            + "; ".join(errors)
        )

    print(
        "Date validation passed."
    )

    print("=" * 80)


# =====================================================
# NORMALIZE VALUE FOR DUPLICATE COMPARISON
# =====================================================

def normalize_compare_value(
    value,
    col
):
    """
    Convert a value to a deterministic
    comparison string.

    Date columns use the shared parser from
    raw_ingestion.py and are converted to YYYY-MM-DD.
    """

    if pd.isna(value):
        return ""

    # -------------------------------------------------
    # Date
    # -------------------------------------------------

    if col in DATE_COLUMNS:

        parsed = parse_source_date(
            value
        )

        if parsed is None:
            return ""

        return parsed.strftime(
            "%Y-%m-%d"
        )

    # -------------------------------------------------
    # Numeric integer represented as float
    # -------------------------------------------------

    if (
        isinstance(value, float)
        and value.is_integer()
    ):
        return str(
            int(value)
        )

    # -------------------------------------------------
    # Normal value
    # -------------------------------------------------

    return str(value).strip()


# =====================================================
# NORMALIZE DATAFRAME FOR DUPLICATE COMPARISON
# =====================================================

def prepare_for_comparison(
    df,
    compare_cols
):

    result = df[
        compare_cols
    ].copy()

    for col in compare_cols:

        result[col] = result[col].apply(
            lambda value:
            normalize_compare_value(
                value,
                col
            )
        )

    return result


# =====================================================
# PROCESS TRANSACTIONS
# =====================================================

def process_transactions(
    cams=None,
    kfin=None
):

    dfs = []

    # =================================================
    # CAMS
    # =================================================

    if cams is not None and not cams.empty:

        cams_mapping = get_transaction_mapping(
            cams,
            "CAMS"
        )

        cams_df = apply_transaction_mapping(
            cams,
            cams_mapping,
            "CAMS"
        )

        # -------------------------------------------------
        # Normalize ordinary columns.
        # Date columns remain untouched.
        # -------------------------------------------------

        cams_df = normalize(
            cams_df
        )

        # -------------------------------------------------
        # Clean identifiers
        # -------------------------------------------------

        cams_df = clean_identifier_columns(
            cams_df
        )

        # -------------------------------------------------
        # USE CENTRAL DATE PARSER
        # FROM raw_ingestion.py
        # -------------------------------------------------

        cams_df = format_dates(
            cams_df
        )

        # -------------------------------------------------
        # Date validation
        # -------------------------------------------------

        validate_date_columns(
            cams_df,
            "CAMS AFTER MAPPING AND DATE FORMATTING"
        )

        if "postdate" in cams_df.columns:

            print(
                "CAMS POSTDATE:"
            )

            print(
                cams_df[
                    "postdate"
                ].head(20)
            )

        if "traddate" in cams_df.columns:

            print(
                "CAMS TRADDATE:"
            )

            print(
                cams_df[
                    "traddate"
                ].head(20)
            )

        dfs.append(
            cams_df
        )

    # =================================================
    # KFIN
    # =================================================

    if kfin is not None and not kfin.empty:

        kfin_mapping = get_transaction_mapping(
            kfin,
            "KFIN"
        )

        kfin_df = apply_transaction_mapping(
            kfin,
            kfin_mapping,
            "KFIN"
        )

        # -------------------------------------------------
        # Normalize ordinary columns.
        # -------------------------------------------------

        kfin_df = normalize(
            kfin_df
        )

        # -------------------------------------------------
        # Clean identifiers
        # -------------------------------------------------

        kfin_df = clean_identifier_columns(
            kfin_df
        )

        # -------------------------------------------------
        # USE CENTRAL DATE PARSER
        # FROM raw_ingestion.py
        # -------------------------------------------------

        kfin_df = format_dates(
            kfin_df
        )

        # -------------------------------------------------
        # Date validation
        # -------------------------------------------------

        validate_date_columns(
            kfin_df,
            "KFIN AFTER MAPPING AND DATE FORMATTING"
        )

        if "postdate" in kfin_df.columns:

            print(
                "KFIN POSTDATE:"
            )

            print(
                kfin_df[
                    "postdate"
                ].head(20)
            )

        if "traddate" in kfin_df.columns:

            print(
                "KFIN TRADDATE:"
            )

            print(
                kfin_df[
                    "traddate"
                ].head(20)
            )

        dfs.append(
            kfin_df
        )

    # =================================================
    # NO FILES
    # =================================================

    if not dfs:
        print("No Transaction file found.")
        return {"total": 0, "new": 0, "duplicate": 0}

    # =================================================
    # MERGE
    # =================================================

    df = pd.concat(
        dfs,
        ignore_index=True
    )

    # =================================================
    # CREATED / UPDATED TIMESTAMP
    # =================================================

    now = pd.Timestamp.now(
        tz="Asia/Kolkata"
    )

    df["created_at"] = now
    df["updated_at"] = now

    # =================================================
    # GET DATABASE COLUMN ORDER
    # =================================================

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
            "Could not find "
            "bronze.transaction_master_new "
            "columns."
        )

    # =================================================
    # DUPLICATE FLAG
    # HASHED ROW CHECK
    # =================================================

    ignore_cols = {
        "flag",
        "created_at",
        "updated_at",
        "source",
        "row_hash",
    }

    # -------------------------------------------------
    # Use database column order.
    #
    # This MUST remain the same as the hash backfill
    # logic.
    # -------------------------------------------------

    compare_cols = [
        c
        for c in db_columns
        if c not in ignore_cols
    ]

    if not compare_cols:

        raise ValueError(
            "No columns available "
            "for duplicate comparison."
        )

    # -------------------------------------------------
    # Add missing bronze columns to dataframe.
    #
    # They participate in hash as NULL.
    # -------------------------------------------------

    for col in compare_cols:

        if col not in df.columns:

            df[col] = None

    # -------------------------------------------------
    # Prepare comparison dataframe
    # -------------------------------------------------

    new_df = prepare_for_comparison(
        df,
        compare_cols
    )

    # -------------------------------------------------
    # Compute row hash and duplicate flag
    # -------------------------------------------------

    df["row_hash"], df["flag"] = (
        compute_flag_via_row_hash(
            new_df,
            compare_cols,
            "bronze",
            "transaction_master_new",
            engine,
        )
    )

    # Captured HERE, not at the end: `df` is re-sliced to db_columns and
    # cleaned below, and these are the numbers the etl_pipeline runner reports
    # back per file. new + duplicate == total by construction, because
    # compute_flag_via_row_hash only ever writes 0 or 1.
    bronze_total = len(df)
    bronze_new = int((df["flag"] == 0).sum())
    bronze_duplicate = int((df["flag"] == 1).sum())

    print("=" * 80)
    print("DUPLICATE CHECK")

    print(
        f"Rows checked : {len(df)}"
    )

    print(
        f"Already seen : "
        f"{(df['flag'] == 1).sum()}"
    )

    print(
        f"New rows     : "
        f"{(df['flag'] == 0).sum()}"
    )

    print("=" * 80)

    # =================================================
    # ADD MISSING DATABASE COLUMNS
    # =================================================

    for col in db_columns:

        if col not in df.columns:

            df[col] = None

    # =================================================
    # KEEP ONLY DATABASE COLUMNS
    # =================================================

    df = df[
        db_columns
    ]

    # =================================================
    # FINAL DATE VALIDATION
    # =================================================

    validate_date_columns(
        df,
        "FINAL DATA BEFORE INSERT"
    )

    # =================================================
    # CLEAN NON-DATE COLUMNS
    # =================================================

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

    # =================================================
    # FINAL IDENTIFIER CLEANING
    # =================================================

    df = clean_identifier_columns(
        df
    )

    # =================================================
    # FINAL COLUMN ORDER CHECK
    # =================================================

    df = df[
        db_columns
    ]

    # =================================================
    # FINAL DATE TYPE SAFETY CHECK
    # =================================================

    for col in DATE_COLUMNS:

        if col not in df.columns:
            continue

        invalid = df[
            df[col].notna()
            & ~df[col].apply(
                lambda x:
                isinstance(x, date)
                or (
                    isinstance(x, str)
                    and bool(
                        re.match(
                            r"^\d{4}-\d{2}-\d{2}$",
                            x
                        )
                    )
                )
            )
        ]

        if not invalid.empty:

            raise ValueError(
                f"FINAL SAFETY CHECK FAILED "
                f"for {col}. "
                f"Found {len(invalid)} "
                f"non-date values."
            )

    # =================================================
    # FINAL DATE SAMPLE
    # =================================================

    print("=" * 80)
    print(
        "FINAL DATE VALUES "
        "BEFORE POSTGRES INSERT"
    )

    for col in DATE_COLUMNS:

        if col not in df.columns:
            continue

        print(
            f"\n{col}"
        )

        print(
            df[col].head(10).tolist()
        )

        non_null = df[
            col
        ].dropna()

        if not non_null.empty:

            sample_val = non_null.iloc[0]

            print(
                "Database representation:",
                (
                    sample_val.strftime("%Y-%m-%d")
                    if hasattr(
                        sample_val,
                        "strftime"
                    )
                    else str(sample_val)
                )
            )

    print("=" * 80)

    # =================================================
    # FINAL NULL CLEANING
    # =================================================

    df = df.where(
        pd.notnull(df),
        None
    )

    # =================================================
    # INSERT INTO POSTGRES
    # =================================================

    print("=" * 80)
    print(
        "Loading Transaction Master..."
    )

    print(
        f"Rows to insert : {len(df)}"
    )

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

    # =================================================
    # SUCCESS
    # =================================================

    print("=" * 80)
    print(
        "Transaction Master "
        "Loaded Successfully"
    )

    print(
        f"Inserted {len(df)} rows"
    )

    print("=" * 80)

    return {
        "total": bronze_total,
        "new": bronze_new,
        "duplicate": bronze_duplicate,
    }
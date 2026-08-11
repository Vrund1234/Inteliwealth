import pandas as pd
from mappings.column_mappings import SIP_MASTER_MAPPING
from utils.db import engine

from bronze.bronze_helpers import (
    clean_columns as _clean_columns,
    normalize as _normalize,
    clean_identifier_columns as _clean_identifier_columns,
    format_dates as _format_dates,
    clean_value
)


# =====================================================
# DATE COLUMNS
# =====================================================

DATE_COLUMNS = [
    "from_date",
    "to_date",
    "cease_date",
    "reg_date",
    "pause_from_date",
    "pause_to_date"
]

# =====================================================
# IDENTIFIER COLUMNS
# (.0 SHOULD NEVER APPEAR)
# =====================================================

IDENTIFIER_COLUMNS = [
    "folio_no",
    "folio_old",
    "scheme_folio_number",
    "instrm_no",
    "cheq_micr_no",
    "request_ref_no",
    "ft_sip_regno",
    "pan"
]


# =====================================================
# INTEGER / NUMERIC COLUMNS
# =====================================================

NUMERIC_COLUMNS = [
    "auto_amount",
    "no_of_installments",
    "top_up_amt",
    "top_up_perc",
    "flag"
]

# =====================================================
# PERIODICITY NORMALIZATION
# =====================================================

PERIODICITY_MAPPING = {
    "OM": "MONTHLY",
    "OW": "WEEKLY",
    "SM": "SEMI_MONTHLY",
    "TM": "BI_MONTHLY",
    "Q": "QUARTERLY",
    "O": "ONE_TIME",
}


# =====================================================
# SHARED HELPERS BOUND TO THIS DOMAIN
# =====================================================
# Thin wrappers over bronze.bronze_helpers that pin this domain's column
# lists and this file's original behaviour, which differs from the other two
# Bronze domains in three ways:
#   - empty-DataFrame guard enabled  (original: `if df is None or df.empty`)
#   - NUMERIC_COLUMNS coerced via pd.to_numeric inside normalize()
#   - duplicate-column dedup enabled; whitespace stripped before quotes
# `clean_value` is imported straight from bronze_helpers (identical original).

def clean_columns(df):
    return _clean_columns(
        df,
        guard_empty=True,
        dedupe_columns=True
    )


def normalize(df):
    return _normalize(
        df,
        DATE_COLUMNS,
        numeric_columns=NUMERIC_COLUMNS,
        guard_empty=True
    )


def clean_identifier_columns(df):
    return _clean_identifier_columns(
        df,
        IDENTIFIER_COLUMNS,
        guard_empty=True
    )


# =====================================================
# FORMAT DATE COLUMNS
# CAMS  -> MM/DD/YYYY HH:MM AM/PM
# KFIN  -> DD/MM/YYYY
# =====================================================
# The per-source format choice is domain knowledge about how each RTA writes
# its files, so it stays here; only the parsing loop is shared.

def format_dates(df, source=None):

    source = str(source).upper()

    if source == "CAMS":
        # CAMS : MM/DD/YYYY HH:MM AM/PM
        date_format = "%m/%d/%Y %I:%M %p"
    else:
        # KFIN : DD/MM/YYYY
        date_format = "%d/%m/%Y"

    return _format_dates(
        df,
        DATE_COLUMNS,
        date_format=date_format,
        guard_empty=True
    )


# =====================================================
# APPLY SIP MAPPING
# =====================================================

def apply_sip_mapping(raw_df, mapping, source):

    if raw_df is None or raw_df.empty:
        return pd.DataFrame()

    # =====================================================
    # CLEAN SOURCE COLUMNS
    # =====================================================

    raw_df = raw_df.copy()

    raw_df.columns = (
        raw_df.columns.astype(str)
        .str.strip()
        .str.strip("'")
        .str.strip('"')
    )
    print(raw_df.columns.tolist())

    if "PERIOD_DAY" in raw_df.columns:
        print("\n========== PERIOD_DAY BEFORE MAPPING ==========")
        print(raw_df["PERIOD_DAY"].head(50).tolist())
        print("==============================================\n")
    print("=" * 80)
    print("Applying SIP Mapping")
    print(f"Rows    : {len(raw_df)}")
    print(f"Columns : {len(raw_df.columns)}")
    print("=" * 80)

    # =====================================================
    # TARGET DATAFRAME
    # =====================================================

    mapped_df = pd.DataFrame(index=raw_df.index)

    # =====================================================
    # APPLY COLUMN MAPPING
    # =====================================================

    for target_col, source_cols in mapping.items():

        if target_col in (
            "flag",
            "created_at",
            "updated_at"
        ):
            continue

        # =====================================================
        # SOURCE-SPECIFIC MAPPING
        # =====================================================

        if target_col == "scheme_code":
            source_cols = ["Scheme"] if source == "KFIN" else ["SCHEME_CODE"]

        elif target_col == "scheme_name":
            source_cols = ["Scheme Name"] if source == "KFIN" else ["SCHEME"]

        mapped_series = None

        for src_col in source_cols:

            possible_names = [
                src_col.strip(),
                src_col.strip().replace(" ", "_"),
            ]

            for col in possible_names:
                if col in raw_df.columns:
                    mapped_series = raw_df[col]
                    break

            if mapped_series is not None:
                break

        if mapped_series is None:

            mapped_series = pd.Series(
                [None] * len(raw_df),
                index=raw_df.index,
                dtype="object"
            )

        mapped_df[target_col] = mapped_series

    # =====================================================
    # NORMALIZATION
    # =====================================================

    mapped_df = normalize(mapped_df)

    mapped_df = clean_identifier_columns(mapped_df)
    print("\n========== PERIOD_DAY AFTER MAPPING ==========")
    print(mapped_df["period_day"].head(50).tolist())
    print("=============================================\n")

    if "periodicity" in mapped_df.columns:

        mapped_df["periodicity"] = (
            mapped_df["periodicity"]
            .fillna("")
            .astype(str)
            .str.strip()
            .str.upper()
        )

        mapped_df["periodicity"] = (
            mapped_df["periodicity"]
            .replace(PERIODICITY_MAPPING)
        )
    mapped_df = mapped_df.where(
        pd.notnull(mapped_df),
        None
    )

    print("Mapped Columns :", len(mapped_df.columns))
    print(mapped_df.head())

    return mapped_df


# =====================================================
# PROCESS SIP
# =====================================================

def process_sip(
    cams=None,
    kfin=None,
    cams_source="CAMS",
    kfin_source="KFIN"
):

    dfs = []

    # =====================================================
    # CAMS FILE
    # =====================================================

    if cams is not None and not cams.empty:

        print("\nProcessing CAMS SIP File...")

        cams_df = apply_sip_mapping(
            cams,
            SIP_MASTER_MAPPING,
            cams_source
        )

        cams_df = format_dates(
            cams_df,
            cams_source
        )

        cams_df["source"] = "CAMS"

        dfs.append(cams_df)

        print(f"CAMS Rows : {len(cams_df)}")

    # =====================================================
    # KFIN FILE
    # =====================================================

    if kfin is not None and not kfin.empty:

        print("\nProcessing KFIN SIP File...")
        print("Before mapping:")
        print(kfin.columns.tolist())

        kfin_df = apply_sip_mapping(
            kfin,
            SIP_MASTER_MAPPING,
            kfin_source
        )

        kfin_df = format_dates(
            kfin_df,
            kfin_source
        )

        kfin_df["source"] = "KFIN"

        dfs.append(kfin_df)

        print(f"KFIN Rows : {len(kfin_df)}")
        print("After mapping:")
        #print(kfin_df[["scheme_code", "product_code"]].head())
        print(kfin_df.head(10))
    # =====================================================
    # NO FILE FOUND
    # =====================================================

    if not dfs:

        print("No SIP file found.")
        return 0

    # =====================================================
    # MERGE CAMS + KFIN
    # =====================================================

    df = pd.concat(
        dfs,
        ignore_index=True
    )

    print("=" * 80)
    print("Merged SIP Data")
    print(f"Total Rows : {len(df)}")
    print("=" * 80)

    # =====================================================
    # REMOVE DUPLICATES INSIDE CURRENT LOAD
    # =====================================================

    before = len(df)

    df = (
        df
        .drop_duplicates()
        .reset_index(drop=True)
    )

    print(f"Duplicate Rows Removed : {before - len(df)}")

    # =====================================================
    # AUDIT COLUMNS
    # =====================================================

    now = pd.Timestamp.now(tz="Asia/Kolkata")

    df["created_at"] = now
    df["updated_at"] = now

    # =====================================================
    # READ EXISTING BRONZE TABLE
    # =====================================================

    try:

        existing = pd.read_sql(
            "SELECT * FROM bronze.sip_master_new",
            engine
        )

        if not existing.empty:

            existing = normalize(existing)

            existing = clean_identifier_columns(existing)
            # existing = format_dates(existing)

        print(f"Existing Bronze Rows : {len(existing)}")

    except Exception:

        existing = pd.DataFrame()

        print("Bronze table not found. Initial Load.")

    # =====================================================
    # DUPLICATE FLAG
    # =====================================================

    # =====================================================
    # DUPLICATE FLAG
    # COMPARE ALL COLUMNS EXCEPT ignore_cols
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

        existing = clean_columns(existing)
        existing = normalize(existing)
        existing = clean_identifier_columns(existing)

        # -------------------------------------------------
        # TAKE COMMON COLUMNS EXCEPT IGNORE COLUMNS
        # -------------------------------------------------

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


        # -------------------------------------------------
        # NORMALIZE BOTH DATASETS SAME WAY
        # -------------------------------------------------

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
                    .str.upper()
                )

                old_df[col] = (
                    old_df[col]
                    .fillna("")
                    .astype(str)
                    .str.strip()
                    .str.upper()
                )


        # -------------------------------------------------
        # CREATE COMPLETE ROW KEY
        # -------------------------------------------------

        new_keys = new_df.astype(str).agg("|".join, axis=1)

        old_keys = set(
            old_df.astype(str).agg("|".join, axis=1)
        )


        # -------------------------------------------------
        # FLAG
        # -------------------------------------------------

        df["flag"] = (
            new_keys.isin(old_keys)
            .astype(int)
        )


        print("=" * 80)
        print("Duplicate Check Result")
        print(df["flag"].value_counts(dropna=False))
        print("=" * 80)

    # =====================================================
    # INSERT INTO BRONZE
    # =====================================================

    # =====================================================
    # MATCH DATABASE COLUMN ORDER
    # =====================================================

    db_columns = pd.read_sql(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema='bronze'
        AND table_name='sip_master_new'
        ORDER BY ordinal_position
        """,
        engine
    )["column_name"].tolist()


    for col in db_columns:

        if col not in df.columns:
            df[col] = None


    df = df[db_columns]


    # =====================================================
    # FINAL DATABASE TYPE CLEANING
    # =====================================================

    for col in NUMERIC_COLUMNS:

        if col in df.columns:

            df[col] = pd.to_numeric(
                df[col],
                errors="coerce"
            )


    # remove empty strings before insert
    for col in df.columns:

        if col not in NUMERIC_COLUMNS:

            df[col] = (
                df[col]
                .replace({
                    "": None,
                    "nan": None,
                    "None": None,
                    "<NA>": None
                })
            )


    df = df.where(
        pd.notnull(df),
        None
    )

    df.to_sql(
        "sip_master_new",
        engine,
        schema="bronze",

        if_exists="append",
        index=False,
        method="multi",
        chunksize=5000
    )

    print("=" * 80)
    print("SIP Master Loaded Successfully")
    print(f"Inserted {len(df)} rows")
    print("=" * 80)

    return len(df)

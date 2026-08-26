import pandas as pd
# from sqlalchemy import create_engine
#import mapping
from utils.db import engine
from mapping import SIP_MASTER_MAPPING
from utils.db import engine
from etl_trans import parse_source_date

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
# CLEAN COLUMN NAMES
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

    # Keep first duplicate column if any
    df = df.loc[:, ~df.columns.duplicated(keep="first")]

    return df


# =====================================================
# NORMALIZE DATA
# =====================================================

def normalize(df):

    if df is None or df.empty:
        return df

    df = df.copy()

    for col in df.columns:

        # skip dates
        if col in DATE_COLUMNS:
            continue


        # handle numeric columns
        if col in NUMERIC_COLUMNS:

            df[col] = (
                pd.to_numeric(
                    df[col],
                    errors="coerce"
                )
            )

            continue


        # datetime skip
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
# CLEAN SINGLE VALUE
# =====================================================

def clean_value(value):

    if pd.isna(value):
        return None

    value = str(value).strip()

    if value.lower() in (
        "",
        "nan",
        "none",
        "<na>",
        "nat"
    ):
        return None

    return value

# =====================================================
# FORMAT DATE COLUMNS
# COMMON FORMAT FOR CAMS + KFIN
# OUTPUT: YYYY-MM-DD
# =====================================================

def format_dates(df, source=None):
    """
    Parse DATE_COLUMNS with the same deterministic, day-first parser
    etl_trans.py already uses for transaction dates (parse_source_date):
    commits to DD-MM-YYYY only when unambiguous (first component > 12),
    and returns None rather than guessing when a value could be read either
    way -- e.g. "05-09-2017" is refused, not silently read as MM-DD-YYYY.

    Previously this used bare pd.to_datetime(df[col], errors="coerce"),
    which has no such rule: pandas' US-style default (no dayfirst) silently
    resolves an ambiguous DD-MM-YYYY value as MM-DD-YYYY. Confirmed live:
    KFIN regno 232086 (folio 7776062333) is stored in bronze with
    reg_date=2017-05-09, but re-parsing the identical source file with the
    old code produced 2017-09-05 for the same row -- day and month swapped.
    """

    if df is None or df.empty:
        return df

    df = df.copy()

    for col in DATE_COLUMNS:

        if col not in df.columns:
            continue

        df[col] = df[col].apply(parse_source_date).apply(
            lambda d: d.strftime("%Y-%m-%d") if d is not None else None
        )

    return df


def dedupe_compare_date(value):
    """Same deterministic day-first parsing as format_dates(), but blank or
    unparseable values become "" rather than None -- process_sip()'s
    duplicate-flag check joins every compared column straight into one
    string key (`.astype(str).agg("|".join, ...)`), where "" is the
    established "no value" representation (matching non-date columns in
    that same comparison)."""

    parsed = parse_source_date(value)
    return parsed.strftime("%Y-%m-%d") if parsed is not None else ""


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
            source_cols = ["Scheme"] if source == "KFIN" else ["SCHEME_CODE", "SCHEME_COD"]

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
    #print(mapped_df["period_day"].head(50).tolist())
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
        #print(kfin_df.head(10))
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

                new_df[col] = new_df[col].apply(dedupe_compare_date)

                old_df[col] = old_df[col].apply(dedupe_compare_date)

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

    # # =====================================================
    # # GET DATABASE COLUMN ORDER
    # # =====================================================

    # db_columns = pd.read_sql(
    #     """
    #     SELECT column_name
    #     FROM information_schema.columns
    #     WHERE table_schema = 'bronze'
    #     AND table_name = 'sip_master_new'
    #     ORDER BY ordinal_position
    #     """,
    #     engine
    # )["column_name"].tolist()

    # # =====================================================
    # # ADD MISSING COLUMNS
    # # =====================================================

    # for col in db_columns:

    #     if col not in df.columns:

    #         df[col] = None

    # # =====================================================
    # # KEEP ONLY DATABASE COLUMNS
    # # =====================================================

    # df = df[db_columns]

    # # =====================================================
    # # FINAL DATE CLEANING
    # # =====================================================

    # for col in DATE_COLUMNS:

    #     if col in df.columns:

    #         df[col] = (
    #             pd.to_datetime(
    #                 df[col],
    #                 errors="coerce"
    #             )
    #             .dt.date
    #         )

    #         df[col] = df[col].where(
    #             pd.notnull(df[col]),
    #             None
    #         )

    # # =====================================================
    # # CLEAN NON-DATE COLUMNS
    # # =====================================================

    # for col in df.columns:

    #     if col not in DATE_COLUMNS:

    #         df[col] = (
    #             df[col]
    #             .replace({
    #                 "": None,
    #                 "nan": None,
    #                 "None": None,
    #                 "<NA>": None,
    #                 "NaT": None
    #             })
    #         )

    # # =====================================================
    # # FINAL CLEAN IDENTIFIER COLUMNS
    # # =====================================================

    # df = clean_identifier_columns(df)

    # # =====================================================
    # # DEBUG DATE COLUMNS
    # # =====================================================

    # print("=" * 80)
    # print("Checking DATE columns before insert")

    # for col in DATE_COLUMNS:

    #     if col in df.columns:

    #         print(f"{col} : {df[col].dtype}")

    #         bad = df[df[col].astype(str) == ""]

    #         if len(bad):

    #             print(f"{col} has {len(bad)} empty strings")

    #         print(df[col].head())

    # print("=" * 80)

    # # =====================================================
    # # REPLACE REMAINING NaN
    # # =====================================================

    # df = df.where(
    #     pd.notnull(df),
    #     None
    # )

    # # =====================================================
    # # FINAL SAFETY CHECK FOR DATE COLUMNS
    # # =====================================================

    # for col in DATE_COLUMNS:

    #     if col in df.columns:

    #         df.loc[
    #             df[col].astype(str).isin(
    #                 ["", "NaT", "nan", "None"]
    #             ),
    #             col
    #         ] = None

    # # =====================================================
    # # FINAL CLEAN IDENTIFIER COLUMNS
    # # =====================================================

    # df = clean_identifier_columns(df)

    # # =====================================================
    # # FINAL COLUMN ORDER CHECK
    # # =====================================================

    # df = df[db_columns]

    # # =====================================================
    # # INSERT INTO POSTGRES
    # # =====================================================

    # print("=" * 80)
    # print("Loading SIP Master...")
    # print(f"Rows to insert : {len(df)}")
    # print("=" * 80)

    # df.to_sql(
    #     "sip_master_new",
    #     engine,
    #     schema="bronze",
    #     if_exists="append",
    #     index=False,
    #     method="multi",
    #     chunksize=5000
    # )

    # print("=" * 80)
    # print("SIP Master Loaded Successfully")
    # print(f"Inserted {len(df)} rows")
    # print("=" * 80)

    # return df
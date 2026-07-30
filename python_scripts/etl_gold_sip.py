import pandas as pd
import traceback
from utils.db import engine


# =====================================================
# SAFE READ
# =====================================================

def safe_read(query):

    try:

        return pd.read_sql(
            query,
            engine
        )

    except Exception as e:

        print("SQL ERROR :", e)

        return pd.DataFrame()


# =====================================================
# GET LAST PROCESSED TIME FROM GOLD
# =====================================================

def get_last_processed_time():

    try:

        result = pd.read_sql(
            """
            SELECT
                MAX(created_at) AS last_time
            FROM gold.sip
            """,
            engine
        )

        last_time = result.iloc[0]["last_time"]

        if pd.isna(last_time):

            return pd.Timestamp("1900-01-01")

        return pd.to_datetime(last_time)

    except Exception:

        return pd.Timestamp("1900-01-01")


# =====================================================
# NORMALIZE DATA
# =====================================================

def normalize_for_compare(df):

    df = df.copy()

    df = df.drop(
        columns=[
            "created_at",
            "updated_at"
        ],
        errors="ignore"
    )

    for col in df.columns:

        if pd.api.types.is_datetime64_any_dtype(df[col]):

            df[col] = (
                pd.to_datetime(
                    df[col],
                    errors="coerce"
                )
                .dt.strftime("%Y-%m-%d")
            )

        else:

            df[col] = (
                df[col]
                .astype("string")
                .str.strip()
            )

    return df


# =====================================================
# CREATE ROW KEY
# =====================================================

def create_row_key(df):

    df = normalize_for_compare(df)

    return (
        df.fillna("")
        .astype(str)
        .agg("|".join, axis=1)
    )


# =====================================================
# GET GOLD TABLE COLUMNS
# =====================================================

def get_table_columns():

    query = """

    SELECT
        column_name

    FROM information_schema.columns

    WHERE table_schema='gold'

    AND table_name='sip'

    ORDER BY ordinal_position

    """

    return pd.read_sql(
        query,
        engine
    )["column_name"].tolist()


# =====================================================
# EXTRACT GOLD SIP SOURCE
# =====================================================

def extract_sip():

    print("=" * 80)
    print("Extracting Gold SIP")
    print("=" * 80)

    last_time = get_last_processed_time()

    df = safe_read(
        """
        SELECT *
        FROM silver.sip_master_new
        """
    )

    if df.empty:

        print("No data found in Silver.")

        return df

    df["created_at"] = pd.to_datetime(
        df["created_at"],
        errors="coerce"
    )

    last_time = pd.Timestamp(last_time)

    if getattr(df["created_at"].dt, "tz", None) is not None:

        df["created_at"] = (
            df["created_at"]
            .dt.tz_localize(None)
        )

    if last_time.tzinfo is not None:

        last_time = last_time.tz_localize(None)

    df = df[
        df["created_at"] > last_time
    ]

    print()

    print(
        f"Rows fetched : {len(df)}"
    )

    print(
        f"Columns : {len(df.columns)}"
    )

    return df

# =====================================================
# TRANSFORM GOLD SIP
# =====================================================

def transform_sip(df):

    print("=" * 80)
    print("Transforming Gold SIP")
    print("=" * 80)

    gold_df = pd.DataFrame()

    # =================================================
    # RTA
    # =================================================

    gold_df["rta"] = (
        df["source"]
        .astype("string")
        .str.strip()
        .str.upper()
        .str[:10]
    )

    # =================================================
    # SIP REGISTRATION NUMBER
    # =================================================

    gold_df["sip_reg_no"] = (
        df["ft_sip_regno"]
        .combine_first(df["request_ref_no"])
        .astype("string")
        .str.strip()
        .str.replace(".0", "", regex=False)
        .replace({
            "": pd.NA,
            "nan": pd.NA,
            "<NA>": pd.NA
        })
        .str[:50]
    )

    # =================================================
    # FOLIO NUMBER
    # =================================================

    gold_df["folio_number"] = (
        df["folio_no"]
        .astype("string")
        .str.strip()
        .str.replace(".0", "", regex=False)
        .replace({
            "": pd.NA
        })
        .str[:40]
    )

    # =================================================
    # SCHEME CODE
    # =================================================

    gold_df["scheme_code"] = (
        df["scheme_code"]
        .astype("string")
        .str.strip()
        .str[:30]
    )

    # =================================================
    # SCHEME NAME
    # =================================================

    gold_df["scheme_name"] = (
        df["scheme_name"]
        .astype("string")
        .str.strip()
        .str[:255]
    )

    # =================================================
    # AMC CODE
    # =================================================

    gold_df["amc_code"] = (
        df["amc_code"]
        .astype("string")
        .str.strip()
        .str[:20]
    )

    # =================================================
    # ISIN
    # =================================================

    gold_df["isin"] = None

    # =================================================
    # AMOUNT
    # =================================================

    gold_df["amount"] = pd.to_numeric(
        df["auto_amount"],
        errors="coerce"
    )

    # =================================================
    # FREQUENCY
    # =================================================

    gold_df["frequency"] = (
        df["periodicity"]
        .astype("string")
        .str.strip()
        .str.upper()
        .str[:20]
    )

    # =================================================
    # START DATE
    # =================================================

    gold_df["start_date"] = (
        pd.to_datetime(
            df["from_date"],
            errors="coerce"
        )
        .dt.date
    )

    # =================================================
    # END DATE
    # =================================================

    gold_df["end_date"] = (
        pd.to_datetime(
            df["to_date"],
            errors="coerce"
        )
        .dt.date
    )

    # =================================================
    # NEXT DUE DATE
    # =================================================

    gold_df["next_due_date"] = None

    # =================================================
    # SIP DAY
    # =================================================

    gold_df["sip_day"] = pd.to_numeric(
        df["period_day"],
        errors="coerce"
    )

    # =================================================
    # MANDATE ID
    # =================================================

    gold_df["mandate_id"] = (
        df["umrn_code"]
        .astype("string")
        .str.strip()
        .str[:50]
    )

    # =================================================
    # STATUS
    # =================================================

    gold_df["status"] = (
        df["status"]
        .astype("string")
        .str.strip()
        .str.upper()
        .str[:20]
    )

    # =================================================
    # REGISTERED DATE
    # =================================================

    gold_df["registered_date"] = (
        pd.to_datetime(
            df["reg_date"],
            errors="coerce"
        )
        .dt.date
    )

    # =================================================
    # CEASED DATE
    # =================================================

    gold_df["ceased_date"] = (
        pd.to_datetime(
            df["cease_date"],
            errors="coerce"
        )
        .dt.date
    )

    # =================================================
    # APPLICATION MANAGED COLUMNS
    # =================================================

    gold_df["scheme_id"] = None
    gold_df["amc_id"] = None
    gold_df["client_id"] = None

    gold_df["sip_type"] = None

    gold_df["registered_installments"] = None
    gold_df["completed_installments"] = None
    gold_df["bounced_installments"] = None

    gold_df["ceased_reason"] = None
    gold_df["arn_id"] = None

    # =================================================
    # KEEP SILVER TIMESTAMP
    # =================================================

    gold_df["created_at"] = df["created_at"]

    gold_df["updated_at"] = None

    # =================================================
    # COLUMN ORDER
    # =================================================

    gold_df = gold_df[[
        "rta",
        "sip_reg_no",
        "folio_number",
        "scheme_code",
        "scheme_name",
        "amc_code",
        "isin",
        "amount",
        "frequency",
        "start_date",
        "end_date",
        "next_due_date",
        "sip_day",
        "mandate_id",
        "status",
        "registered_date",
        "ceased_date",
        "scheme_id",
        "amc_id",
        "client_id",
        "sip_type",
        "registered_installments",
        "completed_installments",
        "bounced_installments",
        "ceased_reason",
        "arn_id",
        "created_at",
        "updated_at"
    ]]

    print("=" * 80)
    print("Gold SIP Preview")
    print("=" * 80)

    print(gold_df.head())

    print()

    print(
        f"Rows Ready : {len(gold_df)}"
    )

    return gold_df

# =====================================================
# DUPLICATE CHECK
# Gold SIP Dedup Key:
# rta + sip_reg_no
# =====================================================

# def check_duplicates(gold_df):

    print("=" * 80)
    print("Checking duplicate SIP records")
    print("=" * 80)


    duplicates = (
        gold_df[gold_df["sip_reg_no"].notna()]
        .groupby(["rta","sip_reg_no"])
        .size()
        .reset_index(name="count")
        .query("count > 1")
    )


    print(
        "Duplicate RTA + SIP Registration records:",
        len(duplicates)
    )


    if len(duplicates) > 0:

        print()

        print(
            duplicates.head(20)
        )


    return duplicates


# =====================================================
# LOAD GOLD SIP
# =====================================================

def load_sip(gold_df):

    print("=" * 80)
    print("Loading Gold SIP")
    print("=" * 80)

    if gold_df.empty:

        print("No new records.")

        return True

    # -------------------------------------------------
    # LOAD EXISTING GOLD DATA
    # -------------------------------------------------

    try:

        existing = pd.read_sql(
            """
            SELECT *
            FROM gold.sip
            """,
            engine
        )

    except Exception:

        existing = pd.DataFrame()

    # -------------------------------------------------
    # REMOVE DUPLICATES
    # -------------------------------------------------

    if not existing.empty:

        old_keys = set(
            create_row_key(existing)
        )

        new_keys = create_row_key(gold_df)

        gold_df = gold_df.loc[
            ~new_keys.isin(old_keys)
        ]

    if gold_df.empty:

        print("Duplicate data skipped.")

        return True

    # -------------------------------------------------
    # GOLD AUDIT TIMESTAMP
    # -------------------------------------------------

    load_time = pd.Timestamp.now()

    gold_df["created_at"] = load_time
    gold_df["updated_at"] = load_time

    # -------------------------------------------------
    # MATCH DATABASE COLUMNS
    # -------------------------------------------------

    db_cols = get_table_columns()

    for col in db_cols:

        if col not in gold_df.columns:

            gold_df[col] = None

    gold_df = gold_df[db_cols]

    # -------------------------------------------------
    # VALIDATE VARCHAR LENGTHS
    # -------------------------------------------------

    varchar_limits = {

        "rta": 10,
        "sip_reg_no": 50,
        "folio_number": 40,
        "scheme_code": 30,
        "scheme_name": 255,
        "amc_code": 20,
        "isin": 20,
        "frequency": 20,
        "mandate_id": 50,
        "status": 20,
        "sip_type": 20,
        "ceased_reason": 100

    }

    for col, limit in varchar_limits.items():

        if col in gold_df.columns:

            max_length = (
                gold_df[col]
                .fillna("")
                .astype(str)
                .str.len()
                .max()
            )

            print(f"{col:<25} Max Length : {max_length}")

            if max_length > limit:

                raise Exception(
                    f"{col} length {max_length} exceeds limit {limit}"
                )

    # -------------------------------------------------
    # INSERT INTO GOLD
    # -------------------------------------------------

    try:

        gold_df.to_sql(
            "sip",
            engine,
            schema="gold",
            if_exists="append",
            index=False,
            method="multi",
            chunksize=5000
        )

        print()
        print(f"{len(gold_df)} rows inserted into Gold.")

        return True

    except Exception as e:

        print("\nFAILED LOADING GOLD SIP\n")

        traceback.print_exc(limit=5)

        if hasattr(e, "orig"):

            print(e.orig)

        return False

# =====================================================
# MAIN EXECUTION
# =====================================================

if __name__ == "__main__":


    print()
    print("=" * 80)
    print("STARTING GOLD SIP ETL")
    print("=" * 80)



    # -------------------------------------------------
    # EXTRACT
    # -------------------------------------------------

    df = extract_sip()



    # -------------------------------------------------
    # TRANSFORM
    # -------------------------------------------------

    gold_df = transform_sip(df)



    # -------------------------------------------------
    # DUPLICATE CHECK
    # -------------------------------------------------

 #   duplicates = check_duplicates(
 #       gold_df
  #  )



    # -------------------------------------------------
    # LOAD
    # -------------------------------------------------

    status = load_sip(
        gold_df
    )



    # -------------------------------------------------
    # FINAL STATUS
    # -------------------------------------------------

    if status:


        print()

        print("=" * 80)

        print(
            "GOLD SIP ETL COMPLETED SUCCESSFULLY"
        )

        print("=" * 80)



    else:


        print()

        print(
            "GOLD SIP ETL FAILED"
        )
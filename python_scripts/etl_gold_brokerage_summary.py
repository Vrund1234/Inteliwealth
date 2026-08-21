# =====================================================
# GOLD : BROKERAGE SUMMARY BY SCHEME
#
# silver.brokerage_summary → gold.brokerage_summary
#
# Source reports : CAMS WBR36, CAMS WBR36H, and any RTA
# brokerage report added later through
# mapping_wbr.BROKERAGE_FILE_PATTERNS.
#
# Natural key : rta | report_type | scheme_code
#               | report_from_date | report_to_date
#
# `id` is a deterministic uuid5 over that key, so the same
# report uploaded twice updates one row instead of adding
# a second copy - unlike gold.transactions, which has no
# duplicate guard.
# =====================================================

import uuid
import traceback

import pandas as pd

from sqlalchemy import text

from utils.db import engine

from mapping_wbr import BROKERAGE_AMOUNT_COLUMNS


# =====================================================
# FIXED BROKERAGE UUID NAMESPACE
# NEVER CHANGE THIS VALUE
# =====================================================

BROKERAGE_NAMESPACE = uuid.UUID(
    "6f2b41c7-9d34-4a58-8b17-2c5e0d9a7f31"
)


GOLD_COLUMNS = [

    "id",
    "rta",
    "report_type",

    "scheme_code",
    "scheme_name",

    "scheme_id",
    "amc_id",
    "amc_code",

    "arn",
    "sub_arn",

    "upfront",
    "afe",
    "trailer_fee",
    "trxn_charges",
    "clawback",
    "incentives",
    "total_brokerage",

    "report_from_date",
    "report_to_date",
    "rep_date",

    "created_at",
    "updated_at"

]


# Every column a re-uploaded report is allowed to correct.
UPDATE_COLUMNS = [

    "scheme_name",
    "scheme_id",
    "amc_id",
    "amc_code",
    "arn",
    "sub_arn",

    "upfront",
    "afe",
    "trailer_fee",
    "trxn_charges",
    "clawback",
    "incentives",
    "total_brokerage",

    "rep_date",
    "updated_at"

]


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
# COMMON CLEANING
# =====================================================

def clean_text(series):

    return (
        series
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()
        .replace({"": None})
    )


# =====================================================
# EXTRACT
# =====================================================

def extract_brokerage_summary():

    print("=" * 80)
    print("EXTRACTING GOLD BROKERAGE SUMMARY")
    print("=" * 80)

    df = safe_read(
        """
        SELECT
            source,
            report_type,
            product_code,
            product_name,
            upfront,
            afe,
            trailer_fee,
            trxn_charges,
            clawback,
            incentives,
            report_from_date,
            report_to_date,
            rep_date,
            amc_code,
            broker_code,
            sub_broker_code,
            scheme_id,
            created_at
        FROM silver.brokerage_summary
        WHERE flag = 0
        """
    )

    if df.empty:

        print(
            "No Silver brokerage records "
            "found with flag = 0"
        )

        return df

    print("Silver flag = 0 rows fetched :", len(df))

    return df


# =====================================================
# SCHEME MAPPING LOOKUP
#
# Supplies the AMC code and the RTA's own scheme name for
# rows whose report did not carry them.
# =====================================================

def load_scheme_mapping():

    return safe_read(
        """
        SELECT
            rta,
            rta_scheme_code,
            rta_amc_code,
            rta_scheme_name
        FROM bronze.scheme_mapping
        """
    )


# =====================================================
# AMC MASTER LOOKUP
# =====================================================

def load_amc_master():

    return safe_read(
        """
        SELECT
            amc_id,
            amc_code
        FROM bronze.amc_master
        """
    )


# =====================================================
# SCHEME ARN LOOKUP
#
# CAMS WBR36 / WBR36H carry no broker column. The ARN the
# rest of the pipeline recorded against that scheme in
# gold.scheme is used instead, so arn / sub_arn are not
# dead columns. A report that does carry a broker code
# wins over this fallback.
# =====================================================

def load_scheme_arn():

    return safe_read(
        """
        SELECT
            rta,
            scheme_code,
            arn,
            sub_arn
        FROM gold.scheme
        """
    )


# =====================================================
# TRANSFORM
# =====================================================

def transform_brokerage_summary(df):

    print("=" * 80)
    print("TRANSFORMING GOLD BROKERAGE SUMMARY")
    print("=" * 80)

    if df.empty:
        return pd.DataFrame()

    df = df.copy()

    # =================================================
    # KEYS
    # =================================================

    df["rta"] = clean_text(df["source"])

    df["report_type"] = clean_text(df["report_type"])

    df["scheme_code"] = clean_text(df["product_code"])

    # Rows without a scheme code cannot be keyed.
    before = len(df)

    df = df[
        df["scheme_code"].notna()
    ].copy()

    if before != len(df):

        print(
            "Rows dropped without scheme_code :",
            before - len(df)
        )

    if df.empty:
        return pd.DataFrame()

    # =================================================
    # SCHEME NAME
    #
    # The report's own name is kept as-is (it is what the
    # RTA statement shows). Only blanks fall back to the
    # scheme mapping.
    # =================================================

    df["scheme_name"] = (
        df["product_name"]
        .fillna("")
        .astype(str)
        .str.strip()
        .replace({"": None})
    )

    scheme_mapping = load_scheme_mapping()

    if not scheme_mapping.empty:

        scheme_mapping["rta"] = clean_text(
            scheme_mapping["rta"]
        )

        scheme_mapping["rta_scheme_code"] = clean_text(
            scheme_mapping["rta_scheme_code"]
        )

        scheme_mapping = scheme_mapping.drop_duplicates(
            subset=["rta", "rta_scheme_code"],
            keep="first"
        )

        name_lookup = dict(
            zip(
                zip(
                    scheme_mapping["rta"],
                    scheme_mapping["rta_scheme_code"]
                ),
                scheme_mapping["rta_scheme_name"]
            )
        )

        amc_code_lookup = dict(
            zip(
                zip(
                    scheme_mapping["rta"],
                    scheme_mapping["rta_scheme_code"]
                ),
                scheme_mapping["rta_amc_code"]
            )
        )

    else:

        name_lookup = {}

        amc_code_lookup = {}

    keys = list(
        zip(
            df["rta"],
            df["scheme_code"]
        )
    )

    mapped_names = pd.Series(
        [
            name_lookup.get(key)
            for key in keys
        ],
        index=df.index
    )

    df["scheme_name"] = df["scheme_name"].fillna(
        mapped_names
    )

    # =================================================
    # AMC CODE / AMC ID
    #
    # The report's own amc_code wins; otherwise it comes
    # from the scheme mapping.
    # =================================================

    mapped_amc_codes = pd.Series(
        [
            amc_code_lookup.get(key)
            for key in keys
        ],
        index=df.index
    )

    df["amc_code"] = (
        clean_text(df["amc_code"])
        .fillna(clean_text(mapped_amc_codes))
    )

    amc_master = load_amc_master()

    if not amc_master.empty:

        amc_master["amc_code"] = clean_text(
            amc_master["amc_code"]
        )

        amc_lookup = (
            amc_master
            .drop_duplicates(
                subset=["amc_code"],
                keep="first"
            )
            .set_index("amc_code")["amc_id"]
            .to_dict()
        )

        df["amc_id"] = df["amc_code"].map(amc_lookup)

    else:

        df["amc_id"] = None

    # =================================================
    # ARN / SUB ARN
    # =================================================

    df["arn"] = clean_text(df["broker_code"])

    df["sub_arn"] = clean_text(df["sub_broker_code"])

    scheme_arn = load_scheme_arn()

    if not scheme_arn.empty:

        scheme_arn["rta"] = clean_text(
            scheme_arn["rta"]
        )

        scheme_arn["scheme_code"] = clean_text(
            scheme_arn["scheme_code"]
        )

        scheme_arn = scheme_arn.drop_duplicates(
            subset=["rta", "scheme_code"],
            keep="first"
        )

        arn_lookup = dict(
            zip(
                zip(
                    scheme_arn["rta"],
                    scheme_arn["scheme_code"]
                ),
                scheme_arn["arn"]
            )
        )

        sub_arn_lookup = dict(
            zip(
                zip(
                    scheme_arn["rta"],
                    scheme_arn["scheme_code"]
                ),
                scheme_arn["sub_arn"]
            )
        )

        df["arn"] = df["arn"].fillna(
            pd.Series(
                [
                    arn_lookup.get(key)
                    for key in keys
                ],
                index=df.index
            )
        )

        df["sub_arn"] = df["sub_arn"].fillna(
            pd.Series(
                [
                    sub_arn_lookup.get(key)
                    for key in keys
                ],
                index=df.index
            )
        )

    # =================================================
    # MONEY COLUMNS
    # =================================================

    for col in BROKERAGE_AMOUNT_COLUMNS:

        df[col] = pd.to_numeric(
            df[col],
            errors="coerce"
        )

    # =================================================
    # TOTAL BROKERAGE
    #
    # Earnings less the clawed-back part.
    # =================================================

    df["total_brokerage"] = (
        df["upfront"].fillna(0)
        + df["afe"].fillna(0)
        + df["trailer_fee"].fillna(0)
        + df["trxn_charges"].fillna(0)
        + df["incentives"].fillna(0)
        - df["clawback"].fillna(0)
    )

    # =================================================
    # DATES
    # =================================================

    for col in [
        "report_from_date",
        "report_to_date",
        "rep_date"
    ]:

        df[col] = pd.to_datetime(
            df[col],
            errors="coerce"
        ).dt.date

        df[col] = df[col].where(
            pd.notnull(df[col]),
            None
        )

    # =================================================
    # ONE ROW PER NATURAL KEY
    #
    # A single upload can hold the same scheme twice
    # (two files of the same report). Keep the last one
    # read, which is the newest file.
    # =================================================

    key_columns = [
        "rta",
        "report_type",
        "scheme_code",
        "report_from_date",
        "report_to_date"
    ]

    before = len(df)

    df = df.drop_duplicates(
        subset=key_columns,
        keep="last"
    ).copy()

    if before != len(df):

        print(
            "Duplicate natural keys collapsed :",
            before - len(df)
        )

    # =================================================
    # DETERMINISTIC ID
    # =================================================

    df["id"] = df.apply(
        lambda row: uuid.uuid5(

            BROKERAGE_NAMESPACE,

            f"{str(row['rta']).strip().lower()}|"
            f"{str(row['report_type']).strip().lower()}|"
            f"{str(row['scheme_code']).strip().lower()}|"
            f"{row['report_from_date']}|"
            f"{row['report_to_date']}"

        ),
        axis=1
    )

    df["id"] = df["id"].astype(str)

    # =================================================
    # AUDIT
    # =================================================

    now = pd.Timestamp.utcnow().tz_localize(None)

    df["created_at"] = pd.to_datetime(
        df["created_at"],
        errors="coerce"
    )

    if getattr(df["created_at"].dt, "tz", None) is not None:

        df["created_at"] = (
            df["created_at"]
            .dt.tz_localize(None)
        )

    df["created_at"] = df["created_at"].fillna(now)

    df["updated_at"] = now

    # =================================================
    # FINAL SHAPE
    # =================================================

    gold_df = df[GOLD_COLUMNS].copy()

    gold_df["amc_id"] = gold_df["amc_id"].where(
        gold_df["amc_id"].notna(),
        None
    )

    print("Rows Ready :", len(gold_df))

    print(
        "Rows with scheme_id :",
        int(gold_df["scheme_id"].notna().sum())
    )

    print(
        "Rows without scheme_id :",
        int(gold_df["scheme_id"].isna().sum())
    )

    print(
        "Total brokerage in batch :",
        float(gold_df["total_brokerage"].sum())
    )

    return gold_df


# =====================================================
# LOAD
# =====================================================

def load_brokerage_summary(gold_df):

    print("=" * 80)
    print("LOADING DATA INTO GOLD.BROKERAGE_SUMMARY")
    print("=" * 80)

    if gold_df.empty:

        print("No brokerage records to process.")

        return True

    gold_df = gold_df[GOLD_COLUMNS].copy()

    # =================================================
    # EXISTING IDS
    # =================================================

    existing = safe_read(
        """
        SELECT id
        FROM gold.brokerage_summary
        """
    )

    existing_ids = set()

    if not existing.empty:

        existing_ids = set(
            existing["id"].astype(str)
        )

    print("Existing gold rows :", len(existing_ids))

    # =================================================
    # UPDATE EXISTING
    #
    # A re-uploaded report corrects the amounts on the
    # row it already owns.
    # =================================================

    update_df = gold_df[
        gold_df["id"].isin(existing_ids)
    ]

    updated = 0

    if not update_df.empty:

        set_parts = [
            f'"{column}" = :{column}'
            for column in UPDATE_COLUMNS
        ]

        sql = text(
            f"""
            UPDATE gold.brokerage_summary
            SET
                {", ".join(set_parts)}
            WHERE
                id = :id
            """
        )

        try:

            with engine.begin() as connection:

                for _, row in update_df.iterrows():

                    params = {
                        "id": row["id"]
                    }

                    for column in UPDATE_COLUMNS:

                        value = row[column]

                        if pd.isna(value):
                            value = None

                        params[column] = value

                    result = connection.execute(
                        sql,
                        params
                    )

                    if result.rowcount > 0:
                        updated += 1

        except Exception:

            print("FAILED UPDATING GOLD BROKERAGE SUMMARY")

            traceback.print_exc(limit=5)

            return False

    print("Existing rows updated :", updated)

    # =================================================
    # INSERT NEW
    # =================================================

    new_df = gold_df[
        ~gold_df["id"].isin(existing_ids)
    ].copy()

    print("New rows to insert :", len(new_df))

    if new_df.empty:

        print("No new brokerage rows to insert.")

        return True

    try:

        new_df.to_sql(

            name="brokerage_summary",

            con=engine,

            schema="gold",

            if_exists="append",

            index=False,

            method="multi",

            chunksize=5000

        )

        print("Inserted Rows :", len(new_df))

        return True

    except Exception:

        print("FAILED LOADING GOLD BROKERAGE SUMMARY")

        traceback.print_exc(limit=5)

        return False


# =====================================================
# MAIN
# =====================================================

if __name__ == "__main__":

    print("=" * 80)
    print("STARTING GOLD BROKERAGE SUMMARY ETL")
    print("=" * 80)

    df = extract_brokerage_summary()

    if not df.empty:

        gold_df = transform_brokerage_summary(df)

        status = load_brokerage_summary(gold_df)

        print("=" * 80)

        if status:

            print(
                "GOLD BROKERAGE SUMMARY ETL "
                "COMPLETED SUCCESSFULLY"
            )

        else:

            print("GOLD BROKERAGE SUMMARY ETL FAILED")

        print("=" * 80)

    else:

        print("No new brokerage records to process.")

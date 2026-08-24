# =====================================================
# GOLD : INVALID EUIN
#
# silver.invalid_euin -> gold.invalid_euin
#
# Source reports
#   CAMS WBR68  - invalid EUIN report
#   KFINTECH    - equivalent report, added later
#
# One row per faulted transaction, in every layer.
#
# rta_txn_no is the RTA transaction number, which is what
# gold.transactions stores under the same name, so
# (rta, rta_txn_no) joins a fault back to the transaction
# it belongs to. gold.transactions has no surrogate key,
# so that natural key is carried here rather than a
# transaction_id.
#
# `id` is a deterministic uuid5 over
# rta | report_type | rta_txn_no, so a re-uploaded report
# updates the row it already owns instead of inserting a
# second copy.
#
# PAN is stored in plain text, matching gold.transactions
# and gold.clients.
# =====================================================

import uuid
import traceback

import pandas as pd

from sqlalchemy import text

from utils.db import engine

from mapping_wbr import INVALID_EUIN_AMOUNT_COLUMNS


# =====================================================
# FIXED INVALID EUIN UUID NAMESPACE
# NEVER CHANGE THIS VALUE
# =====================================================

EUIN_NAMESPACE = uuid.UUID(
    "b960e881-d14f-46c5-8178-fc030601ad56"
)


# =====================================================
# EUIN VALIDITY
#
# The RTA's marker is carried verbatim in
# euin_valid_raw. Only "Y" is read as valid; every other
# spelling (CAMS WBR68 uses "N" and "F") is a fault.
#
# Declared here rather than inline so a new RTA spelling
# is one edit in one place.
# =====================================================

EUIN_VALID_VALUES = {
    "Y"
}


GOLD_COLUMNS = [

    "id",
    "rta",
    "report_type",

    "rta_txn_no",

    "scheme_code",
    "scheme_name",
    "scheme_id",
    "amc_code",
    "amc_id",

    "folio_number",
    "pan",
    "client_id",
    "investor_name",
    "email",

    "euin",
    "euin_valid_raw",
    "is_euin_valid",
    "reason",

    "arn",
    "sub_arn",
    "sub_broker_code",
    "user_code",
    "cons_code",
    "location",

    "txn_type_raw",
    "txn_desc",
    "amount",

    "trade_date",
    "posted_date",
    "sys_reg_date",
    "sip_regn_date",

    "application_no",
    "user_txn_no",
    "auto_txn_no",
    "alt_folio",
    "folio_old",
    "scheme_folio_number",

    "created_at",
    "updated_at"

]


# Every column a re-uploaded report is allowed to correct.
#
# The key columns (rta, report_type, rta_txn_no) are
# absent on purpose: they define the row.
UPDATE_COLUMNS = [

    "scheme_code",
    "scheme_name",
    "scheme_id",
    "amc_code",
    "amc_id",

    "folio_number",
    "pan",
    "client_id",
    "investor_name",
    "email",

    "euin",
    "euin_valid_raw",
    "is_euin_valid",
    "reason",

    "arn",
    "sub_arn",
    "sub_broker_code",
    "user_code",
    "cons_code",
    "location",

    "txn_type_raw",
    "txn_desc",
    "amount",

    "trade_date",
    "posted_date",
    "sys_reg_date",
    "sip_regn_date",

    "application_no",
    "user_txn_no",
    "auto_txn_no",
    "alt_folio",
    "folio_old",
    "scheme_folio_number",

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
        .replace({"": None})
    )


def clean_code(series):

    return (
        series
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()
        .replace({"": None})
    )


def clean_pan(series):

    return (
        series
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()
        .replace(
            {
                "": None,
                "NAN": None,
                "NONE": None,
                "NULL": None,
                "NON RESIDENT": None
            }
        )
        .str[:10]
    )


# =====================================================
# EXTRACT
# =====================================================

def extract_invalid_euin():

    print("=" * 80)
    print("EXTRACTING GOLD INVALID EUIN")
    print("=" * 80)

    df = safe_read(
        """
        SELECT
            source,
            report_type,
            trxn_no,
            usertxn_no,
            auto_trxn_no,
            appln_no,
            sch_code,
            rta_scheme_code,
            sch_name,
            amc_code,
            folio_no,
            folio,
            alt_folio,
            folio_old,
            scheme_folio_number,
            inv_name,
            inv_pan,
            email,
            euin,
            euin_valid,
            reason,
            arn_code,
            subbrok_arn,
            subbrokcod,
            user_code,
            cons_code,
            location,
            trxn_type,
            trxn_desc,
            amount,
            trade_date,
            posted_date,
            sys_reg_dt,
            sip_regn_date,
            scheme_id,
            created_at
        FROM silver.invalid_euin
        WHERE flag = 0
        """
    )

    if df.empty:

        print(
            "No Silver invalid EUIN records "
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
            amc_code,
            amc_id
        FROM bronze.amc_master
        """
    )


# =====================================================
# CLIENT LOOKUP
#
# gold.clients has no surrogate key column; user_id is
# the identifier the rest of Gold joins on.
# =====================================================

def load_clients():

    return safe_read(
        """
        SELECT
            user_id,
            pan
        FROM gold.clients
        WHERE pan IS NOT NULL
        """
    )


# =====================================================
# TRANSFORM
# =====================================================

def transform_invalid_euin(df):

    print("=" * 80)
    print("TRANSFORMING GOLD INVALID EUIN")
    print("=" * 80)

    if df.empty:
        return pd.DataFrame()

    df = df.copy()

    # =================================================
    # KEYS
    # =================================================

    df["rta"] = clean_code(df["source"])

    df["report_type"] = clean_code(df["report_type"])

    df["rta_txn_no"] = clean_code(df["trxn_no"])

    # Rows without a transaction number cannot be keyed.
    before = len(df)

    df = df[
        df["rta_txn_no"].notna()
    ].copy()

    if before != len(df):

        print(
            "Rows dropped without trxn_no :",
            before - len(df)
        )

    if df.empty:
        return pd.DataFrame()

    # =================================================
    # SCHEME
    #
    # The report's own name is kept as-is (it is what the
    # RTA statement shows). Only blanks fall back to the
    # scheme mapping.
    # =================================================

    # WBR68 splits the RTA scheme code across amc_code and
    # sch_code; Silver rebuilt it into rta_scheme_code.
    # That rebuilt code is what bronze.scheme_mapping and
    # gold.transactions both key on, so it is the one Gold
    # publishes. sch_code is the fallback for an RTA that
    # already reports the whole code in one column.

    df["scheme_code"] = clean_code(df["rta_scheme_code"])

    df["scheme_code"] = df["scheme_code"].fillna(
        clean_code(df["sch_code"])
    )

    df["scheme_name"] = clean_text(df["sch_name"])

    df["amc_code"] = clean_code(df["amc_code"])

    scheme_mapping = load_scheme_mapping()

    keys = list(
        zip(
            df["rta"],
            df["scheme_code"]
        )
    )

    if not scheme_mapping.empty:

        scheme_mapping["rta"] = clean_code(
            scheme_mapping["rta"]
        )

        scheme_mapping["rta_scheme_code"] = clean_code(
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

    mapped_amc_codes = pd.Series(
        [
            amc_code_lookup.get(key)
            for key in keys
        ],
        index=df.index
    )

    df["amc_code"] = df["amc_code"].fillna(
        clean_code(mapped_amc_codes)
    )

    # =================================================
    # AMC ID
    # =================================================

    amc_master = load_amc_master()

    if not amc_master.empty:

        amc_master["amc_code"] = clean_code(
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
    # INVESTOR
    # =================================================

    df["folio_number"] = clean_code(df["folio_no"])

    # The report repeats the folio under several names.
    # folio_no is the one the rest of the pipeline uses;
    # fall back to folio when it is blank.
    df["folio_number"] = df["folio_number"].fillna(
        clean_code(df["folio"])
    )

    df["pan"] = clean_pan(df["inv_pan"])

    df["investor_name"] = clean_text(df["inv_name"])

    df["email"] = (
        df["email"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.lower()
        .replace({"": None})
    )

    clients = load_clients()

    if not clients.empty:

        clients["pan_clean"] = clean_pan(
            clients["pan"]
        )

        client_lookup = dict(
            zip(
                clients["pan_clean"],
                clients["user_id"]
            )
        )

    else:

        client_lookup = {}

    df["client_id"] = df["pan"].map(client_lookup)

    print(
        "Rows matched to a client :",
        int(df["client_id"].notna().sum())
    )

    print(
        "Rows with no client match :",
        int(df["client_id"].isna().sum())
    )

    # =================================================
    # EUIN
    # =================================================

    df["euin"] = clean_code(df["euin"])

    df["euin_valid_raw"] = clean_code(df["euin_valid"])

    df["is_euin_valid"] = df["euin_valid_raw"].apply(
        lambda value: (
            None
            if value is None
            else value in EUIN_VALID_VALUES
        )
    )

    df["reason"] = clean_text(df["reason"])

    print(
        "EUIN marker values :",
        df["euin_valid_raw"]
        .value_counts(dropna=False)
        .to_dict()
    )

    # =================================================
    # BROKER
    # =================================================

    df["arn"] = clean_code(df["arn_code"])

    df["sub_arn"] = clean_code(df["subbrok_arn"])

    df["sub_broker_code"] = clean_code(df["subbrokcod"])

    df["user_code"] = clean_code(df["user_code"])

    df["cons_code"] = clean_code(df["cons_code"])

    df["location"] = clean_text(df["location"])

    # =================================================
    # TRANSACTION DETAIL
    # =================================================

    df["txn_type_raw"] = clean_code(df["trxn_type"])

    df["txn_desc"] = clean_text(df["trxn_desc"])

    for col in INVALID_EUIN_AMOUNT_COLUMNS:

        if col not in df.columns:
            continue

        df[col] = pd.to_numeric(
            df[col],
            errors="coerce"
        )

    # =================================================
    # SECONDARY IDENTIFIERS
    # =================================================

    df["application_no"] = clean_code(df["appln_no"])

    df["user_txn_no"] = clean_code(df["usertxn_no"])

    df["auto_txn_no"] = clean_code(df["auto_trxn_no"])

    df["alt_folio"] = clean_code(df["alt_folio"])

    df["folio_old"] = clean_code(df["folio_old"])

    df["scheme_folio_number"] = clean_code(
        df["scheme_folio_number"]
    )

    # =================================================
    # DATES
    #
    # sys_reg_dt is renamed to sys_reg_date in Gold: the
    # RTA abbreviation stops at Silver.
    # =================================================

    df["sys_reg_date"] = df["sys_reg_dt"]

    for col in [
        "trade_date",
        "posted_date",
        "sys_reg_date",
        "sip_regn_date"
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
    # A single upload can hold the same transaction twice
    # (two files of the same report). Keep the last one
    # read, which is the newest file.
    # =================================================

    key_columns = [
        "rta",
        "report_type",
        "rta_txn_no"
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
        lambda row: str(
            uuid.uuid5(

                EUIN_NAMESPACE,

                f"{str(row['rta']).strip().lower()}|"
                f"{str(row['report_type']).strip().lower()}|"
                f"{str(row['rta_txn_no']).strip().lower()}"
            )
        ),
        axis=1
    )

    # =================================================
    # AUDIT COLUMNS
    # =================================================

    now = pd.Timestamp.now()

    df["created_at"] = now
    df["updated_at"] = now

    # =================================================
    # FINAL SHAPE
    # =================================================

    for col in GOLD_COLUMNS:

        if col not in df.columns:

            df[col] = None

    gold_df = df[GOLD_COLUMNS].copy()

    gold_df = gold_df.where(
        pd.notnull(gold_df),
        None
    )

    return gold_df


# =====================================================
# LOAD
# =====================================================

def load_invalid_euin(gold_df):

    print("=" * 80)
    print("LOADING DATA INTO GOLD.INVALID_EUIN")
    print("=" * 80)

    if gold_df.empty:

        print("No invalid EUIN records to process.")

        return True

    gold_df = gold_df[GOLD_COLUMNS].copy()

    # =================================================
    # EXISTING IDS
    # =================================================

    existing = safe_read(
        """
        SELECT id
        FROM gold.invalid_euin
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
    # A re-uploaded report corrects the detail on the row
    # it already owns.
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
            UPDATE gold.invalid_euin
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

                        if value is not None and pd.isna(
                            value
                        ):
                            value = None

                        params[column] = value

                    result = connection.execute(
                        sql,
                        params
                    )

                    if result.rowcount > 0:
                        updated += 1

        except Exception:

            print("FAILED UPDATING GOLD INVALID EUIN")

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

        print("No new invalid EUIN rows to insert.")

        return True

    try:

        new_df.to_sql(

            name="invalid_euin",

            con=engine,

            schema="gold",

            if_exists="append",

            index=False,

            method="multi",

            chunksize=1000
        )

    except Exception:

        print("FAILED INSERTING GOLD INVALID EUIN")

        traceback.print_exc(limit=5)

        return False

    print("=" * 80)
    print("GOLD INVALID EUIN LOADED")
    print(f"Inserted {len(new_df)} rows")
    print("=" * 80)

    return True


# =====================================================
# MAIN
# =====================================================

if __name__ == "__main__":

    silver_df = extract_invalid_euin()

    if not silver_df.empty:

        gold = transform_invalid_euin(silver_df)

        if not gold.empty:

            load_invalid_euin(gold)

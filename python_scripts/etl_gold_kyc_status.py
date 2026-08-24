# =====================================================
# GOLD : INVESTOR KYC STATUS
#
# silver.kyc_status -> gold.investor_kyc_status
#
# Source reports
#   CAMS WBR56  - KYC status of investor
#   KFINTECH    - equivalent report, added later
#
# GRAIN CHANGE
#
# The report packs four holder slots into one folio row.
# Gold unpivots them to ONE ROW PER HOLDER, the same way
# gold.folio_nominees unpivots the three-nominees-per-
# folio layout:
#
#   FH   first holder  inv_name / tax_no         / fh_kyc
#   GU   guardian      guardian / guardian_panno / gu_kyc
#   JH1  joint 1       jname1   / jointpan1      / jh1_kyc
#   JH2  joint 2       jname2   / jointpan2      / jh2_kyc
#
# A slot is emitted only when the RTA reported something
# for it (a name, a PAN or a KYC status). Empty slots
# never become rows.
#
# The folio-level columns (address, contact, broker,
# period) repeat on every holder row of that folio, so a
# single row answers "who is not KYC compliant and how do
# I reach them" without a second join.
#
# `id` is a deterministic uuid5 over
# rta | report_type | amc_code | folio | holder_role,
# so a re-uploaded report updates the row it already owns
# instead of inserting a second copy.
#
# PAN is stored in plain text, matching gold.transactions
# and gold.clients.
# =====================================================

import uuid
import traceback

import pandas as pd

from sqlalchemy import text

from utils.db import engine


# =====================================================
# FIXED KYC UUID NAMESPACE
# NEVER CHANGE THIS VALUE
# =====================================================

KYC_NAMESPACE = uuid.UUID(
    "c3559fb0-e679-4dc4-b485-ff9d7924c36f"
)


# =====================================================
# HOLDER SLOTS
#
# One entry per holder the report can carry. Adding an
# RTA that reports a third joint holder is one more entry
# here plus its aliases in mapping_wbr.py.
#
#   role     value stored in holder_role
#   seq      display / sort order
#   name     silver column holding the holder's name
#   pan      silver column holding the holder's PAN
#   kyc      silver column holding the short KYC status
#   desc     silver column holding the long description
#   aadhaar  silver column holding the aadhaar link
#
# Note FH and GU share fh_g_aadharlink: the report has no
# separate guardian aadhaar column.
# =====================================================

HOLDER_SLOTS = [

    {
        "role": "FH",
        "seq": 1,
        "name": "inv_name",
        "pan": "tax_no",
        "kyc": "fh_kyc",
        "desc": "fh_kyc_desc",
        "aadhaar": "fh_g_aadharlink"
    },

    {
        "role": "GU",
        "seq": 2,
        "name": "guardian",
        "pan": "guardian_panno",
        "kyc": "gu_kyc",
        "desc": "gu_kyc_desc",
        "aadhaar": "fh_g_aadharlink"
    },

    {
        "role": "JH1",
        "seq": 3,
        "name": "jname1",
        "pan": "jointpan1",
        "kyc": "jh1_kyc",
        "desc": "jh1_kyc_desc",
        "aadhaar": "jh1_aadharlink"
    },

    {
        "role": "JH2",
        "seq": 4,
        "name": "jname2",
        "pan": "jointpan2",
        "kyc": "jh2_kyc",
        "desc": "jh2_kyc_desc",
        "aadhaar": "jh2_aadharlink"
    }

]


GOLD_COLUMNS = [

    "id",
    "rta",
    "report_type",

    "amc_code",
    "amc_id",

    "folio_number",

    "holder_role",
    "holder_seq",
    "holder_name",
    "pan",
    "client_id",

    "kyc_status",
    "kyc_status_desc",
    "aadhaar_link_status",
    "is_kyc_compliant",

    "broker_name",
    "arn",

    "address_line1",
    "address_line2",
    "address_line3",
    "city",
    "pincode",
    "state",
    "country",
    "location",

    "email",
    "mobile",
    "phone_res",
    "phone_off",
    "fax_res",
    "fax_off",

    "report_from_date",
    "report_to_date",
    "rep_date",

    "created_at",
    "updated_at"

]


# Every column a re-uploaded report is allowed to correct.
#
# The key columns (rta, report_type, amc_code,
# folio_number, holder_role) are absent on purpose: they
# define the row, so changing one produces a different row
# rather than an update.
UPDATE_COLUMNS = [

    "amc_id",

    "holder_seq",
    "holder_name",
    "pan",
    "client_id",

    "kyc_status",
    "kyc_status_desc",
    "aadhaar_link_status",
    "is_kyc_compliant",

    "broker_name",
    "arn",

    "address_line1",
    "address_line2",
    "address_line3",
    "city",
    "pincode",
    "state",
    "country",
    "location",

    "email",
    "mobile",
    "phone_res",
    "phone_off",
    "fax_res",
    "fax_off",

    "report_from_date",
    "report_to_date",
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


# =====================================================
# CLEAN PAN
#
# Same rule etl_gold_clients.py uses, so a PAN written
# here matches the one gold.clients was keyed on.
# =====================================================

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

def extract_kyc_status():

    print("=" * 80)
    print("EXTRACTING GOLD INVESTOR KYC STATUS")
    print("=" * 80)

    df = safe_read(
        """
        SELECT
            source,
            report_type,
            brok_dlr_code,
            brok_name,
            amc_code,
            folio,
            inv_name,
            tax_no,
            jname1,
            jointpan1,
            jname2,
            jointpan2,
            guardian,
            guardian_panno,
            fh_kyc,
            gu_kyc,
            jh1_kyc,
            jh2_kyc,
            fh_kyc_desc,
            gu_kyc_desc,
            jh1_kyc_desc,
            jh2_kyc_desc,
            fh_g_aadharlink,
            jh1_aadharlink,
            jh2_aadharlink,
            address1,
            address2,
            address3,
            city,
            pincode,
            state,
            country,
            location,
            phone_res,
            phone_off,
            mobile_no,
            email,
            fax_res,
            fax_off,
            rep_from_date,
            rep_to_date,
            rep_date,
            created_at
        FROM silver.kyc_status
        WHERE flag = 0
        """
    )

    if df.empty:

        print(
            "No Silver KYC status records "
            "found with flag = 0"
        )

        return df

    print("Silver flag = 0 rows fetched :", len(df))

    return df


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
# the identifier the rest of Gold joins on (see
# etl_gold_sip.py).
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
# KYC COMPLIANCE FLAG
#
# TRUE when the RTA says the holder's KYC is OK, FALSE
# for any other stated status, NULL when the RTA reported
# no status for that holder.
#
# Kept as one function so the rule lives in one place.
# =====================================================

def derive_is_compliant(status):

    if status is None or pd.isna(status):
        return None

    text_value = str(status).strip().upper()

    if text_value == "":
        return None

    return "KYC OK" in text_value


# =====================================================
# TRANSFORM
# =====================================================

def transform_kyc_status(df):

    print("=" * 80)
    print("TRANSFORMING GOLD INVESTOR KYC STATUS")
    print("=" * 80)

    if df.empty:
        return pd.DataFrame()

    df = df.copy()

    # =================================================
    # KEYS
    # =================================================

    df["rta"] = clean_code(df["source"])

    df["report_type"] = clean_code(df["report_type"])

    df["amc_code"] = clean_code(df["amc_code"])

    df["folio_number"] = clean_code(df["folio"])

    # Rows without a folio cannot be keyed.
    before = len(df)

    df = df[
        df["folio_number"].notna()
    ].copy()

    if before != len(df):

        print(
            "Rows dropped without folio :",
            before - len(df)
        )

    if df.empty:
        return pd.DataFrame()

    # =================================================
    # ONE ROW PER FOLIO BEFORE UNPIVOTING
    #
    # A single upload can hold the same folio twice (two
    # files of the same report). Keep the last one read,
    # which is the newest file.
    # =================================================

    folio_key = [
        "rta",
        "report_type",
        "amc_code",
        "folio_number"
    ]

    before = len(df)

    df = df.drop_duplicates(
        subset=folio_key,
        keep="last"
    ).copy()

    if before != len(df):

        print(
            "Duplicate folios collapsed :",
            before - len(df)
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
    # FOLIO-LEVEL CONTEXT
    #
    # Cleaned once, then repeated on every holder row.
    # =================================================

    df["broker_name"] = clean_text(df["brok_name"])

    df["arn"] = clean_code(df["brok_dlr_code"])

    df["address_line1"] = clean_text(df["address1"])
    df["address_line2"] = clean_text(df["address2"])
    df["address_line3"] = clean_text(df["address3"])

    df["city"] = clean_text(df["city"])
    df["pincode"] = clean_text(df["pincode"])
    df["state"] = clean_text(df["state"])
    df["country"] = clean_text(df["country"])
    df["location"] = clean_text(df["location"])

    df["email"] = (
        df["email"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.lower()
        .replace({"": None})
    )

    df["mobile"] = clean_text(df["mobile_no"])
    df["phone_res"] = clean_text(df["phone_res"])
    df["phone_off"] = clean_text(df["phone_off"])
    df["fax_res"] = clean_text(df["fax_res"])
    df["fax_off"] = clean_text(df["fax_off"])

    df["report_from_date"] = df["rep_from_date"]
    df["report_to_date"] = df["rep_to_date"]

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

    folio_columns = [
        "rta",
        "report_type",
        "amc_code",
        "amc_id",
        "folio_number",
        "broker_name",
        "arn",
        "address_line1",
        "address_line2",
        "address_line3",
        "city",
        "pincode",
        "state",
        "country",
        "location",
        "email",
        "mobile",
        "phone_res",
        "phone_off",
        "fax_res",
        "fax_off",
        "report_from_date",
        "report_to_date",
        "rep_date"
    ]

    # =================================================
    # UNPIVOT HOLDERS
    #
    # One frame per holder slot, concatenated. A slot is
    # kept only when the RTA reported a name, a PAN or a
    # KYC status for it.
    # =================================================

    holder_frames = []

    for slot in HOLDER_SLOTS:

        holder = df[folio_columns].copy()

        holder["holder_role"] = slot["role"]

        holder["holder_seq"] = slot["seq"]

        holder["holder_name"] = (
            clean_text(df[slot["name"]])
            if slot["name"] in df.columns
            else None
        )

        holder["pan"] = (
            clean_pan(df[slot["pan"]])
            if slot["pan"] in df.columns
            else None
        )

        holder["kyc_status"] = (
            clean_text(df[slot["kyc"]])
            if slot["kyc"] in df.columns
            else None
        )

        holder["kyc_status_desc"] = (
            clean_text(df[slot["desc"]])
            if slot["desc"] in df.columns
            else None
        )

        holder["aadhaar_link_status"] = (
            clean_text(df[slot["aadhaar"]])
            if slot["aadhaar"] in df.columns
            else None
        )

        # -------------------------------------------------
        # DROP EMPTY SLOTS
        #
        # Aadhaar is deliberately NOT part of this test:
        # FH and GU share one aadhaar column, so a folio
        # with no guardian would otherwise emit a phantom
        # GU row.
        # -------------------------------------------------

        present = (
            holder["holder_name"].notna()
            | holder["pan"].notna()
            | holder["kyc_status"].notna()
        )

        holder = holder[present].copy()

        print(
            f"Holder {slot['role']} rows :",
            len(holder)
        )

        if not holder.empty:

            holder_frames.append(holder)

    if not holder_frames:

        print("No holder rows produced.")

        return pd.DataFrame()

    gold_df = pd.concat(
        holder_frames,
        ignore_index=True
    )

    print("Total holder rows :", len(gold_df))

    # =================================================
    # KYC COMPLIANCE FLAG
    # =================================================

    gold_df["is_kyc_compliant"] = (
        gold_df["kyc_status"].apply(derive_is_compliant)
    )

    compliant = gold_df["is_kyc_compliant"]

    print(
        "KYC compliant :",
        int((compliant == True).sum())
    )

    print(
        "KYC not compliant :",
        int((compliant == False).sum())
    )

    print(
        "KYC status not reported :",
        int(compliant.isna().sum())
    )

    # =================================================
    # CLIENT ID
    #
    # Matched on PAN, the same key gold.clients is
    # deduplicated on. Holders whose PAN is blank or not
    # yet in gold.clients keep a NULL client_id - the KYC
    # fact is still recorded.
    # =================================================

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

    gold_df["client_id"] = gold_df["pan"].map(
        client_lookup
    )

    print(
        "Holders matched to a client :",
        int(gold_df["client_id"].notna().sum())
    )

    print(
        "Holders with no client match :",
        int(gold_df["client_id"].isna().sum())
    )

    # =================================================
    # DETERMINISTIC ID
    # =================================================

    gold_df["id"] = gold_df.apply(
        lambda row: str(
            uuid.uuid5(

                KYC_NAMESPACE,

                f"{str(row['rta']).strip().lower()}|"
                f"{str(row['report_type']).strip().lower()}|"
                f"{str(row['amc_code']).strip().lower()}|"
                f"{str(row['folio_number']).strip().lower()}|"
                f"{str(row['holder_role']).strip().lower()}"
            )
        ),
        axis=1
    )

    # =================================================
    # AUDIT COLUMNS
    # =================================================

    now = pd.Timestamp.now()

    gold_df["created_at"] = now
    gold_df["updated_at"] = now

    # =================================================
    # FINAL SHAPE
    # =================================================

    for col in GOLD_COLUMNS:

        if col not in gold_df.columns:

            gold_df[col] = None

    gold_df = gold_df[GOLD_COLUMNS].copy()

    gold_df = gold_df.where(
        pd.notnull(gold_df),
        None
    )

    return gold_df


# =====================================================
# LOAD
# =====================================================

def load_kyc_status(gold_df):

    print("=" * 80)
    print("LOADING DATA INTO GOLD.INVESTOR_KYC_STATUS")
    print("=" * 80)

    if gold_df.empty:

        print("No KYC status records to process.")

        return True

    gold_df = gold_df[GOLD_COLUMNS].copy()

    # =================================================
    # EXISTING IDS
    # =================================================

    existing = safe_read(
        """
        SELECT id
        FROM gold.investor_kyc_status
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
    # A re-uploaded report corrects the KYC status on the
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
            UPDATE gold.investor_kyc_status
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

            print(
                "FAILED UPDATING GOLD "
                "INVESTOR KYC STATUS"
            )

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

        print("No new KYC status rows to insert.")

        return True

    try:

        new_df.to_sql(

            name="investor_kyc_status",

            con=engine,

            schema="gold",

            if_exists="append",

            index=False,

            method="multi",

            chunksize=1000
        )

    except Exception:

        print("FAILED INSERTING GOLD INVESTOR KYC STATUS")

        traceback.print_exc(limit=5)

        return False

    print("=" * 80)
    print("GOLD INVESTOR KYC STATUS LOADED")
    print(f"Inserted {len(new_df)} rows")
    print("=" * 80)

    return True


# =====================================================
# MAIN
# =====================================================

if __name__ == "__main__":

    silver_df = extract_kyc_status()

    if not silver_df.empty:

        gold = transform_kyc_status(silver_df)

        if not gold.empty:

            load_kyc_status(gold)

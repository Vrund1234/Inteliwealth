import pandas as pd
import traceback

from datetime import datetime, timezone

from utils.db import engine


# ============================================================
# SAFE READ
# ============================================================

def safe_read(query, params=None, connection=engine):

    try:

        return pd.read_sql(
            query,
            connection,
            params=params
        )

    except Exception as e:

        print("SQL ERROR :", e)

        traceback.print_exc(limit=5)

        return pd.DataFrame()


# ============================================================
# CLEAN STRING
# ============================================================

def clean_string(series):

    return (
        series
        .fillna("")
        .astype(str)
        .str.strip()
        .replace(
            {
                "": pd.NA,
                "NAN": pd.NA,
                "NONE": pd.NA,
                "NULL": pd.NA,
                "NAT": pd.NA
            }
        )
    )


# ============================================================
# CLEAN PAN
# ============================================================

def clean_pan(series):

    s = clean_string(series)

    s = (
        s
        .str.upper()
        .str.strip()
        .replace(
            {
                "": pd.NA,
                "NAN": pd.NA,
                "NONE": pd.NA,
                "NULL": pd.NA,
                "NAT": pd.NA,
                "NON RESIDENT": pd.NA
            }
        )
    )

    return s.str[:10]


# ============================================================
# CLEAN ACCOUNT NUMBER
# ============================================================

def clean_account_number(series):

    # Account numbers must ALWAYS remain strings.
    #
    # Do NOT use:
    #   int()
    #   float()
    #   pd.to_numeric()
    #
    # Leading zeros are meaningful.
    #
    # Example:
    #
    #   00691060000052
    #
    # must remain:
    #
    #   00691060000052

    s = (
        series
        .fillna("")
        .astype("string")
        .str.strip()
    )

    return (
        s
        .replace(
            {
                "": pd.NA,
                "NAN": pd.NA,
                "NONE": pd.NA,
                "NULL": pd.NA,
                "NAT": pd.NA
            }
        )
    )


# ============================================================
# EXTRACT CLIENT BANK DATA
# ============================================================

def extract_client_bank():

    print("=" * 80)
    print("EXTRACTING DATA FOR GOLD CLIENT BANK")
    print("=" * 80)

    query = """

        SELECT

            i.source,

            i.folio_no,

            i.pan_no,

            txn.txn_pan,

            sip.sip_pan,

            i.bank_name,

            CAST(i.bank_account_no AS TEXT) AS bank_account_no,

            i.account_type,

            i.branch,

            i.ifsc_code,

            i.bank_address1,
            i.bank_address2,
            i.bank_address3,

            i.bank_city,

            i.b_pincode,

            i.created_at,
            i.updated_at,

            i.flag

        FROM silver.investor_master i

        LEFT JOIN
        (
            SELECT

                folio_no,

                MAX(pan) AS txn_pan

            FROM silver.transaction_master_new

            WHERE pan IS NOT NULL

              AND TRIM(
                    CAST(pan AS TEXT)
                  ) <> ''

            GROUP BY folio_no

        ) txn

            ON REGEXP_REPLACE(
                TRIM(
                    CAST(i.folio_no AS TEXT)
                ),
                '\\.0$',
                ''
            )
            =
            REGEXP_REPLACE(
                TRIM(
                    CAST(txn.folio_no AS TEXT)
                ),
                '\\.0$',
                ''
            )

        LEFT JOIN
        (
            SELECT

                folio_no,

                MAX(pan) AS sip_pan

            FROM silver.sip_master_new

            WHERE pan IS NOT NULL

              AND TRIM(
                    CAST(pan AS TEXT)
                  ) <> ''

            GROUP BY folio_no

        ) sip

            ON REGEXP_REPLACE(
                TRIM(
                    CAST(i.folio_no AS TEXT)
                ),
                '\\.0$',
                ''
            )
            =
            REGEXP_REPLACE(
                TRIM(
                    CAST(sip.folio_no AS TEXT)
                ),
                '\\.0$',
                ''
            )

        WHERE i.bank_account_no IS NOT NULL

          AND TRIM(
                CAST(i.bank_account_no AS TEXT)
              ) <> ''

        ORDER BY i.created_at

    """

    df = safe_read(query)

    print(
        "Silver investor bank rows fetched :",
        len(df)
    )

    if df.empty:

        print(
            "No bank data found in silver.investor_master"
        )

        return pd.DataFrame()

    df.columns = [
        c.lower()
        for c in df.columns
    ]

    return df


# ============================================================
# TRANSFORM CLIENT BANK DATA
# ============================================================

def transform_client_bank(df):

    print("=" * 80)
    print("TRANSFORMING GOLD CLIENT BANK")
    print("=" * 80)

    if df.empty:

        print("No data to transform")

        return pd.DataFrame()

    df = df.copy()

    # --------------------------------------------------------
    # CLEAN PAN SOURCES
    # --------------------------------------------------------
    #
    # SAME LOGIC AS GOLD.CLIENTS
    #
    # investor_master.pan_no
    #          ↓
    # transaction PAN
    #          ↓
    # SIP PAN
    #
    # guardian_pan is NOT used.
    # --------------------------------------------------------

    df["pan_no"] = clean_pan(
        df["pan_no"]
    )

    df["txn_pan"] = clean_pan(
        df["txn_pan"]
    )

    df["sip_pan"] = clean_pan(
        df["sip_pan"]
    )

    # --------------------------------------------------------
    # FINAL PAN
    # --------------------------------------------------------

    df["pan"] = (
        df["pan_no"]
        .fillna(df["txn_pan"])
        .fillna(df["sip_pan"])
    )

    # --------------------------------------------------------
    # CLEAN ACCOUNT NUMBER
    # --------------------------------------------------------
    #
    # Account number is kept as STRING.
    #
    # Leading zeros are preserved.
    # --------------------------------------------------------

    df["account_number"] = clean_account_number(
        df["bank_account_no"]
    )

    # --------------------------------------------------------
    # REMOVE INVALID RECORDS
    # --------------------------------------------------------

    before = len(df)

    df = df[
        df["pan"].notna()
        & df["account_number"].notna()
    ].copy()

    print(
        "Rows removed due to missing PAN/account :",
        before - len(df)
    )

    if df.empty:

        print(
            "No valid bank records after cleaning"
        )

        return pd.DataFrame()

    # --------------------------------------------------------
    # CLEAN BANK FIELDS
    # --------------------------------------------------------

    df["bank_name"] = clean_string(
        df["bank_name"]
    )

    df["bank_branch"] = clean_string(
        df["branch"]
    )

    df["account_type"] = clean_string(
        df["account_type"]
    )

    df["ifsc"] = clean_string(
        df["ifsc_code"]
    )

    df["bank_city"] = clean_string(
        df["bank_city"]
    )

    df["pincode"] = clean_string(
        df["b_pincode"]
    )

    # --------------------------------------------------------
    # BANK ADDRESS
    # --------------------------------------------------------

    address_columns = [
        "bank_address1",
        "bank_address2",
        "bank_address3"
    ]

    for col in address_columns:

        df[col] = clean_string(
            df[col]
        )

    df["bank_address"] = (
        df[address_columns]
        .fillna("")
        .astype(str)
        .apply(
            lambda row: ", ".join(
                value.strip()
                for value in row
                if value.strip()
                and value.strip().upper()
                not in [
                    "NAN",
                    "NONE",
                    "NULL"
                ]
            ),
            axis=1
        )
    )

    df["bank_address"] = clean_string(
        df["bank_address"]
    )

    # --------------------------------------------------------
    # MICR
    # --------------------------------------------------------
    #
    # No MICR column exists in silver.investor_master.
    #
    # Therefore MICR remains NULL.
    # --------------------------------------------------------

    df["micr"] = pd.NA

    # ========================================================
    # CLIENT ID
    # ========================================================
    #
    # FINAL PAN
    #     ↓
    # gold.clients.pan
    #     ↓
    # gold.clients.id
    #
    # PAN itself is NOT stored in gold.client_bank.
    # ========================================================

    print("=" * 80)
    print("MAPPING PAN TO GOLD CLIENT ID")
    print("=" * 80)

    client_query = """

        SELECT

            id,

            pan

        FROM gold.clients

        WHERE pan IS NOT NULL

    """

    clients = safe_read(
        client_query
    )

    if clients.empty:

        print(
            "No clients found in gold.clients"
        )

        return pd.DataFrame()

    clients["pan"] = clean_pan(
        clients["pan"]
    )

    clients = clients[
        clients["pan"].notna()
    ].copy()

    # --------------------------------------------------------
    # REMOVE DUPLICATE CLIENT PANs
    # --------------------------------------------------------

    clients = (
        clients
        .drop_duplicates(
            subset=["pan"],
            keep="first"
        )
    )

    # --------------------------------------------------------
    # MAP CLIENT UUID
    # --------------------------------------------------------

    df = df.merge(
        clients[
            [
                "pan",
                "id"
            ]
        ],
        on="pan",
        how="left"
    )

    df.rename(
        columns={
            "id": "client_id"
        },
        inplace=True
    )

    print(
        "Client IDs mapped :",
        df["client_id"].notna().sum()
    )

    print(
        "Client IDs missing :",
        df["client_id"].isna().sum()
    )

    # --------------------------------------------------------
    # REMOVE RECORDS WHERE CLIENT DOES NOT EXIST
    # --------------------------------------------------------

    missing_client = df[
        df["client_id"].isna()
    ]

    if not missing_client.empty:

        print(
            "Bank rows skipped because client does not exist :",
            len(missing_client)
        )

    df = df[
        df["client_id"].notna()
    ].copy()

    if df.empty:

        print(
            "No bank records could be mapped to gold.clients"
        )

        return pd.DataFrame()

    # ========================================================
    # DEDUPLICATE CURRENT SILVER DATA
    # ========================================================
    #
    # Business key:
    #
    #     client_id + account_number
    #
    # Different accounts for the same client are allowed.
    # ========================================================

    before = len(df)

    df = (
        df
        .drop_duplicates(
            subset=[
                "client_id",
                "account_number"
            ],
            keep="first"
        )
    )

    print(
        "Duplicate bank rows removed from current batch :",
        before - len(df)
    )

    # ========================================================
    # FINAL GOLD COLUMNS
    # ========================================================

    gold = pd.DataFrame()

    gold["client_id"] = df["client_id"]

    gold["bank_name"] = df["bank_name"]

    gold["bank_branch"] = df["bank_branch"]

    gold["bank_address"] = df["bank_address"]

    gold["account_number"] = df["account_number"]

    gold["account_type"] = df["account_type"]

    gold["bank_city"] = df["bank_city"]

    gold["pincode"] = df["pincode"]

    gold["micr"] = df["micr"]

    gold["ifsc"] = df["ifsc"]

    # --------------------------------------------------------
    # FINAL ACCOUNT NUMBER VALIDATION
    # --------------------------------------------------------
    #
    # Make absolutely sure account_number remains string.
    # --------------------------------------------------------

    gold["account_number"] = (
        gold["account_number"]
        .astype("string")
        .str.strip()
    )

    print(
        "Account numbers preserved as strings"
    )

    return gold.reset_index(
        drop=True
    )


# ============================================================
# LOAD CLIENT BANK
# ============================================================

def load_client_bank(gold_df):

    print("=" * 80)
    print("LOADING DATA INTO GOLD.CLIENT_BANK")
    print("=" * 80)

    if gold_df.empty:

        print(
            "No client bank data to insert"
        )

        return

    gold_df = gold_df.copy()

    # --------------------------------------------------------
    # ENSURE ACCOUNT NUMBER REMAINS STRING
    # --------------------------------------------------------

    gold_df["account_number"] = (
        gold_df["account_number"]
        .astype("string")
        .str.strip()
    )

    # ========================================================
    # CHECK EXISTING BANK ACCOUNTS
    # ========================================================

    print(
        "Checking existing client bank records"
    )

    existing_query = """

        SELECT

            id,

            client_id,

            seq,

            account_number,

            is_main

        FROM gold.client_bank

    """

    existing = safe_read(
        existing_query
    )

    print(
        "Existing client bank records :",
        len(existing)
    )

    # --------------------------------------------------------
    # CLEAN EXISTING ACCOUNT NUMBERS
    # --------------------------------------------------------

    if not existing.empty:

        existing["account_number"] = (
            clean_account_number(
                existing["account_number"]
            )
        )

    # ========================================================
    # REMOVE EXISTING ACCOUNTS
    # ========================================================
    #
    # Existing records are NOT updated or recreated.
    #
    # This preserves:
    #
    #   client_bank.id
    #   seq
    #
    # Existing account values are not modified.
    # ========================================================

    if not existing.empty:

        existing_keys = existing[
            [
                "client_id",
                "account_number"
            ]
        ].drop_duplicates()

        gold_df = gold_df.merge(
            existing_keys.assign(
                already_exists=True
            ),
            on=[
                "client_id",
                "account_number"
            ],
            how="left"
        )

        existing_count = (
            gold_df["already_exists"]
            .eq(True)
            .sum()
        )

        print(
            "Existing bank accounts skipped :",
            existing_count
        )

        gold_df = gold_df[
            gold_df["already_exists"]
            != True
        ].copy()

        gold_df.drop(
            columns=[
                "already_exists"
            ],
            inplace=True
        )

    # ========================================================
    # CHECK WHETHER ANYTHING IS LEFT
    # ========================================================

    if gold_df.empty:

        print(
            "No new client bank accounts to insert."
        )

        return

    # ========================================================
    # GET CURRENT MAX SEQUENCE PER CLIENT
    # ========================================================

    if existing.empty:

        max_seq = pd.DataFrame(
            columns=[
                "client_id",
                "max_seq"
            ]
        )

    else:

        existing["seq"] = pd.to_numeric(
            existing["seq"],
            errors="coerce"
        )

        max_seq = (
            existing
            .groupby("client_id")["seq"]
            .max()
            .reset_index()
        )

        max_seq.rename(
            columns={
                "seq": "max_seq"
            },
            inplace=True
        )

    gold_df = gold_df.merge(
        max_seq,
        on="client_id",
        how="left"
    )

    gold_df["max_seq"] = (
        gold_df["max_seq"]
        .fillna(0)
        .astype(int)
    )

    # ========================================================
    # ASSIGN SEQUENCE
    # ========================================================
    #
    # Existing client:
    #
    # max seq = 2
    # new accounts → 3, 4, 5...
    #
    # New client:
    #
    # first account → 1
    # ========================================================

    gold_df["new_seq"] = (
        gold_df
        .groupby(
            "client_id",
            sort=False
        )
        .cumcount()
        + 1
    )

    gold_df["seq"] = (
        gold_df["max_seq"]
        + gold_df["new_seq"]
    )

    gold_df.drop(
        columns=[
            "max_seq",
            "new_seq"
        ],
        inplace=True
    )

    # ========================================================
    # DETERMINE IS_MAIN
    # ========================================================

    existing_main_clients = set()

    if not existing.empty:

        existing_main_clients = set(
            existing.loc[
                existing["is_main"] == True,
                "client_id"
            ]
        )

    gold_df["is_main"] = False

    # --------------------------------------------------------
    # FIRST ACCOUNT FOR CLIENT = MAIN
    # --------------------------------------------------------

    for client_id, group in gold_df.groupby(
        "client_id",
        sort=False
    ):

        if client_id not in existing_main_clients:

            first_index = group.index[0]

            gold_df.loc[
                first_index,
                "is_main"
            ] = True

    # ========================================================
    # DEFAULT VALUES
    # ========================================================

    gold_df["organization_id"] = None

    gold_df["needs_review"] = False

    gold_df["is_deleted"] = False

    gold_df["deleted_at"] = None

    gold_df["created_by"] = None

    gold_df["updated_by"] = None

    now = datetime.now(
        timezone.utc
    )

    gold_df["created_at"] = now

    gold_df["updated_at"] = now

    # ========================================================
    # FINAL COLUMN ORDER
    # ========================================================
    #
    # Do NOT include:
    #
    #   id
    #   pan
    #   pan_no
    #   folio_no
    #   source
    #   flag
    #
    # PostgreSQL generates id automatically.
    # ========================================================

    final_columns = [

        "organization_id",

        "client_id",

        "seq",

        "is_main",

        "bank_name",

        "bank_branch",

        "bank_address",

        "account_number",

        "account_type",

        "bank_city",

        "pincode",

        "micr",

        "ifsc",

        "needs_review",

        "created_at",

        "updated_at",

        "is_deleted",

        "deleted_at",

        "created_by",

        "updated_by"

    ]

    gold_df = gold_df[
        final_columns
    ].copy()

    # ========================================================
    # FINAL ACCOUNT NUMBER TYPE CHECK
    # ========================================================

    gold_df["account_number"] = (
        gold_df["account_number"]
        .astype("string")
        .str.strip()
    )

    # ========================================================
    # VALIDATION
    # ========================================================

    print("=" * 80)
    print("CLIENT BANK VALIDATION")
    print("=" * 80)

    print(
        "Rows ready for insert :",
        len(gold_df)
    )

    print(
        "Unique clients :",
        gold_df["client_id"].nunique()
    )

    print(
        "Unique accounts :",
        gold_df[
            [
                "client_id",
                "account_number"
            ]
        ]
        .drop_duplicates()
        .shape[0]
    )

    print(
        "Main accounts in new rows :",
        gold_df["is_main"].sum()
    )

    print(
        "Account number dtype :",
        gold_df["account_number"].dtype
    )

    # ========================================================
    # INSERT
    # ========================================================
    #
    # DO NOT insert id.
    #
    # PostgreSQL generates the UUID.
    #
    # Existing IDs and seq values remain unchanged.
    # ========================================================

    print("=" * 80)
    print("INSERTING INTO GOLD.CLIENT_BANK")
    print("=" * 80)

    connection = engine

    try:

        gold_df.to_sql(
            "client_bank",
            connection,
            schema="gold",
            if_exists="append",
            index=False,
            chunksize=100,
            method="multi"
        )

        print(
            "Client bank records inserted :",
            len(gold_df)
        )

    except Exception as e:

        print(
            "CLIENT BANK INSERT ERROR :",
            e
        )

        traceback.print_exc(
            limit=5
        )

        raise

    print(
        "Client bank loaded successfully"
    )


# ============================================================
# MAIN ETL
# ============================================================

def run_client_bank_etl():

    print("\n")
    print("=" * 80)
    print("STARTING GOLD CLIENT BANK ETL")
    print("=" * 80)

    df = extract_client_bank()

    if df.empty:

        print(
            "No client bank data found"
        )

        return

    gold_df = transform_client_bank(
        df
    )

    if gold_df.empty:

        print(
            "No transformed client bank records"
        )

        return

    load_client_bank(
        gold_df
    )

    print("=" * 80)
    print("GOLD CLIENT BANK ETL COMPLETED")
    print("=" * 80)


# ============================================================
# DIRECT EXECUTION
# ============================================================

if __name__ == "__main__":

    run_client_bank_etl()
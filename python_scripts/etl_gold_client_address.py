import pandas as pd
import traceback

from datetime import datetime, timezone

from sqlalchemy import text

from utils.db import engine
from utils.address_key import address_key_series


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
# EXTRACT CLIENT ADDRESS DATA
# ============================================================

def extract_client_address():

    print("=" * 80)
    print("EXTRACTING DATA FOR GOLD CLIENT ADDRESS")
    print("=" * 80)

    query = """

        SELECT

            i.source,

            i.folio_no,

            i.pan_no,

            txn.txn_pan,

            sip.sip_pan,

            i.address1,
            i.address2,
            i.address3,

            i.city,
            i.state,
            i.country,
            i.pincode,

            i.mobile_no,

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

        WHERE

            (
                i.address1 IS NOT NULL
                AND TRIM(
                    CAST(i.address1 AS TEXT)
                ) <> ''
            )

            OR

            (
                i.address2 IS NOT NULL
                AND TRIM(
                    CAST(i.address2 AS TEXT)
                ) <> ''
            )

            OR

            (
                i.address3 IS NOT NULL
                AND TRIM(
                    CAST(i.address3 AS TEXT)
                ) <> ''
            )

            OR

            (
                i.city IS NOT NULL
                AND TRIM(
                    CAST(i.city AS TEXT)
                ) <> ''
            )

            OR

            (
                i.state IS NOT NULL
                AND TRIM(
                    CAST(i.state AS TEXT)
                ) <> ''
            )

            OR

            (
                i.pincode IS NOT NULL
                AND TRIM(
                    CAST(i.pincode AS TEXT)
                ) <> ''
            )

        ORDER BY i.created_at

    """

    df = safe_read(query)

    print(
        "Silver investor address rows fetched :",
        len(df)
    )

    if df.empty:

        print(
            "No address data found in silver.investor_master"
        )

        return pd.DataFrame()

    df.columns = [
        c.lower()
        for c in df.columns
    ]

    return df


# ============================================================
# TRANSFORM CLIENT ADDRESS DATA
# ============================================================

def transform_client_address(df):

    print("=" * 80)
    print("TRANSFORMING GOLD CLIENT ADDRESS")
    print("=" * 80)

    if df.empty:

        print("No data to transform")

        return pd.DataFrame()

    df = df.copy()

    # ========================================================
    # CLEAN PAN SOURCES
    # ========================================================
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
    # ========================================================

    df["pan_no"] = clean_pan(
        df["pan_no"]
    )

    df["txn_pan"] = clean_pan(
        df["txn_pan"]
    )

    df["sip_pan"] = clean_pan(
        df["sip_pan"]
    )

    # ========================================================
    # FINAL PAN
    # ========================================================

    df["pan"] = (
        df["pan_no"]
        .fillna(df["txn_pan"])
        .fillna(df["sip_pan"])
    )

    # ========================================================
    # REMOVE INVALID RECORDS
    # ========================================================

    before = len(df)

    df = df[
        df["pan"].notna()
    ].copy()

    print(
        "Rows removed due to missing PAN :",
        before - len(df)
    )

    if df.empty:

        print(
            "No valid address records after PAN cleaning"
        )

        return pd.DataFrame()

    # ========================================================
    # CLEAN ADDRESS FIELDS
    # ========================================================

    address_columns = [
        "address1",
        "address2",
        "address3",
        "city",
        "state",
        "country",
        "pincode",
        "mobile_no"
    ]

    for col in address_columns:

        df[col] = clean_string(
            df[col]
        )

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
    # PAN itself is NOT stored in gold.client_address.
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

    # ========================================================
    # REMOVE RECORDS WHERE CLIENT DOES NOT EXIST
    # ========================================================

    missing_client = df[
        df["client_id"].isna()
    ]

    if not missing_client.empty:

        print(
            "Address rows skipped because client does not exist :",
            len(missing_client)
        )

    df = df[
        df["client_id"].notna()
    ].copy()

    if df.empty:

        print(
            "No address records could be mapped to gold.clients"
        )

        return pd.DataFrame()

    # ========================================================
    # REMOVE COMPLETELY EMPTY ADDRESSES
    # ========================================================
    #
    # PAN/client may exist but the actual address can be empty.
    #
    # Such records should not be inserted into client_address.
    # ========================================================

    address_check_columns = [
        "address1",
        "address2",
        "address3",
        "city",
        "state",
        "country",
        "pincode",
        "mobile_no"
    ]

    before = len(df)

    df = df[
        df[address_check_columns]
        .notna()
        .any(axis=1)
    ].copy()

    print(
        "Completely empty address rows removed :",
        before - len(df)
    )

    if df.empty:

        print(
            "No valid address records remain"
        )

        return pd.DataFrame()

    # ========================================================
    # DEDUPLICATE CURRENT SILVER DATA
    # ========================================================
    #
    # Business key:
    #
    # client_id +
    # address1 +
    # address2 +
    # address3 +
    # city +
    # state +
    # country +
    # pincode +
    # mobile_no
    #
    # Same address for same client is inserted only once.
    # Different addresses for same client are allowed.
    # ========================================================

    # utils/address_key.py is the single definition of what makes two rows the
    # same address, and gold.client_address.address_key is GENERATED from the
    # identical expression. Keying on the raw strings -- as this did until
    # 2026-09-07 -- let one physical address survive once per RTA spelling:
    # 502 of 1186 rows were formatting variants of a row already present.
    #
    # city / state / country / pincode / mobile_no are NOT part of the key.
    # They are enriched below instead, because a field that arrives blank in
    # one feed and filled in the next would change the key and insert a
    # duplicate rather than match the row already there.
    df = df.copy()

    df["address_key"] = address_key_series(
        df,
        "address1",
        "address2",
        "address3"
    )

    before = len(df)

    df = (
        df
        .drop_duplicates(
            subset=["client_id", "address_key"],
            keep="first"
        )
    )

    print(
        "Duplicate address rows removed from current batch :",
        before - len(df)
    )

    # ========================================================
    # FINAL GOLD DATAFRAME
    # ========================================================

    gold = pd.DataFrame()

    gold["client_id"] = df["client_id"]

    # Matching key only -- dropped before the INSERT, because the column in
    # gold.client_address is GENERATED ALWAYS and rejects a supplied value.
    gold["address_key"] = df["address_key"]

    gold["address_type"] = "CURRENT"

    gold["line1"] = df["address1"]

    gold["line2"] = df["address2"]

    gold["line3"] = df["address3"]

    # No dedicated area field in investor_master.
    gold["area"] = pd.NA

    gold["city"] = df["city"]

    gold["state"] = df["state"]

    gold["country"] = df["country"]

    gold["pincode"] = df["pincode"]

    gold["mobile_no"] = df["mobile_no"]

    # No whatsapp field in investor_master.
    gold["whatsapp_no"] = pd.NA

    return gold.reset_index(
        drop=True
    )


# ============================================================
# ENRICH EXISTING CLIENT ADDRESS
# ============================================================

def enrich_client_address(rows):

    """Fill blanks on addresses gold already holds.

    An RTA feed that describes an address a second time usually differs only
    in which optional fields it bothered to populate -- one dump carries the
    pincode, another the country, a third neither. Because those fields are
    not part of address_key, all of them land on the SAME row, and this is
    where the extra values are picked up.

    COALESCE(existing, new) fills blanks ONLY. A value already recorded wins,
    so a later feed cannot quietly replace a populated field with a different
    one, and cannot blank it either: a NULL arriving from gold is "no opinion",
    never "clear it". That is the same rule the app's gold_sync applies in
    _gold_client_updates, and reversing the COALESCE arguments would make the
    newest feed authoritative instead -- a deliberate choice, not a detail.

    id, seq and is_main are untouched.
    """

    if rows is None or rows.empty:

        print("No existing client addresses to enrich")

        return 0

    statement = text(
        """
        UPDATE gold.client_address
        SET area         = COALESCE(area,         :area),
            city         = COALESCE(city,         :city),
            state        = COALESCE(state,        :state),
            country      = COALESCE(country,      :country),
            pincode      = COALESCE(pincode,      :pincode),
            mobile_no    = COALESCE(mobile_no,    :mobile_no),
            whatsapp_no  = COALESCE(whatsapp_no,  :whatsapp_no),
            updated_at   = now()
        WHERE client_id = :client_id
          AND address_key = :address_key
          AND is_deleted = false
          AND (
                (area        IS NULL AND :area        IS NOT NULL)
             OR (city        IS NULL AND :city        IS NOT NULL)
             OR (state       IS NULL AND :state       IS NOT NULL)
             OR (country     IS NULL AND :country     IS NOT NULL)
             OR (pincode     IS NULL AND :pincode     IS NOT NULL)
             OR (mobile_no   IS NULL AND :mobile_no   IS NOT NULL)
             OR (whatsapp_no IS NULL AND :whatsapp_no IS NOT NULL)
          )
        """
    )

    # The trailing predicate keeps this idempotent in the way that matters:
    # a re-run with nothing new to contribute touches no rows and leaves
    # updated_at alone, so `updated_at` stays a real signal of change for the
    # app's incremental gold_sync rather than being bumped on every run.

    fields = [
        "area", "city", "state", "country",
        "pincode", "mobile_no", "whatsapp_no"
    ]

    payload = []

    for row in rows.to_dict("records"):

        item = {
            "client_id": row["client_id"],
            "address_key": row["address_key"]
        }

        for field in fields:

            value = row.get(field)

            item[field] = (
                None
                if value is None or pd.isna(value)
                else value
            )

        payload.append(item)

    updated = 0

    try:

        with engine.begin() as connection:

            for chunk_start in range(0, len(payload), 500):

                result = connection.execute(
                    statement,
                    payload[chunk_start:chunk_start + 500]
                )

                updated += result.rowcount or 0

    except Exception as e:

        print("Client address enrichment FAILED")

        print(e)

        traceback.print_exc(limit=5)

        return 0

    print(
        "Existing client addresses enriched :",
        updated
    )

    return updated


# ============================================================
# LOAD CLIENT ADDRESS
# ============================================================

def load_client_address(gold_df):

    print("=" * 80)
    print("LOADING DATA INTO GOLD.CLIENT_ADDRESS")
    print("=" * 80)

    if gold_df.empty:

        print(
            "No client address data to insert"
        )

        return

    gold_df = gold_df.copy()

    # ========================================================
    # CHECK EXISTING ADDRESSES
    # ========================================================

    print(
        "Checking existing client address records"
    )

    existing_query = """

        SELECT

            id,

            client_id,

            seq,

            address_type,

            is_main,

            line1,
            line2,
            line3,
            area,
            city,
            state,
            country,
            pincode,
            mobile_no,
            whatsapp_no,

            address_key,

            is_deleted

        FROM gold.client_address

    """

    existing = safe_read(
        existing_query
    )

    print(
        "Existing client address records :",
        len(existing)
    )

    # ========================================================
    # REFUSE TO RUN AGAINST A PRE-MIGRATION SCHEMA
    # ========================================================
    #
    # safe_read() swallows a SQL error and returns an empty frame, so without
    # this guard a database still missing gold.client_address.address_key
    # would look like a table with no rows: every address would be classed as
    # new and re-inserted, silently restoring the duplicates this file exists
    # to remove.
    #
    # Bail out instead. load_gold() reads the False and reports the entity
    # FAILED, which is the correct outcome -- the loader and
    # sql_scripts/client_address_dedup_2026-09-07.sql have to ship together.
    # ========================================================

    if not existing.empty and "address_key" not in existing.columns:

        print(
            "gold.client_address.address_key is missing. Apply "
            "sql_scripts/client_address_dedup_2026-09-07.sql before running "
            "this loader."
        )

        return False

    if existing.empty:

        probe = safe_read(
            "SELECT COUNT(*) AS n FROM gold.client_address"
        )

        if probe.empty or int(probe["n"].iloc[0]) > 0:

            print(
                "Could not read gold.client_address (missing address_key "
                "column, or the read failed). Apply "
                "sql_scripts/client_address_dedup_2026-09-07.sql first."
            )

            return False

    # ========================================================
    # SPLIT: ENRICH WHAT EXISTS, INSERT WHAT DOES NOT
    # ========================================================
    #
    # Matching is on (client_id, address_key) -- the same key the UNIQUE index
    # uq_client_address_natural enforces, so what this code treats as one
    # address and what the database treats as one address cannot diverge.
    #
    # An address already present is ENRICHED, not skipped. Skipping was the
    # old behaviour and it threw away real data: the first feed to describe an
    # address won permanently, so a later dump supplying the pincode or the
    # country it was missing changed nothing. COALESCE(existing, new) fills
    # blanks only -- a value already recorded is never overwritten, matching
    # what etl_gold_clients.py:1490 does for ckyc_no.
    #
    # id, seq and is_main are deliberately untouched on an enrich: rows
    # elsewhere reference them.
    # ========================================================

    to_enrich = pd.DataFrame()

    if not existing.empty:

        # Live rows only. A soft-deleted address must not match -- it is
        # retired, and the UNIQUE index is partial on is_deleted = false, so
        # the same address is allowed to come back as a new row.
        existing_keys = (
            existing.loc[
                existing["is_deleted"] != True,
                ["client_id", "address_key"]
            ]
            .drop_duplicates()
            .assign(already_exists=True)
        )

        gold_df = gold_df.merge(
            existing_keys,
            on=["client_id", "address_key"],
            how="left"
        )

        matched = gold_df["already_exists"].eq(True)

        to_enrich = gold_df[matched].copy()

        print(
            "Existing client addresses to enrich :",
            len(to_enrich)
        )

        gold_df = gold_df[~matched].copy()

        gold_df.drop(
            columns=["already_exists"],
            inplace=True
        )

        to_enrich.drop(
            columns=["already_exists"],
            inplace=True,
            errors="ignore"
        )

    enrich_client_address(to_enrich)

    # ========================================================
    # CHECK WHETHER ANYTHING IS LEFT
    # ========================================================

    if gold_df.empty:

        print(
            "No new client addresses to insert."
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

        # Every row, soft-deleted included: uq_client_address_seq is
        # UNIQUE (client_id, seq) over the whole table, not partial like
        # uq_client_address_natural, so a retired row still owns its seq and
        # reusing it raises a unique violation.
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
    # new addresses → 3, 4, 5...
    #
    # New client:
    #
    # first address → 1
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
                (existing["is_main"] == True)
                & (existing["is_deleted"] != True),
                "client_id"
            ]
        )

    gold_df["is_main"] = False

    # --------------------------------------------------------
    # FIRST ADDRESS FOR CLIENT = MAIN
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

        "address_type",

        "is_main",

        "line1",

        "line2",

        "line3",

        "area",

        "city",

        "state",

        "country",

        "pincode",

        "mobile_no",

        "whatsapp_no",

        "needs_review",

        "is_deleted",

        "deleted_at",

        "created_by",

        "updated_by",

        "created_at",

        "updated_at"

    ]

    # Counted before the reduction: address_key is dropped by it, because the
    # column in gold.client_address is GENERATED ALWAYS and Postgres rejects an
    # INSERT that supplies one.
    unique_addresses = (
        gold_df[["client_id", "address_key"]]
        .drop_duplicates()
        .shape[0]
    )

    gold_df = gold_df[
        final_columns
    ].copy()

    # ========================================================
    # VALIDATION
    # ========================================================

    print("=" * 80)
    print("CLIENT ADDRESS VALIDATION")
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
        "Unique addresses :",
        unique_addresses
    )

    print(
        "Main addresses in new rows :",
        gold_df["is_main"].sum()
    )

    print(
        "Address type :",
        gold_df["address_type"].unique()
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
    print("INSERTING INTO GOLD.CLIENT_ADDRESS")
    print("=" * 80)

    connection = engine

    try:

        gold_df.to_sql(
            "client_address",
            connection,
            schema="gold",
            if_exists="append",
            index=False,
            chunksize=100,
            method="multi"
        )

        print(
            "Client address records inserted :",
            len(gold_df)
        )

    except Exception as e:

        print(
            "CLIENT ADDRESS INSERT ERROR :",
            e
        )

        traceback.print_exc(
            limit=5
        )

        raise

    print(
        "Client address loaded successfully"
    )


# ============================================================
# MAIN ETL
# ============================================================

def run_client_address_etl():

    print("\n")
    print("=" * 80)
    print("STARTING GOLD CLIENT ADDRESS ETL")
    print("=" * 80)

    df = extract_client_address()

    if df.empty:

        print(
            "No client address data found"
        )

        return

    gold_df = transform_client_address(
        df
    )

    if gold_df.empty:

        print(
            "No transformed client address records"
        )

        return

    load_client_address(
        gold_df
    )

    print("=" * 80)
    print("GOLD CLIENT ADDRESS ETL COMPLETED")
    print("=" * 80)


# ============================================================
# DIRECT EXECUTION
# ============================================================

if __name__ == "__main__":

    run_client_address_etl()
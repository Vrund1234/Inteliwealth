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

            i.guardian_pan,

            i.investor_name,

            i.dob,

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
# APPLY APPROVED ADDRESS ALIASES
# ============================================================

def read_address_aliases():

    """Merges already approved, as (client_id, alias key) -> canonical key."""

    return safe_read(
        """
        SELECT

            client_id,

            address_key_alias,

            address_key_canonical

        FROM gold.client_address_alias
        """
    )


def apply_address_aliases(df, aliases):

    """Rewrite alias address_keys to the canonical key of their merge.

    Runs BEFORE dedupe, and that ordering is the whole point. A merge cannot
    be recorded by soft-deleting the loser: silver.investor_master keeps every
    spelling forever, and load_client_address deliberately excludes deleted
    rows from existing_keys (uq_client_address_natural is partial on
    is_deleted = false), so the row would simply come back on the next run.
    Rewriting the key instead means the variant never reaches the INSERT --
    the existing drop_duplicates collapses it onto the canonical row, and the
    variant's city / state / pincode / mobile_no reach that row through
    enrich_client_address rather than being lost with it.

    Scoped per client: two people can live at one address, and a merge
    approved for one of them says nothing about the other.
    """

    if df.empty or aliases is None or aliases.empty:

        return df

    mapping = (
        aliases
        .drop_duplicates(
            subset=["client_id", "address_key_alias"]
        )
        .set_index(
            ["client_id", "address_key_alias"]
        )["address_key_canonical"]
    )

    lookup = pd.MultiIndex.from_arrays(
        [df["client_id"], df["address_key"]]
    )

    canonical = mapping.reindex(lookup).to_numpy()

    df = df.copy()

    df["address_key"] = [
        original if pd.isna(replacement) else replacement
        for replacement, original in zip(
            canonical, df["address_key"]
        )
    ]

    return df


def retire_aliased_addresses():

    """Apply approved merges to the rows already in gold.client_address.

    Idempotent: a pass with nothing to merge touches no rows and leaves
    updated_at alone, so the column stays a real signal of change for the
    app's incremental gold_sync.
    """

    live = safe_read(
        """
        SELECT id, client_id, address_key, is_main
        FROM gold.client_address
        WHERE is_deleted = false
        """
    )

    retire, promote = plan_alias_retirement(
        live,
        read_address_aliases()
    )

    if not retire and not promote:

        print("No aliased addresses to retire")

        return 0

    with engine.begin() as connection:

        # Promote before retiring, so the client is never momentarily left
        # without a main address.
        for client_id, address_key in promote:

            connection.execute(
                text(
                    """
                    UPDATE gold.client_address
                    SET is_main    = true,
                        updated_at = now()
                    WHERE client_id   = :client_id
                      AND address_key = :address_key
                      AND is_deleted  = false
                    """
                ),
                {"client_id": client_id, "address_key": address_key},
            )

        for row_id in retire:

            connection.execute(
                text(
                    """
                    UPDATE gold.client_address
                    SET is_deleted = true,
                        deleted_at = now(),
                        is_main    = false,
                        updated_at = now()
                    WHERE id = :id
                      AND is_deleted = false
                    """
                ),
                {"id": row_id},
            )

    print("Main address moved to survivor :", len(promote))
    print("Addresses retired into a merge :", len(retire))

    return len(retire)


def plan_alias_retirement(live_rows, aliases):

    """Which live address rows a merge makes redundant, and where is_main goes.

    Rewriting keys stops a variant being inserted again, but the rows that
    predate the merge are already in gold.client_address and would stay live
    forever -- the client would keep showing three addresses no matter how
    many times the pipeline ran.

    Returns (ids to soft-delete, [(client_id, canonical key) to promote]).
    The promotion matters: Aashutosh's is_main sits on the typo'd spelling
    while the survivor is a different row, and retiring the flagged row
    without moving the flag leaves the client with no main address.
    """

    empty = ([], [])

    if live_rows is None or live_rows.empty:

        return empty

    if aliases is None or aliases.empty:

        return empty

    alias_keys = set(
        zip(aliases["client_id"], aliases["address_key_alias"])
    )

    canonical_of = dict(
        zip(
            zip(aliases["client_id"], aliases["address_key_alias"]),
            aliases["address_key_canonical"],
        )
    )

    # A merge is only safe to apply once the survivor is actually here.
    # Retiring a row whose canonical is absent -- which a restore that
    # regenerates client ids can cause -- would leave a client with no
    # address at all.
    live_keys = set(
        zip(live_rows["client_id"], live_rows["address_key"])
    )

    retire = []
    promote = []

    main_is_being_retired = set()

    for row in live_rows.itertuples():

        key = (row.client_id, row.address_key)

        if key not in alias_keys:

            continue

        if (row.client_id, canonical_of[key]) not in live_keys:

            continue

        retire.append(row.id)

        if row.is_main:

            main_is_being_retired.add(
                (row.client_id, canonical_of[key])
            )

    if main_is_being_retired:

        # Only promote a survivor that is not already main: rewriting the flag
        # on every pass would bump updated_at forever, and the app's
        # incremental gold_sync reads that column to find real changes.
        already_main = {
            (row.client_id, row.address_key)
            for row in live_rows.itertuples()
            if row.is_main
        }

        promote = [
            target
            for target in sorted(main_is_being_retired)
            if target not in already_main
        ]

    return retire, promote


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
    #
    # pan_no only -- no transaction or SIP fallback.
    #
    # This is what gold.clients does, and the comment above used
    # to claim this file matched it while the fallback below said
    # otherwise. CAMS writes the GUARDIAN's PAN into the ordinary
    # transaction pan field with no flag, so falling back to it
    # resolved a minor's folio to the guardian's PAN and filed
    # the MINOR's address under the GUARDIAN's client_id.
    #
    # A folio with no PAN of its own is a minor, and is matched
    # to its client below on guardian PAN + name + date of birth
    # -- the same key gold.clients de-duplicates them on.
    # ========================================================

    df["pan"] = df["pan_no"]

    # ========================================================
    # REMOVE INVALID RECORDS
    # ========================================================

    # A missing PAN is not a reason to discard an address. A
    # minor has no PAN of their own, and dropping them here --
    # before the client mapping even runs -- is what left every
    # minor in gold.clients with no address at all. Rows that
    # genuinely resolve to no client are removed after the
    # mapping below, where the guardian-identity match has had
    # its chance.
    before = len(df)

    # The guardian PAN was added to this test when the minors
    # were found missing, but that only widened it from one
    # identity rule to two. gold.clients resolves an investor
    # THREE ways -- bronze.client_mapping_review calls them
    # PAN_EXACT (592), GUARDIAN_PAN (22) and NAME_CLUSTER (6) --
    # and a folio carrying neither PAN was still discarded here,
    # before the mapping had any chance to match it by name and
    # date of birth. That is what left six clients with no
    # address while silver held one for every one of them.
    #
    # Deciding WHICH client a row belongs to is the mapping's
    # job, and the "client does not exist" removal after it
    # drops the genuinely unresolvable, so no identity test
    # belongs here at all.
    print(
        "Rows removed before mapping :",
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

            pan,

            guardian_pan,

            full_name,

            date_of_birth

        FROM gold.clients

        -- A superseded client is not a destination. Left
        -- visible here, this ETL re-attaches the address and
        -- bank rows a merge just moved to the survivor, and the
        -- person's data ends up split across both rows again --
        -- three addresses and three accounts came straight back
        -- that way.
        WHERE superseded_by IS NULL

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

    with_pan = (
        clients[clients["pan"].notna()]
        .drop_duplicates(
            subset=["pan"],
            keep="first"
        )
    )

    # --------------------------------------------------------
    # MAP CLIENT UUID -- BY PAN
    # --------------------------------------------------------

    df = df.merge(
        with_pan[
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

    # --------------------------------------------------------
    # MAP CLIENT UUID -- MINORS, BY GUARDIAN IDENTITY
    # --------------------------------------------------------
    #
    # A minor has no PAN, so the merge above leaves client_id
    # null and every one of their addresses was dropped as
    # "client does not exist". They are matched instead on the
    # key gold.clients stores them under.
    # --------------------------------------------------------

    minor_key = [
        "guardian_pan",
        "full_name",
        "date_of_birth"
    ]

    minors = (
        clients[clients["pan"].isna()]
        .dropna(subset=["guardian_pan"])
        .drop_duplicates(
            subset=minor_key,
            keep="first"
        )
    )

    if not minors.empty:

        df["guardian_pan"] = clean_pan(
            df["guardian_pan"]
        )

        df["full_name"] = clean_string(
            df["investor_name"]
        )

        df["date_of_birth"] = (
            pd.to_datetime(
                df["dob"],
                errors="coerce",
                format="ISO8601"
            )
            .dt.date
        )

        df = df.merge(
            minors[minor_key + ["id"]],
            on=minor_key,
            how="left"
        )

        df["client_id"] = (
            df["client_id"]
            .fillna(df["id"])
        )

        df = df.drop(columns=["id"])

        print(
            "Client IDs mapped via guardian identity :",
            int(
                (
                    df["pan"].isna()
                    & df["client_id"].notna()
                ).sum()
            )
        )


    # --------------------------------------------------------
    # MAP CLIENT UUID -- PAN-LESS WITH NO GUARDIAN
    # --------------------------------------------------------
    #
    # gold.clients resolves an investor three ways, and
    # bronze.client_mapping_review names them:
    #
    #   PAN_EXACT     592
    #   GUARDIAN_PAN   22
    #   NAME_CLUSTER    6
    #
    # The two merges above are the first two rules only. Without
    # the third, the six clients carrying NEITHER a PAN nor a
    # guardian PAN can never be matched, and every address of
    # theirs was dropped as "client does not exist" -- Animesh J
    # Mehta, Pritipal Shah, Saleel Y Bhatt, Sureel Yogendra
    # Bhatt, Mina Jitendra Desai and Pavanendra Bhatt Heritage
    # Fund all had source data in silver and nothing in gold.
    #
    # load_clients() keys exactly these on the whole normalised
    # name plus date of birth, so the same key is rebuilt here.
    # norm_name is reused rather than reimplemented so the two
    # cannot drift -- which is how this rule went missing in the
    # first place: the identity logic is written out separately
    # in each of these three files.
    #
    # Built as ONE string because several of these clients have
    # no date of birth at all, and a merge across a date column
    # would then have to rely on null matching null.
    # --------------------------------------------------------

    from client_mapping import norm_name

    name_only = clients[
        clients["pan"].isna()
        &
        clients["guardian_pan"].isna()
    ].copy()

    if not name_only.empty:

        def name_dob_key(names, dobs):

            return (
                norm_name(names).fillna("")
                + "|"
                + pd.to_datetime(dobs, errors="coerce")
                    .dt.strftime("%Y-%m-%d")
                    .fillna("")
            )

        name_only["_name_dob_key"] = name_dob_key(
            name_only["full_name"],
            name_only["date_of_birth"]
        )

        name_only = name_only.drop_duplicates(
            subset=["_name_dob_key"],
            keep="first"
        )

        df["_name_dob_key"] = name_dob_key(
            df["investor_name"],
            df["dob"]
        )

        before_name_match = df["client_id"].notna().sum()

        df = df.merge(
            name_only[["_name_dob_key", "id"]],
            on="_name_dob_key",
            how="left"
        )

        df["client_id"] = (
            df["client_id"]
            .fillna(df["id"])
        )

        df = df.drop(
            columns=["id", "_name_dob_key"]
        )

        print(
            "Client IDs mapped via name + dob        :",
            int(df["client_id"].notna().sum() - before_name_match)
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

    # Merges a person already approved. Applied BEFORE the dedupe below so a
    # variant spelling collapses onto its canonical row instead of insisting
    # on a row of its own -- see apply_address_aliases.
    df = apply_address_aliases(
        df,
        read_address_aliases()
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

    # Before anything else, and regardless of whether this batch has rows:
    # merges approved since the last pass still have to be applied to the
    # rows already here.
    retire_aliased_addresses()

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

        WHERE is_deleted = FALSE

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

    # address_key was only ever needed to match the database's
    # own notion of a duplicate. It is GENERATED ALWAYS, so
    # Postgres computes it on insert and rejects any attempt to
    # supply a value for it.
    #
    # Counted HERE, while the column still exists. The count used to
    # be taken much further down, next to the final column reduction,
    # on the assumption that the reduction was what removed
    # address_key -- but this drop has already removed it by then, so
    # that line raised KeyError: ['address_key'] not in index and
    # load_client_address() died before inserting anything.
    unique_addresses = (
        gold_df[["client_id", "address_key"]]
        .drop_duplicates()
        .shape[0]
    )

    gold_df = gold_df.drop(
        columns=["address_key"]
    )

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

    # unique_addresses was counted earlier, before address_key was
    # dropped -- see the drop site above.
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
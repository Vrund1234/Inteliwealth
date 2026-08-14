import pandas as pd

from datetime import datetime, timezone

from utils.db import engine, master_engine


# ============================================================
# SAFE COLUMN HELPER
# ============================================================

def get_column(df, column, default=None):

    if column in df.columns:
        return df[column]

    return pd.Series(
        [default] * len(df),
        index=df.index
    )


# ============================================================
# CLEAN PAN
# ============================================================

def clean_pan(series):

    result = (
        series
        .astype("string")
        .str.strip()
        .str.upper()
        .str.replace(
            ".0",
            "",
            regex=False
        )
    )

    return result.where(
        result.str.len() == 10
    )


# ============================================================
# CLEAN FOLIO
# ============================================================

def clean_folio(series):

    return (
        series
        .astype("string")
        .str.strip()
        .str.upper()
        .str.replace(
            ".0",
            "",
            regex=False
        )
    )


# ============================================================
# CLEAN SCHEME CODE
#
# IMPORTANT:
# DO NOT TRUNCATE
# DO NOT SLICE
# DO NOT USE .str[:N]
# DO NOT CONVERT TO NUMERIC
#
# Only remove surrounding whitespace and normalize case.
# ============================================================

def clean_scheme_code(series):

    return (
        series
        .astype("string")
        .str.strip()
        .str.upper()
    )


# ============================================================
# FIRST VALID VALUE
# ============================================================

def first_valid_value(series):

    series = series.dropna()

    series = (
        series
        .astype("string")
        .str.strip()
        .str.upper()
    )

    series = series[
        series.ne("")
        &
        series.ne("<NA>")
        &
        series.ne("NAN")
        &
        series.ne("NONE")
    ]

    if series.empty:
        return pd.NA

    return series.iloc[0]


# ============================================================
# EXTRACT SIP DATA
# ============================================================

def extract_sip():

    print("=" * 80)
    print("EXTRACTING DATA FOR GOLD SIP")
    print("=" * 80)

    # --------------------------------------------------------
    # FIND LATEST SILVER BATCH
    # --------------------------------------------------------

    batch_query = """
        SELECT
            MAX(created_at) AS latest_batch
        FROM silver.sip_master_new
    """

    batch_df = pd.read_sql(
        batch_query,
        engine
    )

    if batch_df.empty:

        print("No Silver SIP data found.")

        return pd.DataFrame()

    latest_batch = batch_df.iloc[0]["latest_batch"]

    if pd.isna(latest_batch):

        print(
            "No valid Silver SIP batch timestamp found."
        )

        return pd.DataFrame()

    print(
        "Latest Silver SIP batch:",
        latest_batch
    )

    # --------------------------------------------------------
    # EXTRACT COMPLETE LATEST BATCH
    # --------------------------------------------------------

    query = """
        SELECT
            source,
            zone,
            branch,
            ter_location,
            inv_name,
            pan,
            folio_no,
            folio_old,
            inv_iin,
            inv_dp_id,
            inv_client_id,
            dp_inv_name,

            -- IMPORTANT:
            -- Keep scheme_code exactly as stored.
            scheme_code,

            product_code,
            scheme_name,
            plan,
            sub_arn_code,
            agent_name,
            subbroker,
            euin,
            aut_trntyp,
            payment_mode,
            periodicity,
            auto_amount,
            no_of_installments,
            period_day,
            reg_date,
            from_date,
            to_date,
            cease_date,
            pause_from_date,
            pause_to_date,
            target_scheme,
            target_scheme_code,
            target_scheme_name,
            target_plan,
            bank,
            ac_holder_name,
            ecs_account_no,
            ecsno,
            instrm_no,
            cheq_micr_no,
            umrn_code,
            ac_type,
            amc_code,
            user_code,
            package_name,
            special_product,
            subtrxndesc,
            remarks,
            top_up_frq,
            top_up_amt,
            top_up_perc,
            status,
            modify_flag,
            scheme_folio_number,
            request_ref_no,
            ft_sip_regno,
            created_at,
            updated_at
        FROM silver.sip_master_new
        WHERE created_at = %s
        ORDER BY created_at
    """

    df = pd.read_sql(
        query,
        engine,
        params=(latest_batch,)
    )

    print(
        "Rows fetched:",
        len(df)
    )

    return df


# ============================================================
# TRANSFORM GOLD SIP
# ============================================================

def transform_sip(df):

    print("=" * 80)
    print("TRANSFORMING GOLD SIP")
    print("=" * 80)

    if not isinstance(df, pd.DataFrame):

        raise TypeError(
            f"transform_sip expected DataFrame, "
            f"received {type(df).__name__}"
        )

    df = df.copy()

    original_row_count = len(df)

    print(
        "Rows entering transformation:",
        original_row_count
    )

    # ========================================================
    # NORMALIZE SOURCE / RTA
    # ========================================================

    df["rta_clean"] = (
        get_column(
            df,
            "source"
        )
        .astype("string")
        .str.strip()
        .str.upper()
    )

    # ========================================================
    # CLEAN PAN
    # ========================================================

    df["pan_clean"] = clean_pan(
        get_column(
            df,
            "pan"
        )
    )

    # ========================================================
    # CLEAN FOLIO
    # ========================================================

    df["folio_clean"] = clean_folio(
        get_column(
            df,
            "folio_no"
        )
    )

    # ========================================================
    # CLEAN SCHEME CODE
    #
    # THIS IS THE ONLY SCHEME CODE USED FOR GOLD.SIP.
    #
    # Example:
    #
    # MCOD
    # PRED
    # HAARGR
    # HACGPG
    # HAFRDM
    # HAFRDQ
    # HBFD
    #
    # None of these values are truncated.
    # ========================================================

    df["scheme_code_clean"] = clean_scheme_code(
        get_column(
            df,
            "scheme_code"
        )
    )

    # ========================================================
    # CLEAN AMC CODE
    # ========================================================

    df["amc_code_clean"] = (
        get_column(
            df,
            "amc_code"
        )
        .astype("string")
        .str.strip()
        .str.upper()
    )

    # ========================================================
    # CREATE GOLD DATAFRAME
    # ========================================================

    gold_df = pd.DataFrame(
        index=df.index
    )

    # ========================================================
    # BASIC FIELDS
    # ========================================================

    gold_df["rta"] = df["rta_clean"]

    gold_df["sip_reg_no"] = (
        get_column(
            df,
            "ft_sip_regno"
        )
        .astype("string")
        .str.strip()
    )

    gold_df["folio_number"] = df["folio_clean"]

    # ========================================================
    # GOLD SCHEME CODE
    # ========================================================

    gold_df["scheme_code"] = (
        df["scheme_code_clean"]
    )

    # ========================================================
    # SCHEME NAME
    # ========================================================

    gold_df["scheme_name"] = (
        get_column(
            df,
            "scheme_name"
        )
        .astype("string")
        .str.strip()
    )

    gold_df["amc_code"] = df["amc_code_clean"]

    # ========================================================
    # ISIN
    # ========================================================

    gold_df["isin"] = pd.Series(
        pd.NA,
        index=df.index,
        dtype="string"
    )

    # ========================================================
    # AMOUNT
    # ========================================================

    gold_df["amount"] = pd.to_numeric(
        get_column(
            df,
            "auto_amount"
        ),
        errors="coerce"
    )

    # ========================================================
    # FREQUENCY
    # ========================================================

    gold_df["frequency"] = (
        get_column(
            df,
            "periodicity"
        )
        .astype("string")
        .str.strip()
        .str.upper()
    )

    # ========================================================
    # START DATE
    # ========================================================

    gold_df["start_date"] = (
        pd.to_datetime(
            get_column(
                df,
                "from_date"
            ),
            errors="coerce"
        )
        .dt.date
    )

    # ========================================================
    # END DATE
    # ========================================================

    gold_df["end_date"] = (
        pd.to_datetime(
            get_column(
                df,
                "to_date"
            ),
            errors="coerce"
        )
        .dt.date
    )

    # ========================================================
    # NEXT DUE DATE
    # ========================================================

    gold_df["next_due_date"] = pd.Series(
        pd.NaT,
        index=df.index
    ).dt.date

    # ========================================================
    # SIP DAY
    # ========================================================

    gold_df["sip_day"] = pd.to_numeric(
        get_column(
            df,
            "period_day"
        ),
        errors="coerce"
    )

    # ========================================================
    # MANDATE ID
    # ========================================================

    gold_df["mandate_id"] = (
        get_column(
            df,
            "umrn_code"
        )
        .astype("string")
        .str.strip()
    )

    # ========================================================
    # STATUS
    # ========================================================

    gold_df["status"] = (
        get_column(
            df,
            "status"
        )
        .astype("string")
        .str.strip()
        .str.upper()
    )

    # ========================================================
    # REGISTERED DATE
    # ========================================================

    gold_df["registered_date"] = (
        pd.to_datetime(
            get_column(
                df,
                "reg_date"
            ),
            errors="coerce"
        )
        .dt.date
    )

    # ========================================================
    # CEASED DATE
    # ========================================================

    gold_df["ceased_date"] = (
        pd.to_datetime(
            get_column(
                df,
                "cease_date"
            ),
            errors="coerce"
        )
        .dt.date
    )

    # ========================================================
    # SCHEME ID
    #
    # REQUIRED LOGIC:
    #
    # gold.sip.scheme_code
    #
    #       =
    #
    # silver.sip_master_new.scheme_code
    #
    #       ↓
    #
    # silver.sip_master_new.scheme_id
    #
    # IMPORTANT:
    # product_code IS NOT USED.
    # gold.scheme IS NOT USED.
    # ========================================================

    print()
    print("=" * 80)
    print("MAPPING SCHEME IDs")
    print("=" * 80)

    print(
        "Gold SIP rows:",
        len(gold_df)
    )

    print(
        "Gold SIP scheme_code non-null:",
        gold_df["scheme_code"].notna().sum()
    )

    # --------------------------------------------------------
    # LOAD scheme_code + scheme_id FROM SILVER
    # --------------------------------------------------------

    silver_scheme = pd.read_sql(
        """
        SELECT
            scheme_code,
            scheme_id
        FROM silver.sip_master_new
        WHERE scheme_code IS NOT NULL
        """,
        engine
    )

    print(
        "Silver rows with scheme_code:",
        len(silver_scheme)
    )

    # --------------------------------------------------------
    # CLEAN SILVER SCHEME CODE
    #
    # ONLY strip + uppercase.
    # NO TRUNCATION.
    # --------------------------------------------------------

    silver_scheme["scheme_code_clean"] = (
        clean_scheme_code(
            silver_scheme["scheme_code"]
        )
    )

    # --------------------------------------------------------
    # KEEP scheme_id AS STRING
    # --------------------------------------------------------

    silver_scheme["scheme_id_clean"] = (
        silver_scheme["scheme_id"]
        .astype("string")
        .str.strip()
    )

    # --------------------------------------------------------
    # REMOVE ONLY INVALID MAPPING ENTRIES
    #
    # THIS DOES NOT REMOVE GOLD SIP ROWS.
    # --------------------------------------------------------

    silver_scheme = silver_scheme[
        silver_scheme["scheme_code_clean"].notna()
        &
        silver_scheme["scheme_code_clean"].ne("")
        &
        silver_scheme["scheme_code_clean"].ne("<NA>")
    ].copy()

    # --------------------------------------------------------
    # SHOW THE EXACT CODES USER MENTIONED
    # --------------------------------------------------------

    debug_codes = [
        "MCOD",
        "PRED",
        "HAARGR",
        "HACGPG",
        "HAFRDM",
        "HAFRDQ",
        "HBFD"
    ]

    print()
    print("Checking important scheme codes in Silver:")

    for code in debug_codes:

        matches = silver_scheme[
            silver_scheme["scheme_code_clean"] == code
        ]

        if matches.empty:

            print(
                f"  {code} -> NOT FOUND"
            )

        else:

            print(
                f"  {code} -> "
                f"{matches['scheme_id_clean'].tolist()}"
            )

    # --------------------------------------------------------
    # HANDLE DUPLICATE SCHEME CODES
    #
    # Prefer a non-null scheme_id.
    # --------------------------------------------------------

    silver_scheme["_has_scheme_id"] = (
        silver_scheme["scheme_id_clean"].notna()
        &
        silver_scheme["scheme_id_clean"].ne("")
        &
        silver_scheme["scheme_id_clean"].ne("<NA>")
    )

    silver_scheme = (
        silver_scheme
        .sort_values(
            by=[
                "scheme_code_clean",
                "_has_scheme_id"
            ],
            ascending=[
                True,
                False
            ]
        )
        .drop_duplicates(
            subset=[
                "scheme_code_clean"
            ],
            keep="first"
        )
        .copy()
    )

    # --------------------------------------------------------
    # CREATE LOOKUP
    #
    # scheme_code -> scheme_id
    # --------------------------------------------------------

    scheme_id_lookup = dict(
        zip(
            silver_scheme["scheme_code_clean"],
            silver_scheme["scheme_id_clean"]
        )
    )

    # --------------------------------------------------------
    # MAP SCHEME ID
    #
    # IMPORTANT:
    # .map() NEVER DROPS ROWS.
    # If there is no match, scheme_id becomes NULL.
    # --------------------------------------------------------

    gold_df["scheme_id"] = (
        gold_df["scheme_code"]
        .map(scheme_id_lookup)
        .astype("string")
    )

    # ========================================================
    # SCHEME MAPPING VALIDATION
    # ========================================================

    mapped_scheme_count = int(
        gold_df["scheme_id"].notna().sum()
    )

    missing_scheme_count = int(
        gold_df["scheme_id"].isna().sum()
    )

    print()
    print(
        "Mapped Scheme IDs:",
        mapped_scheme_count
    )

    print(
        "Missing Scheme IDs:",
        missing_scheme_count
    )

    # --------------------------------------------------------
    # VERIFY IMPORTANT CODES AFTER MAPPING
    # --------------------------------------------------------

    print()
    print("Gold scheme_code -> scheme_id mapping:")

    for code in debug_codes:

        matched_rows = gold_df[
            gold_df["scheme_code"] == code
        ]

        if matched_rows.empty:

            print(
                f"  {code} -> no Gold SIP row"
            )

        else:

            print(
                f"  {code} -> "
                f"{matched_rows['scheme_id'].drop_duplicates().tolist()}"
            )

    # --------------------------------------------------------
    # SHOW UNMATCHED CODES
    #
    # ONLY FOR DEBUGGING.
    # NO ROWS ARE DROPPED.
    # --------------------------------------------------------

    if missing_scheme_count > 0:

        unmatched_codes = (
            gold_df.loc[
                gold_df["scheme_id"].isna(),
                "scheme_code"
            ]
            .dropna()
            .astype("string")
            .drop_duplicates()
            .tolist()
        )

        print()
        print(
            "Unmatched scheme codes:",
            len(unmatched_codes)
        )

        print(
            unmatched_codes[:50]
        )

    # ========================================================
    # AMC ID
    # ========================================================

    print()
    print(
        "Loading AMC mapping..."
    )

    amc_master = pd.read_sql(
        """
        SELECT
            id,
            amc_code
        FROM public.amc
        WHERE amc_code IS NOT NULL
        """,
        master_engine
    )

    amc_master["amc_code_clean"] = (
        amc_master["amc_code"]
        .astype("string")
        .str.strip()
        .str.upper()
    )

    amc_master = (
        amc_master[
            [
                "id",
                "amc_code_clean"
            ]
        ]
        .dropna(
            subset=[
                "amc_code_clean"
            ]
        )
        .drop_duplicates(
            subset=[
                "amc_code_clean"
            ]
        )
    )

    amc_lookup = dict(
        zip(
            amc_master["amc_code_clean"],
            amc_master["id"]
        )
    )

    gold_df["amc_id"] = (
        df["amc_code_clean"]
        .map(amc_lookup)
    )

    # ========================================================
    # CLIENT ID
    # ========================================================

    print(
        "Loading client mapping..."
    )

    clients = pd.read_sql(
        """
        SELECT
            user_id,
            pan
        FROM gold.clients
        WHERE pan IS NOT NULL
        """,
        engine
    )

    clients["pan_clean"] = clean_pan(
        clients["pan"]
    )

    clients = (
        clients[
            [
                "user_id",
                "pan_clean"
            ]
        ]
        .dropna(
            subset=[
                "pan_clean"
            ]
        )
        .drop_duplicates(
            subset=[
                "pan_clean"
            ]
        )
    )

    client_lookup = dict(
        zip(
            clients["pan_clean"],
            clients["user_id"]
        )
    )

    gold_df["client_id"] = (
        df["pan_clean"]
        .map(client_lookup)
    )

    # ========================================================
    # SIP TYPE
    # ========================================================

    aut_trntyp_clean = (
        get_column(
            df,
            "aut_trntyp"
        )
        .astype("string")
        .str.strip()
        .str.upper()
    )

    gold_df["sip_type"] = (
        aut_trntyp_clean
        .map(
            {
                "SIP": "SIP",
                "S": "SIP",
                "STP": "STP",
                "SO": "STP",
                "SI": "STP",
                "SWP": "SWP",
                "WO": "SWP"
            }
        )
    )

    gold_df.loc[
        aut_trntyp_clean.notna()
        &
        gold_df["sip_type"].isna(),
        "sip_type"
    ] = "OTHER"

    # ========================================================
    # REGISTERED INSTALLMENTS
    # ========================================================

    gold_df["registered_installments"] = (
        pd.to_numeric(
            get_column(
                df,
                "no_of_installments"
            ),
            errors="coerce"
        )
    )

    # ========================================================
    # TRANSACTIONS
    # ========================================================

    print()
    print(
        "Loading transaction data..."
    )

    transactions = pd.read_sql(
        """
        SELECT
            source,
            folio_no,
            prodcode,
            trxntype,
            trxnstat,
            trxnmode,
            trxnsubtyp,
            trxn_nature,
            siptrxnno,
            sipregslno,
            remarks,
            brokcode,
            src_brk_code
        FROM silver.transaction_master_new
        """,
        engine
    )

    gold_df["completed_installments"] = 0

    gold_df["bounced_installments"] = 0

    gold_df["arn"] = pd.Series(
        pd.NA,
        index=df.index,
        dtype="string"
    )

    gold_df["sub_arn"] = pd.Series(
        pd.NA,
        index=df.index,
        dtype="string"
    )

    # ========================================================
    # PROCESS TRANSACTIONS
    # ========================================================

    if not transactions.empty:

        transactions["rta_clean"] = (
            transactions["source"]
            .astype("string")
            .str.strip()
            .str.upper()
        )

        transactions["folio_clean"] = clean_folio(
            transactions["folio_no"]
        )

        transactions["scheme_code_clean"] = (
            transactions["prodcode"]
            .astype("string")
            .str.strip()
            .str.upper()
        )

        transactions["brokcode_clean"] = (
            transactions["brokcode"]
            .astype("string")
            .str.strip()
            .str.upper()
            .replace(
                "",
                pd.NA
            )
        )

        transactions["src_brk_code_clean"] = (
            transactions["src_brk_code"]
            .astype("string")
            .str.strip()
            .str.upper()
            .replace(
                "",
                pd.NA
            )
        )

        # ----------------------------------------------------
        # ARN / SUB ARN
        # ----------------------------------------------------

        arn_mapping = (
            transactions
            .groupby(
                [
                    "rta_clean",
                    "folio_clean"
                ],
                dropna=False
            )
            .agg(
                {
                    "brokcode_clean":
                        first_valid_value,

                    "src_brk_code_clean":
                        first_valid_value
                }
            )
            .reset_index()
        )

        arn_mapping = arn_mapping.rename(
            columns={
                "brokcode_clean": "arn",
                "src_brk_code_clean": "sub_arn"
            }
        )

        arn_lookup = (
            arn_mapping
            .set_index(
                [
                    "rta_clean",
                    "folio_clean"
                ]
            )
        )

        gold_df["arn"] = [
            arn_lookup.loc[
                (
                    df.loc[idx, "rta_clean"],
                    df.loc[idx, "folio_clean"]
                ),
                "arn"
            ]
            if (
                df.loc[idx, "rta_clean"],
                df.loc[idx, "folio_clean"]
            ) in arn_lookup.index
            else pd.NA
            for idx in df.index
        ]

        gold_df["sub_arn"] = [
            arn_lookup.loc[
                (
                    df.loc[idx, "rta_clean"],
                    df.loc[idx, "folio_clean"]
                ),
                "sub_arn"
            ]
            if (
                df.loc[idx, "rta_clean"],
                df.loc[idx, "folio_clean"]
            ) in arn_lookup.index
            else pd.NA
            for idx in df.index
        ]

        gold_df["arn"] = (
            gold_df["arn"]
            .astype("string")
            .str.strip()
            .str.upper()
        )

        gold_df["sub_arn"] = (
            gold_df["sub_arn"]
            .astype("string")
            .str.strip()
            .str.upper()
        )

        print(
            "Mapped ARN rows:",
            gold_df["arn"].notna().sum()
        )

        print(
            "Mapped Sub ARN rows:",
            gold_df["sub_arn"].notna().sum()
        )

        # ----------------------------------------------------
        # TRANSACTION TEXT
        # ----------------------------------------------------

        transaction_text = (
            transactions["trxntype"]
            .fillna("")
            .astype("string")
            .str.upper()
            + " "
            +
            transactions["trxnstat"]
            .fillna("")
            .astype("string")
            .str.upper()
            + " "
            +
            transactions["trxnmode"]
            .fillna("")
            .astype("string")
            .str.upper()
            + " "
            +
            transactions["trxnsubtyp"]
            .fillna("")
            .astype("string")
            .str.upper()
            + " "
            +
            transactions["trxn_nature"]
            .fillna("")
            .astype("string")
            .str.upper()
            + " "
            +
            transactions["remarks"]
            .fillna("")
            .astype("string")
            .str.upper()
        )

        # ----------------------------------------------------
        # SIP TRANSACTION IDENTIFICATION
        # ----------------------------------------------------

        sip_number_mask = (
            transactions["siptrxnno"]
            .notna()
            &
            transactions["siptrxnno"]
            .astype("string")
            .str.strip()
            .ne("")
        )

        sip_sequence_mask = (
            transactions["sipregslno"]
            .notna()
            &
            transactions["sipregslno"]
            .astype("string")
            .str.strip()
            .ne("")
        )

        sip_text_mask = (
            transaction_text
            .str.contains(
                "SIP",
                regex=False,
                na=False
            )
        )

        sip_mask = (
            sip_number_mask
            |
            sip_sequence_mask
            |
            sip_text_mask
        )

        # ----------------------------------------------------
        # BOUNCED / FAILED / REJECTED
        # ----------------------------------------------------

        bounced_mask = (
            transaction_text
            .str.contains(
                "BOUNCE|BOUNCED|FAILED|FAILURE|REJECT|REJECTED",
                regex=True,
                na=False
            )
        )

        # ----------------------------------------------------
        # COMPLETED
        # ----------------------------------------------------

        completed_mask = (
            sip_mask
            &
            ~bounced_mask
        )

        # ----------------------------------------------------
        # COMPLETED LOOKUP
        # ----------------------------------------------------

        completed = (
            transactions.loc[
                completed_mask
            ]
            .groupby(
                [
                    "rta_clean",
                    "folio_clean",
                    "scheme_code_clean"
                ],
                dropna=False
            )
            .size()
            .reset_index(
                name="completed_installments"
            )
        )

        completed_lookup = (
            completed
            .set_index(
                [
                    "rta_clean",
                    "folio_clean",
                    "scheme_code_clean"
                ]
            )["completed_installments"]
        )

        # ----------------------------------------------------
        # BOUNCED LOOKUP
        # ----------------------------------------------------

        bounced = (
            transactions.loc[
                sip_mask & bounced_mask
            ]
            .groupby(
                [
                    "rta_clean",
                    "folio_clean",
                    "scheme_code_clean"
                ],
                dropna=False
            )
            .size()
            .reset_index(
                name="bounced_installments"
            )
        )

        bounced_lookup = (
            bounced
            .set_index(
                [
                    "rta_clean",
                    "folio_clean",
                    "scheme_code_clean"
                ]
            )["bounced_installments"]
        )

        # ----------------------------------------------------
        # APPLY COMPLETED COUNTS
        # ----------------------------------------------------

        gold_df["completed_installments"] = [
            completed_lookup.get(
                (
                    df.loc[idx, "rta_clean"],
                    df.loc[idx, "folio_clean"],
                    df.loc[idx, "scheme_code_clean"]
                ),
                0
            )
            for idx in df.index
        ]

        # ----------------------------------------------------
        # APPLY BOUNCED COUNTS
        # ----------------------------------------------------

        gold_df["bounced_installments"] = [
            bounced_lookup.get(
                (
                    df.loc[idx, "rta_clean"],
                    df.loc[idx, "folio_clean"],
                    df.loc[idx, "scheme_code_clean"]
                ),
                0
            )
            for idx in df.index
        ]

    # ========================================================
    # CLEAN COUNTS
    # ========================================================

    gold_df["completed_installments"] = (
        pd.to_numeric(
            gold_df["completed_installments"],
            errors="coerce"
        )
        .fillna(0)
        .astype(int)
    )

    gold_df["bounced_installments"] = (
        pd.to_numeric(
            gold_df["bounced_installments"],
            errors="coerce"
        )
        .fillna(0)
        .astype(int)
    )

    # ========================================================
    # ARN ID
    # ========================================================

    sub_arn_code = (
        get_column(
            df,
            "sub_arn_code"
        )
        .astype("string")
        .str.strip()
        .replace(
            "",
            pd.NA
        )
    )

    subbroker = (
        get_column(
            df,
            "subbroker"
        )
        .astype("string")
        .str.strip()
        .replace(
            "",
            pd.NA
        )
    )

    df["arn_code_clean"] = (
        sub_arn_code
        .fillna(subbroker)
        .astype("string")
        .str.strip()
        .str.upper()
    )

    print(
        "Loading ARN mapping..."
    )

    arn_master = pd.read_sql(
        """
        SELECT
            id,
            arn_code
        FROM public.arn
        WHERE arn_code IS NOT NULL
        """,
        master_engine
    )

    arn_master["arn_code_clean"] = (
        arn_master["arn_code"]
        .astype("string")
        .str.strip()
        .str.upper()
    )

    arn_master = (
        arn_master[
            [
                "id",
                "arn_code_clean"
            ]
        ]
        .dropna(
            subset=[
                "arn_code_clean"
            ]
        )
        .drop_duplicates(
            subset=[
                "arn_code_clean"
            ]
        )
    )

    arn_lookup = dict(
        zip(
            arn_master["arn_code_clean"],
            arn_master["id"]
        )
    )

    gold_df["arn_id"] = (
        df["arn_code_clean"]
        .map(arn_lookup)
    )

    # ========================================================
    # CEASED REASON
    # ========================================================

    remarks_clean = (
        get_column(
            df,
            "remarks"
        )
        .astype("string")
        .str.strip()
    )

    status_clean = (
        get_column(
            df,
            "status"
        )
        .astype("string")
        .str.strip()
        .str.upper()
    )

    ceased_reason_value = (
        remarks_clean
        .replace(
            "",
            pd.NA
        )
        .fillna(
            status_clean
        )
    )

    ceased_condition = (
        gold_df["ceased_date"].notna()
        |
        status_clean.isin(
            [
                "CEASED",
                "CANCELLED",
                "EXPIRED"
            ]
        )
    )

    gold_df["ceased_reason"] = pd.Series(
        pd.NA,
        index=gold_df.index,
        dtype="string"
    )

    gold_df.loc[
        ceased_condition,
        "ceased_reason"
    ] = ceased_reason_value.loc[
        ceased_condition
    ]

    gold_df["ceased_reason"] = (
        gold_df["ceased_reason"]
        .astype("string")
        .str.strip()
        .replace(
            "",
            pd.NA
        )
    )

    # ========================================================
    # GOLD CREATED AT
    # ========================================================

    gold_load_timestamp = datetime.now(
        timezone.utc
    )

    gold_df["created_at"] = gold_load_timestamp

    # ========================================================
    # FINAL COLUMN ORDER
    # ========================================================

    columns = [

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
        "arn",
        "sub_arn",
        "created_at"

    ]

    gold_df = gold_df[
        columns
    ].copy()

    # ========================================================
    # CRITICAL ROW COUNT CHECK
    # ========================================================

    final_row_count = len(gold_df)

    print()
    print("=" * 80)
    print("ROW COUNT CHECK")
    print("=" * 80)

    print(
        "Rows before transformation:",
        original_row_count
    )

    print(
        "Rows after transformation:",
        final_row_count
    )

    if final_row_count != original_row_count:

        raise ValueError(
            f"ROW LOSS DETECTED! "
            f"Input rows = {original_row_count}, "
            f"Output rows = {final_row_count}. "
            f"No rows are allowed to be dropped."
        )

    print(
        "Row count check: PASSED"
    )

    # ========================================================
    # VALIDATION
    # ========================================================

    print()
    print("=" * 80)
    print("GOLD SIP VALIDATION")
    print("=" * 80)

    print(
        "Total Gold SIP rows:",
        len(gold_df)
    )

    print(
        "Missing Scheme IDs:",
        gold_df["scheme_id"].isna().sum()
    )

    print(
        "Missing AMC IDs:",
        gold_df["amc_id"].isna().sum()
    )

    print(
        "Missing Client IDs:",
        gold_df["client_id"].isna().sum()
    )

    print(
        "Missing ARN IDs:",
        gold_df["arn_id"].isna().sum()
    )

    print(
        "Missing ARN values:",
        gold_df["arn"].isna().sum()
    )

    print(
        "Missing Sub ARN values:",
        gold_df["sub_arn"].isna().sum()
    )

    print(
        "Completed Installments:",
        gold_df["completed_installments"].sum()
    )

    print(
        "Bounced Installments:",
        gold_df["bounced_installments"].sum()
    )

    return gold_df


# ============================================================
# GET GOLD.SIP COLUMN LIMITS
# ============================================================

def get_gold_sip_column_limits():

    query = """
        SELECT
            column_name,
            data_type,
            character_maximum_length
        FROM information_schema.columns
        WHERE table_schema = 'gold'
          AND table_name = 'sip'
        ORDER BY ordinal_position
    """

    return pd.read_sql(
        query,
        engine
    )


# ============================================================
# VALIDATE STRING LENGTHS
# ============================================================

def validate_string_lengths(gold_df):

    print()
    print("=" * 80)
    print("VALIDATING GOLD.SIP STRING LENGTHS")
    print("=" * 80)

    schema_df = get_gold_sip_column_limits()

    varchar_columns = schema_df[
        schema_df["character_maximum_length"].notna()
    ].copy()

    problems = []

    for _, row in varchar_columns.iterrows():

        column = row["column_name"]

        if column not in gold_df.columns:
            continue

        limit = int(
            row["character_maximum_length"]
        )

        lengths = (
            gold_df[column]
            .astype("string")
            .str.len()
        )

        offending_mask = (
            lengths > limit
        )

        offending_count = int(
            offending_mask.sum()
        )

        if offending_count > 0:

            max_length = int(
                lengths.max()
            )

            problems.append(
                {
                    "column": column,
                    "limit": limit,
                    "max_length_found": max_length,
                    "offending_rows": offending_count
                }
            )

    if problems:

        print()
        print(
            "STRING LENGTH ERRORS FOUND"
        )

        print(
            "-" * 80
        )

        for problem in problems:

            print(
                f"Column: {problem['column']}"
            )

            print(
                f"PostgreSQL limit: "
                f"{problem['limit']}"
            )

            print(
                f"Maximum length found: "
                f"{problem['max_length_found']}"
            )

            print(
                f"Offending rows: "
                f"{problem['offending_rows']}"
            )

            print(
                "-" * 80
            )

        raise ValueError(
            "Gold SIP contains values exceeding "
            "PostgreSQL VARCHAR limits. "
            "No rows were inserted."
        )

    print(
        "String length validation: PASSED"
    )

    return True


# ============================================================
# LOAD GOLD.SIP
# ============================================================

def load_sip(gold_df):

    print()
    print("=" * 80)
    print("LOADING DATA INTO GOLD.SIP")
    print("=" * 80)

    if not isinstance(
        gold_df,
        pd.DataFrame
    ):

        raise TypeError(
            f"load_sip expected DataFrame, "
            f"received {type(gold_df).__name__}"
        )

    print(
        "Rows received:",
        len(gold_df)
    )

    if gold_df.empty:

        print(
            "No SIP rows received."
        )

        return True

    # ========================================================
    # GOLD.SIP COLUMNS
    # ========================================================

    gold_columns = [

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
        "arn",
        "sub_arn",
        "created_at"

    ]

    # ========================================================
    # CHECK COLUMNS
    # ========================================================

    missing_columns = [
        col
        for col in gold_columns
        if col not in gold_df.columns
    ]

    if missing_columns:

        raise ValueError(
            "Missing Gold SIP columns: "
            + ", ".join(missing_columns)
        )

    gold_df = gold_df[
        gold_columns
    ].copy()

    # ========================================================
    # KEEP ROW COUNT
    # ========================================================

    rows_before_load_processing = len(gold_df)

    # ========================================================
    # VALIDATE STRING LENGTHS
    # ========================================================

    validate_string_lengths(
        gold_df
    )

    # ========================================================
    # REMOVE EXACT DUPLICATES INSIDE CURRENT BATCH
    #
    # NOTE:
    # This removes only exact duplicate records.
    # It does NOT remove rows because scheme_id is NULL.
    # ========================================================

    compare_columns = [
        col
        for col in gold_columns
        if col != "created_at"
    ]

    before_batch_dedup = len(gold_df)

    gold_df = (
        gold_df
        .drop_duplicates(
            subset=compare_columns,
            keep="first"
        )
        .copy()
    )

    removed_batch_duplicates = (
        before_batch_dedup
        - len(gold_df)
    )

    print(
        "Exact duplicate rows inside current batch removed:",
        removed_batch_duplicates
    )

    print(
        "Rows after batch deduplication:",
        len(gold_df)
    )

    # ========================================================
    # IMPORTANT ROW LOSS CHECK
    # ========================================================
    #
    # If your source has duplicate rows, exact duplicate
    # removal can reduce the count. This is intentional.
    #
    # But scheme mapping itself must NEVER reduce the count.
    # ========================================================

    # ========================================================
    # LOAD EXISTING GOLD SIP DATA
    # ========================================================

    print(
        "Checking existing Gold SIP records..."
    )

    existing_query = """

        SELECT

            rta,
            sip_reg_no,
            folio_number,
            scheme_code,
            scheme_name,
            amc_code,
            isin,
            amount,
            frequency,
            start_date,
            end_date,
            next_due_date,
            sip_day,
            mandate_id,
            status,
            registered_date,
            ceased_date,
            scheme_id,
            amc_id,
            client_id,
            sip_type,
            registered_installments,
            completed_installments,
            bounced_installments,
            ceased_reason,
            arn_id,
            arn,
            sub_arn

        FROM gold.sip

    """

    existing = pd.read_sql(
        existing_query,
        engine
    )

    print(
        "Existing Gold SIP rows:",
        len(existing)
    )

    # ========================================================
    # NORMALIZE VALUES FOR COMPARISON
    # ========================================================

    def normalize_for_compare(dataframe):

        result = dataframe.copy()

        for column in result.columns:

            if (
                pd.api.types.is_object_dtype(
                    result[column]
                )
                or
                pd.api.types.is_string_dtype(
                    result[column]
                )
            ):

                result[column] = (
                    result[column]
                    .astype("string")
                    .str.strip()
                    .str.upper()
                )

        return result

    new_compare = normalize_for_compare(
        gold_df[compare_columns]
    )

    if not existing.empty:

        existing_compare = normalize_for_compare(
            existing[compare_columns]
        )

        # ----------------------------------------------------
        # CREATE ROW SIGNATURE
        # ----------------------------------------------------

        new_signature = (
            new_compare
            .fillna("<NULL>")
            .astype("string")
            .agg(
                "||".join,
                axis=1
            )
        )

        existing_signature = (
            existing_compare
            .fillna("<NULL>")
            .astype("string")
            .agg(
                "||".join,
                axis=1
            )
        )

        existing_signatures = set(
            existing_signature
        )

        already_exists_mask = (
            new_signature.isin(
                existing_signatures
            )
        )

        already_exists_count = int(
            already_exists_mask.sum()
        )

        print(
            "Rows already present in Gold:",
            already_exists_count
        )

        gold_df = gold_df.loc[
            ~already_exists_mask
        ].copy()

    else:

        print(
            "Gold SIP is currently empty."
        )

    # ========================================================
    # FINAL ROWS TO INSERT
    # ========================================================

    print(
        "Rows to insert:",
        len(gold_df)
    )

    if gold_df.empty:

        print()
        print(
            "No new SIP records to insert."
        )

        print(
            "Gold SIP is already up to date."
        )

        return True

    # ========================================================
    # CONVERT PANDAS NULLS TO DATABASE NULL
    # ========================================================

    gold_df = gold_df.astype(object)

    gold_df = gold_df.where(
        pd.notna(gold_df),
        None
    )

    # ========================================================
    # INSERT
    # ========================================================

    try:

        with engine.begin() as connection:

            gold_df.to_sql(
                name="sip",
                con=connection,
                schema="gold",
                if_exists="append",
                index=False,
                method="multi",
                chunksize=1000
            )

        # ====================================================
        # VERIFY
        # ====================================================

        verification = pd.read_sql(
            """
            SELECT
                COUNT(*) AS total_rows
            FROM gold.sip
            """,
            engine
        )

        total_rows = int(
            verification.iloc[0]["total_rows"]
        )

        print()
        print(
            "Inserted rows:",
            len(gold_df)
        )

        print(
            "Gold SIP rows after load:",
            total_rows
        )

        print()
        print(
            "GOLD SIP LOAD SUCCESSFUL"
        )

        return True

    except Exception as e:

        print()
        print("=" * 80)
        print("GOLD SIP LOAD FAILED")
        print("=" * 80)

        print(
            "Error type:",
            type(e).__name__
        )

        print(
            "Error:",
            str(e).splitlines()[0]
        )

        return False


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    print()
    print("=" * 80)
    print("STARTING GOLD SIP ETL")
    print("=" * 80)

    try:

        # ====================================================
        # EXTRACT
        # ====================================================

        df = extract_sip()

        if not isinstance(
            df,
            pd.DataFrame
        ):

            raise TypeError(
                "extract_sip() did not "
                "return a DataFrame"
            )

        if df.empty:

            print()
            print(
                "No Silver SIP batch available."
            )

            print(
                "GOLD SIP ETL STOPPED"
            )

        else:

            # =================================================
            # TRANSFORM
            # =================================================

            gold_df = transform_sip(
                df
            )

            if not isinstance(
                gold_df,
                pd.DataFrame
            ):

                raise TypeError(
                    "transform_sip() did not "
                    "return a DataFrame"
                )

            # =================================================
            # LOAD
            # =================================================

            success = load_sip(
                gold_df
            )

            # =================================================
            # FINAL STATUS
            # =================================================

            print()

            if success:

                print(
                    "=" * 80
                )

                print(
                    "GOLD SIP ETL COMPLETED SUCCESSFULLY"
                )

                print(
                    "=" * 80
                )

            else:

                print(
                    "=" * 80
                )

                print(
                    "GOLD SIP ETL FAILED"
                )

                print(
                    "=" * 80
                )

    except Exception as e:

        print()
        print("=" * 80)
        print("GOLD SIP ETL FAILED")
        print("=" * 80)

        print(
            "Error type:",
            type(e).__name__
        )

        print(
            "Error:",
            str(e).splitlines()[0]
        )

        print(
            "No success message will be printed."
        )
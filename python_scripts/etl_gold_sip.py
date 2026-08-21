import pandas as pd
import traceback

from datetime import datetime, timezone

from utils.db import engine, master_engine
from utils.gold_result import load_result
from utils.upsert import upsert_dataframe


# ============================================================
# SAFE READ - PROJECT DATABASE
# ============================================================

def safe_read(query, params=None):

    try:
        return pd.read_sql(
            query,
            engine,
            params=params
        )

    except Exception as e:
        print("SQL ERROR:", e)
        return pd.DataFrame()


# ============================================================
# SAFE READ - MASTER DATABASE
#
# IMPORTANT:
# Any table under the PUBLIC schema that belongs to the
# master database must be read using master_engine.
# ============================================================

def safe_master_read(query, params=None):

    try:
        return pd.read_sql(
            query,
            master_engine,
            params=params
        )

    except Exception as e:
        print("MASTER SQL ERROR:", e)
        return pd.DataFrame()


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
# GET LAST GOLD TIMESTAMP
# ============================================================

def get_last_gold_timestamp():

    print("=" * 80)
    print("CHECKING LAST GOLD SIP TIMESTAMP")
    print("=" * 80)

    query = """
        SELECT
            MAX(created_at) AS last_created_at
        FROM gold.sip
    """

    result = safe_read(query)

    if result.empty:
        print("Gold SIP table returned no result.")
        return None

    last_created_at = result.iloc[0]["last_created_at"]

    if pd.isna(last_created_at):
        print("Gold SIP table is empty.")
        return None

    print(
        "Last Gold SIP created_at:",
        last_created_at
    )

    return last_created_at


# ============================================================
# EXTRACT SILVER SIP
# ============================================================

def extract_sip():

    print("=" * 80)
    print("EXTRACTING SILVER SIP")
    print("=" * 80)

    last_gold_timestamp = get_last_gold_timestamp()

    # ========================================================
    # FIRST RUN
    # ========================================================

    if last_gold_timestamp is None:

        print()
        print("No previous Gold SIP timestamp found.")
        print("This is treated as the first Gold SIP load.")

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

                scheme_id,

                created_at,
                updated_at,

                flag

            FROM silver.sip_master_new

            ORDER BY created_at
        """

        df = safe_read(query)

    # ========================================================
    # SUBSEQUENT RUN
    # ========================================================

    else:

        print()
        print(
            "Loading only Silver SIP rows newer than:"
        )
        print(last_gold_timestamp)

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

                scheme_id,

                created_at,
                updated_at,

                flag

            FROM silver.sip_master_new

            WHERE created_at > %s

            ORDER BY created_at
        """

        df = safe_read(
            query,
            params=(last_gold_timestamp,)
        )

    # ========================================================
    # RESULT
    # ========================================================

    if df.empty:

        print()
        print(
            "No new Silver SIP records found "
            "after timestamp comparison."
        )

        return df

    print()
    print("Rows fetched:", len(df))
    print(
        "Minimum Silver created_at:",
        df["created_at"].min()
    )
    print(
        "Maximum Silver created_at:",
        df["created_at"].max()
    )

    # ========================================================
    # FLAG CHECK
    # ========================================================

    if "flag" in df.columns:

        print()
        print("Silver flag values in extracted rows:")
        print(
            df["flag"].value_counts(
                dropna=False
            )
        )

        print(
            "IMPORTANT: Silver flag is NOT modified."
        )

    # ========================================================
    # SCHEME ID CHECK
    # ========================================================

    print()
    print("=" * 80)
    print("SILVER SCHEME ID CHECK")
    print("=" * 80)

    print(
        "Scheme ID datatype:",
        df["scheme_id"].dtype
    )

    print(
        "Scheme IDs present:",
        df["scheme_id"].notna().sum()
    )

    print(
        "Scheme IDs missing:",
        df["scheme_id"].isna().sum()
    )

    print("Sample Silver scheme IDs:")

    print(
        df["scheme_id"]
        .dropna()
        .head(20)
        .tolist()
    )

    return df


# ============================================================
# EXTRACT PENDING SIP RETRY CANDIDATES
#
# Rows in gold.sip whose enrichment (arn/client_id) is still marked
# pending, re-matched back to their original silver.sip_master_new row via
# the SAME natural key load_sip()'s ON CONFLICT clause already uses as
# this row's identity — (rta, folio_number, scheme_code, registered_date,
# amount). Returns rows in extract_sip()'s exact column shape so they can
# be fed straight into transform_sip()/load_sip() unchanged.
# ============================================================

def extract_pending_sip_retry_candidates(limit=200, max_age_days=30):

    print("=" * 80)
    print("EXTRACTING PENDING SIP RETRY CANDIDATES")
    print("=" * 80)

    # The inner query's DISTINCT ON collapses the join down to at most one
    # silver row per gold.sip pending row BEFORE this ever reaches
    # load_sip(). Without it, multiple silver rows genuinely colliding on
    # the same natural key (rta, folio_number, scheme_code, registered_date,
    # amount) — confirmed to exist live in silver.sip_master_new — would
    # all join to the same gold.sip row and be handed to load_sip()'s
    # single-statement ON CONFLICT upsert as a duplicate-key batch, raising
    # CardinalityViolation and silently failing the retry (load_sip()
    # catches and swallows that error), leaving the row pending forever
    # instead of reconciling it. DISTINCT ON requires its ORDER BY to start
    # with its own key columns, so it can't also lead with
    # enrichment_pending_since for oldest-pending-first prioritization —
    # the outer query re-sorts the already-deduped rows by
    # enrichment_pending_since ASC before applying LIMIT, preserving that
    # original prioritization.
    query = """
        SELECT candidate.* FROM (
            SELECT DISTINCT ON (g.rta, g.folio_number, g.scheme_code, g.registered_date, g.amount)

                s.source,
                s.zone,
                s.branch,
                s.ter_location,
                s.inv_name,
                s.pan,
                s.folio_no,
                s.folio_old,
                s.inv_iin,
                s.inv_dp_id,
                s.inv_client_id,
                s.dp_inv_name,

                s.scheme_code,

                s.product_code,
                s.scheme_name,
                s.plan,
                s.sub_arn_code,
                s.agent_name,
                s.subbroker,
                s.euin,
                s.aut_trntyp,
                s.payment_mode,
                s.periodicity,
                s.auto_amount,
                s.no_of_installments,
                s.period_day,
                s.reg_date,
                s.from_date,
                s.to_date,
                s.cease_date,
                s.pause_from_date,
                s.pause_to_date,
                s.target_scheme,
                s.target_scheme_code,
                s.target_scheme_name,
                s.target_plan,
                s.bank,
                s.ac_holder_name,
                s.ecs_account_no,
                s.ecsno,
                s.instrm_no,
                s.cheq_micr_no,
                s.umrn_code,
                s.ac_type,
                s.amc_code,
                s.user_code,
                s.package_name,
                s.special_product,
                s.subtrxndesc,
                s.remarks,
                s.top_up_frq,
                s.top_up_amt,
                s.top_up_perc,
                s.status,
                s.modify_flag,
                s.scheme_folio_number,
                s.request_ref_no,
                s.ft_sip_regno,

                s.scheme_id,

                s.created_at,
                s.updated_at,

                s.flag,

                g.enrichment_pending_since AS _enrichment_pending_since

            FROM silver.sip_master_new s

            JOIN gold.sip g
                ON UPPER(TRIM(s.source)) = g.rta
               -- Mirrors clean_folio() exactly (etl_gold_sip.py:93): strip,
               -- upper, then strip ALL occurrences of the literal ".0"
               -- substring (str.replace(".0", "", regex=False) in pandas) —
               -- NOT just a trailing ".0". A regex anchored to the end (\\.0$)
               -- would silently mismatch any folio with an embedded ".0".
               AND REPLACE(UPPER(TRIM(CAST(s.folio_no AS TEXT))), '.0', '') = g.folio_number
               -- Mirrors clean_scheme_code() exactly (etl_gold_sip.py:112):
               -- strip + upper on scheme_code alone, no product_code fallback
               -- (transform_sip() never falls back to product_code at the gold
               -- stage — that fallback only exists in the SILVER scheme_id
               -- join, a different purpose).
               AND UPPER(TRIM(CAST(s.scheme_code AS TEXT))) = g.scheme_code
               AND s.reg_date = g.registered_date
               AND s.auto_amount = g.amount

            WHERE g.enrichment_pending_since IS NOT NULL
              AND g.enrichment_pending_since > now() - make_interval(days => %(max_age_days)s)

            ORDER BY g.rta, g.folio_number, g.scheme_code, g.registered_date, g.amount,
                     s.created_at DESC
        ) candidate

        ORDER BY candidate._enrichment_pending_since ASC

        LIMIT %(limit)s
    """

    df = safe_read(query, params={"max_age_days": max_age_days, "limit": limit})

    # Ordering-only helper column, not part of extract_sip()'s column shape.
    df = df.drop(columns=["_enrichment_pending_since"], errors="ignore")

    print()
    print("Retry candidates found:", len(df))

    return df


# ============================================================
# RECONCILE PENDING SIP
#
# Runs the EXACT same transform_sip()/load_sip() path as a normal load,
# just fed the bounded retry-candidate batch instead of extract_sip()'s
# cursor-based selection. Rows whose enrichment still can't be resolved
# simply get re-stamped with a fresh (or unchanged) enrichment_pending_since
# by transform_sip() itself and get retried again next run, until either
# they resolve or age past max_age_days and drop out of the candidate set.
# ============================================================

def reconcile_pending_sip(limit=200, max_age_days=30):

    print("=" * 80)
    print("RECONCILING PENDING SIP ENRICHMENT")
    print("=" * 80)

    df = extract_pending_sip_retry_candidates(limit=limit, max_age_days=max_age_days)

    if df.empty:
        print("No pending SIP rows to reconcile.")
        return load_result("skipped", 0)

    gold_df = transform_sip(df)

    if gold_df.empty:
        return load_result("skipped", 0)

    return load_sip(gold_df)


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
        get_column(df, "source")
        .astype("string")
        .str.strip()
        .str.upper()
    )

    # ========================================================
    # CLEAN PAN
    # ========================================================

    df["pan_clean"] = clean_pan(
        get_column(df, "pan")
    )

    # ========================================================
    # CLEAN FOLIO
    # ========================================================

    df["folio_clean"] = clean_folio(
        get_column(df, "folio_no")
    )

    # ========================================================
    # CLEAN SCHEME CODE
    # ========================================================

    df["scheme_code_clean"] = clean_scheme_code(
        get_column(df, "scheme_code")
    )

    # ========================================================
    # CLEAN AMC CODE
    # ========================================================

    df["amc_code_clean"] = (
        get_column(df, "amc_code")
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
        get_column(df, "ft_sip_regno")
        .astype("string")
        .str.strip()
    )

    gold_df["folio_number"] = df["folio_clean"]

    # ========================================================
    # SCHEME CODE
    # ========================================================

    gold_df["scheme_code"] = (
        df["scheme_code_clean"]
    )

    # ========================================================
    # SCHEME NAME
    # ========================================================

    gold_df["scheme_name"] = (
        get_column(df, "scheme_name")
        .astype("string")
        .str.strip()
    )

    # ========================================================
    # AMC CODE
    # ========================================================

    gold_df["amc_code"] = (
        df["amc_code_clean"]
    )

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
        get_column(df, "auto_amount"),
        errors="coerce"
    )

    # ========================================================
    # FREQUENCY
    # ========================================================

    gold_df["frequency"] = (
        get_column(df, "periodicity")
        .astype("string")
        .str.strip()
        .str.upper()
    )

    # ========================================================
    # START DATE
    # ========================================================

    gold_df["start_date"] = (
        pd.to_datetime(
            get_column(df, "from_date"),
            errors="coerce"
        )
        .dt.date
    )

    # ========================================================
    # END DATE
    # ========================================================

    gold_df["end_date"] = (
        pd.to_datetime(
            get_column(df, "to_date"),
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
        get_column(df, "period_day"),
        errors="coerce"
    )

    # ========================================================
    # MANDATE ID
    # ========================================================

    gold_df["mandate_id"] = (
        get_column(df, "umrn_code")
        .astype("string")
        .str.strip()
    )

    # ========================================================
    # STATUS
    # ========================================================

    gold_df["status"] = (
        get_column(df, "status")
        .astype("string")
        .str.strip()
        .str.upper()
    )

    # ========================================================
    # REGISTERED DATE
    # ========================================================

    gold_df["registered_date"] = (
        pd.to_datetime(
            get_column(df, "reg_date"),
            errors="coerce"
        )
        .dt.date
    )

    # ========================================================
    # CEASED DATE
    # ========================================================

    gold_df["ceased_date"] = (
        pd.to_datetime(
            get_column(df, "cease_date"),
            errors="coerce"
        )
        .dt.date
    )

    # ========================================================
    # SCHEME ID
    #
    # Take scheme_id directly from Silver.
    # ========================================================

    print()
    print("=" * 80)
    print("MAPPING SCHEME ID FROM SILVER")
    print("=" * 80)

    gold_df["scheme_id"] = (
        get_column(df, "scheme_id")
        .astype("string")
        .str.strip()
        .replace("", pd.NA)
    )

    print(
        "Silver scheme_id rows:",
        df["scheme_id"].notna().sum()
    )

    print(
        "Gold scheme_id rows:",
        gold_df["scheme_id"].notna().sum()
    )

    print(
        "Missing Gold scheme_id:",
        gold_df["scheme_id"].isna().sum()
    )

    print("Sample Gold scheme IDs:")

    print(
        gold_df["scheme_id"]
        .dropna()
        .head(20)
        .tolist()
    )

    # ========================================================
    # AMC ID
    #
    # IMPORTANT:
    # public.amc belongs to the MASTER DATABASE.
    # Therefore master_engine MUST be used.
    # ========================================================

    print()
    print("Loading AMC mapping from MASTER database...")

    amc_master = safe_master_read(
        """
        SELECT
            id,
            amc_code
        FROM public.amc
        WHERE amc_code IS NOT NULL
        """
    )

    if not amc_master.empty:

        amc_master["amc_code_clean"] = (
            amc_master["amc_code"]
            .astype("string")
            .str.strip()
            .str.upper()
        )

        amc_lookup = dict(
            zip(
                amc_master["amc_code_clean"],
                amc_master["id"]
            )
        )

    else:
        amc_lookup = {}

    gold_df["amc_id"] = (
        df["amc_code_clean"]
        .map(amc_lookup)
    )

    # ========================================================
    # CLIENT ID
    #
    # gold.clients belongs to the PROJECT DATABASE.
    # Therefore engine is intentionally used here.
    # ========================================================

    print("Loading client mapping...")

    clients = safe_read(
        """
        SELECT
            user_id,
            pan
        FROM gold.clients
        WHERE pan IS NOT NULL
        """
    )

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

    gold_df["client_id"] = (
        df["pan_clean"]
        .map(client_lookup)
    )

    # ========================================================
    # ENRICHMENT PENDING TRACKING — client match
    #
    # A PAN not present in gold.clients at all means WBR9 (or its
    # downstream gold.clients row) hasn't arrived yet, NOT that this client
    # will never exist. Track separately from client_id itself so a later
    # reconciliation pass can tell "not yet resolved" apart from "resolved
    # to nothing".
    # ========================================================

    client_match_found = df["pan_clean"].isin(client_lookup.keys())

    # ========================================================
    # SIP TYPE
    # ========================================================

    aut_trntyp_clean = (
        get_column(df, "aut_trntyp")
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
            get_column(df, "no_of_installments"),
            errors="coerce"
        )
    )

    # ========================================================
    # LOAD TRANSACTION DATA
    # ========================================================

    print()
    print("Loading transaction data...")

    transactions = safe_read(
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
        """
    )

    # ========================================================
    # DEFAULT VALUES
    # ========================================================

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

    txn_match_found = pd.Series(False, index=df.index)  # no transaction table at all yet

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
            .replace("", pd.NA)
        )

        transactions["src_brk_code_clean"] = (
            transactions["src_brk_code"]
            .astype("string")
            .str.strip()
            .str.upper()
            .replace("", pd.NA)
        )

        # ====================================================
        # ARN / SUB ARN MAPPING
        # ====================================================

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

        # ====================================================
        # ENRICHMENT PENDING TRACKING — transaction match
        #
        # A (rta, folio) not present in arn_lookup.index at all means no
        # transaction data exists yet for this folio (WBR2 hasn't arrived),
        # NOT that this SIP is structurally distributor-less. A folio THAT
        # IS present but with a blank brokcode (a genuine direct-plan
        # investment) is correctly resolved, not pending.
        # ====================================================

        txn_match_found = pd.Series(
            [
                (df.loc[idx, "rta_clean"], df.loc[idx, "folio_clean"]) in arn_lookup.index
                for idx in df.index
            ],
            index=df.index,
        )

        # ====================================================
        # TRANSACTION TEXT
        # ====================================================

        transaction_text = (
            transactions["trxntype"]
            .fillna("")
            .astype("string")
            .str.upper()
            + " "
            + transactions["trxnstat"]
            .fillna("")
            .astype("string")
            .str.upper()
            + " "
            + transactions["trxnmode"]
            .fillna("")
            .astype("string")
            .str.upper()
            + " "
            + transactions["trxnsubtyp"]
            .fillna("")
            .astype("string")
            .str.upper()
            + " "
            + transactions["trxn_nature"]
            .fillna("")
            .astype("string")
            .str.upper()
            + " "
            + transactions["remarks"]
            .fillna("")
            .astype("string")
            .str.upper()
        )

        # ====================================================
        # SIP TRANSACTION IDENTIFICATION
        # ====================================================

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

        # ====================================================
        # BOUNCED / FAILED / REJECTED
        # ====================================================

        bounced_mask = (
            transaction_text
            .str.contains(
                "BOUNCE|BOUNCED|FAILED|FAILURE|REJECT|REJECTED",
                regex=True,
                na=False
            )
        )

        # ====================================================
        # COMPLETED
        # ====================================================

        completed_mask = (
            sip_mask
            &
            ~bounced_mask
        )

        # ====================================================
        # COMPLETED LOOKUP
        # ====================================================

        completed = (
            transactions.loc[completed_mask]
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

        # ====================================================
        # BOUNCED LOOKUP
        # ====================================================

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

        # ====================================================
        # APPLY COMPLETED COUNTS
        # ====================================================

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

        # ====================================================
        # APPLY BOUNCED COUNTS
        # ====================================================

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
    #
    # IMPORTANT:
    # public.arn belongs to the MASTER DATABASE.
    # Therefore master_engine MUST be used.
    # ========================================================

    sub_arn_code = (
        get_column(df, "sub_arn_code")
        .astype("string")
        .str.strip()
        .replace("", pd.NA)
    )

    subbroker = (
        get_column(df, "subbroker")
        .astype("string")
        .str.strip()
        .replace("", pd.NA)
    )

    df["arn_code_clean"] = (
        sub_arn_code
        .fillna(subbroker)
        .astype("string")
        .str.strip()
        .str.upper()
    )

    print("Loading ARN mapping from MASTER database...")

    arn_master = safe_master_read(
        """
        SELECT
            id,
            arn_code
        FROM public.arn
        WHERE arn_code IS NOT NULL
        """
    )

    if not arn_master.empty:

        arn_master["arn_code_clean"] = (
            arn_master["arn_code"]
            .astype("string")
            .str.strip()
            .str.upper()
        )

        arn_lookup = dict(
            zip(
                arn_master["arn_code_clean"],
                arn_master["id"]
            )
        )

    else:
        arn_lookup = {}

    gold_df["arn_id"] = (
        df["arn_code_clean"]
        .map(arn_lookup)
    )

    # ========================================================
    # CEASED REASON
    # ========================================================

    remarks_clean = (
        get_column(df, "remarks")
        .astype("string")
        .str.strip()
    )

    status_clean = (
        get_column(df, "status")
        .astype("string")
        .str.strip()
        .str.upper()
    )

    ceased_reason_value = (
        remarks_clean
        .replace("", pd.NA)
        .fillna(status_clean)
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
        .replace("", pd.NA)
    )

    # ========================================================
    # ENRICHMENT PENDING SINCE
    #
    # Set when at least one of the transaction match or client match was not
    # found — i.e. at least one enrichment source is still pending, not just
    # "matched but the field happens to be blank" (e.g. a real direct-plan
    # investment with no ARN). NULL = fully resolved or structurally complete.
    # ========================================================

    gold_load_timestamp_for_pending = datetime.now(timezone.utc)

    enrichment_missing = (~txn_match_found) | (~client_match_found)

    # Initialized with an explicit tz-aware dtype (rather than a bare
    # `pd.NaT` scalar assign) so the later partial `.loc` assignment of a
    # tz-aware timestamp below doesn't hit pandas' strict same-dtype
    # upcasting check.
    gold_df["enrichment_pending_since"] = pd.Series(
        pd.NaT,
        index=df.index,
        dtype="datetime64[ns, UTC]"
    )
    gold_df.loc[enrichment_missing, "enrichment_pending_since"] = gold_load_timestamp_for_pending

    # ========================================================
    # GOLD CREATED AT
    # ========================================================

    gold_load_timestamp = datetime.now(
        timezone.utc
    )

    gold_df["created_at"] = (
        gold_load_timestamp
    )

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
        "created_at",
        "enrichment_pending_since"
    ]

    gold_df = gold_df[columns].copy()

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

    print("Row count check: PASSED")

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
#
# gold.sip is in the PROJECT DATABASE.
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

    return safe_read(query)


# ============================================================
# VALIDATE STRING LENGTHS
# ============================================================

def validate_string_lengths(gold_df):

    print()
    print("=" * 80)
    print("VALIDATING GOLD.SIP STRING LENGTHS")
    print("=" * 80)

    schema_df = get_gold_sip_column_limits()

    if schema_df.empty:

        print("Could not read Gold SIP schema.")

        raise ValueError(
            "Unable to validate Gold SIP column lengths."
        )

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
        print("STRING LENGTH ERRORS FOUND")
        print("-" * 80)

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

            print("-" * 80)

        raise ValueError(
            "Gold SIP contains values exceeding "
            "PostgreSQL VARCHAR limits. "
            "No rows were inserted."
        )

    print("String length validation: PASSED")

    return True


# ============================================================
# LOAD GOLD.SIP
# ============================================================

def load_sip(gold_df):

    print()
    print("=" * 80)
    print("LOADING DATA INTO GOLD.SIP")
    print("=" * 80)

    if not isinstance(gold_df, pd.DataFrame):

        raise TypeError(
            f"load_sip expected DataFrame, "
            f"received {type(gold_df).__name__}"
        )

    print(
        "Rows received:",
        len(gold_df)
    )

    if gold_df.empty:

        print("No SIP rows received.")

        return load_result("skipped", 0)

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
        "created_at",
        "enrichment_pending_since"
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
    # ROW COUNT BEFORE LOAD
    # ========================================================

    rows_to_insert = len(gold_df)

    print(
        "Rows received (pre-dedup):",
        rows_to_insert
    )

    # ========================================================
    # VALIDATE STRING LENGTHS
    # ========================================================

    validate_string_lengths(
        gold_df
    )

    # ========================================================
    # DEDUP ON NATURAL KEY BEFORE UPSERT
    #
    # Postgres's single-statement multi-row ON CONFLICT ... DO UPDATE
    # cannot affect the same conflicting row twice within one statement
    # (CardinalityViolation) -- confirmed live in production. Multiple
    # silver rows can legitimately collapse to the same gold natural key
    # (rta, folio_number, scheme_code, registered_date, amount); keep only
    # one per key. extract_sip() orders its query by silver's created_at
    # ASC, and transform_sip() preserves that row order into gold_df, so
    # keep="last" here keeps the most-recently-created SOURCE row per key
    # -- do NOT sort by gold_df's own "created_at" column first, since
    # every row in a single transform_sip() call shares one identical
    # batch timestamp and can't discriminate between duplicates at all.
    # ========================================================

    before_dedup = len(gold_df)

    gold_df = gold_df.drop_duplicates(
        subset=["rta", "folio_number", "scheme_code", "registered_date", "amount"],
        keep="last",
    ).reset_index(drop=True)

    deduped_count = before_dedup - len(gold_df)

    if deduped_count:
        print(
            f"Deduplicated {deduped_count} row(s) sharing the same natural key "
            f"before upsert (kept the most recently created source row per key)"
        )

    print(
        "Rows to insert (post-dedup):",
        len(gold_df)
    )

    # ========================================================
    # DUPLICATE LOGIC
    # ========================================================

    print()
    print("Duplicate filtering: DISABLED")
    print("Existing Gold comparison: DISABLED")
    print("Row signature comparison: DISABLED")
    print("Timestamp-based incremental loading: ENABLED")
    print("Silver flag update: DISABLED")

    # ========================================================
    # CONVERT PANDAS NULLS TO DATABASE NULL
    # ========================================================

    gold_df = gold_df.astype(object)

    gold_df = gold_df.where(
        pd.notna(gold_df),
        None
    )

    # ========================================================
    # UPSERT
    # ========================================================

    conflict_target_sql = (
        "((rta IS NULL), (COALESCE(rta, '')), "
        "(folio_number IS NULL), (COALESCE(folio_number, '')), "
        "(scheme_code IS NULL), (COALESCE(scheme_code, '')), "
        "(registered_date IS NULL), (COALESCE(registered_date, '0001-01-01'::date)), "
        "(amount IS NULL), (COALESCE(amount, 0)))"
    )
    update_set_sql = (
        "status = EXCLUDED.status, ceased_date = EXCLUDED.ceased_date, "
        "ceased_reason = EXCLUDED.ceased_reason, "
        "completed_installments = EXCLUDED.completed_installments, "
        "bounced_installments = EXCLUDED.bounced_installments, "
        "next_due_date = EXCLUDED.next_due_date, mandate_id = EXCLUDED.mandate_id, "
        "sip_day = EXCLUDED.sip_day, sip_type = EXCLUDED.sip_type, "
        "registered_installments = EXCLUDED.registered_installments, "
        "client_id = EXCLUDED.client_id, amc_id = EXCLUDED.amc_id, "
        "scheme_id = EXCLUDED.scheme_id, arn_id = EXCLUDED.arn_id, "
        "arn = EXCLUDED.arn, sub_arn = EXCLUDED.sub_arn, isin = EXCLUDED.isin, "
        "scheme_name = EXCLUDED.scheme_name, amc_code = EXCLUDED.amc_code, "
        "frequency = EXCLUDED.frequency, start_date = EXCLUDED.start_date, "
        "end_date = EXCLUDED.end_date, "
        "enrichment_pending_since = EXCLUDED.enrichment_pending_since"
    )

    try:
        affected = upsert_dataframe(
            engine, gold_df, schema="gold", table="sip",
            conflict_target_sql=conflict_target_sql, update_set_sql=update_set_sql,
        )

        verification = safe_read("SELECT COUNT(*) AS total_rows FROM gold.sip")
        total_rows = int(verification.iloc[0]["total_rows"]) if not verification.empty else -1

        print()
        print("Upserted rows:", affected)
        print("Gold SIP rows after load:", total_rows)
        print()
        print("GOLD SIP LOAD SUCCESSFUL")

        return load_result("ok", affected)

    except Exception as e:

        print()
        print("=" * 80)
        print("GOLD SIP LOAD FAILED")
        print("=" * 80)

        traceback.print_exc(
            limit=10
        )

        return load_result("error", 0, str(e))


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

        if not isinstance(df, pd.DataFrame):

            raise TypeError(
                "extract_sip() did not "
                "return a DataFrame"
            )

        if df.empty:

            print()
            print("No new SIP records found.")

            print(
                "GOLD SIP ETL COMPLETED - "
                "NOTHING NEW TO LOAD"
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
            # GOLD DATA SAMPLE
            # =================================================

            print()
            print("=" * 80)
            print("GOLD SIP DATA SAMPLE")
            print("=" * 80)

            print(
                gold_df[
                    [
                        "rta",
                        "sip_reg_no",
                        "folio_number",
                        "scheme_id",
                        "scheme_code",
                        "created_at"
                    ]
                ].head(20)
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

            if success["status"] != "error":

                print("=" * 80)
                print(
                    "GOLD SIP ETL COMPLETED SUCCESSFULLY"
                )
                print("=" * 80)

            else:

                print("=" * 80)
                print("GOLD SIP ETL FAILED")
                print("=" * 80)

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

        print("=" * 80)
import pandas as pd
import uuid
import numpy as np

from datetime import datetime, timezone

from utils.db import engine, master_engine


# ============================================================
# EXTRACT GOLD HOLDINGS DATA
# ============================================================

def extract_holdings():

    print("=" * 80)
    print("EXTRACTING DATA FOR GOLD HOLDINGS")
    print("=" * 80)

    query = """

        WITH transaction_scheme AS
        (
            SELECT
                source,
                pan,
                folio_no,
                brokcode,
                scheme_id,
                traddate,

                CASE
                    WHEN pan IS NOT NULL
                         AND TRIM(pan) <> ''
                    THEN
                        UPPER(TRIM(pan))

                    ELSE
                        COALESCE(
                            NULLIF(TRIM(folio_no), ''),
                            NULLIF(TRIM(brokcode), '')
                        )
                END AS mapping_key,

                ROW_NUMBER() OVER
                (
                    PARTITION BY
                        source,

                        CASE
                            WHEN pan IS NOT NULL
                                 AND TRIM(pan) <> ''
                            THEN
                                UPPER(TRIM(pan))

                            ELSE
                                COALESCE(
                                    NULLIF(TRIM(folio_no), ''),
                                    NULLIF(TRIM(brokcode), '')
                                )
                        END

                    ORDER BY
                        traddate DESC NULLS LAST
                ) AS rn

            FROM silver.transaction_master_new

            WHERE scheme_id IS NOT NULL
              AND TRIM(scheme_id::text) <> ''
        ),

        investor_base AS
        (
            SELECT DISTINCT ON
            (
                source,
                folio_no
            )

                source,
                folio_no,

                holding_nature,

                nominee1_name,
                nominee1_relation,
                nominee1_percentage,

                bank_name,
                bank_account_no,

                demat_flag,
                ckyc_no

            FROM silver.investor_master

            ORDER BY
                source,
                folio_no
        )

        SELECT
            t.*,

            ts.scheme_id AS mapped_scheme_id,

            i.holding_nature
                AS investor_holding_nature,

            i.nominee1_name
                AS investor_nominee_name,

            i.nominee1_relation
                AS investor_nominee_relation,

            i.nominee1_percentage
                AS investor_nominee_percentage,

            i.bank_name
                AS investor_bank_name,

            i.bank_account_no
                AS investor_bank_account_no,

            i.demat_flag
                AS investor_demat_flag,

            i.ckyc_no
                AS investor_ckyc_no

        FROM silver.transaction_master_new t

        LEFT JOIN transaction_scheme ts

            ON ts.source = t.source

            AND ts.rn = 1

            AND
            (
                /* =================================================
                   PRIMARY MAPPING:
                   PAN
                   ================================================= */

                (
                    t.pan IS NOT NULL
                    AND TRIM(t.pan) <> ''

                    AND ts.pan IS NOT NULL
                    AND TRIM(ts.pan) <> ''

                    AND UPPER(TRIM(t.pan))
                        =
                        UPPER(TRIM(ts.pan))
                )

                OR

                /* =================================================
                   FALLBACK MAPPING:
                   PAN NULL
                   COALESCE(FOLIO, ARN)
                   ================================================= */

                (
                    (
                        t.pan IS NULL
                        OR TRIM(t.pan) = ''
                    )

                    AND

                    COALESCE(
                        NULLIF(TRIM(t.folio_no), ''),
                        NULLIF(TRIM(t.brokcode), '')
                    )
                    =
                    COALESCE(
                        NULLIF(TRIM(ts.folio_no), ''),
                        NULLIF(TRIM(ts.brokcode), '')
                    )
                )
            )

        LEFT JOIN investor_base i

            ON t.source = i.source
            AND t.folio_no = i.folio_no
    """

    df = pd.read_sql(
        query,
        engine
    )

    # ========================================================
    # FORCE SCHEME ID TO STRING
    # ========================================================

    if "mapped_scheme_id" in df.columns:

        df["mapped_scheme_id"] = (
            df["mapped_scheme_id"]
            .astype("string")
            .str.strip()
        )

        df.loc[
            df["mapped_scheme_id"]
            .isin(
                [
                    "",
                    "nan",
                    "None",
                    "NULL",
                    "<NA>"
                ]
            ),
            "mapped_scheme_id"
        ] = pd.NA

    print()
    print("Extraction Completed")
    print("-" * 80)

    print(
        f"Transactions extracted: "
        f"{len(df):,}"
    )

    print(
        f"Columns fetched: "
        f"{len(df.columns):,}"
    )

    if "mapped_scheme_id" in df.columns:

        print(
            f"Scheme IDs mapped: "
            f"{df['mapped_scheme_id'].notna().sum():,}"
        )

        print(
            f"Scheme IDs missing: "
            f"{df['mapped_scheme_id'].isna().sum():,}"
        )

        print()
        print("Sample mapped scheme IDs:")
        print(
            df.loc[
                df["mapped_scheme_id"].notna(),
                "mapped_scheme_id"
            ]
            .drop_duplicates()
            .head(20)
            .tolist()
        )

    return df


# ============================================================
# SAFE COLUMN HELPER
# ============================================================

def get_column(
    df,
    column,
    default=None
):

    if column in df.columns:

        return df[column]

    return pd.Series(
        [default] * len(df),
        index=df.index
    )


# ============================================================
# CLEAN STRING
# ============================================================

def clean_string(series):

    return (
        series
        .astype("string")
        .str.strip()
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

    result = result.where(
        result.str.len() == 10
    )

    return result


# ============================================================
# CLEAN FOLIO
# ============================================================

def clean_folio(series):

    return (
        series
        .astype("string")
        .str.strip()
        .str.replace(
            ".0",
            "",
            regex=False
        )
    )


# ============================================================
# CLEAN SCHEME ID
# ============================================================

def clean_scheme_id(series):

    result = (
        series
        .astype("string")
        .str.strip()
        .str.upper()
    )

    result = result.replace(
        {
            "": pd.NA,
            "NAN": pd.NA,
            "NONE": pd.NA,
            "NULL": pd.NA,
            "<NA>": pd.NA
        }
    )

    return result


# ============================================================
# PURCHASE MASK
# ============================================================

def purchase_mask(df):

    txn_type = (
        get_column(
            df,
            "trxntype"
        )
        .fillna("")
        .astype("string")
        .str.strip()
        .str.upper()
    )

    txn_nature = (
        get_column(
            df,
            "trxn_nature"
        )
        .fillna("")
        .astype("string")
        .str.strip()
        .str.upper()
    )

    return (
        txn_type.str.contains(
            "PURCHASE|BUY|SIP",
            regex=True,
            na=False
        )
        |
        txn_nature.str.contains(
            "PURCHASE|BUY|SIP",
            regex=True,
            na=False
        )
    )


# ============================================================
# SIGNED TRANSACTION AMOUNT
# ============================================================

def signed_transaction_amount(df):

    amount = pd.to_numeric(
        get_column(
            df,
            "amount"
        ),
        errors="coerce"
    ).fillna(0)

    txn_type = (
        get_column(
            df,
            "trxntype"
        )
        .fillna("")
        .astype("string")
        .str.strip()
        .str.upper()
    )

    txn_nature = (
        get_column(
            df,
            "trxn_nature"
        )
        .fillna("")
        .astype("string")
        .str.strip()
        .str.upper()
    )

    transaction_text = (
        txn_type
        + " "
        + txn_nature
    )

    negative_mask = transaction_text.str.contains(
        "REDEEM|REDEMPTION|SELL|SWITCH.?OUT|"
        "WITHDRAW|REVERSAL|REV",
        regex=True,
        na=False
    )

    positive_mask = transaction_text.str.contains(
        "PURCHASE|BUY|SIP|SWITCH.?IN|"
        "DIVIDEND.?REINVEST|REINVEST",
        regex=True,
        na=False
    )

    signed_amount = amount.copy()

    signed_amount.loc[
        negative_mask
    ] = (
        -signed_amount.loc[
            negative_mask
        ].abs()
    )

    signed_amount.loc[
        positive_mask
    ] = (
        signed_amount.loc[
            positive_mask
        ].abs()
    )

    return signed_amount


# ============================================================
# XIRR
# ============================================================

def calculate_xirr(cashflows):

    if (
        cashflows is None
        or len(cashflows) < 2
    ):
        return None

    cashflows = cashflows.dropna(
        subset=[
            "date",
            "amount"
        ]
    )

    if len(cashflows) < 2:
        return None

    amounts = (
        cashflows["amount"]
        .astype(float)
        .to_numpy()
    )

    dates = (
        pd.to_datetime(
            cashflows["date"]
        )
        .to_numpy()
    )

    if not (
        (amounts > 0).any()
        and
        (amounts < 0).any()
    ):
        return None

    first_date = dates[0]

    years = (
        (dates - first_date)
        / pd.Timedelta(days=365)
    )

    def npv(rate):

        if rate <= -0.999999:
            return float("inf")

        try:

            return (
                amounts
                /
                np.power(
                    1.0 + rate,
                    years
                )
            ).sum()

        except Exception:

            return float("inf")

    rate = 0.10

    for _ in range(30):

        if rate <= -0.999999:
            break

        value = npv(rate)

        if not pd.notna(value):
            break

        if abs(value) < 1e-6:
            return float(rate)

        denominator = np.power(
            1.0 + rate,
            years + 1
        )

        derivative = (
            -(
                amounts
                * years
                / denominator
            )
        ).sum()

        if (
            not pd.notna(derivative)
            or
            abs(derivative) < 1e-12
        ):
            break

        new_rate = (
            rate
            - value / derivative
        )

        if not pd.notna(new_rate):
            break

        if new_rate <= -0.999999:
            break

        if new_rate > 1000:
            break

        if abs(new_rate - rate) < 1e-7:
            return float(new_rate)

        rate = new_rate

    low = -0.9999
    high = 10.0

    low_value = npv(low)
    high_value = npv(high)

    if (
        not pd.notna(low_value)
        or
        not pd.notna(high_value)
    ):
        return None

    for _ in range(10):

        if low_value * high_value <= 0:
            break

        high *= 2

        if high > 10000:
            return None

        high_value = npv(high)

    if low_value * high_value > 0:
        return None

    for _ in range(60):

        mid = (
            low + high
        ) / 2

        mid_value = npv(mid)

        if not pd.notna(mid_value):
            return None

        if abs(mid_value) < 1e-6:
            return float(mid)

        if low_value * mid_value <= 0:

            high = mid
            high_value = mid_value

        else:

            low = mid
            low_value = mid_value

    return float(
        (low + high) / 2
    )


# ============================================================
# TRANSFORM GOLD HOLDINGS DATA
# ============================================================

def transform_holdings(df):

    print("=" * 80)
    print("TRANSFORMING GOLD HOLDINGS")
    print("=" * 80)

    if not isinstance(
        df,
        pd.DataFrame
    ):

        raise TypeError(
            "transform_holdings expected DataFrame, "
            f"received {type(df).__name__}"
        )

    df = df.copy()

    # ========================================================
    # NORMALIZE SOURCE
    # ========================================================

    df["source"] = (
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

    folio = clean_folio(
        get_column(
            df,
            "folio_no"
        )
    )

    scheme_folio = clean_folio(
        get_column(
            df,
            "scheme_folio_number"
        )
    )

    df["folio_clean"] = (
        folio
        .replace(
            "",
            pd.NA
        )
        .fillna(
            scheme_folio
        )
    )

    # ========================================================
    # CLEAN PRODUCT CODE
    # ========================================================

    df["prodcode_clean"] = (
        get_column(
            df,
            "prodcode"
        )
        .astype("string")
        .str.strip()
        .str.upper()
    )

    # ========================================================
    # SCHEME ID
    #
    # IMPORTANT:
    #
    # scheme_id is VARCHAR/TEXT.
    #
    # Examples:
    # B20933
    # P102135
    # B1024G
    #
    # NEVER convert this to numeric.
    # NEVER convert this to UUID.
    # ========================================================

    gold_df = pd.DataFrame(
        index=df.index
    )

    gold_df["scheme_id"] = clean_scheme_id(
        get_column(
            df,
            "mapped_scheme_id"
        )
    )

    print()
    print(
        "Scheme ID mapping from "
        "silver.transaction_master_new"
    )

    print(
        f"Scheme IDs available: "
        f"{gold_df['scheme_id'].notna().sum():,}"
    )

    print(
        f"Scheme IDs missing: "
        f"{gold_df['scheme_id'].isna().sum():,}"
    )

    print()
    print("Sample scheme IDs:")

    print(
        gold_df.loc[
            gold_df["scheme_id"].notna(),
            "scheme_id"
        ]
        .drop_duplicates()
        .head(20)
        .tolist()
    )

    # ========================================================
    # ID
    # ========================================================

    gold_df["id"] = [
        uuid.uuid4()
        for _ in range(len(df))
    ]

    # ========================================================
    # RTA
    # ========================================================

    gold_df["rta"] = df["source"]

    # ========================================================
    # PAN
    # ========================================================

    gold_df["pan"] = df["pan_clean"]

    # ========================================================
    # FOLIO
    # ========================================================

    gold_df["folio_number"] = (
        df["folio_clean"]
    )

    # ========================================================
    # UNITS
    # ========================================================

    gold_df["units"] = pd.to_numeric(
        get_column(
            df,
            "units"
        ),
        errors="coerce"
    )

    # ========================================================
    # MARKET VALUE
    # ========================================================

    gold_df["market_value"] = pd.to_numeric(
        get_column(
            df,
            "amount"
        ),
        errors="coerce"
    )

    # ========================================================
    # AS ON DATE
    # ========================================================

    gold_df["as_on_date"] = (
        pd.to_datetime(
            get_column(
                df,
                "rep_date"
            ),
            errors="coerce"
        )
        .dt.date
    )

    # ========================================================
    # FOLIO DATE
    # ========================================================

    gold_df["folio_date"] = (
        pd.to_datetime(
            get_column(
                df,
                "traddate"
            ),
            errors="coerce"
        )
        .dt.date
    )

    # ========================================================
    # ARN
    # ========================================================

    gold_df["arn"] = (
        get_column(
            df,
            "brokcode"
        )
        .fillna("")
        .astype("string")
        .str.strip()
    )

    gold_df.loc[
        gold_df["arn"] == "",
        "arn"
    ] = None

    # ========================================================
    # SUB ARN
    # ========================================================

    gold_df["subarn"] = (
        get_column(
            df,
            "src_brk_code"
        )
        .fillna("")
        .astype("string")
        .str.strip()
    )

    gold_df.loc[
        gold_df["subarn"] == "",
        "subarn"
    ] = None

    # ========================================================
    # HOLDING NATURE
    # ========================================================

    gold_df["holding_nature"] = (
        get_column(
            df,
            "investor_holding_nature"
        )
        .astype("string")
        .str.strip()
    )

    # ========================================================
    # NOMINEE NAME
    # ========================================================

    gold_df["nominee_name"] = (
        get_column(
            df,
            "investor_nominee_name"
        )
        .astype("string")
        .str.strip()
    )

    # ========================================================
    # NOMINEE RELATION
    # ========================================================

    gold_df["nominee_relation"] = (
        get_column(
            df,
            "investor_nominee_relation"
        )
        .astype("string")
        .str.strip()
    )

    # ========================================================
    # NOMINEE PERCENTAGE
    # ========================================================

    gold_df["nominee_pct"] = (
        get_column(
            df,
            "investor_nominee_percentage"
        )
        .astype("string")
        .str.strip()
    )

    # ========================================================
    # KYC
    # ========================================================

    gold_df["kyc_status"] = None

    kyc_available = (
        get_column(
            df,
            "investor_ckyc_no"
        )
        .fillna("")
        .astype(str)
        .str.strip()
        != ""
    )

    gold_df.loc[
        kyc_available,
        "kyc_status"
    ] = "Verified"

    # ========================================================
    # BANK NAME
    # ========================================================

    gold_df["bank_name"] = (
        get_column(
            df,
            "investor_bank_name"
        )
        .astype("string")
        .str.strip()
    )

    gold_df.loc[
        gold_df["bank_name"] == "",
        "bank_name"
    ] = None

    # ========================================================
    # BANK ACCOUNT LAST 4
    # ========================================================

    gold_df["bank_ac_last4"] = (
        get_column(
            df,
            "investor_bank_account_no"
        )
        .fillna("")
        .astype(str)
        .str.replace(
            ".0",
            "",
            regex=False
        )
        .str.strip()
        .str[-4:]
    )

    gold_df.loc[
        gold_df["bank_ac_last4"] == "",
        "bank_ac_last4"
    ] = None

    # ========================================================
    # DEMAT
    # ========================================================

    gold_df["demat_flag"] = (
        get_column(
            df,
            "investor_demat_flag"
        )
        .astype("string")
        .str.strip()
    )

    gold_df.loc[
        gold_df["demat_flag"] == "",
        "demat_flag"
    ] = None

    # ========================================================
    # CLIENT ID
    # ========================================================

    print("Loading gold.clients...")

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
        gold_df["pan"]
        .map(client_lookup)
    )

    print(
        "Client mapping completed"
    )

    # ========================================================
    # AMC ID
    # ========================================================

    print("Loading public.amc...")

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

    df["amc_code_clean"] = (
        get_column(
            df,
            "amc_code"
        )
        .astype("string")
        .str.strip()
        .str.upper()
    )

    gold_df["amc_id"] = (
        df["amc_code_clean"]
        .map(amc_lookup)
    )

    # ========================================================
    # TRANSACTION DATE
    # ========================================================

    df["traddate_clean"] = pd.to_datetime(
        get_column(
            df,
            "traddate"
        ),
        errors="coerce"
    )

    # ========================================================
    # PURCHASE
    # ========================================================

    df["is_purchase"] = purchase_mask(
        df
    )

    # ========================================================
    # SIGNED AMOUNT
    # ========================================================

    df["signed_amount"] = (
        signed_transaction_amount(
            df
        )
    )

    # ========================================================
    # GROUP KEYS
    # ========================================================

    group_cols = [
        "source",
        "folio_clean",
        "prodcode_clean"
    ]

    # ========================================================
    # INVESTED AMOUNT
    # ========================================================

    invested_amounts = (
        df
        .groupby(
            group_cols,
            dropna=False,
            sort=False
        )["signed_amount"]
        .sum()
        .rename(
            "invested_amount"
        )
        .reset_index()
    )

    df = df.merge(
        invested_amounts,
        on=group_cols,
        how="left"
    )

    gold_df["invested_amount"] = (
        pd.to_numeric(
            df["invested_amount"],
            errors="coerce"
        )
    )

    # ========================================================
    # AVG COST NAV
    # ========================================================

    gold_df["avg_cost_nav"] = (
        gold_df["invested_amount"]
        /
        gold_df["units"].replace(
            0,
            pd.NA
        )
    )

    # ========================================================
    # PURCHASE DATE
    # ========================================================

    purchase_dates = (
        df.loc[
            df["is_purchase"],
            group_cols
            +
            [
                "traddate_clean"
            ]
        ]
        .dropna(
            subset=[
                "traddate_clean"
            ]
        )
        .groupby(
            group_cols,
            dropna=False,
            sort=False
        )["traddate_clean"]
        .min()
        .rename(
            "purchase_date"
        )
        .reset_index()
    )

    df = df.merge(
        purchase_dates,
        on=group_cols,
        how="left"
    )

    gold_df["purchase_date"] = (
        pd.to_datetime(
            df["purchase_date"],
            errors="coerce"
        )
        .dt.date
    )

    # ========================================================
    # ARN ID
    # ========================================================

    print("Loading public.arn...")

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

    df["brokcode_clean"] = (
        get_column(
            df,
            "brokcode"
        )
        .astype("string")
        .str.strip()
        .str.upper()
    )

    gold_df["arn_id"] = (
        df["brokcode_clean"]
        .map(arn_lookup)
    )

    # ========================================================
    # CURRENT NAV
    # ========================================================

    if "nav" in df.columns:

        gold_df["current_nav"] = pd.to_numeric(
            df["nav"],
            errors="coerce"
        )

    else:

        gold_df["current_nav"] = pd.NA

    # ========================================================
    # CURRENT VALUE
    # ========================================================

    gold_df["current_value"] = (
        gold_df["units"]
        *
        gold_df["current_nav"]
    )

    # ========================================================
    # NAV DATE
    # ========================================================

    if "nav_date" in df.columns:

        gold_df["nav_date"] = (
            pd.to_datetime(
                df["nav_date"],
                errors="coerce"
            )
            .dt.date
        )

    else:

        gold_df["nav_date"] = None

    # ========================================================
    # UNREALISED GAIN
    # ========================================================

    gold_df["unrealised_gain"] = (
        gold_df["current_value"]
        -
        gold_df["invested_amount"]
    )

    # ========================================================
    # XIRR
    # ========================================================

    print(
        "Calculating XIRR..."
    )

    xirr_groups = (
        df[
            group_cols
            +
            [
                "traddate_clean",
                "signed_amount"
            ]
        ]
        .dropna(
            subset=[
                "traddate_clean",
                "signed_amount"
            ]
        )
    )

    xirr_lookup = {}

    total_groups = (
        xirr_groups[
            group_cols
        ]
        .drop_duplicates()
        .shape[0]
    )

    print(
        f"XIRR groups: "
        f"{total_groups:,}"
    )

    processed_groups = 0

    for key, group in xirr_groups.groupby(
        group_cols,
        dropna=False,
        sort=False
    ):

        xirr_lookup[key] = calculate_xirr(
            group[
                [
                    "traddate_clean",
                    "signed_amount"
                ]
            ]
            .rename(
                columns={
                    "traddate_clean": "date",
                    "signed_amount": "amount"
                }
            )
        )

        processed_groups += 1

    print(
        f"XIRR completed: "
        f"{processed_groups:,} groups"
    )

    group_index = pd.MultiIndex.from_frame(
        df[group_cols]
    )

    xirr_series = pd.Series(
        xirr_lookup
    )

    gold_df["xirr"] = (
        xirr_series
        .reindex(
            group_index
        )
        .to_numpy()
    )

    # ========================================================
    # FIRST PURCHASE DATE
    # ========================================================

    first_purchase_dates = (
        df.loc[
            df["is_purchase"],
            group_cols
            +
            [
                "traddate_clean"
            ]
        ]
        .dropna(
            subset=[
                "traddate_clean"
            ]
        )
        .groupby(
            group_cols,
            dropna=False,
            sort=False
        )["traddate_clean"]
        .min()
        .rename(
            "first_purchase_date"
        )
        .reset_index()
    )

    df = df.merge(
        first_purchase_dates,
        on=group_cols,
        how="left"
    )

    gold_df["first_purchase_date"] = (
        pd.to_datetime(
            df["first_purchase_date"],
            errors="coerce"
        )
        .dt.date
    )

    # ========================================================
    # SOURCE FILE ID
    # ========================================================

    gold_df["source_file_id"] = None

    # ========================================================
    # TIMESTAMPS
    # ========================================================

    current_time = datetime.now(
        timezone.utc
    )

    gold_df["last_synced_at"] = current_time
    gold_df["created_at"] = current_time

    # ========================================================
    # FINAL COLUMN ORDER
    # ========================================================

    columns = [

        "id",
        "rta",
        "pan",
        "folio_number",

        "units",
        "market_value",

        "as_on_date",
        "folio_date",

        "arn",
        "subarn",

        "holding_nature",
        "nominee_name",
        "nominee_relation",
        "nominee_pct",

        "kyc_status",

        "bank_name",
        "bank_ac_last4",

        "demat_flag",

        "client_id",
        "amc_id",

        # VARCHAR SCHEME ID
        "scheme_id",

        "purchase_date",

        "arn_id",

        "avg_cost_nav",
        "invested_amount",

        "current_nav",
        "current_value",
        "nav_date",

        "unrealised_gain",
        "xirr",

        "first_purchase_date",

        "source_file_id",
        "last_synced_at",
        "created_at"

    ]

    gold_df = gold_df[
        columns
    ].copy()

    # ========================================================
    # FINAL SCHEME ID CLEANUP
    # ========================================================

    gold_df["scheme_id"] = clean_scheme_id(
        gold_df["scheme_id"]
    )

    # ========================================================
    # REMOVE INVALID NATURAL KEYS
    # ========================================================

    before = len(
        gold_df
    )

    gold_df = gold_df[
        gold_df["rta"].notna()
        &
        gold_df["folio_number"].notna()
        &
        gold_df["scheme_id"].notna()
    ].copy()

    removed = (
        before
        -
        len(gold_df)
    )

    print(
        f"Invalid natural-key rows removed: "
        f"{removed:,}"
    )

    # ========================================================
    # DEDUPLICATE HOLDINGS
    # ========================================================

    before_dedup = len(
        gold_df
    )

    gold_df = (
        gold_df
        .drop_duplicates(
            subset=[
                "rta",
                "folio_number",
                "scheme_id"
            ]
        )
        .reset_index(
            drop=True
        )
    )

    print(
        f"Duplicate holdings removed: "
        f"{before_dedup - len(gold_df):,}"
    )

    # ========================================================
    # FINAL VALIDATION
    # ========================================================

    print("=" * 80)
    print("GOLD HOLDINGS VALIDATION")
    print("=" * 80)

    print(
        f"Gold holdings generated: "
        f"{len(gold_df):,}"
    )

    print(
        f"Missing Scheme IDs: "
        f"{gold_df['scheme_id'].isna().sum():,}"
    )

    print(
        f"Missing Client IDs: "
        f"{gold_df['client_id'].isna().sum():,}"
    )

    print(
        f"Missing AMC IDs: "
        f"{gold_df['amc_id'].isna().sum():,}"
    )

    print(
        f"Missing ARN IDs: "
        f"{gold_df['arn_id'].isna().sum():,}"
    )

    print(
        f"Missing ARN values: "
        f"{gold_df['arn'].isna().sum():,}"
    )

    print(
        f"Missing Sub ARN values: "
        f"{gold_df['subarn'].isna().sum():,}"
    )

    print(
        f"Missing Units: "
        f"{gold_df['units'].isna().sum():,}"
    )

    print(
        f"Missing Market Value: "
        f"{gold_df['market_value'].isna().sum():,}"
    )

    print(
        f"Missing Invested Amount: "
        f"{gold_df['invested_amount'].isna().sum():,}"
    )

    print(
        f"Missing Current NAV: "
        f"{gold_df['current_nav'].isna().sum():,}"
    )

    print(
        f"Missing Current Value: "
        f"{gold_df['current_value'].isna().sum():,}"
    )

    print()
    print("Scheme ID dtype:")
    print(
        gold_df["scheme_id"].dtype
    )

    print()
    print("Sample final scheme IDs:")
    print(
        gold_df["scheme_id"]
        .dropna()
        .drop_duplicates()
        .head(20)
        .tolist()
    )

    print("=" * 80)
    print("TRANSFORM COMPLETED")
    print("=" * 80)

    return gold_df


# ============================================================
# LOAD GOLD HOLDINGS DATA
# ============================================================

def load_holdings(gold_df):

    print("=" * 80)
    print("LOADING DATA INTO GOLD.HOLDINGS")
    print("=" * 80)

    if not isinstance(
        gold_df,
        pd.DataFrame
    ):

        raise TypeError(
            "load_holdings received "
            f"{type(gold_df).__name__}"
        )

    if gold_df.empty:

        print(
            "No holdings to load."
        )

        return True

    # ========================================================
    # VARCHAR LIMITS
    # ========================================================

    varchar_limits = {

        "rta": 10,
        "pan": 10,
        "folio_number": 40,

        "arn": 20,
        "subarn": 20,

        "scheme_id": 50,

        "holding_nature": 40,

        "nominee_name": 255,
        "nominee_relation": 40,
        "nominee_pct": 20,

        "kyc_status": 20,

        "bank_name": 120,
        "bank_ac_last4": 8,

        "demat_flag": 4

    }

    for col, limit in varchar_limits.items():

        if col not in gold_df.columns:
            continue

        max_len = (
            gold_df[col]
            .fillna("")
            .astype(str)
            .str.len()
            .max()
        )

        print(
            f"{col:<25} Max Length : {max_len}"
        )

        if max_len > limit:

            raise ValueError(
                f"{col} length {max_len} "
                f"exceeds limit {limit}"
            )

    # ========================================================
    # REQUIRED NATURAL KEYS
    # ========================================================

    required = [
        "rta",
        "folio_number",
        "scheme_id"
    ]

    for col in required:

        if gold_df[col].isna().any():

            raise ValueError(
                f"{col} contains NULL values"
            )

    # ========================================================
    # SCHEME ID TYPE VALIDATION
    #
    # IMPORTANT:
    # scheme_id is VARCHAR.
    #
    # DO NOT:
    # - pd.to_numeric()
    # - Int64
    # - int64
    # - BIGINT
    # - UUID
    # ========================================================

    print(
        "Validating scheme_id type..."
    )

    gold_df["scheme_id"] = clean_scheme_id(
        gold_df["scheme_id"]
    )

    if gold_df["scheme_id"].isna().any():

        raise ValueError(
            "scheme_id contains NULL/empty values"
        )

    print(
        "scheme_id validated as VARCHAR/string"
    )

    print()
    print("Sample scheme_id before INSERT:")

    print(
        gold_df["scheme_id"]
        .drop_duplicates()
        .head(20)
        .tolist()
    )

    # ========================================================
    # EXISTING HOLDINGS
    # ========================================================

    print(
        "Checking existing holdings..."
    )

    existing_holdings = pd.read_sql(
        """
        SELECT
            rta,
            folio_number,
            scheme_id

        FROM gold.holdings
        """,
        engine
    )

    print(
        f"Existing holdings: "
        f"{len(existing_holdings):,}"
    )

    # ========================================================
    # NORMALIZE KEYS
    # ========================================================

    for col in [
        "rta",
        "folio_number",
        "scheme_id"
    ]:

        existing_holdings[col] = (
            existing_holdings[col]
            .fillna("")
            .astype(str)
            .str.strip()
            .str.upper()
        )

        gold_df[col] = (
            gold_df[col]
            .fillna("")
            .astype(str)
            .str.strip()
            .str.upper()
        )

    # ========================================================
    # REMOVE EXISTING HOLDINGS
    # ========================================================

    if not existing_holdings.empty:

        existing_keys = (
            existing_holdings[
                [
                    "rta",
                    "folio_number",
                    "scheme_id"
                ]
            ]
            .drop_duplicates()
        )

        gold_df = gold_df.merge(

            existing_keys.assign(
                _exists=True
            ),

            on=[
                "rta",
                "folio_number",
                "scheme_id"
            ],

            how="left"
        )

        gold_df = (
            gold_df[
                gold_df["_exists"].isna()
            ]
            .drop(
                columns=[
                    "_exists"
                ]
            )
        )

    print(
        f"Rows to insert: "
        f"{len(gold_df):,}"
    )

    if gold_df.empty:

        print(
            "No new holdings to insert."
        )

        return True

    # ========================================================
    # INSERT
    # ========================================================

    try:

        gold_df.to_sql(
            name="holdings",
            con=engine,
            schema="gold",
            if_exists="append",
            index=False,
            method="multi",
            chunksize=5000
        )

        print()
        print(
            "Inserted rows:",
            len(gold_df)
        )

        print(
            "Holdings loaded successfully"
        )

        return True

    except Exception as e:

        print()
        print("=" * 80)
        print(
            "ERROR WHILE LOADING GOLD.HOLDINGS"
        )
        print("=" * 80)

        print(
            type(e).__name__,
            ":",
            e
        )

        return False


# ============================================================
# MAIN EXECUTION
# ============================================================

if __name__ == "__main__":

    print()
    print("=" * 80)
    print("STARTING GOLD HOLDINGS ETL")
    print("=" * 80)

    start_time = datetime.now()

    try:

        # ====================================================
        # EXTRACT
        # ====================================================

        df = extract_holdings()

        if not isinstance(
            df,
            pd.DataFrame
        ):

            raise TypeError(
                "extract_holdings() did not "
                "return a DataFrame"
            )

        # ====================================================
        # TRANSFORM
        # ====================================================

        gold_df = transform_holdings(
            df
        )

        if not isinstance(
            gold_df,
            pd.DataFrame
        ):

            raise TypeError(
                "transform_holdings() did not "
                "return a DataFrame"
            )

        # ====================================================
        # LOAD
        # ====================================================

        status = load_holdings(
            gold_df
        )

        # ====================================================
        # FINAL STATUS
        # ====================================================

        elapsed = (
            datetime.now()
            -
            start_time
        ).total_seconds()

        print()
        print("=" * 80)

        if status:

            print(
                "GOLD HOLDINGS ETL "
                "COMPLETED SUCCESSFULLY"
            )

        else:

            print(
                "GOLD HOLDINGS ETL FAILED"
            )

        print(
            f"Total execution time: "
            f"{elapsed:.1f} seconds"
        )

        print("=" * 80)

    except Exception as e:

        print()
        print("=" * 80)
        print(
            "GOLD HOLDINGS ETL ERROR"
        )
        print("=" * 80)

        print(
            "Error type:",
            type(e).__name__
        )

        print(
            "Error:",
            e
        )

        import traceback

        traceback.print_exc()
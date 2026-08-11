import pandas as pd
import uuid
import numpy as np

from datetime import datetime, timezone

from utils.db import engine, restore_engine


# ============================================================
# EXTRACT GOLD HOLDINGS DATA
# ============================================================

def extract_holdings():

    print("=" * 80)
    print("EXTRACTING DATA FOR GOLD HOLDINGS")
    print("=" * 80)

    query = """

    WITH investor_base AS
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
            ckyc_no,

            broker_code,
            subbroker

        FROM silver.investor_master

        ORDER BY
            source,
            folio_no
    )

    SELECT

        t.*,

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
            AS investor_ckyc_no,

        t.brokcode
            AS investor_broker_code,

        i.subbroker
            AS investor_subbroker

    FROM silver.transaction_master_new t

    LEFT JOIN investor_base i

        ON t.source = i.source
        AND t.folio_no = i.folio_no

    """

    df = pd.read_sql(
        query,
        engine
    )

    print(
        f"Transactions extracted: {len(df):,}"
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
    ] = -signed_amount.loc[
        negative_mask
    ].abs()

    signed_amount.loc[
        positive_mask
    ] = signed_amount.loc[
        positive_mask
    ].abs()

    return signed_amount


# ============================================================
# FAST XIRR
# ============================================================

def calculate_xirr(cashflows):

    if cashflows is None or len(cashflows) < 2:
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
        (
            dates - first_date
        )
        / pd.Timedelta(days=365)
    )

    # --------------------------------------------------------
    # NPV
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Newton-Raphson
    # --------------------------------------------------------

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
            -
            value / derivative
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

    # --------------------------------------------------------
    # BISECTION FALLBACK
    # --------------------------------------------------------

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

    # Expand high if necessary
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
            f"transform_holdings expected DataFrame, "
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
        .replace("", pd.NA)
        .fillna(scheme_folio)
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
    # GOLD SCHEME
    #
    # ENGINE DATABASE
    # ========================================================

    print("Loading gold.scheme...")

    gold_scheme = pd.read_sql(
        """
        SELECT
            id,
            rta,
            scheme_code
        FROM gold.scheme
        """,
        engine
    )

    gold_scheme["rta"] = (
        clean_string(
            gold_scheme["rta"]
        )
        .str.upper()
    )

    gold_scheme["scheme_code"] = (
        clean_string(
            gold_scheme["scheme_code"]
        )
        .str.upper()
    )

    gold_scheme = (
        gold_scheme
        .dropna(
            subset=[
                "rta",
                "scheme_code"
            ]
        )
        .drop_duplicates(
            subset=[
                "rta",
                "scheme_code"
            ]
        )
    )

    # ========================================================
    # SCHEME ID MAPPING
    # ========================================================

    df = df.merge(
        gold_scheme[
            [
                "id",
                "rta",
                "scheme_code"
            ]
        ],
        left_on=[
            "source",
            "prodcode_clean"
        ],
        right_on=[
            "rta",
            "scheme_code"
        ],
        how="left"
    )

    df.rename(
        columns={
            "id": "mapped_scheme_id"
        },
        inplace=True
    )

    matched = (
        df["mapped_scheme_id"]
        .notna()
        .sum()
    )

    missing = (
        df["mapped_scheme_id"]
        .isna()
        .sum()
    )

    print(
        f"Scheme mapping: {matched:,} matched | "
        f"{missing:,} missing"
    )

    if missing > 0:

        missing_scheme = df[
            df["mapped_scheme_id"].isna()
        ]

        sample_columns = [
            "source",
            "prodcode"
        ]

        for col in [
            "scheme",
            "funddesc"
        ]:

            if col in df.columns:
                sample_columns.append(col)

        print(
            "Missing scheme sample:"
        )

        print(
            missing_scheme[
                sample_columns
            ]
            .drop_duplicates()
            .head(10)
            .to_string(index=False)
        )

    # ========================================================
    # BASIC GOLD DATAFRAME
    # ========================================================

    gold_df = pd.DataFrame(
        index=df.index
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

    gold_df["rta"] = df[
        "source"
    ]

    # ========================================================
    # PAN
    # ========================================================

    gold_df["pan"] = df[
        "pan_clean"
    ]

    # ========================================================
    # FOLIO
    # ========================================================

    gold_df["folio_number"] = df[
        "folio_clean"
    ]

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
            "investor_broker_code"
        )
        .astype("string")
        .str.strip()
    )

    gold_df.loc[
        gold_df["arn"] == "",
        "arn"
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
    # NOMINEE
    # ========================================================

    gold_df["nominee_name"] = (
        get_column(
            df,
            "investor_nominee_name"
        )
        .astype("string")
        .str.strip()
    )

    gold_df["nominee_relation"] = (
        get_column(
            df,
            "investor_nominee_relation"
        )
        .astype("string")
        .str.strip()
    )

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
    # BANK
    # ========================================================

    gold_df["bank_name"] = (
        get_column(
            df,
            "investor_bank_name"
        )
        .astype("string")
        .str.strip()
    )

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

    # ========================================================
    # CLIENT ID
    #
    # ENGINE -> gold.clients
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
    # AMC
    #
    # RESTORE ENGINE -> public.amc
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
        restore_engine
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
    # SCHEME ID
    # ========================================================

    gold_df["scheme_id"] = (
        df["mapped_scheme_id"]
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
    #
    # Calculate once per group.
    # ========================================================

    invested_amounts = (
        df
        .groupby(
            group_cols,
            dropna=False,
            sort=False
        )[
            "signed_amount"
        ]
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

    gold_df["invested_amount"] = pd.to_numeric(
        df["invested_amount"],
        errors="coerce"
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
            group_cols + [
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
        )[
            "traddate_clean"
        ]
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
    #
    # RESTORE ENGINE -> public.arn
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
        restore_engine
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
    # GOLD SCHEME NAV
    #
    # ENGINE -> gold.scheme_nav
    #
    # IMPORTANT:
    # Only latest NAV per scheme is fetched.
    # Do NOT load the entire scheme_nav table.
    # ========================================================

    print("Loading latest gold.scheme_nav...")

    scheme_nav = pd.read_sql(
        """
        SELECT
            sn.scheme_id,
            sn.nav_date,
            sn.nav,
            sn.repurchase_nav
        FROM gold.scheme_nav sn
        INNER JOIN
        (
            SELECT
                scheme_id,
                MAX(nav_date) AS max_nav_date
            FROM gold.scheme_nav
            WHERE nav_date IS NOT NULL
            GROUP BY scheme_id
        ) latest
            ON latest.scheme_id = sn.scheme_id
            AND latest.max_nav_date = sn.nav_date
        """,
        engine
    )

    print(
        f"Latest scheme NAV rows: {len(scheme_nav):,}"
    )

    if not scheme_nav.empty:

        scheme_nav["nav_date"] = pd.to_datetime(
            scheme_nav["nav_date"],
            errors="coerce"
        )

        scheme_nav["nav"] = pd.to_numeric(
            scheme_nav["nav"],
            errors="coerce"
        )

        scheme_nav = (
            scheme_nav
            .drop_duplicates(
                subset=[
                    "scheme_id"
                ],
                keep="last"
            )
        )

    # ========================================================
    # MAP NAV
    # ========================================================

    if not scheme_nav.empty:

        scheme_nav_lookup = scheme_nav[
            [
                "scheme_id",
                "nav",
                "nav_date"
            ]
        ].copy()

        scheme_nav_lookup.rename(
            columns={
                "nav": "mapped_nav",
                "nav_date": "mapped_nav_date"
            },
            inplace=True
        )

        df = df.merge(
            scheme_nav_lookup,
            left_on="mapped_scheme_id",
            right_on="scheme_id",
            how="left"
        )

    else:

        df["mapped_nav"] = pd.NA
        df["mapped_nav_date"] = pd.NaT

    # ========================================================
    # CURRENT NAV
    # ========================================================

    gold_df["current_nav"] = pd.to_numeric(
        df["mapped_nav"],
        errors="coerce"
    )

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

    gold_df["nav_date"] = (
        pd.to_datetime(
            df["mapped_nav_date"],
            errors="coerce"
        )
        .dt.date
    )

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
    #
    # IMPORTANT OPTIMIZATION:
    # Calculate XIRR only once per unique holding group.
    # ========================================================

    print("Calculating XIRR...")

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
        f"XIRR groups: {total_groups:,}"
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
            ].rename(
                columns={
                    "traddate_clean": "date",
                    "signed_amount": "amount"
                }
            )
        )

        processed_groups += 1

    print(
        f"XIRR completed: {processed_groups:,} groups"
    )

    # ========================================================
    # MAP XIRR
    # ========================================================

    group_index = pd.MultiIndex.from_frame(
        df[group_cols]
    )

    xirr_series = pd.Series(
        xirr_lookup
    )

    gold_df["xirr"] = (
        xirr_series
        .reindex(group_index)
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
        )[
            "traddate_clean"
        ]
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
    # LAST SYNCED
    # ========================================================

    current_time = datetime.now(
        timezone.utc
    )

    gold_df["last_synced_at"] = (
        current_time
    )

    # ========================================================
    # SUBARN
    # ========================================================

    src_subarn = (
        get_column(
            df,
            "src_brk_code"
        )
        .astype("string")
        .str.strip()
    )

    investor_subarn = (
        get_column(
            df,
            "investor_subbroker"
        )
        .astype("string")
        .str.strip()
    )

    gold_df["subarn"] = (
        src_subarn
        .replace(
            "",
            pd.NA
        )
        .fillna(
            investor_subarn
        )
    )

    # ========================================================
    # CREATED AT
    # ========================================================

    gold_df["created_at"] = (
        current_time
    )

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
        "subarn",
        "created_at"

    ]

    gold_df = gold_df[
        columns
    ].copy()

    # ========================================================
    # REMOVE INVALID NATURAL KEYS
    # ========================================================

    before = len(gold_df)

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
        f"Invalid natural-key rows removed: {removed:,}"
    )

    # ========================================================
    # DEDUPLICATE HOLDINGS
    # ========================================================

    before_dedup = len(gold_df)

    gold_df = (
        gold_df
        .drop_duplicates(
            subset=[
                "rta",
                "folio_number",
                "scheme_id"
            ]
        )
        .reset_index(drop=True)
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
        f"Gold holdings generated: {len(gold_df):,}"
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
        "holding_nature": 40,
        "nominee_name": 255,
        "nominee_relation": 40,
        "nominee_pct": 20,
        "kyc_status": 20,
        "bank_name": 120,
        "bank_ac_last4": 8,
        "demat_flag": 4,
        "subarn": 20

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

    # ========================================================
    # NORMALIZE KEYS
    # ========================================================

    for col in [
        "rta",
        "folio_number"
    ]:

        existing_holdings[col] = (
            existing_holdings[col]
            .astype("string")
            .str.strip()
            .str.upper()
        )

        gold_df[col] = (
            gold_df[col]
            .astype("string")
            .str.strip()
            .str.upper()
        )

    # ========================================================
    # REMOVE EXISTING
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
        f"Rows to insert: {len(gold_df):,}"
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

        print(
            f"Inserted rows: {len(gold_df):,}"
        )

        return True

    except Exception as e:

        print("=" * 80)
        print("ERROR WHILE LOADING GOLD.HOLDINGS")
        print("=" * 80)

        print(
            type(e).__name__,
            ":",
            e
        )

        return False


# ============================================================
# MAIN
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
                "GOLD HOLDINGS ETL COMPLETED SUCCESSFULLY"
            )

        else:

            print(
                "GOLD HOLDINGS ETL FAILED"
            )

        print(
            f"Total execution time: {elapsed:.1f} seconds"
        )

        print("=" * 80)

    except Exception as e:

        print()
        print("=" * 80)
        print("GOLD HOLDINGS ETL ERROR")
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
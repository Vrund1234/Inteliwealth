import pandas as pd
import uuid

from datetime import datetime, timezone

from utils.db import engine
from utils.db import master_engine



# =====================================================
# DATABASE CONNECTION CHECK
# =====================================================


print(
    pd.read_sql(
        "SELECT current_database();",
        master_engine
    )
)



# =====================================================
# HOLDING IDENTITY
# =====================================================

# A holding is one folio's position in one scheme, so its id is derived from
# that pair rather than generated fresh, which kept changing the id of the same
# position on every run.

HOLDING_NAMESPACE = uuid.UUID(
    "6f9619ff-8b86-d011-b42d-00c04fc964ff"
)



# =====================================================
# GOLD.HOLDINGS COLUMN ORDER
# =====================================================

# Shared by the per-transaction build and the position rollup that follows it,
# so the two cannot drift apart.

HOLDINGS_COLUMNS = [

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

    "created_at"

]



# =====================================================
# UNIT DIRECTION PER TRANSACTION TYPE
# =====================================================

# Which transactions add units to a position and which take them away. The
# units column itself is no guide: 1,858 of the 73,838 CAMS dividend-reinvest
# rows carry a negative value and all 14,266 KFIN redemptions carry a positive
# one, so the sign has to come from the transaction type.

CAMS_INFLOW_FLAGS = {

    "ADDITIONAL PURCHASE",
    "ADDITIONAL PURCHASE SYSTEMATIC",
    "FRESH PURCHASE",
    "FRESH PURCHASE SYSTEMATIC",
    "SWITCH IN",
    "DIVIDEND REINVEST",
    "BONUS",
    "NFOAP",
    "NFO FP",
    "NFO SI",
    "TI INTO NEW FOLIO",
    "TI INTO EXISTING FOLIO",
    "TICOB"

}


CAMS_OUTFLOW_FLAGS = {

    "PARTIAL REDEMPTION",
    "FULL REDEMPTION",
    "PARTIAL SWITCH OUT",
    "FULL SWITCH OUT",
    "TRANSFER OUT",
    "TOCOB"

}


# Moves money, not units: a dividend payout leaves the position alone. DRO
# carries 0 units on all 124 rows, so its direction never matters.

CAMS_NEUTRAL_FLAGS = {

    "DIVIDEND PAYOUT",
    "DRO"

}



# =====================================================
# EXTRACT GOLD HOLDINGS DATA
# =====================================================


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

            broker_code


        FROM silver.investor_master


        ORDER BY

            source,
            folio_no

    )


    SELECT DISTINCT ON
    (

        t.source,
        t.trxnno

    )


        -- Only the columns this module actually reads. t.* pulled all 116
        -- columns of silver.transaction_master_new across ~258k rows, and
        -- everything past these is carried through the merge and the gold build
        -- unused.

        t.source,

        t.trxnno,

        t.folio_no,

        t.pan,

        t.prodcode,

        t.units,

        t.amount,


        -- KFIN fills traddate on only 38,230 of its 76,460 rows, so a bare
        -- traddate left half its positions with no date at all. postdate is the
        -- next best, and crdate is present on every KFIN row.

        coalesce(

            t.traddate,

            t.postdate,

            t.crdate

        ) AS traddate,

        t.rep_date,

        t.scheme_folio_number,

        t.scheme,

        t.funddesc,


        -- Direction markers. CAMS fills trxn_type_flag and leaves td_purred
        -- blank; KFIN does the reverse. See holding_direction().

        t.trxn_type_flag,

        t.td_purred,

        t.trxntype,


        i.holding_nature AS investor_holding_nature,

        i.nominee1_name AS investor_nominee_name,

        i.nominee1_relation AS investor_nominee_relation,

        i.nominee1_percentage AS investor_nominee_percentage,


        i.bank_name AS investor_bank_name,

        i.bank_account_no AS investor_bank_account_no,


        i.demat_flag AS investor_demat_flag,

        i.ckyc_no AS investor_ckyc_no,

        i.broker_code AS investor_broker_code



    FROM silver.transaction_master_new t



    LEFT JOIN investor_base i


    ON t.source = i.source

    AND t.folio_no = i.folio_no


    -- One row per transaction. silver.transaction_master_new holds every
    -- delivery it was ever given, so the same transaction appears once per
    -- delivery: 257,532 rows over 117,904 distinct (source, trxnno). Summing
    -- units without this multiplies a position by however many times its file
    -- was ingested.
    --
    -- created_at DESC keeps the newest delivery, which matters: 869 of those
    -- keys carry a different units value between deliveries, so an arbitrary
    -- tiebreak moves the totals between runs — it did, by 3.3M units, until
    -- this list was made total.
    --
    -- created_at alone is not enough: 947 keys have two or more rows sharing
    -- their newest created_at. With the columns below added, no remaining tie
    -- differs in any column this query selects, so which row wins cannot change
    -- the output. They are compared as text on purpose — units::numeric would
    -- throw on a single unparseable value in a feed, and a tiebreak has no
    -- business failing the run.

    ORDER BY

        t.source,

        t.trxnno,

        t.created_at DESC,

        t.units DESC NULLS LAST,

        t.amount DESC NULLS LAST,

        coalesce(

            t.traddate,

            t.postdate,

            t.crdate

        ) DESC NULLS LAST,

        t.folio_no DESC NULLS LAST,

        t.prodcode DESC NULLS LAST,

        t.rep_date DESC NULLS LAST,

        t.scheme DESC NULLS LAST


    """



    df = pd.read_sql(

        query,

        engine

    )



    print()

    print("Extraction Completed")

    print("-" * 80)


    print(
        "Rows fetched:",
        len(df)
    )


    print(
        "Columns fetched:",
        len(df.columns)
    )


    print(df.head())


    return df

# =====================================================
# TRANSFORM GOLD HOLDINGS DATA
# =====================================================


def transform_holdings(df):


    print("=" * 80)
    print("TRANSFORMING GOLD HOLDINGS")
    print("=" * 80)



    gold_df = pd.DataFrame()



    # The id is assigned after the rollup, from (rta, folio_number, scheme_id).
    # A per-transaction id would be thrown away by the aggregation anyway. The
    # column is still created here, over df's index, because every assignment
    # below aligns against it.

    gold_df["id"] = pd.Series(

        None,

        index=df.index,

        dtype="object"

    )



    # =====================================================
    # CLEAN PRODUCT CODE
    # =====================================================


    df["prodcode"] = (
        df["prodcode"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()

    )

    # =====================================================
    # LOAD GOLD SCHEME
    # prodcode -> scheme_id
    # =====================================================

    gold_scheme = pd.read_sql(
        """SELECT
            id,
            rta,
            scheme_code
        FROM gold.scheme""",
        engine
    )


    print(
        "Gold Scheme Rows:",
        len(gold_scheme)
    )

    # =====================================================
    # CLEAN SCHEME KEYS
    # =====================================================

    gold_scheme["scheme_code"] = (
        gold_scheme["scheme_code"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()
    )

    gold_scheme["rta"] = (
        gold_scheme["rta"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()
    )

    df["source"] = (
        df["source"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()
    )

    # =====================================================
    # MAP SCHEME ID
    # =====================================================

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
            "prodcode"
        ],

        right_on=[
            "rta",
            "scheme_code"
        ],

        how="left"
    )

    df.rename(
        columns={
            "id":"scheme_id"
        },
        inplace=True
    )

    print("=" * 80)
    print("SCHEME ID VALIDATION")
    print("=" * 80)

    print("Total Holdings:",len(df))

    print("Matched scheme_id:",df["scheme_id"].notna().sum())

    print("Missing scheme_id:",df["scheme_id"].isna().sum())

    print("\nMissing Scheme Samples")

    print(df.loc[df["scheme_id"].isna(),
            [
                "source",
                "prodcode",
                "scheme",
                "funddesc"
            ]
        ]
        .drop_duplicates()
        .head(20)
    )

    # Transaction grain in, position grain out.

    return aggregate_holdings(

        create_holdings_columns(df, gold_df),

        holding_direction(df)

    )

# =====================================================
# CREATE GOLD HOLDINGS COLUMNS
# =====================================================

def create_holdings_columns(df, gold_df):

    # =====================================================
    # RTA
    # =====================================================


    gold_df["rta"] = (
        df["source"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()
    )

    # =====================================================
    # PAN
    # =====================================================

    gold_df["pan"] = (
        df["pan"]
        .astype("string")
        .str.strip()
        .str.upper()
        .str.replace(".0", "", regex=False)
    )


    gold_df.loc[
        gold_df["pan"].isna()
        |
        (gold_df["pan"].str.len() != 10),
        "pan"
    ] = None

    # =====================================================
    # FOLIO NUMBER
    # =====================================================

    folio = (
        df["folio_no"]
        .astype("string")
        .str.strip()
        .str.replace(".0", "", regex=False)
    )

    scheme_folio = (
        df["scheme_folio_number"]
        .astype("string")
        .str.strip()
        .str.replace(".0", "", regex=False)
    )

    gold_df["folio_number"] = (
        folio
        .fillna(scheme_folio)
    )

    gold_df.loc[
        gold_df["folio_number"] == "",
        "folio_number"
    ] = None

    # =====================================================
    # HOLDING VALUES
    # =====================================================

    gold_df["units"] = pd.to_numeric(
        df["units"],
        errors="coerce"
    )

    gold_df["market_value"] = pd.to_numeric(
        df["amount"],
        errors="coerce"
    )

    # =====================================================
    # DATES
    # =====================================================


    gold_df["as_on_date"] = (
        pd.to_datetime(
            df["rep_date"],
            errors="coerce"
        )
        .dt.date
    )

    gold_df["folio_date"] = (
        pd.to_datetime(
            df["traddate"],
            errors="coerce"
        )
        .dt.date
    )

    # =====================================================
    # ARN
    # =====================================================

    gold_df["arn"] = (
        df["investor_broker_code"]
        .fillna("")
        .astype(str)
        .str.strip()
    )


    gold_df.loc[
        gold_df["arn"] == "",
        "arn"
    ] = None

    # =====================================================
    # HOLDING DETAILS
    # =====================================================


    gold_df["holding_nature"] = (
        df["investor_holding_nature"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    gold_df["nominee_name"] = (
        df["investor_nominee_name"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    gold_df["nominee_relation"] = (
        df["investor_nominee_relation"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    gold_df["nominee_pct"] = (
        df["investor_nominee_percentage"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    # =====================================================
    # KYC STATUS
    # =====================================================

    gold_df["kyc_status"] = None

    kyc_available = (
        df["investor_ckyc_no"]
        .fillna("")
        .astype(str)
        .str.strip()
        != ""
    )

    gold_df.loc[
        kyc_available,
        "kyc_status"
    ] = "Verified"

    # =====================================================
    # BANK DETAILS
    # =====================================================


    gold_df["bank_name"] = (

        df["investor_bank_name"]

        .fillna("")

        .astype(str)

        .str.strip()

    )


    gold_df.loc[

        gold_df["bank_name"] == "",

        "bank_name"

    ] = None



    gold_df["bank_ac_last4"] = (

        df["investor_bank_account_no"]

        .fillna("")

        .astype(str)

        .str.replace(".0", "", regex=False)

        .str.strip()

        .str[-4:]

    )


    gold_df.loc[

        gold_df["bank_ac_last4"] == "",

        "bank_ac_last4"

    ] = None



    # =====================================================
    # DEMAT FLAG
    # =====================================================


    gold_df["demat_flag"] = (

        df["investor_demat_flag"]

        .fillna("")

        .astype(str)

        .str.strip()

    )


    gold_df.loc[

        gold_df["demat_flag"] == "",

        "demat_flag"

    ] = None

        # =====================================================
    # APPLICATION MANAGED FIELDS
    # =====================================================

    gold_df["client_id"] = None
    gold_df["amc_id"] = None

    # mapped from gold.scheme bridge

    gold_df["scheme_id"] = df["scheme_id"]

    gold_df["purchase_date"] = None


    gold_df["arn_id"] = None


    gold_df["avg_cost_nav"] = None


    gold_df["invested_amount"] = None


    gold_df["current_nav"] = None


    gold_df["current_value"] = None


    gold_df["nav_date"] = None


    gold_df["unrealised_gain"] = None


    gold_df["xirr"] = None


    gold_df["first_purchase_date"] = None


    gold_df["source_file_id"] = None



    # =====================================================
    # TIMESTAMP FIELDS
    # =====================================================


    current_time = datetime.now(
        timezone.utc
    )



    gold_df["last_synced_at"] = current_time



    gold_df["created_at"] = datetime.now()



    # =====================================================
    # FINAL GOLD HOLDINGS COLUMN ORDER
    # =====================================================


    gold_df = gold_df[

        HOLDINGS_COLUMNS

    ]



    # =====================================================
    # FINAL VALIDATION
    # =====================================================


    print("=" * 80)

    print("TRANSACTION GRAIN PREVIEW")

    print("=" * 80)



    print(gold_df.head())



    print(

        "Transaction rows:",

        len(gold_df)

    )



    print(

        "Missing Scheme IDs:",

        gold_df["scheme_id"].isna().sum()

    )



    return gold_df

# =====================================================
# UNIT DIRECTION
# =====================================================


def holding_direction(df):

    """+1 where the transaction adds units to the position, -1 where it takes
    them away, 0 where it moves only money."""


    flag = (

        df["trxn_type_flag"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()

    )


    purred = (

        df["td_purred"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()

    )


    direction = pd.Series(

        0,

        index=df.index,

        dtype="int8"

    )


    direction.loc[

        flag.isin(CAMS_INFLOW_FLAGS)

    ] = 1


    direction.loc[

        flag.isin(CAMS_OUTFLOW_FLAGS)

    ] = -1


    # KFIN leaves trxn_type_flag blank and carries the RTA's own
    # purchase / redemption / dividend marker instead. Note that it marks
    # pledging (PLDO) as R; this follows the feed rather than overriding it.

    without_flag = flag.eq("")


    direction.loc[

        without_flag
        &
        purred.eq("P")

    ] = 1


    direction.loc[

        without_flag
        &
        purred.eq("R")

    ] = -1



    # Anything left at 0 that is not a known money-only type is a transaction
    # type nobody has mapped. It contributes no units, which is the safe
    # default, but it is printed rather than swallowed.

    unmapped = (

        direction.eq(0)

        &
        ~flag.isin(CAMS_NEUTRAL_FLAGS)

        &
        ~(without_flag & purred.eq("D"))

    )


    print()

    print(

        "Unit direction: {} in, {} out, {} money-only".format(

            int(direction.eq(1).sum()),

            int(direction.eq(-1).sum()),

            int(

                direction.eq(0).sum()

                -
                int(unmapped.sum())

            )

        )

    )


    if unmapped.any():


        print(

            "{} row(s) of unmapped transaction type, "
            "contributing 0 units:".format(

                int(unmapped.sum())

            )

        )


        print(

            df.loc[unmapped]
            .groupby(
                [
                    "source",
                    "trxn_type_flag",
                    "trxntype"
                ],
                dropna=False
            )
            .size()
            .sort_values(ascending=False)
            .head(20)

        )


    return direction

# =====================================================
# AGGREGATE TO POSITION GRAIN
# =====================================================


def aggregate_holdings(gold_df, direction):

    """Roll the per-transaction rows up to one row per
    (rta, folio_number, scheme_id) — a folio's position in a scheme, which is
    what a holding is.

    Before this, gold.holdings was written at transaction grain: 166,996 rows
    over 4,947 distinct positions, one per transaction, each run appending
    more."""


    print("=" * 80)

    print("AGGREGATING TO POSITION GRAIN")

    print("=" * 80)


    keys = [

        "rta",

        "folio_number",

        "scheme_id"

    ]


    # The date columns hold datetime.date objects next to NaN, which min/max
    # cannot order ('>=' not supported between date and float). They go back to
    # dates after the rollup.

    gold_df = gold_df.copy()


    for column in [

        "as_on_date",

        "folio_date"

    ]:

        gold_df[column] = pd.to_datetime(

            gold_df[column],

            errors="coerce"

        )


    # The sign lives in the transaction type, not in the value, so the value is
    # taken as a magnitude and signed here.

    gold_df = gold_df.assign(

        signed_units=(

            pd.to_numeric(
                gold_df["units"],
                errors="coerce"
            )
            .abs()

            * direction

        ),

        signed_amount=(

            pd.to_numeric(
                gold_df["market_value"],
                errors="coerce"
            )
            .abs()

            * direction

        ),

        inflow_date=gold_df["folio_date"].where(

            direction > 0

        )

    )


    # Attributes belong to the folio, not to the transaction, and can be
    # restated over time. Ordering by transaction date and taking "last" gives
    # the most recent non-null value of each — GroupBy.last() skips nulls.

    attributes = [

        "pan",

        "arn",

        "holding_nature",

        "nominee_name",

        "nominee_relation",

        "nominee_pct",

        "kyc_status",

        "bank_name",

        "bank_ac_last4",

        "demat_flag"

    ]


    aggregation = {

        column: "last"

        for column in attributes

    }


    aggregation.update({

        "signed_units": "sum",

        "signed_amount": "sum",

        "as_on_date": "max",

        "folio_date": "min",

        "inflow_date": "min"

    })


    position = (

        gold_df

        .sort_values(
            "folio_date",
            na_position="first"
        )

        .groupby(
            keys,
            dropna=False,
            sort=False
        )

        .agg(aggregation)

        .reset_index()

    )


    print()

    print(

        "Transaction rows in :",

        len(gold_df)

    )


    print(

        "Positions out       :",

        len(position)

    )



    # =====================================================
    # POSITION MEASURES
    # =====================================================


    position["units"] = (

        position.pop("signed_units")
        .round(4)

    )


    # Net of what went in and what came back out — a cost, not a valuation.

    position["invested_amount"] = (

        position.pop("signed_amount")
        .round(4)

    )


    position["first_purchase_date"] = position.pop(

        "inflow_date"

    )


    for column in [

        "as_on_date",

        "folio_date",

        "first_purchase_date"

    ]:

        position[column] = position[column].dt.date


    position["avg_cost_nav"] = (

        (

            position["invested_amount"]

            /
            position["units"].where(

                position["units"] > 0

            )

        )
        .round(6)

    )


    print(

        "Positions with units <= 0 :",

        int(

            (

                position["units"].fillna(0)

                <= 0

            ).sum()

        ),

        "(redemptions at or past the units this delivery accounts for)"

    )



    # =====================================================
    # NOT DERIVABLE HERE
    # =====================================================

    # market_value, current_nav, current_value, nav_date and unrealised_gain all
    # need a NAV as of a valuation date. gold.scheme_nav has one, but pricing a
    # position is not this module's job, so they stay NULL rather than carrying
    # the old per-transaction amount, which was never a market value.

    for column in [

        "market_value",

        "client_id",

        "amc_id",

        "purchase_date",

        "arn_id",

        "current_nav",

        "current_value",

        "nav_date",

        "unrealised_gain",

        "xirr",

        "source_file_id"

    ]:

        position[column] = None



    # =====================================================
    # IDENTITY AND TIMESTAMPS
    # =====================================================


    position["id"] = [

        uuid.uuid5(

            HOLDING_NAMESPACE,

            "{}|{}|{}".format(

                "" if pd.isna(rta) else str(rta).strip().lower(),

                "" if pd.isna(folio) else str(folio).strip().lower(),

                "" if pd.isna(scheme) else str(scheme)

            )

        )

        for rta, folio, scheme in zip(

            position["rta"],

            position["folio_number"],

            position["scheme_id"]

        )

    ]


    position["last_synced_at"] = datetime.now(

        timezone.utc

    )


    position["created_at"] = datetime.now()


    position = position[

        HOLDINGS_COLUMNS

    ]


    print()

    print("GOLD HOLDINGS PREVIEW")

    print("-" * 80)

    print(

        position[
            [
                "rta",
                "folio_number",
                "units",
                "invested_amount",
                "avg_cost_nav",
                "first_purchase_date"
            ]
        ]
        .head()

    )


    return position

# =====================================================
# LOAD GOLD HOLDINGS DATA
# =====================================================


def load_holdings(gold_df):


    print("=" * 80)
    print("LOADING DATA INTO GOLD.HOLDINGS")
    print("=" * 80)



    # =====================================================
    # VARCHAR VALIDATION
    # =====================================================


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

        "demat_flag": 4

    }



    for col, limit in varchar_limits.items():


        if col in gold_df.columns:


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

                    f"{col} length {max_len} exceeds limit {limit}"

                )



    # =====================================================
    # NORMALIZE KEYS
    # =====================================================

    # The stored rows were written with these two uppercased and stripped, so the
    # upsert has to match on the same form or it would insert a second row for
    # the same position.

    for col in [

        "rta",

        "folio_number"

    ]:

        gold_df[col] = (

            gold_df[col]

            .fillna("")

            .astype(str)

            .str.strip()

            .str.upper()

        )



    if len(gold_df) == 0:


        print(

            "No holdings to load"

        )


        return True



    # =====================================================
    # UPSERT INTO GOLD
    # =====================================================

    # A position is not immutable: every new transaction changes its units, its
    # cost and its dates. An insert-only load could never express that — a
    # position already stored was simply skipped, and went stale until somebody
    # rebuilt the table.
    #
    # So the rows are staged and merged in on holdings_position_key. id and
    # created_at are deliberately left out of the UPDATE: the id is derived from
    # the key and so cannot change, and created_at records when the position was
    # first seen.

    updatable = [

        column

        for column in HOLDINGS_COLUMNS

        if column not in {

            "id",

            "rta",

            "folio_number",

            "scheme_id",

            "created_at"

        }

    ]


    columns = ", ".join(HOLDINGS_COLUMNS)


    assignments = ", ".join(

        f"{column} = EXCLUDED.{column}"

        for column in updatable

    )


    try:


        before = pd.read_sql(

            "SELECT count(*) AS n FROM gold.holdings",

            engine

        ).iloc[0]["n"]


        # The staging table is created from gold.holdings itself rather than
        # letting to_sql infer it. Left to pandas, every column comes out text,
        # and the INSERT then fails with 'column "id" is of type uuid but
        # expression is of type text'. UNLOGGED because it lives for one
        # statement.

        with engine.begin() as connection:


            connection.exec_driver_sql(

                "DROP TABLE IF EXISTS gold.holdings_stage"

            )


            connection.exec_driver_sql(

                """

                CREATE UNLOGGED TABLE gold.holdings_stage

                (LIKE gold.holdings)

                """

            )


        gold_df.to_sql(

            name="holdings_stage",

            con=engine,

            schema="gold",

            if_exists="append",

            index=False,

            method="multi",

            chunksize=5000

        )


        with engine.begin() as connection:


            connection.exec_driver_sql(

                f"""

                INSERT INTO gold.holdings ({columns})

                SELECT {columns}

                FROM gold.holdings_stage

                ON CONFLICT ON CONSTRAINT holdings_position_key

                DO UPDATE SET {assignments}

                """

            )


            connection.exec_driver_sql(

                "DROP TABLE IF EXISTS gold.holdings_stage"

            )


        after = pd.read_sql(

            "SELECT count(*) AS n FROM gold.holdings",

            engine

        ).iloc[0]["n"]


        print()

        print(

            "Positions upserted :",

            len(gold_df)

        )


        print(

            "  inserted         :",

            int(after - before)

        )


        print(

            "  updated in place :",

            int(len(gold_df) - (after - before))

        )


        return True

    except Exception as e:
        print()
        print("ERROR WHILE LOADING GOLD HOLDINGS")
        print(type(e).__name__)
        print(e)

        # Staging is a scratch table; leaving it behind would make the next run's
        # if_exists="replace" the only thing that cleans it up.

        try:

            with engine.begin() as connection:

                connection.exec_driver_sql(

                    "DROP TABLE IF EXISTS gold.holdings_stage"

                )

        except Exception:

            pass

        return False

# =====================================================
# MAIN EXECUTION
# =====================================================


if __name__ == "__main__":


    print("\n")

    print("=" * 80)

    print("STARTING GOLD HOLDINGS ETL")

    print("=" * 80)



    try:


        # =================================================
        # EXTRACT
        # =================================================


        df = extract_holdings()



        # =================================================
        # TRANSFORM
        # =================================================


        gold_df = transform_holdings(

            df

        )



        # =================================================
        # LOAD
        # =================================================


        status = load_holdings(

            gold_df

        )



        if status:


            print("\n")

            print("=" * 80)

            print("GOLD HOLDINGS ETL COMPLETED SUCCESSFULLY")

            print("=" * 80)



        else:


            print("\n")

            print("=" * 80)

            print("GOLD HOLDINGS ETL FAILED")

            print("=" * 80)

    except Exception as e:

        print("\n")
        print("=" * 80)
        print("GOLD HOLDINGS ETL ERROR")
        print("=" * 80)
        print(type(e).__name__)
        print(e)
    
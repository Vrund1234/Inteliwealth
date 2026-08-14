import os
import uuid

import pandas as pd
from sqlalchemy import text

from utils.db import engine


# =====================================================
# GOLD LAYER  -  CAMS WBR REPORTS, DERIVED
# =====================================================
#
# extract_ / transform_ / load_ per report, same shape as
# etl_gold_transaction.py and etl_gold_holdings.py.
#
# The WBR reports are OUTPUT. They are built out of the silver tables the CAMS
# R2 / R9 / R49 and KFIN MFSD feeds populate — there is no WBR input file and no
# WBR bronze or silver stage:
#
#   WBR36 / WBR36H  <- silver.transaction_master_new  (scheme list)
#   WBR56           <- silver.investor_master         (folio demographics)
#   WBR68           <- silver.transaction_master_new  (invalid-EUIN ledger)
#
# Three things are done differently from the existing gold loaders, each for a
# reason that showed up while running this against the real files:
#
#   1. Row ids are uuid5 over the natural key, so re-running produces the SAME
#      id for the same business row. gold.holdings uses uuid4() and therefore
#      regenerates every id on every run, which breaks the
#      folio_nominees.holding_id references that point at it.
#
#   2. The write is a real UPSERT against the UNIQUE constraint on the natural
#      key, inside one transaction. The existing gold loaders append after an
#      anti-join, which means a row whose values CHANGED is skipped rather than
#      updated.
#
#   3. Grain is asserted after every load. That one check is what would have
#      caught gold.holdings carrying 128,766 rows for 3,591 distinct positions.


NAMESPACE = uuid.UUID("3f2b6c48-7f4a-5d21-9e6b-1c8a4d0e5b72")


# =====================================================
# WHAT THE CAMS FEED CANNOT SOURCE
# =====================================================
#
# These columns exist in the report layout and in the gold tables, and they load
# as NULL. Each was checked against the actual input files, not assumed. They are
# listed here rather than silently omitted because the layout is the contract
# with whoever consumes the report: dropping a column changes the file, leaving
# it empty does not.
#
# Anything added to this dict is also printed at the end of every load, so an
# empty column stays visible instead of becoming normal.

UNAVAILABLE = {

    "WBR36": {
        "upfront": "no brokerage component breakdown in R2",
        "afe": "no brokerage component breakdown in R2",
        "trailer_fee": (
            "trail commission is computed by the RTA on AUM over a period. R2's "
            "BROKCOMM is per-transaction commission: 1,036,504.67 against the "
            "provider's 3,139,008.5685 for the same schemes, and unrelated "
            "per product (D104: 15,931.82 vs 3,950.456368)"
        ),
        "trxn_charges": "TRXN_CHARGES is 0 on all 90,536 R2 rows",
        "clawback": "no clawback column in R2",
        "incentives": "no incentives column in R2"
    },

    "WBR56": {
        "fh_kyc": "CAMS R9 carries FH_CKYC_NO, a CKYC number, not a KYC status",
        "gu_kyc": "same, G_CKYC_NO",
        "jh1_kyc": "same, JH1_CKYC",
        "jh2_kyc": "same, JH2_CKYC",
        "fh_kyc_desc": "no KYC status description anywhere in the CAMS feed",
        "gu_kyc_desc": "no KYC status description anywhere in the CAMS feed",
        "jh1_kyc_desc": "no KYC status description anywhere in the CAMS feed",
        "jh2_kyc_desc": "no KYC status description anywhere in the CAMS feed",
        "fh_g_aadharlink": "R9's AADHAAR column is blank on every CAMS folio",
        "jh1_aadharlink": "no per-holder Aadhaar link status in the CAMS feed",
        "jh2_aadharlink": "no per-holder Aadhaar link status in the CAMS feed",
        "brok_name": "R9 carries BROKER_CODE only; no broker master to join to"
    },

    "WBR68": {
        "trxn_desc": "R2 carries TRXNTYPE but no description for it",
        "email": (
            "the provider writes the DISTRIBUTOR's email here, not the "
            "investor's: all 9 rows of the sample carry prerakmpatel@gmail.com "
            "across 5 different folios. R2 has no distributor contact column and "
            "there is no broker master to join to. Filling it from "
            "investor_master.email looked right and was wrong"
        ),
        "sip_regn_date": (
            "no clean key from a transaction to its SIP registration. "
            "SIPTRXNNO to sip_master_new.ft_sip_regno fans out to 359,518 "
            "pairs, so joining on it would multiply the ledger"
        )
    }
}


# Populated only for folios the KFIN feed supplies. The CAMS R9 file leaves every
# one of them blank, which is why they are in UNAVAILABLE above as well: for a
# CAMS-only delivery these stay NULL, and for a KFIN delivery they fill in.
KFIN_ONLY_COLUMNS = {
    "fh_kyc": "kyc1flag",
    "jh1_kyc": "kyc2flag",
    "jh2_kyc": "kyc3flag",
    "gu_kyc": "kycgflag",
    "fh_g_aadharlink": "holder_1_aadhaar_info",
    "jh1_aadharlink": "holder_2_aadhaar_info",
    "jh2_aadharlink": "holder_3_aadhaar_info"
}


# =====================================================
# NATURAL KEYS
# =====================================================
#
# Also the UNIQUE constraints in sql/wbr_gold_tables.sql and the ON CONFLICT
# targets. One declaration, so the three can never drift apart.

NATURAL_KEYS = {

    # report_variant stays in the key because the provider delivers two variants
    # of this report that share most of their product codes. Only STD is
    # derivable from the CAMS feed.
    "brokerage_by_scheme": [
        "report_period",
        "report_variant",
        "product_code"
    ],

    "investor_kyc_status": [
        "amc_code",
        "folio"
    ],

    "invalid_euin": [
        "amc_code",
        "trxn_no"
    ]
}


# =====================================================
# STABLE ID
# =====================================================

def stable_uuid(*parts):

    key = "|".join(
        "" if p is None or pd.isna(p) else str(p).strip()
        for p in parts
    )

    return str(uuid.uuid5(NAMESPACE, key))


# =====================================================
# SAFE READ
# =====================================================

def read_silver(query):

    try:

        return pd.read_sql(query, engine)

    except Exception as e:

        print(f"Could not read silver : {e}")

        return pd.DataFrame()


# =====================================================
# RESOLVE REPORT PERIOD
# =====================================================
#
# WBR36 has no date of its own, and report_period is part of its natural key, so
# a wrong value does not collide with the right one: both rows survive and the
# report doubles. Order of preference:
#
#   1. WBR_REPORT_PERIOD in the environment, for re-running an old delivery
#   2. the latest trade date in silver.transaction_master_new
#   3. the latest report date in silver.investor_master
#   4. the current year, with a warning

def resolve_report_period():

    override = os.environ.get("WBR_REPORT_PERIOD", "").strip()

    if override:

        print(f"report_period : {override} (WBR_REPORT_PERIOD)")

        return override

    sources = [
        ("silver.transaction_master_new", "traddate"),
        ("silver.investor_master", "report_date")
    ]

    for table, column in sources:

        try:

            result = pd.read_sql(
                f"SELECT max({column}) AS period_date FROM {table}",
                engine
            )

        except Exception:

            continue

        period_date = result.iloc[0]["period_date"]

        if period_date is not None and not pd.isna(period_date):

            period = str(pd.Timestamp(period_date).year)

            print(f"report_period : {period} ({table}.{column})")

            return period

    period = str(pd.Timestamp.now().year)

    print(
        f"report_period : {period} (current year - no dates available in "
        f"silver). Set WBR_REPORT_PERIOD if the delivery is not for this year."
    )

    return period


# =====================================================
# UPSERT
# =====================================================

def upsert(table_name, df, chunksize=2000):

    if df is None or df.empty:

        print(f"gold.{table_name} : no rows")
        return 0

    conflict_cols = NATURAL_KEYS[table_name]

    db_columns = pd.read_sql(
        f"""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'gold'
        AND table_name = '{table_name}'
        ORDER BY ordinal_position
        """,
        engine
    )["column_name"].tolist()

    if not db_columns:

        raise RuntimeError(
            f"gold.{table_name} does not exist. "
            f"Run python_scripts/sql/wbr_gold_tables.sql first."
        )

    columns = [c for c in db_columns if c in df.columns]

    payload = df[columns].copy()

    # A duplicate key inside one batch aborts the statement, so collapse it here.
    before = len(payload)

    payload = payload.drop_duplicates(
        subset=conflict_cols,
        keep="last"
    )

    if len(payload) != before:

        print(
            f"gold.{table_name} : collapsed {before - len(payload)} rows "
            f"duplicated on {conflict_cols}"
        )

    payload = payload.astype(object).where(pd.notnull(payload), None)

    quoted = ", ".join(f'"{c}"' for c in columns)

    placeholders = ", ".join(f":{c}" for c in columns)

    update_cols = [
        c for c in columns
        if c not in conflict_cols
        and c not in ("id", "created_at")
    ]

    update_set = ", ".join(
        f'"{c}" = EXCLUDED."{c}"' for c in update_cols
    )

    conflict_target = ", ".join(f'"{c}"' for c in conflict_cols)

    statement = text(
        f'INSERT INTO gold."{table_name}" ({quoted}) '
        f'VALUES ({placeholders}) '
        f'ON CONFLICT ({conflict_target}) DO UPDATE SET {update_set}'
        if update_set else
        f'INSERT INTO gold."{table_name}" ({quoted}) '
        f'VALUES ({placeholders}) '
        f'ON CONFLICT ({conflict_target}) DO NOTHING'
    )

    records = payload.to_dict(orient="records")

    # One transaction for the whole load. A failure part-way leaves the table as
    # it was rather than half-written.
    with engine.begin() as conn:

        for start in range(0, len(records), chunksize):

            conn.execute(
                statement,
                records[start:start + chunksize]
            )

    print(f"gold.{table_name} : {len(records)} rows upserted")

    return len(records)


# =====================================================
# ASSERT GRAIN
# =====================================================
#
# Rows must equal distinct natural keys. Anything else means the declared grain
# and the actual data disagree, and the table is inflating.

def assert_grain(table_name):

    key_cols = ", ".join(
        f'"{c}"' for c in NATURAL_KEYS[table_name]
    )

    result = pd.read_sql(
        f"""
        SELECT
            count(*) AS rows,
            count(DISTINCT ({key_cols})) AS keys
        FROM gold.{table_name}
        """,
        engine
    )

    rows = int(result.iloc[0]["rows"])
    keys = int(result.iloc[0]["keys"])

    ratio = (rows / keys) if keys else 0.0

    print(
        f"gold.{table_name} : {rows} rows / {keys} keys (ratio {ratio:.2f})"
    )

    if keys and rows > keys:

        raise AssertionError(
            f"gold.{table_name} grain violated : {rows} rows for {keys} "
            f"distinct {NATURAL_KEYS[table_name]}"
        )

    return rows, keys, ratio


# =====================================================
# HELPERS
# =====================================================

def clean_text(series):

    return (
        series
        .astype("string")
        .str.strip()
        .replace({"": None, "nan": None, "None": None, "NaT": None})
    )


def to_date(series):

    return pd.to_datetime(series, errors="coerce").dt.date


def to_number(series):

    return pd.to_numeric(series, errors="coerce")


def blank_zero(series):
    """A folio or transaction reference of "0" means absent, not zero.

    R2 writes 0 into ALTFOLIO and SIPTRXNNO when there is no alternate folio and
    no SIP; the provider's report leaves those cells empty. Emitting "0" would
    read as a real reference.
    """

    cleaned = clean_text(series)

    return cleaned.where(cleaned.ne("0"), None)


def strip_amc_prefix(product_code, amc_code):
    """R2's PRODCODE carries the AMC letter in front of the scheme code; the
    report does not.

    B51 -> 51, G201 -> 201, TSCFG -> SCFG, matching the provider's own file on
    every row of the WBR68 sample. Only a genuine prefix is removed, so a code
    that merely happens to start with those letters survives intact.
    """

    if product_code is None or pd.isna(product_code):
        return None

    code = str(product_code).strip()
    amc = "" if amc_code is None or pd.isna(amc_code) else str(amc_code).strip()

    if amc and len(code) > len(amc) and code.upper().startswith(amc.upper()):
        return code[len(amc):]

    return code or None


def compound(code, name):
    """The provider writes location and state as "GU/Gujarat", and a bare "/"
    when both halves are unknown.

    bronze.state_code carries a numeric state_id, not the provider's two-letter
    code, so the code half cannot be reproduced and is left empty — "/Gujarat"
    reads as "name known, code unknown" in the provider's own convention.
    """

    code = "" if code is None or pd.isna(code) else str(code).strip()
    name = "" if name is None or pd.isna(name) else str(name).strip()

    return f"{code}/{name}"


def add_unavailable_columns(df, report_code):

    for column in UNAVAILABLE.get(report_code, {}):

        if column not in df.columns:
            df[column] = None

    return df


def report_unavailable(report_code, df):
    """Print every layout column that came out entirely empty, with the reason.

    Three cases, and they are not the same thing:

      - the feed has no such column at all, which is what UNAVAILABLE records
      - the column exists and every row in THIS delivery happens to be blank
      - neither, which is a bug

    Printing them together under one heading would make the third invisible, so
    they are separated. Only the third asks for a person.
    """

    reasons = UNAVAILABLE.get(report_code, {})

    if df is None or df.empty:
        return

    empty = sorted(
        c for c in df.columns
        if df[c].isna().all()
    )

    if not empty:
        return

    structural = [c for c in empty if c in reasons]
    incidental = [c for c in empty if c not in reasons]

    if structural:

        print(
            f"  {report_code} : {len(structural)} column(s) the feed cannot "
            f"source"
        )

        for column in structural:
            print(f"    {column:20s} {reasons[column]}")

    if incidental:

        print(
            f"  {report_code} : {len(incidental)} column(s) present in the feed "
            f"but blank on every row of this delivery"
        )

        for column in incidental:
            print(f"    {column}")


# =====================================================
# WBR36 / WBR36H  -  BROKERAGE SUMMARY BY SCHEME
# =====================================================
#
# The scheme list is derivable; the money is not. Every measure is NULL, for the
# reasons in UNAVAILABLE. What this table therefore delivers is the report's
# skeleton — one row per scheme transacted in the period, in the provider's
# column order — and it is honest about the rest.
#
# Only the STD variant is produced. Nothing in R2 marks which schemes belong to
# the H variant, so inventing that split would be worse than omitting it.

def extract_brokerage_by_scheme():

    return read_silver(
        """
        SELECT
            prodcode,
            scheme,
            traddate,
            source
        FROM silver.transaction_master_new
        WHERE prodcode IS NOT NULL
        AND btrim(prodcode) <> ''
        """
    )


def transform_brokerage_by_scheme(df, report_period=None):

    if df is None or df.empty:
        return pd.DataFrame()

    df = df.copy()

    if report_period is None:
        report_period = resolve_report_period()

    df["product_code"] = clean_text(df["prodcode"])
    df["product_name"] = clean_text(df["scheme"])

    df = df[df["product_code"].notna()]

    # Earliest appearance decides the row order, which is what lets the export
    # be byte-stable across runs.
    df["seen"] = to_date(df["traddate"])

    grouped = (
        df
        .groupby("product_code", dropna=False)
        .agg(
            product_name=("product_name", "last"),
            source=("source", "last"),
            first_seen=("seen", "min")
        )
        .reset_index()
    )

    grouped = grouped.sort_values(
        ["first_seen", "product_code"],
        na_position="last"
    ).reset_index(drop=True)

    grouped["report_period"] = report_period
    grouped["report_variant"] = "STD"
    grouped["source_row"] = range(1, len(grouped) + 1)

    grouped = add_unavailable_columns(grouped, "WBR36")

    grouped["id"] = [
        stable_uuid(row.report_period, row.report_variant, row.product_code)
        for row in grouped.itertuples()
    ]

    grouped["updated_at"] = pd.Timestamp.now()

    print(
        f"gold brokerage_by_scheme : {len(df)} transaction rows -> "
        f"{len(grouped)} schemes at declared grain"
    )

    return grouped


def load_brokerage_by_scheme(df):

    upsert("brokerage_by_scheme", df)

    assert_grain("brokerage_by_scheme")

    report_unavailable("WBR36", df)


# =====================================================
# WBR56  -  KYC STATUS OF INVESTOR
# =====================================================
#
# silver.investor_master holds one row per folio per scheme, so the folio grain
# is reached by deduplication. The KYC and Aadhaar columns come from the KFIN
# feed when it supplies them and stay NULL otherwise; the CAMS R9 file carries
# none of them.

def extract_investor_kyc_status():

    return read_silver(
        """
        SELECT
            amc_code,
            folio_no,
            broker_code,
            investor_name,
            pan_no,
            joint_name_1,
            joint1_pan,
            joint_name_2,
            joint2_pan,
            guardian_name,
            guardian_pan,
            address1,
            address2,
            address3,
            city,
            pincode,
            state,
            country,
            phone_res,
            phone_off,
            mobile_no,
            email,
            fax_residence,
            fax_office,
            kyc1flag,
            kyc2flag,
            kyc3flag,
            kycgflag,
            holder_1_aadhaar_info,
            holder_2_aadhaar_info,
            holder_3_aadhaar_info,
            report_date,
            source
        FROM silver.investor_master
        WHERE folio_no IS NOT NULL
        AND btrim(folio_no::text) <> ''
        """
    )


def transform_investor_kyc_status(df):

    if df is None or df.empty:
        return pd.DataFrame()

    df = df.copy()

    df["amc_code"] = clean_text(df["amc_code"])
    df["folio"] = clean_text(df["folio_no"].astype("string"))

    # amc_code is half the natural key, so a row without one cannot be written.
    # The count is printed rather than swallowed: on the current data this drops
    # every KFIN folio, because the KFIN feed leaves amc_code blank, and that is
    # a data-mapping gap worth seeing on every run instead of a quiet 60% loss.
    before = len(df)

    df = df[df["folio"].notna() & df["amc_code"].notna()]

    dropped = before - len(df)

    if dropped:

        print(
            f"  investor_kyc_status : {dropped} of {before} silver rows skipped "
            f"for a missing amc_code or folio (natural key incomplete)"
        )

    if df.empty:
        return pd.DataFrame()

    df["rep_date"] = to_date(df["report_date"])

    # One row per folio. The most recently reported row wins, because a folio's
    # demographics change over time and the report carries the current state.
    df = (
        df
        .sort_values(["amc_code", "folio", "rep_date"], na_position="first")
        .drop_duplicates(subset=["amc_code", "folio"], keep="last")
        .reset_index(drop=True)
    )

    out = pd.DataFrame()

    out["amc_code"] = df["amc_code"]
    out["folio"] = df["folio"]

    out["brok_dlr_code"] = clean_text(df["broker_code"])
    out["inv_name"] = clean_text(df["investor_name"])
    out["tax_no"] = clean_text(df["pan_no"])

    out["jname1"] = clean_text(df["joint_name_1"])
    out["jointpan1"] = clean_text(df["joint1_pan"])
    out["jname2"] = clean_text(df["joint_name_2"])
    out["jointpan2"] = clean_text(df["joint2_pan"])
    out["guardian"] = clean_text(df["guardian_name"])
    out["guardian_panno"] = clean_text(df["guardian_pan"])

    out["address1"] = clean_text(df["address1"])
    out["address2"] = clean_text(df["address2"])
    out["address3"] = clean_text(df["address3"])
    out["city"] = clean_text(df["city"])
    out["pincode"] = clean_text(df["pincode"].astype("string"))
    out["country"] = clean_text(df["country"])

    # Compound columns, code half unknown. See compound().
    out["location"] = [
        compound(None, city) for city in df["city"]
    ]

    out["state"] = [
        compound(None, state) for state in df["state"]
    ]

    out["phone_res"] = clean_text(df["phone_res"])
    out["phone_off"] = clean_text(df["phone_off"])
    out["mobile_no"] = clean_text(df["mobile_no"].astype("string"))
    out["email"] = clean_text(df["email"])
    out["fax_res"] = clean_text(df["fax_residence"])
    out["fax_off"] = clean_text(df["fax_office"])

    for target, source_column in KFIN_ONLY_COLUMNS.items():
        out[target] = clean_text(df[source_column])

    out["rep_date"] = df["rep_date"]

    # The reporting window is the span the delivery covers. It is a report
    # parameter, not a per-folio value, so every row carries the same pair.
    reported = out["rep_date"].dropna()

    out["rep_from_date"] = reported.min() if len(reported) else None
    out["rep_to_date"] = reported.max() if len(reported) else None

    out["source"] = clean_text(df["source"])
    out["source_row"] = range(1, len(out) + 1)

    out = add_unavailable_columns(out, "WBR56")

    out["id"] = [
        stable_uuid(row.amc_code, row.folio)
        for row in out.itertuples()
    ]

    out["updated_at"] = pd.Timestamp.now()

    print(
        f"gold investor_kyc_status : {len(df)} folios at declared grain"
    )

    return out


def load_investor_kyc_status(df):

    upsert("investor_kyc_status", df)

    assert_grain("investor_kyc_status")

    report_unavailable("WBR56", df)


# =====================================================
# WBR68  -  INVALID EUIN REPORT
# =====================================================
#
# Every transaction that quoted an EUIN which is not valid. The filter is
# euin_valid <> 'Y' AND euin <> '', never euin_valid = 'N':
#
#   - the provider's own file carries both 'N' and 'F' under the same reason
#   - a blank euin_valid with a blank euin means no EUIN was quoted at all,
#     which is a different condition and not this report's subject
#
# A BLANK euin_valid is not an invalid EUIN. 43,894 silver rows carry an EUIN
# with euin_valid blank — validity simply is not reported for them — against 406
# that carry an explicit non-'Y' verdict. Treating blank as invalid inflated this
# report from 406 rows to 44,299.
#
# email is NOT the investor's — see UNAVAILABLE. It is the distributor's, and
# nothing in the CAMS feed carries it.

def extract_invalid_euin():

    return read_silver(
        """
        SELECT
            t.amc_code,
            t.trxnno,
            t.brokcode,
            t.application_no,
            t.folio_no,
            t.old_folio,
            t.altfolio,
            t.scheme_folio_number,
            t.inv_name,
            t.pan,
            t.prodcode,
            t.scheme,
            t.trxntype,
            t.amount,
            t.subbrok,
            t.sub_brk_arn,
            t.ter_location,
            t.location,
            t.usercode,
            t.usrtrxno,
            t.euin,
            t.euin_valid,
            t.traddate,
            t.postdate,
            t.sys_regn_date,
            t.siptrxnno,
            t.source
        FROM silver.transaction_master_new t
        WHERE btrim(coalesce(t.euin, '')) <> ''
        AND btrim(coalesce(t.euin_valid, '')) <> ''
        AND upper(btrim(t.euin_valid)) <> 'Y'
        """
    )


def transform_invalid_euin(df):

    if df is None or df.empty:
        return pd.DataFrame()

    df = df.copy()

    out = pd.DataFrame()

    out["amc_code"] = clean_text(df["amc_code"])
    out["trxn_no"] = clean_text(df["trxnno"].astype("string"))

    out["arn_code"] = clean_text(df["brokcode"])
    out["appln_no"] = clean_text(df["application_no"].astype("string"))
    out["folio_no"] = clean_text(df["folio_no"].astype("string"))
    out["folio"] = out["folio_no"]
    out["folio_old"] = clean_text(df["old_folio"].astype("string"))
    out["alt_folio"] = blank_zero(df["altfolio"].astype("string"))
    out["scheme_folio_number"] = clean_text(
        df["scheme_folio_number"].astype("string")
    )

    out["inv_name"] = clean_text(df["inv_name"])
    out["inv_pan"] = clean_text(df["pan"])

    out["sch_code"] = [
        strip_amc_prefix(code, amc)
        for code, amc in zip(df["prodcode"], df["amc_code"])
    ]

    # CAMS truncates SCHEME at 100 characters in R2 itself — 6,945 rows sit at
    # exactly 100 and none exceed it — so a long scheme name arrives already cut.
    # Nothing downstream can restore it.
    out["sch_name"] = clean_text(df["scheme"])

    out["trxn_type"] = clean_text(df["trxntype"])
    out["amount"] = to_number(df["amount"])

    out["subbrokcod"] = clean_text(df["subbrok"])
    out["subbrok_arn"] = clean_text(df["sub_brk_arn"])

    # The provider writes this compound, "PKD491/Palakkad" — a branch code and a
    # city. R2's TER_LOCATION is a single letter ('T', 'B'), not that branch code,
    # so pairing them produced "B/Palakkad" against the provider's
    # "PKD491/Palakkad": a plausible-looking wrong value. The city half is real
    # and the code half is left empty.
    out["location"] = [
        compound(None, city) for city in df["location"]
    ]

    out["user_code"] = clean_text(df["usercode"].astype("string"))
    out["usertxn_no"] = clean_text(df["usrtrxno"].astype("string"))

    # An inference, not a mapping: in the provider's own file cons_code holds
    # ARN-266051 on every row, the same value as arn_code. The consolidation code
    # is the distributor's main ARN, and R2 carries no separate column for it.
    # Recorded here rather than left blank because a wrong-but-visible value that
    # says where it came from beats an empty column that says nothing.
    out["cons_code"] = out["arn_code"]

    out["euin"] = clean_text(df["euin"])
    out["euin_valid"] = clean_text(df["euin_valid"])

    # Constant by definition: every row in this report is here for one reason,
    # and the provider writes it out on every row.
    out["reason"] = "Invalid EUIN"

    out["trade_date"] = to_date(df["traddate"])
    out["posted_date"] = to_date(df["postdate"])
    out["sys_reg_dt"] = to_date(df["sys_regn_date"])

    out["auto_trxn_no"] = blank_zero(df["siptrxnno"].astype("string"))
    out["sip_regn_date"] = None

    out = out[out["amc_code"].notna() & out["trxn_no"].notna()]

    if out.empty:
        return pd.DataFrame()

    out = (
        out
        .sort_values(["trade_date", "amc_code", "trxn_no"], na_position="last")
        .drop_duplicates(subset=["amc_code", "trxn_no"], keep="last")
        .reset_index(drop=True)
    )

    out["source"] = clean_text(df["source"]).iloc[0] if len(df) else None
    out["source_row"] = range(1, len(out) + 1)

    out = add_unavailable_columns(out, "WBR68")

    out["id"] = [
        stable_uuid(row.amc_code, row.trxn_no)
        for row in out.itertuples()
    ]

    out["updated_at"] = pd.Timestamp.now()

    print(
        f"gold invalid_euin : {len(out)} invalid-EUIN transactions at declared "
        f"grain"
    )

    return out


def load_invalid_euin(df):

    upsert("invalid_euin", df)

    assert_grain("invalid_euin")

    report_unavailable("WBR68", df)


# =====================================================
# LOAD ALL THREE
# =====================================================
#
# Each report loads on its own, so a failure in one does not hide the other two.
# The grain assertion inside each load_ raises, and that is deliberate: a grain
# violation means the table is inflating, which is worth stopping for.

WBR_GOLD_ENTITIES = [
    (
        "Brokerage By Scheme",
        extract_brokerage_by_scheme,
        transform_brokerage_by_scheme,
        load_brokerage_by_scheme
    ),
    (
        "Investor KYC Status",
        extract_investor_kyc_status,
        transform_investor_kyc_status,
        load_investor_kyc_status
    ),
    (
        "Invalid EUIN",
        extract_invalid_euin,
        transform_invalid_euin,
        load_invalid_euin
    )
]


def load_wbr_gold():

    print("=" * 80)
    print("GOLD : CAMS WBR REPORTS (derived from silver)")
    print("=" * 80)

    for label, extract_fn, transform_fn, load_fn in WBR_GOLD_ENTITIES:

        try:

            print(f"\nLoading Gold {label}")

            silver_df = extract_fn()

            if silver_df.empty:

                print(f"No source rows for {label}")
                continue

            gold_df = transform_fn(silver_df)

            if gold_df.empty:

                print(f"No {label} rows after transform")
                continue

            load_fn(gold_df)

            print(f"{label} loaded successfully")

        except Exception as e:

            print(f"{label} Gold Failed")
            print(e)


if __name__ == "__main__":

    load_wbr_gold()

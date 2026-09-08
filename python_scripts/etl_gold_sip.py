import calendar
import pandas as pd
import traceback

from datetime import date, datetime, timedelta, timezone

from utils.db import engine, master_engine


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
# NEXT DUE DATE DERIVATION
# ============================================================
#
# No RTA feed carries a next-due-date -- CAMS WBR49 and KFIN MFSD243 both
# stop at period_day / periodicity / from_date / to_date -- so it is derived
# here. The two RTAs are near-complementary: CAMS fills period_day (735/743)
# but never status (0/743); KFIN fills status (661/661) but never period_day
# (0/661). Hence the debit day comes from the SIP record for CAMS and from
# transaction history for KFIN, and liveness comes from status for KFIN and
# from the date columns for CAMS.
#
# See docs/superpowers/specs/2026-09-08-sip-next-due-date-design.md

# A mode over one or two debits is noise, not evidence: folio 7779295505 has a
# single debit (2026-08-18) against a from_date of the 15th. 23 live KFIN SIPs
# sit below this threshold and 14 of them contradict their own from_date.
MIN_MODAL_DEBITS = 3

# Window for the preferred mode. KFIN's older records settle T+1, so the
# all-history mode is systematically one day late; on the 11 live SIPs where
# the two windows disagree, the recent one matches day(from_date) every time.
MODAL_RECENT_MONTHS = 12

# 146 KFIN rows carry to_date = 2099-12-31 as a "no end date" sentinel. Its
# day component is an artifact and must never be read as a debit day.
SENTINEL_YEAR = 2099

CADENCE_MONTH_STEP = {
    "MONTHLY": 1,
    "BI_MONTHLY": 2,
    "QUARTERLY": 3,
    "HALF_YEARLY": 6,
    "YEARLY": 12,
}


def parse_period_days(raw):
    """period_day as a list of days-of-month.

    SEMI_MONTHLY carries 4-5 days in one field ("7,14,21,28"), which
    pd.to_numeric() coerced to NaN -- the reason sip_day was NULL on 14 rows.
    """
    days = []

    for part in str(raw if raw is not None else "").split(","):
        part = part.strip()

        if part.isdigit():
            value = int(part)

            if 1 <= value <= 31 and value not in days:
                days.append(value)

    return sorted(days)


def modal_day(counts):
    """Most frequent day in {day: debit_count}, or None below the threshold."""
    if not counts:
        return None

    day, hits = max(counts.items(), key=lambda kv: (kv[1], -kv[0]))

    return day if hits >= MIN_MODAL_DEBITS else None


def _as_text(value):
    """Text columns are pandas "string" dtype, so a missing value is pd.NA and
    `pd.NA or ""` raises. Normalise to a plain stripped str."""
    if value is None or value is pd.NA or pd.isna(value):
        return ""

    return str(value).strip()


def _as_date(value):
    """Missing dates reach these helpers as pd.NaT, which is truthy, is not
    None, and raises TypeError when compared to a date. Normalise to None."""
    if value is None or value is pd.NaT or pd.isna(value):
        return None

    return value


def sip_is_live(status, cease_date, to_date, pause_from, pause_to, today):
    """Whether the SIP should be shown an upcoming instalment.

    CAMS sends no status at all, so its liveness is inferred from the dates.
    Checked against KFIN's declared status: 238 true positives, 0 false
    positives, 2 false negatives.
    """
    cease_date = _as_date(cease_date)
    to_date = _as_date(to_date)
    pause_from = _as_date(pause_from)
    pause_to = _as_date(pause_to)

    if pause_from and pause_from <= today:
        if pause_to is None or pause_to >= today:
            return False

    declared = _as_text(status)

    if declared:
        return declared.lower().startswith("live")

    if cease_date is not None:
        return False

    # A missing to_date means "no end date", not "already finished": 14 rows
    # have neither, and 8 of them debited within the last three months.
    return to_date is None or to_date >= today


def resolve_sip_day(
    periodicity,
    period_day,
    modal_dom_recent,
    modal_dom_all,
    modal_dow,
    source,
    to_date,
    from_date,
    recent_days=None,
):
    """(kind, days, provenance) -- kind is "dom", "dow", or None."""
    cadence = _as_text(periodicity).upper()
    to_date = _as_date(to_date)
    from_date = _as_date(from_date)

    # period_day is NOT a weekday: it reads 2 on nearly every weekly SIP while
    # debits land on every weekday (39/198 CAMS, 0/185 KFIN agreement).
    if cadence == "WEEKLY":

        if modal_dow:
            return ("dow", [int(modal_dow)], "modal_weekday")

        if from_date:
            return ("dow", [from_date.isoweekday()], "from_date_weekday")

        return (None, [], "unresolved")

    days = parse_period_days(period_day)

    if days:
        return ("dom", days, "period_day")

    if modal_dom_recent:

        # A mode taken over real debits can land a day or two off the
        # schedule, because a debit that falls on a holiday or weekend moves.
        # When the declared start day is itself one of the observed debit
        # days, it is the schedule and the mode is one of those shifts:
        # folio 910106525876 debits across 10-13 with from_date on the 10th.
        if from_date and recent_days:
            start_day = from_date.day

            if start_day != int(modal_dom_recent) and start_day in recent_days:
                return ("dom", [start_day], "from_date_confirmed")

        return ("dom", [int(modal_dom_recent)], "modal_day_recent")

    if modal_dom_all:
        return ("dom", [int(modal_dom_all)], "modal_day_all")

    # to_date's day matches period_day on 478/523 CAMS monthly rows, but only
    # 47% of the time on KFIN, so it is a CAMS-only fallback.
    if _as_text(source).upper() == "CAMS":
        if to_date and to_date.year < SENTINEL_YEAR:
            return ("dom", [to_date.day], "to_date")

    if from_date:
        return ("dom", [from_date.day], "from_date")

    return (None, [], "unresolved")


def _clamp_to_month(year, month, day):
    """The 31st of a 30-day month is that month's last day."""
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _next_month_day(day, on_or_after, step, anchor):
    """First occurrence of `day` on/after `on_or_after`, stepping `step` months.

    Multi-month cadences keep the phase of `anchor`: a quarterly SIP that
    started in April falls due Apr/Jul/Oct/Jan, not three months from now.
    """
    if step == 1:
        year, month = on_or_after.year, on_or_after.month
    else:
        year, month = anchor.year, anchor.month

        while (year, month) < (on_or_after.year, on_or_after.month):
            month += step

            while month > 12:
                month -= 12
                year += 1

    for _ in range(600):
        candidate = _clamp_to_month(year, month, day)

        if candidate >= on_or_after:
            return candidate

        month += step

        while month > 12:
            month -= 12
            year += 1

    return None


def project_next_due(kind, days, periodicity, from_date, today):
    """First scheduled instalment on/after today, or None.

    Always counts forward from today rather than from the last transaction,
    so missed instalments need no special handling: a SIP that skipped August
    is still due in September, not retrospectively due in August.
    """
    if not kind or not days:
        return None

    cadence = _as_text(periodicity).upper()
    from_date = _as_date(from_date)

    # A SIP that has not started yet cannot be due before its own start date.
    start = max(today, from_date) if from_date else today

    if cadence == "ONE_TIME":
        return from_date if from_date and from_date >= start else None

    if kind == "dow":
        shift = (days[0] - start.isoweekday()) % 7

        if shift == 0 and start == today:
            shift = 7

        return start + timedelta(days=shift)

    if cadence == "DAILY":
        return start if start > today else today + timedelta(days=1)

    step = CADENCE_MONTH_STEP.get(cadence, 1)
    anchor = from_date or start

    candidates = [
        found
        for found in (
            _next_month_day(day, start, step, anchor)
            for day in days
        )
        if found
    ]

    return min(candidates) if candidates else None


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

                -- Changed from inv_iin to ihno
                ihno,

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

                -- Changed from inv_iin to ihno
                ihno,

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
    # RTA
    # ========================================================

    gold_df["rta"] = df["rta_clean"]

    # ========================================================
    # SIP REGISTRATION NUMBER
    # ========================================================

    ft_sip_regno = (
        get_column(df, "ft_sip_regno")
        .astype("string")
        .str.strip()
        .replace(
            {
                "0": pd.NA,
                "": pd.NA
            }
        )
    )

    request_ref_no = (
        get_column(df, "request_ref_no")
        .astype("string")
        .str.strip()
        .replace(
            {
                "": pd.NA
            }
        )
    )

    gold_df["sip_reg_no"] = (
        ft_sip_regno.fillna(
            request_ref_no
        )
    )

    # ========================================================
    # FOLIO NUMBER
    # ========================================================

    gold_df["folio_number"] = (
        df["folio_clean"]
    )

    # ========================================================
    # IHNO
    #
    # Silver column is ihno.
    # Gold column is also ihno.
    # ========================================================

    gold_df["ihno"] = (
        get_column(df, "ihno")
        .astype("string")
        .str.strip()
        .replace("", pd.NA)
    )

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
    #
    # Silver SIP does NOT have an isin column.
    # Keep ISIN NULL for now.
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
            errors="coerce",
            format="ISO8601"
        )
        .dt.date
    )

    # ========================================================
    # END DATE
    # ========================================================

    gold_df["end_date"] = (
        pd.to_datetime(
            get_column(df, "to_date"),
            errors="coerce",
            format="ISO8601"
        )
        .dt.date
    )

    # ========================================================
    # NEXT DUE DATE / SIP DAY
    # ========================================================
    #
    # Both are derived further down, once transaction history has been
    # loaded -- KFIN never sends period_day, so its debit day can only come
    # from the SIP's own past debits.

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
            errors="coerce",
            format="ISO8601"
        )
        .dt.date
    )

    # ========================================================
    # CEASED DATE
    # ========================================================

    gold_df["ceased_date"] = (
        pd.to_datetime(
            get_column(df, "cease_date"),
            errors="coerce",
            format="ISO8601"
        )
        .dt.date
    )

    # ========================================================
    # SCHEME ID
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
            traddate,
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
    # NEXT DUE DATE / SIP DAY
    # ========================================================

    print()
    print("Deriving SIP day and next due date...")

    today = datetime.now(timezone.utc).astimezone().date()
    recent_cutoff = today - timedelta(days=MODAL_RECENT_MONTHS * 31)

    # Keyed on the RTA's own registration serial number, NOT on folio +
    # scheme. Folio 408175507827 holds two registrations in one scheme -- one
    # dead, one live -- and the folio-level key blends their debits into a
    # day that belongs to neither (day 4 instead of 21, a month adrift).
    recent_counts = {}
    all_counts = {}
    dow_counts = {}

    if not transactions.empty and "traddate" in transactions.columns:

        debits = pd.DataFrame({
            "reg_no": (
                transactions["sipregslno"]
                .astype("string")
                .str.strip()
                .replace({"": pd.NA, "0": pd.NA})
            ),
            "traddate": pd.to_datetime(
                transactions["traddate"],
                errors="coerce",
                format="ISO8601"
            ),
        }).dropna()

        for reg_no, debit_date in zip(debits["reg_no"], debits["traddate"]):
            day = debit_date.day
            weekday = debit_date.isoweekday()

            all_counts.setdefault(reg_no, {})
            all_counts[reg_no][day] = all_counts[reg_no].get(day, 0) + 1

            dow_counts.setdefault(reg_no, {})
            dow_counts[reg_no][weekday] = dow_counts[reg_no].get(weekday, 0) + 1

            if debit_date.date() >= recent_cutoff:
                recent_counts.setdefault(reg_no, {})
                recent_counts[reg_no][day] = recent_counts[reg_no].get(day, 0) + 1

    reg_nos = (
        get_column(df, "ft_sip_regno")
        .astype("string")
        .str.strip()
        .replace({"": pd.NA, "0": pd.NA})
    )

    periodicities = gold_df["frequency"]
    period_days = get_column(df, "period_day")
    statuses = gold_df["status"]

    start_dates = gold_df["start_date"]
    end_dates = gold_df["end_date"]
    ceased_dates = gold_df["ceased_date"]

    pause_from = pd.to_datetime(
        get_column(df, "pause_from_date"), errors="coerce", format="ISO8601"
    ).dt.date

    pause_to = pd.to_datetime(
        get_column(df, "pause_to_date"), errors="coerce", format="ISO8601"
    ).dt.date

    sources = gold_df["rta"]

    sip_days = []
    next_due_dates = []
    provenance = {}

    for idx in df.index:
        reg_no = reg_nos.get(idx)
        reg_no = reg_no if isinstance(reg_no, str) else None

        kind, days, how = resolve_sip_day(
            periodicity=periodicities.get(idx),
            period_day=period_days.get(idx),
            modal_dom_recent=modal_day(recent_counts.get(reg_no, {})),
            modal_dom_all=modal_day(all_counts.get(reg_no, {})),
            modal_dow=modal_day(dow_counts.get(reg_no, {})),
            source=sources.get(idx),
            to_date=end_dates.get(idx),
            from_date=start_dates.get(idx),
            recent_days=recent_counts.get(reg_no, {}),
        )

        provenance[how] = provenance.get(how, 0) + 1
        sip_days.append(days[0] if days else None)

        live = sip_is_live(
            status=statuses.get(idx),
            cease_date=ceased_dates.get(idx),
            to_date=end_dates.get(idx),
            pause_from=pause_from.get(idx),
            pause_to=pause_to.get(idx),
            today=today,
        )

        if not live:
            next_due_dates.append(None)
            continue

        due = project_next_due(
            kind,
            days,
            periodicities.get(idx),
            start_dates.get(idx),
            today,
        )

        # A projection past the SIP's own end is not a due date.
        finish = _as_date(ceased_dates.get(idx)) or _as_date(end_dates.get(idx))

        if due and finish and due > finish:
            due = None

        next_due_dates.append(due)

    # sip_day describes the mandate, so it is written for every row; the
    # projection is only meaningful while the SIP is still running.
    gold_df["sip_day"] = pd.Series(sip_days, index=df.index, dtype="Int64")
    gold_df["next_due_date"] = pd.Series(next_due_dates, index=df.index, dtype="object")

    print("Resolved sip_day rows :", int(gold_df["sip_day"].notna().sum()))
    print("Next due date rows    :", int(gold_df["next_due_date"].notna().sum()))

    for how in sorted(provenance):
        print("  via {:<20} {}".format(how, provenance[how]))

    # ========================================================
    # ARN ID
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

        # New Gold SIP column
        "ihno",

        "scheme_code",
        "scheme_name",
        "amc_code",

        # ISIN intentionally kept NULL
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
        "Missing ISIN values:",
        gold_df["isin"].isna().sum()
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

        return True

    # ========================================================
    # GOLD.SIP COLUMNS
    # ========================================================

    gold_columns = [
        "rta",
        "sip_reg_no",
        "folio_number",

        # New column
        "ihno",

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
    # ROW COUNT BEFORE LOAD
    # ========================================================

    rows_to_insert = len(gold_df)

    print(
        "Rows to insert:",
        rows_to_insert
    )

    # ========================================================
    # VALIDATE STRING LENGTHS
    # ========================================================

    validate_string_lengths(
        gold_df
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
    # INSERT
    # ========================================================

    try:

        from utils.db import upsert_dataframe

        upsert_dataframe(
            gold_df,
            schema="gold",
            table="sip",
            conflict_index_expr=(
                '"rta", "folio_number", "scheme_code", '
                '"registered_date", "amount", '
                '(COALESCE(NULLIF("sip_reg_no", \'\'), \'\'))'
            ),
            chunksize=500,
            updated_at_column=None,
        )

        # ====================================================
        # VERIFY
        # ====================================================

        verification = safe_read(
            """
            SELECT
                COUNT(*) AS total_rows
            FROM gold.sip
            """
        )

        if not verification.empty:

            total_rows = int(
                verification.iloc[0]["total_rows"]
            )

        else:

            total_rows = -1

        print()
        print(
            "Inserted rows:",
            rows_to_insert
        )

        print(
            "Gold SIP rows after load:",
            total_rows
        )

        print()
        print("GOLD SIP LOAD SUCCESSFUL")

        return True

    except Exception:

        print()
        print("=" * 80)
        print("GOLD SIP LOAD FAILED")
        print("=" * 80)

        traceback.print_exc(
            limit=10
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
            # GOLD SIP DATA SAMPLE
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
                        "ihno",
                        "scheme_id",
                        "scheme_code",
                        "isin",
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

            if success:

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
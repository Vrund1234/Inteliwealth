import pandas as pd
import re
import traceback

from datetime import datetime, timezone
from utils.db import engine, master_engine
from sqlalchemy import text
from psycopg2.extras import execute_values


# ============================================================
# SAFE READ
# ============================================================

def safe_read(query, connection=engine):

    try:

        return pd.read_sql(
            query,
            connection
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
            [
                "",
                "NAN",
                "NONE",
                "NULL",
                "NAT"
            ],
            pd.NA
        )
    )


# ============================================================
# CLEAN IDENTIFIER
# ============================================================

def clean_identifier(series):

    """clean_string, plus: a value that is nothing but zeros is ABSENT.

    Both RTAs write "0" where they have no value to send, and clean_string
    does not catch it -- it maps "", NAN, NONE, NULL and NAT to NA, but a
    literal zero survives and is then stored as though it were data.

    Measured 2026-09-07 on silver.investor_master: ckyc_no is "0" on 480 of
    the 1297 rows that look populated, and those 480 span 207 different PANs.
    Before this function, 159 of the 355 clients gold reported as having a
    CKYC number carried "0" -- 45% false positives for anything downstream
    filtering on `ckyc_no IS NOT NULL`, and the same "0" would have appeared
    against 159 unrelated clients in the app once these columns are synced.

    The same convention shows up across this feed: pincode arrives as "0" or
    "000000" for overseas addresses, and account numbers are zero-padded to
    each RTA's own width. Only fields that are IDENTIFIERS should use this --
    a genuine zero is meaningful in an amount or a unit count.
    """

    cleaned = clean_string(series)

    return cleaned.where(
        ~cleaned.fillna("").astype(str).str.fullmatch(r"0+"),
        pd.NA
    )


# ============================================================
# CLEAN PAN
# ============================================================

def clean_pan(series):

    return (
        clean_string(series)
        .str.upper()
        .str.strip()
        .replace(
            [
                "",
                "NAN",
                "NONE",
                "NULL",
                "NON RESIDENT"
            ],
            pd.NA
        )
        .str[:10]
    )


# ============================================================
# CLEAN PHONE
# ============================================================

def clean_phone(series):

    raw = clean_string(series)

    raw = (
        raw
        .str.split(",")
        .str[0]
        .str.strip()
    )

    invalid_alpha = raw.str.contains(
        r"[A-Za-z]",
        regex=True,
        na=False
    )

    raw.loc[invalid_alpha] = pd.NA

    raw = (
        raw
        .str.replace(
            r"\D",
            "",
            regex=True
        )
    )

    raw = raw.str[:20]

    return raw.replace("", pd.NA)


# ============================================================
# NORMALIZE MOBILE
# ============================================================

def normalize_mobile(series):

    raw = clean_string(series)

    digits = (
        raw
        .fillna("")
        .astype(str)
        .str.replace(
            r"\D",
            "",
            regex=True
        )
    )

    mobile = pd.Series(
        pd.NA,
        index=series.index,
        dtype="object"
    )

    mobile_isd = pd.Series(
        pd.NA,
        index=series.index,
        dtype="object"
    )

    # ========================================================
    # 91 + 10 DIGITS
    # ========================================================

    mask_91 = (
        (digits.str.len() == 12)
        &
        digits.str.startswith("91")
    )

    mobile.loc[mask_91] = (
        digits.loc[mask_91].str[-10:]
    )

    mobile_isd.loc[mask_91] = "91"

    # ========================================================
    # 10 DIGITS
    # ========================================================

    mask_10 = (
        digits.str.len() == 10
    )

    mobile.loc[mask_10] = (
        digits.loc[mask_10]
    )

    mobile_isd.loc[mask_10] = "91"

    # ========================================================
    # INTERNATIONAL
    # ========================================================

    mask_other = (
        (digits.str.len() > 10)
        &
        (~mask_91)
    )

    mobile.loc[mask_other] = (
        digits.loc[mask_other].str[-10:]
    )

    mobile_isd.loc[mask_other] = (
        digits.loc[mask_other]
        .str[:-10]
        .str[-5:]
    )

    return mobile, mobile_isd


# ============================================================
# EXTRACT CLIENTS
# ============================================================

def extract_clients():

    print("=" * 80)
    print("EXTRACTING DATA FOR GOLD CLIENTS")
    print("=" * 80)

    query = """

    WITH transaction_ranked AS
    (
        SELECT

            folio_no,
            source,
            pan,
            traddate,
            common_account_number,
            brokcode,
            src_brk_code,
            created_at,

            ROW_NUMBER() OVER
            (
                PARTITION BY
                    UPPER(TRIM(source)),
                    REGEXP_REPLACE(
                        TRIM(CAST(folio_no AS TEXT)),
                        '\\.0$',
                        ''
                    )

                ORDER BY
                    created_at DESC NULLS LAST,
                    traddate DESC NULLS LAST,

                    -- created_at and traddate alone do NOT
                    -- decide it. The same transaction arrives
                    -- twice on one folio, identical on both,
                    -- with src_brk_code filled on one row and
                    -- blank on the other -- so ROW_NUMBER()
                    -- picked whichever Postgres returned first
                    -- and sub_arn flipped between ARN-76793 and
                    -- NULL from one run to the next (PAN
                    -- AUDPP6124D, folio 25883720/56).
                    --
                    -- Prefer the row that carries information,
                    -- then break any remaining tie on the
                    -- table's own natural key so the winner is
                    -- fixed.
                    (NULLIF(TRIM(src_brk_code), '') IS NULL),
                    NULLIF(TRIM(src_brk_code), ''),
                    trxnno,
                    amount,
                    units

            ) AS rn

        FROM silver.transaction_master_new

        WHERE pan IS NOT NULL
          AND TRIM(pan) <> ''
    ),

    transaction_data AS
    (
        SELECT

            folio_no,
            source,
            pan,
            traddate,
            common_account_number,
            brokcode,
            src_brk_code,
            created_at

        FROM transaction_ranked

        WHERE rn = 1
    ),

    sip_ranked AS
    (
        SELECT

            folio_no,
            source,
            pan,
            created_at,

            ROW_NUMBER() OVER
            (
                PARTITION BY
                    UPPER(TRIM(source)),
                    REGEXP_REPLACE(
                        TRIM(CAST(folio_no AS TEXT)),
                        '\\.0$',
                        ''
                    )

                ORDER BY
                    created_at DESC NULLS LAST,

                    -- Same reason as the transaction ranking
                    -- above: created_at ties, so the tie is
                    -- broken on a stable key rather than on
                    -- whatever order the scan produced.
                    pan,
                    folio_no

            ) AS rn

        FROM silver.sip_master_new

        WHERE pan IS NOT NULL
          AND TRIM(pan) <> ''
    ),

    sip_data AS
    (
        SELECT

            folio_no,
            source,
            pan,
            created_at

        FROM sip_ranked

        WHERE rn = 1
    )

    SELECT

        i.*,

        t.pan AS txn_pan,

        t.traddate AS txn_traddate,

        t.common_account_number
            AS txn_common_account_number,

        t.brokcode AS txn_brokcode,

        t.src_brk_code AS txn_src_brk_code,

        t.created_at AS txn_created_at,

        s.pan AS sip_pan,

        s.created_at AS sip_created_at

    FROM silver.investor_master i

    LEFT JOIN transaction_data t

        ON REGEXP_REPLACE(
            TRIM(CAST(i.folio_no AS TEXT)),
            '\\.0$',
            ''
        )
        =
        REGEXP_REPLACE(
            TRIM(CAST(t.folio_no AS TEXT)),
            '\\.0$',
            ''
        )

        AND UPPER(TRIM(i.source))
            =
            UPPER(TRIM(t.source))

    LEFT JOIN sip_data s

        ON REGEXP_REPLACE(
            TRIM(CAST(i.folio_no AS TEXT)),
            '\\.0$',
            ''
        )
        =
        REGEXP_REPLACE(
            TRIM(CAST(s.folio_no AS TEXT)),
            '\\.0$',
            ''
        )

        AND UPPER(TRIM(i.source))
            =
            UPPER(TRIM(s.source))

    """

    df = safe_read(query)

    if df.empty:

        print("No client data extracted.")

        return df

    df.columns = (
        df.columns
        .astype(str)
        .str.lower()
        .str.strip()
    )

    print("\nExtraction Completed")
    print("-" * 80)

    print(
        "Rows fetched :",
        len(df)
    )

    print(
        "Transaction PAN mapped :",
        df["txn_pan"].notna().sum()
    )

    print(
        "SIP PAN mapped :",
        df["sip_pan"].notna().sum()
    )

    print(
        "Investor PAN available :",
        df["pan_no"].notna().sum()
    )

    return df


# ============================================================
# GET CKYC / DP ID LOOKUP
#
# IMPORTANT:
# Mapping is done using PAN.
#
# silver.investor_master
#        |
#        | PAN
#        v
# ckyc_no / dp_id
# ============================================================

def get_ckyc_dp_lookup():

    print()
    print("=" * 80)
    print("BUILDING PAN -> CKYC / DP ID LOOKUP")
    print("=" * 80)

    query = """

    SELECT
        pan_no,
        ckyc_no,
        dp_id,
        -- The RTA calls this client_id, but it is the DEPOSITORY BENEFICIARY
        -- ACCOUNT, not a client key. It arrives on the same row as dp_id and
        -- is useless without it: a DP ID alone identifies the depository
        -- participant, not the account.
        client_id AS beneficiary_ac_no
    FROM silver.investor_master
    WHERE pan_no IS NOT NULL
      AND TRIM(pan_no) <> ''

    """

    lookup = safe_read(query)

    if lookup.empty:

        print(
            "No CKYC / DP ID records found "
            "in silver.investor_master."
        )

        return pd.DataFrame(
            columns=[
                "pan",
                "ckyc_no",
                "dp_id"
            ]
        )

    # ========================================================
    # CLEAN PAN
    # ========================================================

    lookup["pan"] = clean_pan(
        lookup["pan_no"]
    )

    # ========================================================
    # CLEAN CKYC / DP ID
    # ========================================================

    # clean_identifier, not clean_string: "0" is this feed's "not supplied",
    # and storing it makes 159 unrelated clients appear to share one CKYC.
    lookup["ckyc_no"] = clean_identifier(
        lookup["ckyc_no"]
    )

    lookup["dp_id"] = clean_identifier(
        lookup["dp_id"]
    )

    lookup["beneficiary_ac_no"] = clean_identifier(
        lookup["beneficiary_ac_no"]
    )

    lookup = lookup[
        lookup["pan"].notna()
    ].copy()

    # ========================================================
    # SORT SO THAT RECORDS HAVING CKYC / DP ID
    # ARE PREFERRED
    # ========================================================

    lookup["_has_ckyc"] = (
        lookup["ckyc_no"].notna()
    )

    lookup["_has_dp_id"] = (
        lookup["dp_id"].notna()
    )

    lookup = lookup.sort_values(
        by=[
            "pan",
            "_has_ckyc",
            "_has_dp_id"
        ],
        ascending=[
            True,
            False,
            False
        ]
    )

    # ========================================================
    # ONE RECORD PER PAN
    # ========================================================

    lookup = (
        lookup
        .drop_duplicates(
            subset=["pan"],
            keep="first"
        )
        [
            [
                "pan",
                "ckyc_no",
                "dp_id",
                "beneficiary_ac_no"
            ]
        ]
    )

    print(
        "Unique PANs in CKYC / DP ID lookup:",
        len(lookup)
    )

    print(
        "PANs having CKYC:",
        lookup["ckyc_no"].notna().sum()
    )

    print(
        "PANs having DP ID:",
        lookup["dp_id"].notna().sum()
    )

    return lookup


# ============================================================
# TRANSFORM CLIENTS
# ============================================================

def transform_clients(df):

    print("=" * 80)
    print("TRANSFORMING GOLD CLIENTS")
    print("=" * 80)

    if df.empty:

        return pd.DataFrame()

    df = df.copy()

    df.columns = (
        df.columns
        .astype(str)
        .str.lower()
        .str.strip()
    )

    # ========================================================
    # SOURCE
    # ========================================================

    df["source"] = (
        clean_string(df["source"])
        .str.upper()
        .str.strip()
    )

    cams_mask = df["source"] == "CAMS"

    kfin_mask = df["source"].isin(
        [
            "KFIN",
            "KFINTECH"
        ]
    )

    # ========================================================
    # PAN
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
    #
    # silver.investor_master.pan_no  ->  gold.clients.pan
    #
    # Straight across, with no transaction or SIP fallback.
    #
    # Those fallbacks were how a guardian's PAN ended up in the
    # pan column: CAMS puts the guardian's PAN into the ordinary
    # transaction `pan` field with no flag at all, so a folio
    # with no PAN of its own silently picked it up and the minor
    # was stored as though the PAN were theirs. A folio's own
    # PAN is the one the registry recorded against the investor,
    # and that is pan_no.
    # ========================================================

    df["pan"] = clean_pan(df["pan_no"])

    # ========================================================
    # GUARDIAN PAN
    #
    # Carried through as an attribute only. Deciding WHICH
    # client a folio belongs to -- including the guardian-PAN
    # rule for minors -- is not done here: that lives in
    # python_scripts/client_mapping.py and is recorded in
    # bronze.client_mapping_review.
    # ========================================================

    # Validated to the PAN format, not just cleaned: clean_pan
    # accepts anything non-blank, and six KFIN folios carry the
    # placeholder guard_pan '0'. Left in, that placeholder is
    # stored as a real guardian PAN and -- because the PAN-less
    # dedup key below is guardian_pan + name + dob -- becomes a
    # family key filing unrelated minors under one guardian.
    #
    # client_mapping.valid_pan is the same check the mapper
    # applies when it builds GPAN: keys, reused rather than
    # reimplemented so the two cannot drift apart.
    from client_mapping import valid_pan

    if "guardian_pan" in df.columns:

        df["guardian_pan"] = valid_pan(df["guardian_pan"])

    else:

        df["guardian_pan"] = pd.NA

    # A guardian's PAN belongs in guardian_pan, never in pan.
    #
    # CAMS puts the guardian's PAN into the ordinary transaction
    # `pan` field with no flag at all, so the txn_pan fallback
    # above silently adopts it as the investor's own. Whenever
    # the resolved PAN is this folio's own guardian_pan, it is
    # not the investor's PAN and is cleared.
    borrowed = (
        df["pan"].notna()
        &
        df["guardian_pan"].notna()
        &
        (df["pan"] == df["guardian_pan"])
    )

    if borrowed.any():

        print(
            "Guardian PAN cleared from pan :",
            int(borrowed.sum())
        )

    df["pan"] = df["pan"].where(~borrowed, pd.NA)


    print("\nPAN Statistics")
    print("-" * 80)

    print(
        "Investor PAN    :",
        df["pan_no"].notna().sum()
    )

    print(
        "Transaction PAN :",
        df["txn_pan"].notna().sum()
    )

    print(
        "SIP PAN         :",
        df["sip_pan"].notna().sum()
    )

    print(
        "Final PAN       :",
        df["pan"].notna().sum()
    )

    print(
        "Missing PAN     :",
        df["pan"].isna().sum()
    )

    # ========================================================
    # CKYC / DP ID PAN MAPPING
    #
    # THIS IS THE IMPORTANT FIX.
    #
    # We do NOT take CKYC / DP ID simply from the
    # folio-joined investor row.
    #
    # Instead:
    #
    # final gold PAN
    #       ↓
    # silver.investor_master.pan_no
    #       ↓
    # ckyc_no / dp_id
    # ========================================================

    ckyc_dp_lookup = get_ckyc_dp_lookup()

    if not ckyc_dp_lookup.empty:

        df = df.merge(
            ckyc_dp_lookup,
            how="left",
            left_on="pan",
            right_on="pan",
            suffixes=(
                "",
                "_lookup"
            )
        )

        # ====================================================
        # TAKE THE LOOKUP'S VALUE, NOT THE ROW'S OWN
        # ====================================================
        #
        # extract_clients() already selects ckyc_no and dp_id per row, so the
        # merge above collides on both names. An EMPTY first suffix means the
        # frame's own column keeps the plain name and the lookup's lands in
        # *_lookup -- so without these two lines the whole lookup is computed,
        # merged, and then never read.
        #
        # That is not cosmetic. The lookup resolves ONE value per PAN from all
        # of that PAN's silver rows; the raw column carries it only on the row
        # that happened to have it. load_clients() then keeps one row per PAN
        # with drop_duplicates(keep="last"), so whether a client kept its CKYC
        # came down to which of its rows sorted last.
        #
        # Measured 2026-09-07, before this fix: PAN AMEPP9018M has 41 silver
        # rows and exactly one carries dp_id IN301549 -- it was not last, so
        # gold.clients.dp_id was 0 of 593. CKYC lost 31 clients the same way,
        # 324 in gold against 355 the lookup resolves.
        #
        # Assigned explicitly rather than by flipping the suffixes: which side
        # wins is then visible here, instead of depending on argument order.

        df["ckyc_no"] = df["ckyc_no_lookup"]

        df["dp_id"] = df["dp_id_lookup"]

        # beneficiary_ac_no has no same-named column on df to collide with, so
        # the merge brings it in unsuffixed -- but assign defensively in case
        # extract_clients ever selects one.
        if "beneficiary_ac_no_lookup" in df.columns:
            df["beneficiary_ac_no"] = df["beneficiary_ac_no_lookup"]

        df.drop(
            columns=[
                "ckyc_no_lookup",
                "dp_id_lookup",
                "beneficiary_ac_no_lookup"
            ],
            inplace=True,
            errors="ignore"
        )

    else:

        df["ckyc_no"] = pd.NA
        df["dp_id"] = pd.NA
        df["beneficiary_ac_no"] = pd.NA

    print("\nPAN Based CKYC / DP ID Mapping")
    print("-" * 80)

    print(
        "Final PANs:",
        df["pan"].notna().sum()
    )

    print(
        "CKYC mapped:",
        df["ckyc_no"].notna().sum()
    )

    print(
        "CKYC missing:",
        df["ckyc_no"].isna().sum()
    )

    print(
        "DP ID mapped:",
        df["dp_id"].notna().sum()
    )

    print(
        "DP ID missing:",
        df["dp_id"].isna().sum()
    )

    # ========================================================
    # CREATE GOLD DATAFRAME
    # ========================================================

    gold = pd.DataFrame(
        index=df.index
    )

    # ========================================================
    # BASIC
    # ========================================================

    gold["status"] = None

    gold["full_name"] = clean_string(
        df["investor_name"]
    )

    gold["client_label"] = None

    # ========================================================
    # PHONE
    # ========================================================

    if "phone_res" in df.columns:

        phone_res = clean_phone(
            df["phone_res"]
        )

    else:

        phone_res = pd.Series(
            pd.NA,
            index=df.index
        )

    if "phone_off" in df.columns:

        phone_off = clean_phone(
            df["phone_off"]
        )

    else:

        phone_off = pd.Series(
            pd.NA,
            index=df.index
        )

    gold["phone"] = (
        phone_res
        .fillna(phone_off)
    )

    # ========================================================
    # MOBILE
    # ========================================================

    if "mobile_no" in df.columns:

        (
            gold["mobile"],
            gold["mobile_isd"]
        ) = normalize_mobile(
            df["mobile_no"]
        )

    else:

        gold["mobile"] = pd.NA
        gold["mobile_isd"] = pd.NA

    # ========================================================
    # WHATSAPP
    # ========================================================

    gold["whatsapp_same_as_mobile"] = None
    gold["whatsapp_isd"] = None
    gold["whatsapp_no"] = None

    # ========================================================
    # AADHAAR
    # ========================================================

    # There is no Aadhaar NUMBER anywhere in this warehouse.
    # holder_1_aadhaar_info is a SEEDING STATUS -- its values are
    # Y (1400), DELINKED (260), N (57), AVAILABLE (12) and
    # INVALID (2).
    #
    # This used to write the literal 'Y' into `aadhaar` whenever
    # that field was merely PRESENT, which was wrong twice over:
    # it put a status code in a VARCHAR(12) column named for a
    # 12-digit identifier, and it recorded 'Y' even for a client
    # whose Aadhaar is explicitly N, DELINKED or INVALID. It
    # leaked too -- the backend copies `aadhaar` 1:1, so 165 app
    # clients ended up holding 'Y' as their Aadhaar number.
    #
    # The status is kept, at the fidelity the source provides,
    # in a column that says what it is. `aadhaar` stays null
    # until a real number is available: a column named for an
    # identifier must never carry a status code.
    gold["aadhaar"] = pd.NA

    if "holder_1_aadhaar_info" in df.columns:

        gold["aadhaar_seeding_status"] = (
            clean_string(df["holder_1_aadhaar_info"])
            .str.upper()
        )

    else:

        gold["aadhaar_seeding_status"] = pd.NA

    # ========================================================
    # PAN
    # ========================================================

    gold["pan"] = df["pan"]

    gold["pan_verified"] = False

    gold["pan_verified_at"] = None

    gold["guardian_pan"] = df["guardian_pan"]

    # ========================================================
    # EMAIL
    # ========================================================

    if "email" in df.columns:

        gold["email"] = clean_string(
            df["email"]
        )

    else:

        gold["email"] = None

    # ========================================================
    # DOB
    # ========================================================

    if "dob" in df.columns:

        gold["date_of_birth"] = (
            pd.to_datetime(
                df["dob"],
                errors="coerce",
                format="ISO8601"
            )
            .dt.date
        )

    else:

        gold["date_of_birth"] = pd.NaT

    # ========================================================
    # INDIVIDUAL vs NON-INDIVIDUAL ASSESSEE
    # ========================================================
    #
    # The 4th character of a PAN is the Income Tax Department's
    # holder-type code:
    #
    #   A  Association of Persons (AOP)
    #   B  Body of Individuals (BOI)
    #   C  Company
    #   F  Firm / LLP
    #   G  Government
    #   H  Hindu Undivided Family (HUF)
    #   J  Artificial Juridical Person
    #   L  Local Authority
    #   P  Person (Individual)      <-- the only natural person
    #   T  Trust
    #
    # Only a 'P' assessee has a date of birth. Every other code
    # is an entity whose registry `dob` is an INCORPORATION or
    # formation date, and deriving an age from it produces
    # nonsense -- DEVSTREE IT SERVICES PVT LTD (AADCI1450Q,
    # incorporated 2013-01-09) was being stored as a 13-year-old
    # minor, and 34 entities in total carried a derived age.
    #
    # The PAN code is used rather than tax_status because a SOLE
    # PROPRIETORSHIP trades under a firm name but transacts on
    # the PROPRIETOR'S OWN individual PAN -- its dob is a real
    # person's and its age is correct. Shashwat Textile is PAN
    # AMVPB6434E, i.e. Nishant Rakeshkumar Bhandari, born
    # 1988-09-17 (the same PAN that is the guardian PAN for the
    # Bhandari minors). Keying on tax_status would wrongly blank
    # those ages; keying on the PAN keeps them.
    # ========================================================

    # The assessee's constitution, as a stable upper-case
    # constant: INDIVIDUAL, COMPANY, HUF, FIRM, TRUST, AOP, BOI,
    # GOVERNMENT, LOCAL_AUTHORITY, ARTIFICIAL_JURIDICAL_PERSON.
    #
    # transformations/transform.py owns the rule for the silver
    # layer and is reused here rather than reimplemented, so the
    # two layers cannot disagree about what a client is. That
    # matters: the entity test below used to be a second,
    # separate copy of the same idea -- a PAN check plus its own
    # tax_status regex -- and a rule written twice is a rule
    # that eventually drifts.
    #
    # A folio with no PAN and no guardian PAN falls through to
    # tax_status inside that helper. One carrying a guardian PAN
    # is a minor, hence a natural person, and its PAN-less-ness
    # is not evidence of being an entity -- so it is answered
    # INDIVIDUAL here rather than left to a tax_status that says
    # only "On Behalf Of Minor".
    from transformations.transform import _individual_category_type

    category = _individual_category_type(
        df["pan"],
        df["tax_status"] if "tax_status" in df.columns else None,
        df.index
    )

    minor_on_guardian_pan = (
        df["pan"].isna()
        &
        df["guardian_pan"].notna()
    )

    category = category.mask(
        minor_on_guardian_pan,
        "INDIVIDUAL"
    )

    gold["individual_category_type"] = category

    # Everything that is not a natural person. An unresolved
    # category (neither a valid PAN nor a recognised tax_status)
    # is NOT treated as an entity: age and is_minor stay
    # governed by the age bands, which is the behaviour a client
    # with no identifying data had before this column existed.
    is_entity = (
        category.notna()
        &
        (category != "INDIVIDUAL")
    )

    # ========================================================
    # AGE
    # ========================================================
    #
    # Recomputed from date_of_birth on EVERY run, rather than
    # copied from silver.investor_master.age.
    #
    # silver's age is a snapshot taken by transformations/
    # transform.py at the moment a folio was ingested, and
    # bronze_to_silver is incremental -- it only re-transforms
    # bronze rows newer than MAX(created_at) in silver -- so a
    # folio that receives no new registry file is never
    # recalculated and its age silently ages out of date. On
    # 2026-09-10, 19 silver rows across 6 people were a full
    # year behind, every one of them somebody whose birthday had
    # just passed.
    #
    # Copying that value made gold stale in the same way, and no
    # amount of re-running the pipeline could fix it: gold
    # faithfully matched silver, so refresh_derived_demographics
    # correctly reported nothing to change.
    #
    # Deriving it here instead makes age a function of the run
    # date, so the 15-minute schedule keeps it exact. It also
    # matters beyond cosmetics: is_minor and
    # is_documentupdaterequired below are both derived from the
    # age bands, so a client turning 18 was not flagged as
    # needing their own PAN until their folio happened to be
    # re-ingested -- and never, if that folio had gone quiet.
    #
    # The arithmetic matches transform.py's: the birthday must
    # have PASSED this year, not merely fall in it.
    # ========================================================

    dob_for_age = pd.to_datetime(
        gold["date_of_birth"],
        errors="coerce"
    )

    today = pd.Timestamp.today().normalize()

    years = today.year - dob_for_age.dt.year

    birthday_not_yet_reached = (
        (today.month < dob_for_age.dt.month)
        |
        (
            (today.month == dob_for_age.dt.month)
            &
            (today.day < dob_for_age.dt.day)
        )
    )

    gold["age"] = (
        (years - birthday_not_yet_reached.astype("Int64"))
        .astype("Int64")
        .where(dob_for_age.notna(), pd.NA)
    )

    # An entity has no age. Blanked HERE, before the age bands
    # below are computed, so nothing downstream can derive a
    # minor status from it: an entity then falls into NEITHER
    # age band and is_documentupdaterequired stays null too.
    #
    # date_of_birth is deliberately LEFT ALONE -- the
    # incorporation date is real data, it is simply not a birth
    # date.
    gold.loc[is_entity, "age"] = pd.NA

    # ========================================================
    # AGE BANDS
    # ========================================================
    #
    # fillna(False) because age is Int64: a null age compares to
    # pd.NA, and an NA mask cannot be used with .loc at all.
    # A client with no age falls into NEITHER band and keeps a
    # null status, which is what the source fallback is for.
    # ========================================================

    age_under_18 = (
        gold["age"] < 18
    ).fillna(False)

    age_18_or_over = (
        gold["age"] >= 18
    ).fillna(False)

    own_pan = df["pan"]

    guardian_pan = df["guardian_pan"]

    # ========================================================
    # GUARDIAN PAN STILL BEING USED
    # ========================================================
    #
    # CASE 1:
    # Own PAN and Guardian PAN are the same.
    #
    #   PAN          = ABCDE1234F
    #   Guardian PAN = ABCDE1234F
    #
    # CASE 2:
    # Own PAN is missing but Guardian PAN exists -- no evidence
    # the investor's own PAN was ever recorded.
    #
    # Both mean the guardian's PAN is still the one in use.
    #
    # CASE 1 is kept for completeness but cannot fire here: the
    # FINAL PAN block above already clears pan wherever it equals
    # this folio's guardian_pan, which turns every CASE 1 row
    # into a CASE 2 row before it reaches this point. The answer
    # is identical either way.
    # ========================================================

    guardian_pan_still_used = (
        (
            own_pan.notna()
            &
            guardian_pan.notna()
            &
            (own_pan == guardian_pan)
        )
        |
        (
            own_pan.isna()
            &
            guardian_pan.notna()
        )
    )

    # ========================================================
    # OWN PAN IS BEING USED
    # ========================================================

    own_pan_is_being_used = (
        own_pan.notna()
        &
        (
            guardian_pan.isna()
            |
            (
                guardian_pan.notna()
                &
                (guardian_pan != own_pan)
            )
        )
    )

    # ========================================================
    # DOCUMENT UPDATE REQUIRED
    # ========================================================
    #
    # TRUE means the document STILL HAS TO BE UPDATED, matching
    # the column name is_documentupdaterequired.
    #
    # Note this is the inverse of an "is the document updated?"
    # flag: an adult still operating on their guardian's PAN has
    # NOT updated, so an update IS required (True), while an
    # adult on their own PAN has updated, so none is required
    # (False). Storing it the other way round would make the
    # column say the opposite of what it is named.
    # ========================================================

    is_documentupdaterequired = pd.Series(
        pd.NA,
        index=df.index,
        dtype="boolean"
    )

    # --------------------------------------------------------
    # RULE 1: Age < 18
    #
    # A minor is SUPPOSED to be on the guardian's PAN, so
    # nothing is outstanding.
    # --------------------------------------------------------

    is_documentupdaterequired.loc[
        age_under_18
    ] = False

    # --------------------------------------------------------
    # RULE 2: Age >= 18 + guardian PAN still being used
    #
    # They have come of age without the PAN being updated.
    # --------------------------------------------------------

    is_documentupdaterequired.loc[
        age_18_or_over
        &
        guardian_pan_still_used
    ] = True

    # --------------------------------------------------------
    # RULE 3: Age >= 18 + own PAN is being used
    #
    # Already updated.
    # --------------------------------------------------------

    is_documentupdaterequired.loc[
        age_18_or_over
        &
        own_pan_is_being_used
    ] = False

    # An entity fell into neither age band, so this is already
    # null for one -- set explicitly so the intent survives any
    # later change to how the bands are built.
    is_documentupdaterequired.loc[is_entity] = pd.NA

    gold["is_documentupdaterequired"] = (
        is_documentupdaterequired
    )

    # ========================================================
    # IS MINOR
    # ========================================================

    is_minor = pd.Series(
        pd.NA,
        index=df.index,
        dtype="boolean"
    )

    # --------------------------------------------------------
    # RULE 1: Age >= 18 but the PAN was never updated.
    #
    # Still TREATED as a minor: every registry record still
    # stands in the guardian's name.
    # --------------------------------------------------------

    is_minor.loc[
        age_18_or_over
        &
        (gold["is_documentupdaterequired"] == True)
    ] = True

    # --------------------------------------------------------
    # RULE 2: Age >= 18 and operating on their own PAN -> major.
    # --------------------------------------------------------

    is_minor.loc[
        age_18_or_over
        &
        (gold["is_documentupdaterequired"] == False)
    ] = False

    # --------------------------------------------------------
    # RULE 3: Under 18 -> a minor, by definition.
    #
    # Decided here rather than left to the source fallback
    # below. Now that age is recomputed from date_of_birth on
    # every run, this is the one authoritative answer; the
    # fallback would otherwise reach back into silver's
    # is_minor, which is derived from silver's snapshot age and
    # carries exactly the staleness the age block above exists
    # to remove.
    #
    # An entity is excluded: a company incorporated less than 18
    # years ago is not a child. Its age has already been blanked
    # above, so age_under_18 is False for it -- this guard is
    # belt-and-braces against that ordering ever changing.
    # --------------------------------------------------------

    is_minor.loc[
        age_under_18
        &
        (~is_entity)
    ] = True

    # ========================================================
    # FALLBACK TO SOURCE IS_MINOR
    # ========================================================
    #
    # Only where the PAN-based rules above reached no verdict --
    # which is every client under 18 (deliberately not decided
    # here) and every client with no age at all.
    # ========================================================

    if (
        "is_minor" in df.columns
        and
        df["is_minor"].notna().any()
    ):

        source_is_minor = (
            df["is_minor"]
            .astype("boolean")
        )

        is_minor = is_minor.fillna(source_is_minor)

    # The fallback above reads silver's own is_minor, which is a
    # raw age test made before holder type was considered -- it
    # says True for a company incorporated under 18 years ago.
    # Blanking the age alone is therefore not enough: without
    # this, an entity's minor status comes straight back in
    # through the fallback.
    is_minor.loc[is_entity] = pd.NA

    gold["is_minor"] = is_minor

    # ========================================================
    # MINOR / DOCUMENT UPDATE STATISTICS
    # ========================================================

    print("\nMinor / Document Update Statistics")
    print("-" * 80)

    print(
        "Age < 18                         :",
        int(age_under_18.sum())
    )

    print(
        "Age >= 18                        :",
        int(age_18_or_over.sum())
    )

    print(
        "Own PAN available                :",
        int(own_pan.notna().sum())
    )

    print(
        "Guardian PAN available           :",
        int(guardian_pan.notna().sum())
    )

    print(
        "Guardian PAN still being used    :",
        int(guardian_pan_still_used.sum())
    )

    print(
        "Own PAN being used               :",
        int(own_pan_is_being_used.sum())
    )

    print(
        "Document update required = TRUE  :",
        int(gold["is_documentupdaterequired"].eq(True).sum())
    )

    print(
        "Document update required = FALSE :",
        int(gold["is_documentupdaterequired"].eq(False).sum())
    )

    print(
        "Document update status = NULL    :",
        int(gold["is_documentupdaterequired"].isna().sum())
    )

    print(
        "Final minor clients              :",
        int(gold["is_minor"].eq(True).sum())
    )

    print(
        "Final adult clients              :",
        int(gold["is_minor"].eq(False).sum())
    )

    print(
        "Final minor status = NULL        :",
        int(gold["is_minor"].isna().sum())
    )

    # ========================================================
    # APP MANAGED
    # ========================================================

    gold["marital_status"] = None
    gold["anniversary_date"] = None
    gold["blood_group"] = None
    gold["equity_ucc"] = None

    # ========================================================
    # CAN
    # ========================================================

    gold["can"] = None

    if "commonaccno" in df.columns:

        gold.loc[
            kfin_mask,
            "can"
        ] = clean_string(
            df.loc[
                kfin_mask,
                "commonaccno"
            ]
        )

    if "txn_common_account_number" in df.columns:

        missing_can = (
            kfin_mask
            &
            gold["can"].isna()
        )

        gold.loc[
            missing_can,
            "can"
        ] = clean_string(
            df.loc[
                missing_can,
                "txn_common_account_number"
            ]
        )

    # ========================================================
    # OCCUPATION
    # ========================================================

    gold["occupation"] = pd.NA

    if "occupation_description" in df.columns:

        gold["occupation"] = clean_string(
            df["occupation_description"]
        )

    # ========================================================
    # APP MANAGED
    # ========================================================

    gold["user_id"] = None
    gold["family_id"] = None
    gold["family_relation"] = None
    gold["gender"] = None

    # ========================================================
    # INVESTOR TYPE
    # ========================================================

    investor_type_source = pd.Series(
        pd.NA,
        index=df.index,
        dtype="object"
    )

    if "tax_status" in df.columns:

        investor_type_source.loc[
            cams_mask
        ] = clean_string(
            df.loc[
                cams_mask,
                "tax_status"
            ]
        )

    if "statusdesc" in df.columns:

        investor_type_source.loc[
            kfin_mask
        ] = clean_string(
            df.loc[
                kfin_mask,
                "statusdesc"
            ]
        )

    if "categorydesc" in df.columns:

        missing_type = (
            kfin_mask
            &
            investor_type_source.isna()
        )

        investor_type_source.loc[
            missing_type
        ] = clean_string(
            df.loc[
                missing_type,
                "categorydesc"
            ]
        )

    def derive_investor_type(value):

        if pd.isna(value):

            return pd.NA

        value = (
            str(value)
            .upper()
            .strip()
        )

        if "HUF" in value:
            return "HUF"

        if "NRI" in value:
            return "NRI"

        if "TRUST" in value:
            return "TRUST"

        if "INDIVID" in value:
            return "INDIVIDUAL"

        return pd.NA

    gold["investor_type"] = (
        investor_type_source
        .apply(derive_investor_type)
    )

    # ========================================================
    # TAX STATUS
    # ========================================================

    if "tax_status" in df.columns:

        gold["tax_status"] = clean_string(
            df["tax_status"]
        )

    else:

        gold["tax_status"] = None

    # ========================================================
    # KYC
    # ========================================================

    gold["kyc_status"] = pd.NA

    if "ckyc_no" in df.columns:

        cams_ckyc = clean_string(
            df["ckyc_no"]
        )

        gold.loc[
            cams_mask,
            "kyc_status"
        ] = "Not Verified"

        gold.loc[
            cams_mask & cams_ckyc.notna(),
            "kyc_status"
        ] = "Verified"

    if "kyc1flag" in df.columns:

        kfin_kyc = (
            clean_string(
                df["kyc1flag"]
            )
            .fillna("")
            .astype(str)
            .str.upper()
            .str.strip()
        )

        kfin_verified = kfin_kyc.isin(
            [
                "Y",
                "YES",
                "1",
                "TRUE",
                "VERIFIED"
            ]
        )

        gold.loc[
            kfin_mask,
            "kyc_status"
        ] = "Not Verified"

        gold.loc[
            kfin_mask & kfin_verified,
            "kyc_status"
        ] = "Verified"

    # ========================================================
    # CKYC NO
    # ========================================================

    gold["ckyc_no"] = clean_identifier(
        df["ckyc_no"]
    )

    # ========================================================
    # DP ID
    # ========================================================

    gold["dp_id"] = clean_identifier(
        df["dp_id"]
    )

    # ========================================================
    # BENEFICIARY ACCOUNT NUMBER
    # ========================================================
    #
    # Travels with dp_id: the app's client_demat holds the pair as one record,
    # and a DP ID on its own names a depository participant, not an account.

    gold["beneficiary_ac_no"] = clean_identifier(
        df["beneficiary_ac_no"]
    )

    # ========================================================
    # APP MANAGED
    # ========================================================

    gold["risk_profile"] = None
    gold["rm_id"] = None
    gold["branch_id"] = None

    # ========================================================
    # ARN
    # ========================================================

    gold["arn"] = (
        clean_string(
            df["txn_brokcode"]
        )
        .str.upper()
        .str.strip()
        .replace("", pd.NA)
        .str[:50]
    )

    # ========================================================
    # SUB ARN
    # ========================================================

    gold["sub_arn"] = (
        clean_string(
            df["txn_src_brk_code"]
        )
        .str.upper()
        .str.strip()
        .replace("", pd.NA)
        .str[:50]
    )

    print("\nARN Mapping")
    print("-" * 80)

    print(
        "ARN values found      :",
        gold["arn"].notna().sum()
    )

    print(
        "ARN values missing    :",
        gold["arn"].isna().sum()
    )

    print(
        "Sub ARN values found  :",
        gold["sub_arn"].notna().sum()
    )

    print(
        "Sub ARN values missing:",
        gold["sub_arn"].isna().sum()
    )

    # ========================================================
    # ARN ID
    # ========================================================

    gold["arn_id"] = None

    broker_code = clean_string(
        df["txn_brokcode"]
    )

    if broker_code.notna().any():

        arn_lookup = safe_read(
            """
            SELECT
                arn_code,
                id AS arn_id
            FROM arn
            WHERE arn_code IS NOT NULL
              AND TRIM(arn_code) <> ''
              AND COALESCE(is_deleted, FALSE) = FALSE
            """,
            master_engine
        )

        if not arn_lookup.empty:

            arn_lookup["arn_code"] = (
                arn_lookup["arn_code"]
                .astype(str)
                .str.strip()
                .str.upper()
            )

            arn_lookup = (
                arn_lookup
                .drop_duplicates(
                    subset=["arn_code"]
                )
                .set_index("arn_code")["arn_id"]
            )

            gold["arn_id"] = (
                broker_code
                .astype("string")
                .str.strip()
                .str.upper()
                .map(arn_lookup)
            )

    print("\nARN ID Mapping")
    print("-" * 80)

    print(
        "Broker codes found :",
        broker_code.notna().sum()
    )

    print(
        "ARN IDs mapped     :",
        gold["arn_id"].notna().sum()
    )

    print(
        "ARN IDs missing    :",
        gold["arn_id"].isna().sum()
    )

    # ========================================================
    # ONBOARDED AT
    # ========================================================

    gold["onboarded_at"] = pd.NaT

    if "folio_date" in df.columns:

        folio_date = pd.to_datetime(
            df["folio_date"],
            errors="coerce",
            format="ISO8601"
        )

        gold.loc[
            cams_mask,
            "onboarded_at"
        ] = folio_date.loc[
            cams_mask
        ]

    if "txn_traddate" in df.columns:

        txn_date = pd.to_datetime(
            df["txn_traddate"],
            errors="coerce",
            format="ISO8601"
        )

        gold.loc[
            kfin_mask,
            "onboarded_at"
        ] = txn_date.loc[
            kfin_mask
        ]

    gold["onboarded_at"] = (
        pd.to_datetime(
            gold["onboarded_at"],
            errors="coerce",
            format="ISO8601"
        )
        .dt.date
    )

    # ========================================================
    # SOURCE
    # ========================================================

    gold["source"] = clean_string(
        df["source"]
    )

    # ========================================================
    # CREATED AT
    # ========================================================

    gold["created_at"] = (
        datetime.now(timezone.utc)
        .replace(tzinfo=None)
    )

    # ========================================================
    # FINAL COLUMN ORDER
    # ========================================================

    gold = gold[
        [
            "status",
            "full_name",
            "client_label",
            "phone",
            "mobile_isd",
            "mobile",
            "whatsapp_same_as_mobile",
            "whatsapp_isd",
            "whatsapp_no",
            "aadhaar",
            "pan",
            "pan_verified",
            "pan_verified_at",
            "guardian_pan",
            "arn",
            "sub_arn",
            "email",
            "date_of_birth",
            "age",
            "is_minor",
            "is_documentupdaterequired",
            "marital_status",
            "anniversary_date",
            "blood_group",
            "equity_ucc",
            "can",
            "occupation",
            "user_id",
            "family_id",
            "family_relation",
            "gender",
            "investor_type",
            "tax_status",
            "kyc_status",
            "ckyc_no",
            "dp_id",
            "beneficiary_ac_no",
            "risk_profile",
            "rm_id",
            "branch_id",
            "arn_id",
            "onboarded_at",
            "source",
            "created_at",
            "individual_category_type",
            "aadhaar_seeding_status"
        ]
    ]

    print("\nTransformation Completed")
    print("-" * 80)

    print(
        "Gold rows generated :",
        len(gold)
    )

    print(
        "Gold CKYC values    :",
        gold["ckyc_no"].notna().sum()
    )

    print(
        "Gold DP ID values   :",
        gold["dp_id"].notna().sum()
    )

    # ========================================================
    # RECENCY KEY (not a gold column)
    # ========================================================
    #
    # report_date is the registry statement date, and it is the
    # ONLY usable recency signal on a folio: created_at and
    # updated_at are batch-load stamps shared by every row of a
    # load (all 20 of PAN ACOPP7220P's folios carry the same
    # updated_at), lastupdateddate is only ~41% populated and
    # folio_date ~59%, while report_date is 100%.
    #
    # load_clients() uses it to decide which folio wins when one
    # person's folios disagree. It is NOT a gold.clients column;
    # the pre-insert filter there drops any column the table
    # does not have, so it never reaches the database.
    # ========================================================

    if "report_date" in df.columns:

        gold["report_date"] = pd.to_datetime(
            df["report_date"],
            errors="coerce"
        )

    else:

        gold["report_date"] = pd.NaT

    # The folio's natural key, carried for ONE purpose: to make
    # the de-duplication sort in load_clients() a TOTAL order.
    #
    # Without it the sort is not total -- two folios of one
    # person can tie on report_date AND on every name key -- and
    # pandas' default quicksort is not stable, so the winner
    # varied between runs. PAN AUDPP6124D took sub_arn
    # "ARN-76793" on one run and NULL on the next from two
    # otherwise identical folios. Ties are now broken by the
    # folio itself, which is unique per row in silver
    # (source, folio_no, product_code), so the outcome cannot
    # depend on the order Postgres happened to return.
    #
    # Dropped before the INSERT alongside report_date.
    for column in ("folio_no", "product_code"):

        gold["_tiebreak_" + column] = (
            df[column].astype("string")
            if column in df.columns
            else pd.NA
        )

    return gold


# ============================================================
# UPDATE EXISTING CLIENT ATTRIBUTES
# ============================================================

def update_existing_client_attributes(gold_df):

    """Backfill registry-derived attributes onto already-loaded clients.

    load_clients() below drops every row whose PAN is already in
    gold.clients, so its upsert never reaches an existing client
    and a newly mapped column cannot arrive by re-running the
    pipeline. That is how guardian_pan stayed NULL for all 594
    clients loaded before it was mapped: the value was correct in
    silver, survived extract and transform, and was then filtered
    out one step short of the table.

    Only ever fills a NULL, so a value the app or a human has
    already set is never disturbed and this stays safe to re-run.
    """

    print()
    print("=" * 80)
    print("UPDATING EXISTING CLIENT ATTRIBUTES")
    print("=" * 80)

    update_df = gold_df[
        gold_df["pan"].notna()
        &
        (
            gold_df["ckyc_no"].notna()
            |
            gold_df["dp_id"].notna()
            |
            gold_df["beneficiary_ac_no"].notna()
            |
            gold_df["guardian_pan"].notna()
        )
    ][
        [
            "pan",
            "ckyc_no",
            "dp_id",
            "beneficiary_ac_no",
            "guardian_pan"
        ]
    ].copy()

    if update_df.empty:

        print(
            "No existing clients have attribute values to update."
        )

        return 0

    # ========================================================
    # GET EXISTING CLIENTS
    # ========================================================

    existing = safe_read(
        """
        SELECT
            id,
            pan,
            ckyc_no,
            dp_id,
            beneficiary_ac_no,
            guardian_pan
        FROM gold.clients
        WHERE pan IS NOT NULL
          AND superseded_by IS NULL
        """
    )

    if existing.empty:

        print(
            "No existing clients found."
        )

        return 0

    existing["pan"] = clean_pan(
        existing["pan"]
    )

    # ========================================================
    # MAP GOLD DATA TO EXISTING CLIENT ID
    # ========================================================

    update_df = update_df.merge(
        existing[
            [
                "id",
                "pan",
                "ckyc_no",
                "dp_id",
                "beneficiary_ac_no",
                "guardian_pan"
            ]
        ],
        on="pan",
        how="inner",
        suffixes=(
            "_new",
            "_existing"
        )
    )

    if update_df.empty:

        print(
            "No matching existing clients found."
        )

        return 0

    # ========================================================
    # ONLY UPDATE WHEN GOLD HAS A VALUE
    # AND EXISTING VALUE IS NULL
    # ========================================================

    update_df["final_ckyc"] = (
        update_df["ckyc_no_existing"]
        .fillna(update_df["ckyc_no_new"])
    )

    update_df["final_dp_id"] = (
        update_df["dp_id_existing"]
        .fillna(update_df["dp_id_new"])
    )

    changed = update_df[
        (
            update_df["ckyc_no_existing"].isna()
            &
            update_df["ckyc_no_new"].notna()
        )
        |
        (
            update_df["dp_id_existing"].isna()
            &
            update_df["dp_id_new"].notna()
        )
        |
        (
            update_df["beneficiary_ac_no_existing"].isna()
            &
            update_df["beneficiary_ac_no_new"].notna()
        )
        |
        (
            update_df["guardian_pan_existing"].isna()
            &
            update_df["guardian_pan_new"].notna()
        )
    ].copy()

    if changed.empty:

        print(
            "No existing client attributes required updating."
        )

        return 0

    # ========================================================
    # UPDATE DATABASE
    # ========================================================

    updated_count = 0

    try:

        with engine.begin() as connection:

            for _, row in changed.iterrows():

                query = """

                UPDATE gold.clients

                SET
                    ckyc_no = COALESCE(:ckyc_no, ckyc_no),
                    dp_id = COALESCE(:dp_id, dp_id),
                    beneficiary_ac_no = COALESCE(
                        :beneficiary_ac_no, beneficiary_ac_no
                    ),
                     guardian_pan = COALESCE(
                        guardian_pan, :guardian_pan
                    )

                WHERE id = :id

                """

                connection.execute(
                    __import__(
                        "sqlalchemy"
                    ).text(query),
                    {
                        "ckyc_no": (
                            None
                            if pd.isna(
                                row["ckyc_no_new"]
                            )
                            else str(
                                row["ckyc_no_new"]
                            )
                        ),
                        "dp_id": (
                            None
                            if pd.isna(
                                row["dp_id_new"]
                            )
                            else str(
                                row["dp_id_new"]
                            )
                        ),
                        "beneficiary_ac_no": (
                            None
                            if pd.isna(
                                row["beneficiary_ac_no_new"]
                            )
                            else str(
                                row["beneficiary_ac_no_new"]
                            )
                        ),
                        "guardian_pan": (
                            None
                            if pd.isna(
                                row["guardian_pan_new"]
                            )
                            else str(
                                row["guardian_pan_new"]
                            )
                        ),
                        "id": row["id"]
                    }
                )

                updated_count += 1

        print(
            "Existing clients updated:",
            updated_count
        )

        return updated_count

    except Exception as e:

        print(
            "Existing client attribute update failed:",
            str(e)[:3000]
        )

        traceback.print_exc()

        return 0


# ============================================================
# LOAD CLIENTS INTO DATABASE
# ============================================================

def refresh_derived_demographics(gold_df):

    """Push recomputed date_of_birth / age / is_minor /
    is_documentupdaterequired onto clients ALREADY in gold.clients.

    Without this a correction to any of the four can never reach an
    existing client. load_clients() drops every row whose PAN is
    already in the table before it inserts, and
    update_existing_client_attributes() only ever fills a NULL and
    only touches ckyc_no / dp_id / guardian_pan -- so a re-run would
    compute the right value and then throw it away. That is how 34
    entities kept an age derived from an incorporation date, and how
    12 clients kept a NULL date of birth that silver could fill.

    These four are OVERWRITTEN rather than coalesced, unlike the
    attributes that function handles. They are pure functions of
    silver (dob -> age -> the minor and document-update rules) with
    the pipeline as their only author, so the newest run is
    authoritative. ckyc_no / dp_id / guardian_pan stay fill-only
    because the app or a human may have set them.

    Rows are compared with IS DISTINCT FROM in the statement itself,
    so a run that changes nothing reports 0 and writes nothing.
    """

    from psycopg2.extras import execute_values

    print()
    print("=" * 80)
    print("REFRESHING DERIVED DEMOGRAPHICS")
    print("=" * 80)

    # (column, postgres cast, mode)
    #
    #   OVERWRITE -- the pipeline is the sole author, so the
    #                newest run wins outright, NULL included.
    #                These are pure functions of silver
    #                (dob -> age -> the minor / document rules).
    #
    #   LATEST    -- registry-sourced description of the client.
    #                Takes the newest non-null value and NEVER
    #                writes a NULL over something already there,
    #                so a statement that omits a field cannot
    #                erase it. This is what lets a corrected
    #                name, email or mobile reach a client who is
    #                already in gold.
    #
    # `pan` is the match key and is never in either list -- it
    # identifies the row being updated, so it must not be
    # updatable by this statement.
    #
    # Deliberately absent:
    #   guardian_pan, ckyc_no, dp_id, beneficiary_ac_no
    #       fill-only in update_existing_client_attributes(),
    #       because the app or a human may have set them.
    #   onboarded_at
    #       first-seen date, not a latest-wins attribute.
    #   aadhaar, pan_verified, pan_verified_at, rm_id, branch_id,
    #   arn_id, risk_profile, user_id, family_id, gender, status,
    #   ...
    #       app-managed; the ETL sets them to None, so refreshing
    #       them could only ever blank an app value.
    # Each cast carries the column's OWN declared width, not a
    # bare `varchar`. Two reasons: an explicit cast to
    # varchar(n) truncates where an assignment would raise --
    # occupation_description "Public Sector / Government
    # Service" is 34 characters against a varchar(30) column,
    # and gold already holds it cut to 30 -- and a width here
    # that drifts from the DDL shows up as an error rather than
    # as silently mangled data.
    OVERWRITE = "overwrite"
    LATEST = "latest"

    refresh_spec = [
        ("date_of_birth", "date", OVERWRITE),
        ("age", "integer", OVERWRITE),
        ("is_minor", "boolean", OVERWRITE),
        ("is_documentupdaterequired", "boolean", OVERWRITE),
        ("individual_category_type", "varchar(30)", OVERWRITE),
        ("aadhaar_seeding_status", "varchar(20)", OVERWRITE),

        ("full_name", "varchar(255)", LATEST),
        ("email", "varchar(255)", LATEST),
        ("phone", "varchar(20)", LATEST),
        ("mobile", "varchar(20)", LATEST),
        ("mobile_isd", "varchar(5)", LATEST),
        ("whatsapp_no", "varchar(20)", LATEST),
        ("whatsapp_isd", "varchar(5)", LATEST),
        ("whatsapp_same_as_mobile", "boolean", LATEST),
        ("occupation", "varchar(30)", LATEST),
        ("investor_type", "varchar(20)", LATEST),
        ("tax_status", "varchar(30)", LATEST),
        ("kyc_status", "varchar(20)", LATEST),
        ("can", "varchar(30)", LATEST),
        ("arn", "varchar(50)", LATEST),
        ("sub_arn", "varchar(50)", LATEST),
        ("source", "varchar(30)", LATEST),
    ]

    # The SET clause, the VALUES alias and the changed-row test
    # are all generated from that one list, because they MUST
    # agree and Postgres will not say so if they do not: a VALUES
    # tuple wider than its alias is silently truncated, so adding
    # a column here and forgetting it in the alias updated
    # nothing at all and reported success.
    columns = ["pan"] + [c for c, _, _ in refresh_spec]

    set_clause = ",\n               ".join(
        "{c} = v.{c}::{t}".format(c=c, t=t)
        if mode == OVERWRITE
        else "{c} = coalesce(v.{c}::{t}, c.{c})".format(c=c, t=t)
        for c, t, mode in refresh_spec
    )

    values_alias = ",\n                   ".join(columns)

    # A LATEST column only counts as changed when the incoming
    # value is non-null, mirroring the coalesce above -- without
    # that, a client whose name is absent from this batch would
    # match the WHERE every run and be rewritten to itself.
    changed_clause = "\n                OR ".join(
        "c.{c} IS DISTINCT FROM v.{c}::{t}".format(c=c, t=t)
        if mode == OVERWRITE
        else (
            "(v.{c} IS NOT NULL"
            " AND c.{c} IS DISTINCT FROM v.{c}::{t})"
        ).format(c=c, t=t)
        for c, t, mode in refresh_spec
    )

    # guardian_pan and full_name are not refreshed themselves --
    # they are the identity key for the PAN-less pass below.
    missing = [
        c for c in columns + ["guardian_pan", "full_name"]
        if c not in gold_df.columns
    ]

    if missing:

        print("Skipped -- gold_df has no", missing)

        return 0

    refresh = (
        gold_df[gold_df["pan"].notna()][columns]
        .drop_duplicates(subset=["pan"], keep="first")
    )

    if refresh.empty:

        print("No PAN'd clients to refresh.")

        return 0

    records = (
        refresh
        .astype(object)
        .where(pd.notnull(refresh), None)
        .to_dict(orient="records")
    )

    rows = [
        tuple(record[c] for c in columns)
        for record in records
    ]

    sql = """
        UPDATE gold.clients AS c

           SET {set_clause}

          FROM (VALUES %s) AS v (
                   {values_alias}
               )

         WHERE c.pan = v.pan::varchar
           AND c.superseded_by IS NULL

           AND (
                   {changed_clause}
               )
    """.format(
        set_clause=set_clause,
        values_alias=values_alias,
        changed_clause=changed_clause
    )

    updated = 0

    try:

        with engine.begin() as connection:

            cursor = connection.connection.cursor()

            for i in range(0, len(rows), 500):

                batch = rows[i:i + 500]

                execute_values(
                    cursor,
                    sql,
                    batch,
                    page_size=len(batch)
                )

                updated += cursor.rowcount

        updated += _refresh_panless_demographics(gold_df)

        print("Clients whose derived columns changed:", updated)

        return updated

    except Exception as e:

        print("Refresh failed:", e)

        traceback.print_exc(limit=5)

        return 0


def _refresh_panless_demographics(gold_df):

    """The same refresh, for the clients who have no PAN.

    The pass above matches on pan, so it reaches none of them --
    and they are the 22 minors, i.e. precisely the population
    whose age matters most. Ahaan Vedant Maheshwari turned 15 on
    2026-09-10 and stayed 14 in gold.clients after a full run,
    because nothing in the refresh could address him.

    Matched on the same identity gold.clients stores a PAN-less
    client under: guardian PAN + FIRST name + date of birth. The
    first name rather than the whole one because a client's
    stored spelling can shift between runs (DHYANI PATEL /
    Dhyani N Patel), while the first name survives middle-name
    drift and still separates twins -- NIVAA and NIVAAN VAISHAL
    SHAH share a guardian PAN and a date of birth, so nothing
    weaker can tell them apart.

    Keys are built as text with a '~' sentinel for NULL: several
    of these clients have no guardian PAN and no date of birth,
    and a join would otherwise have to match null against null.
    """

    from psycopg2.extras import execute_values

    panless = gold_df[gold_df["pan"].isna()].copy()

    if panless.empty:

        return 0

    def first_name(series):

        return (
            series
            .fillna("")
            .astype(str)
            .str.upper()
            .str.replace(r"\s+", " ", regex=True)
            .str.strip()
            .str.split(" ")
            .str[0]
        )

    panless["_gpan"] = (
        panless["guardian_pan"]
        .fillna("~")
        .astype(str)
        .str.upper()
        .str.strip()
    )

    panless["_name"] = first_name(panless["full_name"])

    panless["_dob"] = (
        pd.to_datetime(panless["date_of_birth"], errors="coerce")
        .dt.strftime("%Y-%m-%d")
        .fillna("~")
    )

    panless = panless.drop_duplicates(
        subset=["_gpan", "_name", "_dob"],
        keep="first"
    )

    key_columns = [
        "_gpan",
        "_name",
        "_dob",
        "date_of_birth",
        "age",
        "is_minor",
        "is_documentupdaterequired",
        "individual_category_type",
        "aadhaar_seeding_status",
    ]

    records = (
        panless[key_columns]
        .astype(object)
        .where(pd.notnull(panless[key_columns]), None)
        .to_dict(orient="records")
    )

    rows = [
        tuple(record[c] for c in key_columns)
        for record in records
    ]

    sql = """
        UPDATE gold.clients AS c

           SET date_of_birth = v.date_of_birth::date,
               age           = v.age::integer,
               is_minor      = v.is_minor::boolean,
               is_documentupdaterequired =
                   v.is_documentupdaterequired::boolean,
               individual_category_type =
                   v.individual_category_type::varchar,
               aadhaar_seeding_status =
                   v.aadhaar_seeding_status::varchar

          FROM (VALUES %s) AS v (
                   gpan, name_key, dob_key,
                   date_of_birth, age, is_minor,
                   is_documentupdaterequired,
                   individual_category_type,
                   aadhaar_seeding_status
               )

         WHERE c.pan IS NULL

           AND c.superseded_by IS NULL

           AND coalesce(upper(btrim(c.guardian_pan)), '~') = v.gpan::text

           AND split_part(
                   upper(regexp_replace(btrim(c.full_name), '\s+', ' ', 'g')),
                   ' ', 1
               ) = v.name_key::text

           AND coalesce(c.date_of_birth::text, '~') = v.dob_key::text

           AND (
                   c.date_of_birth IS DISTINCT FROM v.date_of_birth::date
                OR c.age           IS DISTINCT FROM v.age::integer
                OR c.is_minor      IS DISTINCT FROM v.is_minor::boolean
                OR c.is_documentupdaterequired
                       IS DISTINCT FROM v.is_documentupdaterequired::boolean
                OR c.individual_category_type
                       IS DISTINCT FROM v.individual_category_type::varchar
                OR c.aadhaar_seeding_status
                       IS DISTINCT FROM v.aadhaar_seeding_status::varchar
               )
    """

    updated = 0

    with engine.begin() as connection:

        cursor = connection.connection.cursor()

        for i in range(0, len(rows), 500):

            batch = rows[i:i + 500]

            execute_values(
                cursor,
                sql,
                batch,
                page_size=len(batch)
            )

            updated += cursor.rowcount

    return updated


def adopt_pan_via_folio():

    """Give an arriving PAN to the client who already owns the folio.

    A minor transacts on a guardian's PAN and is stored with
    pan = NULL. The day they file their own, the registry sends the
    SAME folio back carrying it -- and load_clients() below, which
    identifies a PAN'd row by its PAN alone, finds that PAN nowhere
    in gold.clients and inserts a SECOND client.

    Veerpal Shah (folio 1017395889, guardian ACWPS4328K) became two
    rows that way in a simulation: the old one kept her bank and
    address and her folio link, the new one got neither, and the old
    one became unreachable by BOTH refresh passes -- the PAN'd pass
    matches on a PAN it does not have, and the PAN-less pass no
    longer produces a matching key because the source folio now
    carries a PAN. A stranded row that can never again be corrected.

    The folio is the stable identity here: it does not change when a
    PAN is issued, when a name is respelled, or when a middle name
    appears. So the folio decides, and the PAN is written onto the
    client it already belongs to.

    Deliberately narrow. A client adopts a PAN only when:

      * one of their folios carries it in silver,
      * they have NO PAN of their own yet -- so this can never
        overwrite an identity that is already settled,
      * no other client holds that PAN already, and
      * the match is unambiguous BOTH ways: one PAN, one client.

    Anything else is reported and left alone. Five clients are
    currently one filing away from needing this: Veerpal Shah (22),
    YUG SANGHVI (21), Raina K Patel (20), AVLEENKAUR SAINI (19) and
    Heer Pritipal Shah (19).
    """

    print()
    print("=" * 80)
    print("RESOLVING ARRIVING PANs AGAINST LINKED FOLIOS")
    print("=" * 80)

    candidates = safe_read(
        """
        WITH linked AS (

            SELECT DISTINCT
                   upper(btrim(im.pan_no)) AS candidate_pan,
                   cf.client_id

            FROM silver.investor_master im

            JOIN gold.client_folio cf

              ON upper(btrim(cf.source)) = upper(btrim(im.source))
             AND cf.folio_no = gold.normalise_folio(im.folio_no)

            WHERE upper(btrim(im.pan_no))
                  ~ '^[A-Z]{5}[0-9]{4}[A-Z]$'
        ),

        adoptable AS (

            SELECT l.candidate_pan,
                   l.client_id,
                   c.full_name,
                   c.guardian_pan,
                   c.date_of_birth

            FROM linked l

            JOIN gold.clients c
              ON c.id = l.client_id
             AND c.superseded_by IS NULL

             -- never overwrite a settled identity
            WHERE c.pan IS NULL

             -- and never duplicate one that exists elsewhere
              AND NOT EXISTS (
                      SELECT 1
                      FROM gold.clients x
                      WHERE x.pan = l.candidate_pan
                        AND x.superseded_by IS NULL
                  )
        )

        SELECT a.candidate_pan,
               a.client_id,
               a.full_name,
               a.guardian_pan,
               a.date_of_birth,

               count(*) OVER (PARTITION BY a.candidate_pan)
                   AS clients_for_pan,

               count(*) OVER (PARTITION BY a.client_id)
                   AS pans_for_client

        FROM adoptable a

        ORDER BY a.full_name
        """
    )

    if candidates.empty:

        print("No arriving PAN matched a linked folio.")

        return 0

    unambiguous = candidates[
        (candidates["clients_for_pan"] == 1)
        &
        (candidates["pans_for_client"] == 1)
    ]

    ambiguous = candidates[
        (candidates["clients_for_pan"] > 1)
        |
        (candidates["pans_for_client"] > 1)
    ]

    if not ambiguous.empty:

        print()
        print(
            "AMBIGUOUS -- left untouched, needs a decision:",
            len(ambiguous)
        )

        for row in ambiguous.itertuples(index=False):

            print(
                "   {name}  pan={pan}  "
                "clients_for_pan={c}  pans_for_client={p}".format(
                    name=row.full_name,
                    pan=row.candidate_pan,
                    c=row.clients_for_pan,
                    p=row.pans_for_client
                )
            )

    if unambiguous.empty:

        print("Nothing unambiguous to adopt.")

        return 0

    rows = [
        (row.candidate_pan, str(row.client_id))
        for row in unambiguous.itertuples(index=False)
    ]

    adopted = 0

    try:

        with engine.begin() as connection:

            cursor = connection.connection.cursor()

            execute_values(
                cursor,
                """
                UPDATE gold.clients AS c

                   SET pan = v.pan::varchar(10)

                  FROM (VALUES %s) AS v (pan, client_id)

                 WHERE c.id = v.client_id::uuid

                   -- re-checked inside the transaction, so a
                   -- concurrent writer cannot slip a PAN in
                   -- between the SELECT above and this UPDATE
                   AND c.pan IS NULL
                """,
                rows,
                page_size=len(rows)
            )

            adopted = cursor.rowcount

    except Exception as e:

        print("PAN adoption failed:", e)

        traceback.print_exc(limit=5)

        return 0

    print()
    print("Clients who adopted their own PAN:", adopted)

    for row in unambiguous.itertuples(index=False):

        print(
            "   {name}  {gpan} -> {pan}".format(
                name=row.full_name,
                gpan=row.guardian_pan or "(no guardian)",
                pan=row.candidate_pan
            )
        )

    return adopted


def merge_duplicate_clients(apply_merges=True):

    """Fold a duplicated person back into one client row.

    Prevention lives in load_clients() -- folio-first identity,
    PAN adoption, the guardian/name keys. This is what happens
    when prevention was not there yet, or a new blind spot lets
    one through.

    A pair is merged only when ALL FIVE hold:

      1. IDENTICAL name token sets. Order-independent, so
         "Saleel Y Bhatt" meets "SALEEL Y BHATT" -- but not
         fuzzy: SUREEL BHATT and SALEEL BHATT differ by one
         letter and are two different men.

      2. AT MOST ONE SIDE HAS A PAN. Two PANs mean two people.
         MIHIRKUMAR PATEL (ALGPP2503E, 1982) and Patel
         Mihirkumar (BJYPP6199P, 1986) share a token set and
         are not the same person; this test alone rejects them.

      3. DATES OF BIRTH AGREE, or one side has none.

      4. THEY SHARE A BANK ACCOUNT OR A POSTAL ADDRESS. A name
         and a date of birth are an assertion; a shared bank
         account is evidence.

         Corroboration, not proof: joint accounts are shared
         across a family, and 12 accounts in this warehouse are
         held by two or more clients -- ANIMESH J MEHTA shares
         691050067571 with his wife KRUTIKA. It only carries
         weight on top of a name match that already holds.

      5. NEITHER IS THE OTHER'S GUARDIAN. See the predicate
         below: this one is proof, not evidence, and it is the
         only rule here that can override all the others.

    Every merge is written to gold.client_merge_log with the
    evidence that justified it, and the loser is superseded
    rather than deleted, so the decision can be read back and
    anything still holding the old id can follow it forward.

    Pass apply_merges=False to report without changing anything.
    """

    print()
    print("=" * 80)
    print("MERGING DUPLICATE CLIENTS")
    print("=" * 80)

    candidates = safe_read(
        """
        WITH client_key AS (

            SELECT c.id, c.pan, c.guardian_pan,
                   c.full_name, c.date_of_birth,
                   gold.name_token_key(c.full_name) AS tokens,
                   string_to_array(
                       gold.name_token_key(c.full_name), ' '
                   ) AS token_arr,
                   (SELECT count(*) FROM gold.client_folio f
                     WHERE f.client_id = c.id) AS folios

            FROM gold.clients c

            WHERE c.superseded_by IS NULL
              AND gold.name_token_key(c.full_name) IS NOT NULL
        ),

        pair AS (

            SELECT a.id AS a_id, b.id AS b_id,
                   a.pan AS a_pan, b.pan AS b_pan,
                   a.full_name AS a_name, b.full_name AS b_name,
                   a.folios AS a_folios, b.folios AS b_folios,
                   a.tokens

            FROM client_key a

            JOIN client_key b

              -- SUBSET, not equality. One side routinely carries
              -- a middle name the other does not -- and the
              -- de-duplication upstream now PREFERS the fuller
              -- spelling, so the very fix that improved the name
              -- moved "PRITIPAL SHAH" to "PRITIPAL MANUBHAI
              -- SHAH" and put it out of reach of an exact match
              -- against its own PAN-less twin.
              --
              -- The smaller side must carry at least two tokens:
              -- {SHAH} is a subset of half the book and says
              -- nothing.
              ON (a.token_arr <@ b.token_arr
                  OR b.token_arr <@ a.token_arr)
             AND array_length(a.token_arr, 1) >= 2
             AND array_length(b.token_arr, 1) >= 2
             AND b.id > a.id

            WHERE (a.pan IS NULL OR b.pan IS NULL)

              AND (a.date_of_birth IS NULL
                   OR b.date_of_birth IS NULL
                   OR a.date_of_birth = b.date_of_birth)

              -- A GUARDIAN IS NOT THEIR WARD.
              --
              -- Positive proof that two rows are different
              -- people, and it outranks every similarity: if one
              -- holds the other's PAN as its guardian_pan, the
              -- registry is telling us this is a parent and a
              -- child.
              --
              -- Needed because the other rules can all line up
              -- on exactly this pair. Indian names make a
              -- guardian's name a subset of the ward's -- Vedant
              -- Maheshwari inside Ahaan Vedant Maheshwari -- and
              -- a minor usually transacts on the guardian's bank
              -- account, so the shared-account corroboration
              -- fires too. Only the differing dates of birth
              -- were holding that pair apart, and a minor with
              -- no date of birth on file has nothing holding it:
              -- rule 3 passes on a NULL, and the child is
              -- absorbed into the parent.
              AND upper(btrim(coalesce(a.guardian_pan, '~')))
                  IS DISTINCT FROM upper(btrim(coalesce(b.pan, '!')))

              AND upper(btrim(coalesce(b.guardian_pan, '~')))
                  IS DISTINCT FROM upper(btrim(coalesce(a.pan, '!')))
        ),

        corroborated AS (

            SELECT p.*,

                   (SELECT string_agg(DISTINCT x.account_number, ',')
                      FROM gold.client_bank x
                      JOIN gold.client_bank y
                        ON y.account_key = x.account_key
                     WHERE x.client_id = p.a_id
                       AND y.client_id = p.b_id) AS shared_bank,

                   (SELECT count(*)
                      FROM gold.client_address x
                      JOIN gold.client_address y
                        ON y.address_key = x.address_key
                     WHERE x.client_id = p.a_id
                       AND y.client_id = p.b_id) AS shared_addresses

            FROM pair p
        )

        SELECT
            CASE WHEN a_pan IS NOT NULL THEN a_id
                 WHEN b_pan IS NOT NULL THEN b_id
                 WHEN a_folios >= b_folios THEN a_id
                 ELSE b_id END AS winner_id,

            CASE WHEN a_pan IS NOT NULL THEN b_id
                 WHEN b_pan IS NOT NULL THEN a_id
                 WHEN a_folios >= b_folios THEN b_id
                 ELSE a_id END AS loser_id,

            tokens, a_name, b_name,
            coalesce(shared_bank, '') AS shared_bank,
            shared_addresses

        FROM corroborated

        WHERE shared_bank IS NOT NULL OR shared_addresses > 0

        ORDER BY tokens
        """
    )

    if candidates.empty:

        print("No duplicate clients found.")

        return 0

    print("Duplicate pairs found:", len(candidates))

    merged = 0

    for row in candidates.itertuples(index=False):

        evidence = (
            "shared bank account " + row.shared_bank
            if row.shared_bank
            else "shared postal address x%d" % row.shared_addresses
        )

        print(
            "   {a}  +  {b}   [{ev}]".format(
                a=row.a_name, b=row.b_name, ev=evidence
            )
        )

        if not apply_merges:

            continue

        try:

            with engine.begin() as connection:

                connection.execute(
                    text(
                        "SELECT gold.merge_client("
                        ":loser, :winner, :reason, :evidence)"
                    ),
                    {
                        "loser": str(row.loser_id),
                        "winner": str(row.winner_id),
                        "reason": "NAME_TOKENS_DOB_CORROBORATED",
                        "evidence": evidence,
                    }
                )

            merged += 1

        except Exception as e:

            print("      merge failed:", e)

    if apply_merges:

        print("Clients merged:", merged)

    else:

        print("Report only -- nothing changed.")

    return merged


def link_client_folios():

    """Keep gold.client_folio current as new folios arrive.

    The table was built by a one-off backfill and nothing has
    maintained it since, so every folio received after that day went
    unlinked and the map decayed from the moment it was written --
    including for clients the pipeline creates perfectly well.

    A folio is linked to the client who holds its PAN, or, when it
    carries none, to the PAN-less client stored under the same
    guardian PAN + first name + date of birth that load_clients()
    de-duplicates on.

    ON CONFLICT DO NOTHING: an existing link is never re-pointed
    here. Moving a folio from one client to another is a merge, and
    a merge is not something to do as a side effect of a load.
    """

    print()
    print("=" * 80)
    print("LINKING FOLIOS TO CLIENTS")
    print("=" * 80)

    try:

        with engine.begin() as connection:

            result = connection.execute(text(
                """
                INSERT INTO gold.client_folio
                    (source, folio_no, client_id, resolved_by)

                SELECT DISTINCT ON (src, folio)
                       src, folio, client_id, resolved_by

                FROM (

                    -- The folio carries a PAN: its owner holds it.
                    SELECT upper(btrim(im.source)) AS src,
                           gold.normalise_folio(im.folio_no) AS folio,
                           c.id AS client_id,
                           'PAN' AS resolved_by

                    FROM silver.investor_master im

                    JOIN gold.clients c
                      ON c.pan = upper(btrim(im.pan_no))
                     AND c.superseded_by IS NULL

                    WHERE upper(btrim(im.pan_no))
                          ~ '^[A-Z]{5}[0-9]{4}[A-Z]$'
                      AND gold.normalise_folio(im.folio_no)
                          IS NOT NULL

                    UNION ALL

                    -- No usable PAN, but a guardian: the same
                    -- guardian + FIRST name + DOB key the loader
                    -- de-duplicates this population on.
                    --
                    -- gold.norm_name(), not name_token_key():
                    -- the latter SORTS the tokens, so its first
                    -- element is the alphabetically first token,
                    -- not the first name. The loader takes the
                    -- first name as written, and this has to
                    -- agree with it or the map records an
                    -- ownership the loader does not believe.
                    SELECT upper(btrim(im.source)) AS src,
                           gold.normalise_folio(im.folio_no) AS folio,
                           c.id AS client_id,
                           'GUARDIAN_NAME_DOB' AS resolved_by

                    FROM silver.investor_master im

                    JOIN gold.clients c
                      ON c.pan IS NULL
                     AND c.superseded_by IS NULL
                     AND upper(btrim(c.guardian_pan))
                         = upper(btrim(im.guardian_pan))
                     AND split_part(
                             gold.norm_name(c.full_name), ' ', 1
                         )
                         = split_part(
                             gold.norm_name(im.investor_name), ' ', 1
                         )
                     AND coalesce(c.date_of_birth, DATE '0001-01-01')
                         = coalesce(im.dob, DATE '0001-01-01')

                    WHERE coalesce(upper(btrim(im.pan_no)), '')
                          !~ '^[A-Z]{5}[0-9]{4}[A-Z]$'
                      AND nullif(btrim(im.guardian_pan), '')
                          IS NOT NULL
                      AND gold.normalise_folio(im.folio_no)
                          IS NOT NULL

                    UNION ALL

                    -- No PAN and no guardian. With no family to
                    -- disambiguate within, the loader keys these
                    -- on the WHOLE normalised name + DOB, so this
                    -- does too.
                    --
                    -- Without this branch the seven folios of
                    -- this population went unlinked -- the very
                    -- clients whose identity is weakest and who
                    -- most need a folio to anchor it.
                    SELECT upper(btrim(im.source)) AS src,
                           gold.normalise_folio(im.folio_no) AS folio,
                           c.id AS client_id,
                           'NAME_DOB' AS resolved_by

                    FROM silver.investor_master im

                    JOIN gold.clients c
                      ON c.pan IS NULL
                     AND c.superseded_by IS NULL
                     AND c.guardian_pan IS NULL
                     AND gold.norm_name(c.full_name)
                         = gold.norm_name(im.investor_name)
                     AND coalesce(c.date_of_birth, DATE '0001-01-01')
                         = coalesce(im.dob, DATE '0001-01-01')

                     -- "NON RESIDENT" sits in pan_no on one of
                     -- these folios, so a NULL test is not
                     -- enough: anything that is not a PAN means
                     -- this client has none.
                    WHERE coalesce(upper(btrim(im.pan_no)), '')
                          !~ '^[A-Z]{5}[0-9]{4}[A-Z]$'
                      AND nullif(btrim(im.guardian_pan), '') IS NULL
                      AND gold.normalise_folio(im.folio_no)
                          IS NOT NULL

                ) resolved

                -- A folio that resolves two ways takes the PAN
                -- answer; ORDER BY makes DISTINCT ON deterministic.
                ORDER BY src, folio, resolved_by

                ON CONFLICT (source, folio_no) DO NOTHING
                """
            ))

            linked = result.rowcount

    except Exception as e:

        print("Folio linking failed:", e)

        traceback.print_exc(limit=5)

        return 0

    print("New folio links created:", linked)

    return linked


def load_clients(gold_df):

    print("=" * 80)
    print("LOADING DATA INTO GOLD.CLIENTS")
    print("=" * 80)

    if gold_df.empty:

        print("No client rows generated.")

        return False

    gold_df = gold_df.copy()

    # ========================================================
    # CLEAN VALUES
    # ========================================================

    gold_df["pan"] = clean_pan(
        gold_df["pan"]
    )

    gold_df["ckyc_no"] = clean_string(
        gold_df["ckyc_no"]
    )

    gold_df["dp_id"] = clean_string(
        gold_df["dp_id"]
    )

    # ========================================================
    # DROP ROWS WITH NO IDENTITY AT ALL
    #
    # A missing PAN is no longer a reason to discard a client.
    # A minor has no PAN of their own -- the registry records
    # only the guardian's -- so requiring one deleted every
    # minor along with their address and bank rows.
    #
    # A row still needs SOMETHING to be identified by: a PAN, or
    # failing that a name.
    # ========================================================

    before = len(gold_df)

    gold_df = gold_df[
        gold_df["pan"].notna()
        |
        gold_df["full_name"].notna()
    ].copy()

    print(
        "Rows with no identity removed:",
        before - len(gold_df)
    )

    # ========================================================
    # REMOVE DUPLICATES
    #
    # Two populations, two keys:
    #   pan present -> the PAN identifies the person
    #   pan absent  -> guardian PAN + name + date of birth does
    # ========================================================

    has_pan = gold_df["pan"].notna()

    before = len(gold_df)

    # ========================================================
    # NEWEST STATEMENT WINS, FIELD BY FIELD
    # ========================================================
    #
    # This used to be drop_duplicates(subset=["pan"],
    # keep="last"), which kept ONE arbitrary folio row whole and
    # threw the rest away. That lost data two different ways:
    #
    #   * If the surviving row had a blank field, the value was
    #     gone even though other folios carried it. JITENDRA C
    #     PATEL (ACOPP7220P) has 20 folios, 17 of them stating
    #     his date of birth -- gold ended up with none, and so
    #     with no age and no is_minor either. 12 clients were
    #     affected.
    #
    #   * If two folios disagreed, the winner was arbitrary. 14
    #     PANs carry conflicting dates of birth (mostly day/month
    #     transpositions such as SONAL TUSHAR SHAH's 1965-02-11
    #     vs 1965-11-02).
    #
    # Both are resolved by the same rule: order a person's
    # folios newest statement first, then take each field from
    # the most recent folio that actually has one. A newer
    # statement therefore overrides an older one, but never
    # overwrites a real value with a blank.
    #
    # groupby preserves within-group order, so after this sort
    # dropna().iloc[0] IS "most recent non-null".
    #
    # report_date alone does not decide it. Every one of the 24
    # clients whose name changed across a rebuild had TWO OR MORE
    # spellings sharing the SAME latest report_date --
    #
    #   DHANPAL M SHAH      vs  DHANPAL MANUBHAI SHAH
    #   Lalbhai Patel       vs  Lalbhai Shankerdas Patel
    #   KASHYAP PATEL       vs  KASHYAP BHOGIBHAI PATEL
    #
    # -- so the winner fell to whatever order the rows happened
    # to arrive in. Reproducible, but not chosen: half the time
    # it dropped the middle name.
    #
    # The tie therefore breaks on completeness: most name tokens,
    # then longest, then alphabetical so the result is total.
    # "DHANPAL MANUBHAI SHAH" beats "DHANPAL M SHAH" on token
    # length, and a fuller name is never worse than the initial
    # it abbreviates.
    with_pan_sorted = gold_df[has_pan].copy()

    _name = (
        with_pan_sorted["full_name"]
        .fillna("")
        .astype(str)
    )

    with_pan_sorted["_name_tokens"] = (
        _name.str.split().str.len()
    )

    with_pan_sorted["_name_len"] = _name.str.len()

    with_pan = (
        with_pan_sorted
        .sort_values(
            [
                "report_date",
                "_name_tokens",
                "_name_len",
                "full_name",
                "_tiebreak_folio_no",
                "_tiebreak_product_code"
            ],
            ascending=[False, False, False, True, True, True],
            na_position="last",
            kind="mergesort"
        )
        .drop(columns=["_name_tokens", "_name_len"])
        .groupby("pan", sort=False, as_index=False)
        .agg(
            lambda column: (
                column.dropna().iloc[0]
                if column.notna().any()
                else pd.NA
            )
        )
    )

    # The same person arrives under several spellings:
    #   "Agam Singh Saini"  / "AGAM SINGH SAINI"   (case)
    #   "Dhyani N Patel"    / "DHYANI PATEL"       (middle initial)
    #
    # client_mapping normalises exactly this, so its helpers are
    # reused rather than reimplemented here -- one matcher, one
    # place to fix.
    from client_mapping import norm_name

    without_pan = gold_df[~has_pan].copy()

    # Inside one guardian's family the first name identifies the
    # child: it survives middle-name drift while still keeping
    # twins apart (NIVAA / NIVAAN).
    without_pan["_name_key"] = (
        norm_name(without_pan["full_name"])
        .fillna("")
        .str.split(" ")
        .str[0]
    )

    # With no guardian PAN there is no family to disambiguate
    # within, so the whole name is the key.
    no_guardian = without_pan["guardian_pan"].isna()

    without_pan.loc[no_guardian, "_name_key"] = (
        norm_name(without_pan.loc[no_guardian, "full_name"])
        .fillna("")
    )

    # Same "newest statement wins" rule as the PAN branch above,
    # so the two populations resolve consistently. keep="last"
    # on unsorted rows picked an arbitrary folio; sorting newest
    # first and keeping "first" makes the winner deterministic
    # and the most recent.
    #
    # Fields are NOT coalesced here as they are for PAN'd
    # clients: date_of_birth is part of the identity key, so two
    # folios that disagree on it are two different keys and
    # never meet in the same group to be merged.
    without_pan = (
        without_pan
        .sort_values(
            [
                "report_date",
                "_tiebreak_folio_no",
                "_tiebreak_product_code"
            ],
            ascending=[False, True, True],
            na_position="last",
            kind="mergesort"
        )
        .drop_duplicates(
            subset=[
                "guardian_pan",
                "_name_key",
                "date_of_birth"
            ],
            keep="first"
        )
        .drop(columns=["_name_key"])
        .copy()
    )

    # A PAN-less row is NEVER folded into a client who has a PAN.
    #
    # This used to match the two by name, mirroring
    # client_mapping's NAME_ATTACH rule. Both have been removed:
    # a different PAN means a different person, and a folio
    # carrying no PAN is not evidence that it belongs to someone
    # who has one. Names here differ by one letter between
    # different people -- SUREEL BHATT (AQEPB6066F, 1977) and
    # SALEEL BHATT (AAYPB0139M, 1971) -- and a null DOB cannot
    # conflict, so the DOB veto did not guard it.
    #
    # A PAN-less row therefore stays its own client, keyed on
    # guardian PAN + name + date of birth above.

    gold_df = pd.concat(
        [with_pan, without_pan],
        ignore_index=True
    )

    print(
        "Duplicate rows removed:",
        before - len(gold_df)
    )

    print(
        "  keyed on PAN                   :",
        len(with_pan)
    )

    print(
        "  keyed on guardian PAN + name   :",
        len(without_pan)
    )

    print(
        "Unique client rows:",
        len(gold_df)
    )

    if gold_df.empty:

        print(
            "No valid client records to load."
        )

        return True

    # ========================================================
    # UPDATE EXISTING CLIENTS FIRST
    # ========================================================

    # Ahead of everything else, because it decides WHICH client a
    # row is. A PAN arriving on a folio that already belongs to a
    # PAN-less client is that client acquiring a PAN, not a new
    # person -- so it is written onto them here, before the PAN is
    # looked up below. Once adopted the PAN is present in
    # gold.clients, so the existing-PAN filter excludes the row
    # from the INSERT and refresh_derived_demographics() picks it
    # up by its new PAN. Get this order wrong and the client is
    # duplicated exactly as before.
    adopt_pan_via_folio()

    update_existing_client_attributes(
        gold_df
    )

    # Corrections to the four pipeline-derived columns cannot
    # travel through the INSERT below (existing PANs are filtered
    # out of it) nor through the call above (fill-NULL only, and
    # a different set of columns), so they get their own pass.
    refresh_derived_demographics(
        gold_df
    )

    # report_date has now done both of its jobs -- ordering the
    # de-duplication above and travelling into the refresh -- and
    # gold.clients has no such column. The INSERT path validates
    # its columns against the table and RAISES on an unknown one
    # rather than ignoring it, so the helper is dropped here.
    gold_df = gold_df.drop(
        columns=["report_date"],
        errors="ignore"
    )

    # _tiebreak_folio_no is NOT dropped here: the existing-client
    # check below needs the folio to recognise a client whose
    # name has drifted. It goes just before the INSERT instead.

    # ========================================================
    # GET EXISTING PANS
    # ========================================================

    print()
    print("Checking existing clients in gold.clients...")

    existing = safe_read(
        """
        SELECT
            pan,
            guardian_pan,
            full_name,
            date_of_birth
        FROM gold.clients

        -- A superseded row is no longer anybody: its identity
        -- moved to the winner of the merge. Left visible here it
        -- would keep absorbing arrivals that belong to the
        -- survivor.
        WHERE superseded_by IS NULL
        """
    )

    # safe_read() answers a failed query with an EMPTY frame, and
    # an empty frame here reads as "gold.clients is empty" -- so
    # every client is treated as new and re-inserted. Running this
    # code against a database that has not had the merge migration
    # applied did exactly that: the superseded_by predicate raised
    # UndefinedColumn, the error was swallowed, and 620 clients
    # became 632.
    #
    # An empty answer is only believable if the table really is
    # empty. Anything else is a failed read, and the safe response
    # to a failed identity lookup is to load nobody.
    if existing.empty:

        actual = safe_read(
            "SELECT count(*) AS n FROM gold.clients"
        )

        if actual.empty or int(actual.iloc[0]["n"]) > 0:

            print()
            print("ABORTING: could not read existing clients.")
            print(
                "gold.clients is not empty, so inserting would "
                "duplicate every client in it."
            )
            print(
                "Most likely the SQL migrations have not been "
                "applied -- run sql_scripts/"
                "fix_panless_uniqueness_indexes.sql then "
                "sql_scripts/add_client_merge.sql."
            )

            return False

    if not existing.empty:

        existing_pans = set(
            clean_pan(existing["pan"]).dropna().tolist()
        )

        # PAN-less clients are recognised by the same key they
        # are de-duplicated on, so a re-run does not insert them
        # a second time.
        def person_key(guardian, name, dob):

            """The SAME identity key the in-batch de-duplication uses.

            These two keys must agree. This one used to compare the
            whole name verbatim while the batch key normalises it and,
            inside one guardian's family, keys on the FIRST name only.
            A person already stored under one spelling was therefore
            not recognised when they arrived under another, and was
            inserted a second time -- "DHYANI PATEL" and "Dhyani N
            Patel", one child with guardian AIDPP2849R born
            2011-10-31, became two clients.

            Which spelling reaches this function is not stable either:
            it is whichever folio wins de-duplication, so the bug lay
            dormant until the ordering changed.
            """

            guardian_key = (
                None if pd.isna(guardian)
                else str(guardian).upper()
            )

            normalised = (
                norm_name(pd.Series([name]))
                .fillna("")
                .iloc[0]
            )

            # Within a family the first name identifies the child and
            # survives middle-name drift; with no guardian there is no
            # family to disambiguate within, so the whole name is key.
            name_key = (
                normalised.split(" ")[0]
                if guardian_key
                else normalised
            )

            return (
                guardian_key,
                name_key or None,
                None if pd.isna(dob) else str(dob),
            )

        existing_people = {
            person_key(row[0], row[1], row[2])
            for row in existing.loc[
                existing["pan"].isna(),
                ["guardian_pan", "full_name", "date_of_birth"]
            ].to_numpy()
        }

        # A PAN-less row is matched ONLY against other PAN-less
        # clients, on guardian PAN + name + date of birth.
        #
        # It used to also be name-matched against clients who
        # HAVE a PAN, so that a folio of theirs missing its PAN
        # would not create a second row. That has been removed
        # along with client_mapping's NAME_ATTACH rule: a
        # different PAN means a different person, and a name is
        # not an identifier -- SUREEL BHATT (AQEPB6066F, 1977)
        # and SALEEL BHATT (AAYPB0139M, 1971) are two men whose
        # names differ by one letter. A null date of birth
        # cannot conflict, so the DOB veto never guarded it.
        before = len(gold_df)

        def already_loaded(row):

            if pd.notna(row["pan"]):

                return row["pan"] in existing_pans

            return person_key(
                row["guardian_pan"],
                row["full_name"],
                row["date_of_birth"],
            ) in existing_people

        # ----------------------------------------------------
        # THE FOLIO OUTRANKS THE NAME
        #
        # person_key above is the loader's best guess at "is
        # this the same child?", and it is made of the name. So
        # it breaks exactly when the name changes: a first name
        # respelled, or a registry sending "Shah Heer Pritipal"
        # where it used to send "Heer Pritipal Shah", produces a
        # key that matches nothing and a second client for a
        # person already here.
        #
        # A folio does not change when a name is rewritten. If
        # the folio this row came in on already belongs to
        # somebody, then this row is that somebody, whatever it
        # calls them -- the same reasoning that lets an arriving
        # PAN find its owner in adopt_pan_via_folio().
        #
        # Checked BEFORE the name key, so the name never gets
        # the chance to be wrong.
        #
        # EVERY live client counts here, not only the PAN-less
        # ones. The rule elsewhere that "a PAN-less row is never
        # folded into a client who has a PAN" guards against
        # matching them BY NAME, where SUREEL and SALEEL BHATT
        # are one letter apart. A folio is not a name: folio
        # 25883720/56 is the same folio whoever it is addressed
        # to, so a statement arriving on it without a PAN is its
        # owner's statement, not a new person's.
        #
        # Restricting this to PAN-less owners also silently
        # undid every merge -- the loser's folio now belongs to
        # a winner who HAS a PAN, so the arriving row matched
        # nothing and the duplicate was recreated on the next
        # run. And a folio that simply forgot its PAN is how
        # all three of those duplicates were born.
        # ----------------------------------------------------
        folio_owned = set()

        if "_tiebreak_folio_no" in gold_df.columns:

            owned = safe_read(
                """
                SELECT cf.source, cf.folio_no
                FROM gold.client_folio cf
                JOIN gold.clients c ON c.id = cf.client_id
                WHERE c.superseded_by IS NULL
                """
            )

            folio_owned = {
                (str(row[0]).upper(), str(row[1]))
                for row in owned.to_numpy()
            }

        def folio_key(row):

            folio = row.get("_tiebreak_folio_no")

            if folio is None or pd.isna(folio):

                return None

            # gold.normalise_folio() strips the ".0" a numeric
            # folio picks up on its way through pandas.
            folio = re.sub(r"\.0$", "", str(folio).strip())

            source = row.get("source")

            if source is None or pd.isna(source):

                return None

            return (str(source).upper(), folio)

        def already_loaded_by_folio(row):

            if pd.notna(row["pan"]):

                return False

            key = folio_key(row)

            return key is not None and key in folio_owned

        by_folio = gold_df.apply(already_loaded_by_folio, axis=1)

        if by_folio.any():

            print(
                "PAN-less rows matched to an existing client by "
                "FOLIO rather than by name:",
                int(by_folio.sum())
            )

        gold_df = gold_df[
            ~(by_folio | gold_df.apply(already_loaded, axis=1))
        ].copy()

        print(
            "Existing client rows skipped for INSERT:",
            before - len(gold_df)
        )

    print(
        "Rows actually going to INSERT:",
        len(gold_df)
    )

    # ========================================================
    # NOTHING NEW
    # ========================================================

    if gold_df.empty:

        count_df = safe_read(
            """
            SELECT COUNT(*) AS total_clients
            FROM gold.clients
            WHERE superseded_by IS NULL
            """
        )

        if not count_df.empty:

            print(
                "Current Gold Clients:",
                int(
                    count_df.iloc[0]["total_clients"]
                )
            )

        return True

    # The folio helpers have now done their work -- ordering the
    # de-duplication and identifying drifted-name clients above.
    # gold.clients has no such columns and the INSERT validator
    # RAISES on an unknown one, so they go here.
    gold_df = gold_df.drop(
        columns=["_tiebreak_folio_no", "_tiebreak_product_code"],
        errors="ignore"
    )

    # ========================================================
    # CHECK TABLE COLUMNS
    # ========================================================

    table_columns = safe_read(
        """
        SELECT
            column_name
        FROM information_schema.columns
        WHERE table_schema = 'gold'
          AND table_name = 'clients'
        ORDER BY ordinal_position
        """
    )

    if table_columns.empty:

        print(
            "ERROR: gold.clients table was not found."
        )

        return False

    database_columns = set(
        table_columns["column_name"]
    )

    missing_database_columns = [
        col
        for col in gold_df.columns
        if col not in database_columns
    ]

    if missing_database_columns:

        print()
        print(
            "ERROR: These columns exist in the DataFrame "
            "but not in gold.clients:"
        )

        for col in missing_database_columns:

            print(
                " -",
                col
            )

        return False

    # ========================================================
    # KEEP DATABASE COLUMNS
    # ========================================================

    gold_df = gold_df[
        [
            col
            for col in gold_df.columns
            if col in database_columns
        ]
    ].copy()

    # ========================================================
    # INSERT
    # ========================================================

    print()
    print("Starting database insert...")

    inserted_rows = 0

    try:

        from utils.db import upsert_dataframe

        # This needs uq_gold_clients_pan to be a NULLS DISTINCT
        # unique constraint on pan, so that real PANs stay unique
        # while any number of PAN-less clients is allowed.
        #
        # Built as NULLS NOT DISTINCT it permits exactly ONE
        # PAN-less client in the entire table and every minor
        # after the first is rejected -- a minor has no PAN of
        # their own, so there is no code-side way around it:
        #   ALTER TABLE gold.clients
        #     DROP CONSTRAINT uq_gold_clients_pan;
        #   ALTER TABLE gold.clients
        #     ADD CONSTRAINT uq_gold_clients_pan UNIQUE (pan);
        #
        # A PAN-less row therefore never matches ON CONFLICT and
        # is simply inserted -- which is correct, because the
        # ones already in the table were filtered out above on
        # guardian_pan + name + date_of_birth. The partial index
        # uq_clients_panless_identity backstops that check.
        # Rows WITH a PAN go through the upsert, keyed on pan.
        #
        # Rows WITHOUT one cannot: upsert_dataframe de-duplicates
        # the batch with PARTITION BY <conflict column>, and
        # every PAN-less row has pan = NULL, so all of them fall
        # into a single partition and 35 of 36 are silently
        # discarded. They are plain-inserted instead, which is
        # safe because the ones already in the table were
        # filtered out above on guardian_pan + name + dob, and
        # uq_clients_panless_identity backstops that check.
        with_pan = gold_df[gold_df["pan"].notna()]

        without_pan = gold_df[gold_df["pan"].isna()]

        inserted_rows = 0

        if not with_pan.empty:

            upsert_result = upsert_dataframe(
                with_pan,
                schema="gold",
                table="clients",
                conflict_columns=["pan"],
                chunksize=100,
                updated_at_column=None,
            )

            inserted_rows += upsert_result["inserted"]

        if not without_pan.empty:

            # Row at a time, each in its own transaction.
            #
            # Sent as one batch, a single rejected row aborts the
            # whole statement AND the surrounding transaction, so
            # nothing at all reaches gold.clients and the ETL
            # returns False -- which is how a full run could
            # print every statistic and still leave the table
            # empty. One unloadable row must not cost the other
            # 27, nor the client_address and client_bank loads
            # that depend on this table being populated.
            blocked = []

            for _, one in without_pan.iterrows():

                try:

                    with engine.begin() as connection:

                        pd.DataFrame([one]).to_sql(
                            "clients",
                            connection,
                            schema="gold",
                            if_exists="append",
                            index=False,
                        )

                    inserted_rows += 1

                except Exception as row_error:

                    blocked.append(
                        (one.get("full_name"), row_error)
                    )

            print(
                "PAN-less clients inserted:",
                len(without_pan) - len(blocked)
            )

            if blocked:

                print(
                    "PAN-less clients REJECTED:",
                    len(blocked)
                )

                print(
                    "  first reason:",
                    str(blocked[0][1])[:300]
                )

        print(
            f"Inserted {inserted_rows} / "
            f"{len(gold_df)} rows"
        )

        # ====================================================
        # VERIFY
        # ====================================================

        print()
        print("=" * 80)
        print("VERIFYING GOLD.CLIENTS")
        print("=" * 80)

        count_df = safe_read(
            """
            SELECT
                COUNT(*) AS total_clients
            FROM gold.clients
            WHERE superseded_by IS NULL
            """
        )

        if not count_df.empty:

            print(
                "Total rows in gold.clients:",
                int(
                    count_df.iloc[0]["total_clients"]
                )
            )

        # ====================================================
        # CKYC / DP ID COUNTS
        # ====================================================

        validation_df = safe_read(
            """
            SELECT

                COUNT(*) AS total_clients,

                COUNT(ckyc_no) AS clients_with_ckyc,

                COUNT(dp_id) AS clients_with_dp_id

            FROM gold.clients
            WHERE superseded_by IS NULL
            """
        )

        if not validation_df.empty:

            print()
            print(
                "Clients with CKYC:",
                int(
                    validation_df.iloc[0][
                        "clients_with_ckyc"
                    ]
                )
            )

            print(
                "Clients with DP ID:",
                int(
                    validation_df.iloc[0][
                        "clients_with_dp_id"
                    ]
                )
            )

        # ====================================================
        # PREVIEW
        # ====================================================

        database_preview = safe_read(
            """
            SELECT
                pan,
                full_name,
                ckyc_no,
                dp_id,
                arn,
                sub_arn,
                email,
                investor_type,
                tax_status,
                kyc_status,
                arn_id,
                onboarded_at,
                source,
                created_at
            FROM gold.clients
            WHERE ckyc_no IS NOT NULL
               OR dp_id IS NOT NULL
            ORDER BY created_at DESC
            LIMIT 10
            """
        )

        print()
        print(
            "Sample clients having CKYC / DP ID:"
        )

        if database_preview.empty:

            print(
                "WARNING: No CKYC / DP ID values found "
                "in gold.clients."
            )

        else:

            print(
                database_preview.to_string(
                    index=False
                )
            )

        print()
        print("=" * 80)
        print("GOLD.CLIENTS DATABASE INSERT SUCCESSFUL")
        print("=" * 80)

        return True

    except Exception as e:

        print()
        print("=" * 80)
        print("GOLD.CLIENTS DATABASE INSERT FAILED")
        print("=" * 80)

        print(
            "Error type:",
            type(e).__name__
        )

        print(
            "Database error:",
            str(e)[:3000]
        )

        print(
            "Rows successfully inserted before failure:",
            inserted_rows
        )

        print(
            "Rows remaining:",
            len(gold_df) - inserted_rows
        )

        traceback.print_exc()

        return False


# ============================================================
# MAIN FUNCTION
# ============================================================

def main():

    print("=" * 80)
    print("STARTING GOLD CLIENTS ETL")
    print("=" * 80)

    try:

        # ====================================================
        # EXTRACT
        # ====================================================

        clients_df = extract_clients()

        if clients_df.empty:

            print(
                "No client data found."
            )

            return False

        # ====================================================
        # TRANSFORM
        # ====================================================

        gold_clients = transform_clients(
            clients_df
        )

        if gold_clients.empty:

            print(
                "No Gold client records generated."
            )

            return False

        # ====================================================
        # LOAD
        # ====================================================

        status = load_clients(
            gold_clients
        )

        # After the load, so folios belonging to clients created
        # on this very run are linked too. Runs on every path --
        # a run that inserts nobody can still bring new folios
        # for clients who are already here.
        link_client_folios()

        # After linking, because the sweep reads folio counts to
        # decide which of two rows survives, and because a merge
        # repoints folios -- both want the map current first.
        merge_duplicate_clients()

        # ====================================================
        # FINAL STATUS
        # ====================================================

        if status:

            print()
            print("=" * 80)
            print(
                "GOLD CLIENTS ETL COMPLETED SUCCESSFULLY"
            )
            print("=" * 80)

            return True

        else:

            print()
            print("=" * 80)
            print(
                "GOLD CLIENTS ETL FAILED"
            )
            print("=" * 80)

            return False

    except Exception as e:

        print()
        print("=" * 80)
        print("GOLD CLIENTS ETL ERROR")
        print("=" * 80)

        print(
            "Error type:",
            type(e).__name__
        )

        print(
            "Error:",
            str(e)[:3000]
        )

        traceback.print_exc()

        return False


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    main()
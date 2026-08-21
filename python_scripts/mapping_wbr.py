# =========================================================
# WBR REPORT COLUMN MAPPINGS
#
# Kept out of mapping.py on purpose.
#
# mapping.py owns the three transactional feeds
# (transaction master / investor master / SIP master).
# A WBR report must never force a change there, so every
# WBR mapping lives in this file instead.
#
# Adding a new RTA to an existing WBR report needs two
# edits in this file and nothing anywhere else:
#
#   1. a row in BROKERAGE_FILE_PATTERNS
#   2. the RTA's header spellings appended to the alias
#      lists in BROKERAGE_SUMMARY_MAPPING
#      (or an entry in BROKERAGE_SOURCE_OVERRIDES when the
#       RTA reuses a header name with a different meaning)
# =========================================================


# =========================================================
# FILE REGISTRY
#
# The single place that decides which uploaded file is
# which brokerage report. raw_ingestion.py and app.py both
# read this list, so their filename rules cannot drift
# apart.
#
#   match       : lower-cased substring tested against the
#                 uploaded file name
#   source      : RTA stamped on every row of that file
#   report_type : distinguishes reports that share one
#                 table (WBR36 and WBR36H repeat the same
#                 product codes)
#
# ORDER MATTERS - the first match wins, so the more
# specific pattern must come first ("wbr36h" contains
# "wbr36").
# =========================================================

BROKERAGE_FILE_PATTERNS = [

    # ---------- CAMS ----------

    {
        "match": "wbr36h",
        "source": "CAMS",
        "report_type": "WBR36H"
    },

    {
        "match": "wbr36",
        "source": "CAMS",
        "report_type": "WBR36"
    },

    # ---------- KFINTECH ----------
    #
    # Placeholder. Fill in the real KFIN file identifier
    # (the way transactions use "mfsd201") when the KFIN
    # brokerage report is available, e.g.
    #
    # {
    #     "match": "mfsd???",
    #     "source": "KFIN",
    #     "report_type": "BROKERAGE"
    # },

]


def identify_brokerage_file(file_name):
    """
    Return the BROKERAGE_FILE_PATTERNS entry matching
    file_name, or None when the file is not a brokerage
    report.
    """

    name = str(file_name).lower()

    for pattern in BROKERAGE_FILE_PATTERNS:

        if pattern["match"] in name:

            return pattern

    return None


# =========================================================
# BROKERAGE SUMMARY BY SCHEME
#
# Target: bronze.brokerage_summary
#
# Aliases are matched AFTER the incoming header has been
# lower-cased and de-spaced (space, "-", "/" become "_",
# "#" is dropped), so every alias below is written in that
# form. Mixed-case aliases can never match.
#
# Resolution rule: first alias present in the file wins.
#
# CAMS WBR36 / WBR36H header row:
#
#   product_code, product_name, upfront, afe,
#   trailer_fee, trxn_charges, clawback, incentives
# =========================================================

BROKERAGE_SUMMARY_MAPPING = {

    # =====================================================
    # SYSTEM
    #
    # Both are stamped by the loader from the file registry,
    # not read from the file. Listed so the target column
    # set is visible in one place.
    # =====================================================

    "source": ["source"],

    "report_type": ["report_type"],

    # =====================================================
    # SCHEME IDENTIFICATION
    #
    # CAMS: product_code = RTA product code (D104, FTI970)
    #       which is what bronze.scheme_mapping stores as
    #       rta_scheme_code, so scheme_id resolves directly.
    # =====================================================

    "product_code": [
        "product_code",
        "prodcode",
        "product",
        "scheme_code",
        "sch_code",
        "fund_code"
    ],

    "product_name": [
        "product_name",
        "scheme_name",
        "sch_name",
        "scheme",
        "funddesc",
        "fund_description"
    ],

    # =====================================================
    # BROKERAGE COMPONENTS
    # =====================================================

    "upfront": [
        "upfront",
        "upfront_brokerage",
        "upfront_amt",
        "upfront_amount"
    ],

    "afe": [
        "afe",
        "additional_expense",
        "afe_amount"
    ],

    "trailer_fee": [
        "trailer_fee",
        "trail",
        "trail_fee",
        "trail_brokerage",
        "trailer_amount"
    ],

    "trxn_charges": [
        "trxn_charges",
        "trxn_charge",
        "trcharges",
        "transaction_charges"
    ],

    "clawback": [
        "clawback",
        "claw_back",
        "clawback_amount"
    ],

    "incentives": [
        "incentives",
        "incentive",
        "incentive_amount"
    ],

    # =====================================================
    # REPORTING PERIOD
    #
    # CAMS WBR36 / WBR36H carry no period column at all -
    # these stay NULL for CAMS. Aliases are in place for
    # RTAs that do report a period.
    # =====================================================

    "report_from_date": [
        "report_from_date",
        "rep_from_date",
        "from_date",
        "period_from"
    ],

    "report_to_date": [
        "report_to_date",
        "rep_to_date",
        "to_date",
        "period_to"
    ],

    "rep_date": [
        "rep_date",
        "report_date",
        "as_on_date"
    ],

    # =====================================================
    # OPTIONAL CONTEXT
    # =====================================================

    "amc_code": [
        "amc_code",
        "fund",
        "td_fund"
    ],

    "broker_code": [
        "broker_code",
        "brokcode",
        "brok_dlr_code",
        "arn_code",
        "arn"
    ],

    "sub_broker_code": [
        "sub_broker_code",
        "subbrok",
        "subbrokcod",
        "sub_brk_arn",
        "subarncode"
    ],

    # =====================================================
    # NOT FROM FILE
    # =====================================================

    "flag": [],
    "created_at": [],
    "updated_at": []

}


# =========================================================
# PER-SOURCE ALIAS OVERRIDES
#
# Use only when an RTA reuses a header name that already
# means something else in the shared alias list. The value
# replaces the alias list for that target column and that
# source, exactly like the SIP loader's scheme_code /
# scheme_name special case - but declared here instead of
# hard-coded in the loader.
#
# Example, once the KFIN header names are known:
#
# BROKERAGE_SOURCE_OVERRIDES = {
#     "KFIN": {
#         "product_code": ["fund_code"],
#         "product_name": ["fund_description"]
#     }
# }
# =========================================================

BROKERAGE_SOURCE_OVERRIDES = {}


# =========================================================
# COLUMN ROLES
#
# Shared by the bronze loader, the silver transform and the
# gold ETL so the three layers cannot disagree about what
# a column is.
# =========================================================

# Money columns - TEXT in bronze, NUMERIC from silver on.
BROKERAGE_AMOUNT_COLUMNS = [
    "upfront",
    "afe",
    "trailer_fee",
    "trxn_charges",
    "clawback",
    "incentives"
]

BROKERAGE_DATE_COLUMNS = [
    "report_from_date",
    "report_to_date",
    "rep_date"
]

# Columns that must never pick up Excel's trailing ".0"
BROKERAGE_IDENTIFIER_COLUMNS = [
    "product_code",
    "amc_code",
    "broker_code",
    "sub_broker_code"
]

# Business key of one brokerage row.
BROKERAGE_KEY_COLUMNS = [
    "source",
    "report_type",
    "product_code",
    "report_from_date",
    "report_to_date"
]

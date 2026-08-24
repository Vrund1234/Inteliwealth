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
        "report_type": "WBR36H",
        "report_key": "BROKERAGE_SUMMARY"
    },

    {
        "match": "wbr36",
        "source": "CAMS",
        "report_type": "WBR36",
        "report_key": "BROKERAGE_SUMMARY"
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


# =========================================================
# KYC STATUS OF INVESTOR
#
# Target: bronze.kyc_status
#
# Source reports
#   CAMS WBR56  - KYC status of investor
#   KFINTECH    - equivalent report, added later
#
# Grain of the FILE is one row per (amc_code, folio). The
# KYC payload inside that row repeats per holder:
#
#   first holder  : inv_name    / tax_no         / fh_kyc
#                   / fh_kyc_desc  / fh_g_aadharlink
#   guardian      : guardian    / guardian_panno / gu_kyc
#                   / gu_kyc_desc  / fh_g_aadharlink
#   joint 1       : jname1      / jointpan1      / jh1_kyc
#                   / jh1_kyc_desc / jh1_aadharlink
#   joint 2       : jname2      / jointpan2      / jh2_kyc
#                   / jh2_kyc_desc / jh2_aadharlink
#
# Bronze and Silver keep the file's own wide shape, so the
# raw layers stay a faithful copy of the report. The
# holders are unpivoted to one row per holder in Gold
# only - same treatment gold.folio_nominees gives the
# three-nominees-per-folio layout.
#
# Note there is no separate guardian aadhaar column in the
# report: fh_g_aadharlink covers first holder AND guardian.
#
# Aliases are matched AFTER the incoming header has been
# lower-cased and de-spaced, exactly like
# BROKERAGE_SUMMARY_MAPPING.
# =========================================================

KYC_STATUS_MAPPING = {

    # =====================================================
    # SYSTEM
    #
    # Stamped by the loader from the file registry.
    # =====================================================

    "source": ["source"],

    "report_type": ["report_type"],

    # =====================================================
    # BROKER / DISTRIBUTOR
    # =====================================================

    "brok_dlr_code": [
        "brok_dlr_code",
        "brokcode",
        "broker_code",
        "arn_code",
        "arn"
    ],

    "brok_name": [
        "brok_name",
        "broker_name",
        "distributor_name"
    ],

    # =====================================================
    # FOLIO IDENTIFICATION
    # =====================================================

    "amc_code": [
        "amc_code",
        "fund",
        "td_fund"
    ],

    "folio": [
        "folio",
        "folio_no",
        "folio_number",
        "acno"
    ],

    # =====================================================
    # FIRST HOLDER
    # =====================================================

    "inv_name": [
        "inv_name",
        "invname",
        "investor_name",
        "first_holder_name",
        "name"
    ],

    "tax_no": [
        "tax_no",
        "pan",
        "pan_no",
        "pan_number",
        "inv_pan"
    ],

    # =====================================================
    # JOINT HOLDERS
    # =====================================================

    "jname1": [
        "jname1",
        "joint1_name",
        "jointname1",
        "jt_name1"
    ],

    "jointpan1": [
        "jointpan1",
        "joint1_pan",
        "jt_pan1",
        "pan2"
    ],

    "jname2": [
        "jname2",
        "joint2_name",
        "jointname2",
        "jt_name2"
    ],

    "jointpan2": [
        "jointpan2",
        "joint2_pan",
        "jt_pan2",
        "pan3"
    ],

    # =====================================================
    # GUARDIAN
    # =====================================================

    "guardian": [
        "guardian",
        "guardian_name",
        "guard_name"
    ],

    "guardian_panno": [
        "guardian_panno",
        "guardpanno",
        "guardian_pan",
        "guard_pan"
    ],

    # =====================================================
    # KYC STATUS PER HOLDER
    #
    # Short status ("KYC OK" / "KYC Not Verified") and the
    # long description ("KYC VALIDATED" / "KYC REGISTERED
    # - New KYC") are two different columns in the report
    # and are kept apart here.
    # =====================================================

    "fh_kyc": [
        "fh_kyc",
        "first_holder_kyc",
        "kyc_status"
    ],

    "gu_kyc": [
        "gu_kyc",
        "guardian_kyc"
    ],

    "jh1_kyc": [
        "jh1_kyc",
        "joint1_kyc"
    ],

    "jh2_kyc": [
        "jh2_kyc",
        "joint2_kyc"
    ],

    "fh_kyc_desc": [
        "fh_kyc_desc",
        "first_holder_kyc_desc",
        "kyc_desc"
    ],

    "gu_kyc_desc": [
        "gu_kyc_desc",
        "guardian_kyc_desc"
    ],

    "jh1_kyc_desc": [
        "jh1_kyc_desc",
        "joint1_kyc_desc"
    ],

    "jh2_kyc_desc": [
        "jh2_kyc_desc",
        "joint2_kyc_desc"
    ],

    # =====================================================
    # AADHAAR LINK STATUS
    #
    # fh_g_aadharlink is shared by the first holder and the
    # guardian - the report has no separate guardian column.
    # =====================================================

    "fh_g_aadharlink": [
        "fh_g_aadharlink",
        "fh_aadharlink",
        "aadhar_link",
        "aadhaar_link"
    ],

    "jh1_aadharlink": [
        "jh1_aadharlink",
        "joint1_aadharlink"
    ],

    "jh2_aadharlink": [
        "jh2_aadharlink",
        "joint2_aadharlink"
    ],

    # =====================================================
    # ADDRESS
    # =====================================================

    "address1": [
        "address1",
        "add1",
        "addr1",
        "address_line1"
    ],

    "address2": [
        "address2",
        "add2",
        "addr2",
        "address_line2"
    ],

    "address3": [
        "address3",
        "add3",
        "addr3",
        "address_line3"
    ],

    "city": [
        "city",
        "town"
    ],

    "pincode": [
        "pincode",
        "pin",
        "pin_code",
        "zip"
    ],

    "state": [
        "state",
        "statename"
    ],

    "country": [
        "country",
        "countryname"
    ],

    "location": [
        "location",
        "branch",
        "servicing_branch"
    ],

    # =====================================================
    # CONTACT
    # =====================================================

    "phone_res": [
        "phone_res",
        "phone_off_res",
        "res_phone",
        "phoneres"
    ],

    "phone_off": [
        "phone_off",
        "off_phone",
        "phoneoff"
    ],

    "mobile_no": [
        "mobile_no",
        "mobile",
        "mobileno",
        "cell_no"
    ],

    "email": [
        "email",
        "email_id",
        "emailid"
    ],

    "fax_res": [
        "fax_res",
        "faxres"
    ],

    "fax_off": [
        "fax_off",
        "faxoff"
    ],

    # =====================================================
    # REPORTING PERIOD
    #
    # Unlike WBR36/WBR36H, WBR56 does carry a period.
    # =====================================================

    "rep_from_date": [
        "rep_from_date",
        "report_from_date",
        "from_date",
        "period_from"
    ],

    "rep_to_date": [
        "rep_to_date",
        "report_to_date",
        "to_date",
        "period_to"
    ],

    "rep_date": [
        "rep_date",
        "report_date",
        "as_on_date"
    ],

    # =====================================================
    # NOT FROM FILE
    # =====================================================

    "flag": [],
    "created_at": [],
    "updated_at": []

}


KYC_STATUS_SOURCE_OVERRIDES = {}


# WBR56 carries no money column.
KYC_STATUS_AMOUNT_COLUMNS = []


KYC_STATUS_DATE_COLUMNS = [
    "rep_from_date",
    "rep_to_date",
    "rep_date"
]


# Columns that must never pick up Excel's trailing ".0".
# Phone / mobile / pincode are here for that reason, not
# because they identify anything.
KYC_STATUS_IDENTIFIER_COLUMNS = [
    "brok_dlr_code",
    "amc_code",
    "folio",
    "tax_no",
    "jointpan1",
    "jointpan2",
    "guardian_panno",
    "pincode",
    "phone_res",
    "phone_off",
    "mobile_no",
    "fax_res",
    "fax_off"
]


# Business key of one KYC row as the FILE reports it.
KYC_STATUS_KEY_COLUMNS = [
    "source",
    "report_type",
    "amc_code",
    "folio"
]


# =========================================================
# INVALID EUIN REPORT
#
# Target: bronze.invalid_euin
#
# Source reports
#   CAMS WBR68  - invalid EUIN report
#   KFINTECH    - equivalent report, added later
#
# One row per transaction. trxn_no is the RTA transaction
# number, which is what gold.transactions stores as
# rta_txn_no, so a row joins straight back to the
# transaction it faults.
#
# sch_code is the RTA scheme code, i.e. exactly what
# bronze.scheme_mapping.rta_scheme_code holds, so scheme_id
# resolves through the existing lookup with no
# report-specific rule.
# =========================================================

INVALID_EUIN_MAPPING = {

    # =====================================================
    # SYSTEM
    # =====================================================

    "source": ["source"],

    "report_type": ["report_type"],

    # =====================================================
    # TRANSACTION IDENTIFICATION
    # =====================================================

    "trxn_no": [
        "trxn_no",
        "txn_no",
        "trxnno",
        "transaction_no",
        "rta_txn_no"
    ],

    "usertxn_no": [
        "usertxn_no",
        "user_trxn_no",
        "usertrxnno"
    ],

    "auto_trxn_no": [
        "auto_trxn_no",
        "autotrxnno"
    ],

    "appln_no": [
        "appln_no",
        "application_no",
        "applnno",
        "app_no"
    ],

    # =====================================================
    # SCHEME
    # =====================================================

    "sch_code": [
        "sch_code",
        "scheme_code",
        "product_code",
        "prodcode",
        "fund_code"
    ],

    "sch_name": [
        "sch_name",
        "scheme_name",
        "product_name",
        "funddesc",
        "fund_description"
    ],

    "amc_code": [
        "amc_code",
        "fund",
        "td_fund"
    ],

    # =====================================================
    # FOLIO
    #
    # The report carries several folio spellings. folio_no
    # is the one used everywhere else in the pipeline; the
    # rest are kept for traceability.
    # =====================================================

    "folio_no": [
        "folio_no",
        "folio_number",
        "acno"
    ],

    "folio": [
        "folio"
    ],

    "alt_folio": [
        "alt_folio",
        "alternate_folio"
    ],

    "folio_old": [
        "folio_old",
        "old_folio"
    ],

    "scheme_folio_number": [
        "scheme_folio_number",
        "sch_folio_no"
    ],

    # =====================================================
    # INVESTOR
    # =====================================================

    "inv_name": [
        "inv_name",
        "invname",
        "investor_name",
        "name"
    ],

    "inv_pan": [
        "inv_pan",
        "pan",
        "pan_no",
        "tax_no"
    ],

    "email": [
        "email",
        "email_id",
        "emailid"
    ],

    # =====================================================
    # EUIN
    #
    # euin_valid is the RTA's own validity marker. It is
    # stored exactly as the file spells it - the derived
    # boolean is computed in Gold, never here.
    # =====================================================

    "euin": [
        "euin",
        "euin_no",
        "euin_code"
    ],

    "euin_valid": [
        "euin_valid",
        "euinvalid",
        "euin_flag",
        "valid_euin"
    ],

    "reason": [
        "reason",
        "remarks",
        "error_reason"
    ],

    # =====================================================
    # BROKER
    # =====================================================

    "arn_code": [
        "arn_code",
        "arn",
        "brok_dlr_code",
        "brokcode",
        "broker_code"
    ],

    "subbrok_arn": [
        "subbrok_arn",
        "sub_brk_arn",
        "sub_broker_arn",
        "subarncode"
    ],

    "subbrokcod": [
        "subbrokcod",
        "subbrok",
        "sub_broker_code"
    ],

    "user_code": [
        "user_code",
        "usercode"
    ],

    "cons_code": [
        "cons_code",
        "conscode",
        "consolidation_code"
    ],

    "location": [
        "location",
        "branch",
        "servicing_branch"
    ],

    # =====================================================
    # TRANSACTION DETAIL
    # =====================================================

    "trxn_type": [
        "trxn_type",
        "txn_type",
        "trxntype",
        "transaction_type"
    ],

    "trxn_desc": [
        "trxn_desc",
        "txn_desc",
        "trxndesc",
        "transaction_description"
    ],

    "amount": [
        "amount",
        "trxn_amount",
        "amt",
        "gross_amount"
    ],

    # =====================================================
    # DATES
    # =====================================================

    "trade_date": [
        "trade_date",
        "trxn_date",
        "txn_date",
        "transaction_date"
    ],

    "posted_date": [
        "posted_date",
        "post_date",
        "postdate"
    ],

    "sys_reg_dt": [
        "sys_reg_dt",
        "sys_reg_date",
        "system_reg_date"
    ],

    "sip_regn_date": [
        "sip_regn_date",
        "sip_reg_date",
        "sip_registration_date"
    ],

    # =====================================================
    # NOT FROM FILE
    # =====================================================

    "flag": [],
    "created_at": [],
    "updated_at": []

}


INVALID_EUIN_SOURCE_OVERRIDES = {}


INVALID_EUIN_AMOUNT_COLUMNS = [
    "amount"
]


INVALID_EUIN_DATE_COLUMNS = [
    "trade_date",
    "posted_date",
    "sys_reg_dt",
    "sip_regn_date"
]


INVALID_EUIN_IDENTIFIER_COLUMNS = [
    "amc_code",
    "arn_code",
    "appln_no",
    "folio_no",
    "folio",
    "alt_folio",
    "folio_old",
    "scheme_folio_number",
    "inv_pan",
    "sch_code",
    "trxn_no",
    "usertxn_no",
    "auto_trxn_no",
    "subbrokcod",
    "subbrok_arn",
    "cons_code",
    "user_code",
    "euin"
]


# Business key of one invalid-EUIN row.
INVALID_EUIN_KEY_COLUMNS = [
    "source",
    "report_type",
    "trxn_no"
]


# =========================================================
# REBUILDING THE RTA SCHEME CODE
#
# WBR68 does NOT report the CAMS product code in one
# column. It splits it:
#
#   amc_code = "B"     sch_code = "51"     -> "B51"
#   amc_code = "L"     sch_code = "081G"   -> "L081G"
#   amc_code = "T"     sch_code = "SCFG"   -> "TSCFG"
#
# "B51" is what bronze.scheme_mapping stores as
# rta_scheme_code, and what gold.transactions stores as
# scheme_code, so the parts must be joined back together
# before scheme_id can resolve. Mapping sch_code straight
# to rta_scheme_code matches nothing.
#
# Declared per RTA rather than hard-coded in the
# transform, because another RTA may well report the full
# code in a single column. An RTA that is not listed here
# falls back to sch_code on its own.
# =========================================================

INVALID_EUIN_SCHEME_CODE_BUILD = {

    "CAMS": ["amc_code", "sch_code"],

    # KFINTECH placeholder. Set to ["sch_code"] if the
    # KFIN report carries the whole code in one column, or
    # list its parts in order if it splits them too.

}


# =========================================================
# REPORT REGISTRY
#
# The single description of every WBR report the pipeline
# knows. etl_wbr_report.py is driven entirely by these
# entries, so a NEW REPORT IS A DATA CHANGE IN THIS FILE
# and nothing else, and a NEW RTA for an existing report
# is two edits:
#
#   1. a row in <REPORT>_FILE_PATTERNS
#   2. the RTA's header spellings appended to the alias
#      lists (or an entry in <REPORT>_SOURCE_OVERRIDES
#      when the RTA reuses a header name with a different
#      meaning)
#
# Keys
#   label              name shown in the Streamlit UI
#   table              bronze / silver table name
#   gold_table         gold table name
#   mapping            target column -> header aliases
#   overrides          per-RTA alias overrides
#   amount_columns     money columns
#   date_columns       date columns
#   identifier_columns columns that must not gain ".0"
#   key_columns        business key of one row
#   required_columns   row is dropped when these are blank
#                      (RTA reports end in a total line
#                      that has no key)
#   scheme_code_build  per-RTA recipe for rebuilding the
#                      RTA scheme code when the report
#                      splits it across columns
#   sql_script         DDL to run when the table is missing
# =========================================================

WBR_REPORTS = {

    "BROKERAGE_SUMMARY": {
        "label": "Brokerage Summary",
        "table": "brokerage_summary",
        "gold_table": "brokerage_summary",
        "mapping": BROKERAGE_SUMMARY_MAPPING,
        "overrides": BROKERAGE_SOURCE_OVERRIDES,
        "amount_columns": BROKERAGE_AMOUNT_COLUMNS,
        "date_columns": BROKERAGE_DATE_COLUMNS,
        "identifier_columns": BROKERAGE_IDENTIFIER_COLUMNS,
        "key_columns": BROKERAGE_KEY_COLUMNS,
        "required_columns": ["product_code"],
        "sql_script": "sql_scripts/brokerage_summary.sql"
    },

    "KYC_STATUS": {
        "label": "KYC Status",
        "table": "kyc_status",
        "gold_table": "investor_kyc_status",
        "mapping": KYC_STATUS_MAPPING,
        "overrides": KYC_STATUS_SOURCE_OVERRIDES,
        "amount_columns": KYC_STATUS_AMOUNT_COLUMNS,
        "date_columns": KYC_STATUS_DATE_COLUMNS,
        "identifier_columns": KYC_STATUS_IDENTIFIER_COLUMNS,
        "key_columns": KYC_STATUS_KEY_COLUMNS,
        "required_columns": ["folio"],
        "sql_script": "sql_scripts/kyc_status.sql"
    },

    "INVALID_EUIN": {
        "label": "Invalid EUIN",
        "table": "invalid_euin",
        "gold_table": "invalid_euin",
        "mapping": INVALID_EUIN_MAPPING,
        "overrides": INVALID_EUIN_SOURCE_OVERRIDES,
        "amount_columns": INVALID_EUIN_AMOUNT_COLUMNS,
        "date_columns": INVALID_EUIN_DATE_COLUMNS,
        "identifier_columns": (
            INVALID_EUIN_IDENTIFIER_COLUMNS
        ),
        "key_columns": INVALID_EUIN_KEY_COLUMNS,
        "required_columns": ["trxn_no"],
        "scheme_code_build": INVALID_EUIN_SCHEME_CODE_BUILD,
        "sql_script": "sql_scripts/invalid_euin.sql"
    }

}


# =========================================================
# FILE REGISTRY - KYC STATUS
# =========================================================

KYC_STATUS_FILE_PATTERNS = [

    # ---------- CAMS ----------

    {
        "match": "wbr56",
        "source": "CAMS",
        "report_type": "WBR56",
        "report_key": "KYC_STATUS"
    },

    # ---------- KFINTECH ----------
    #
    # Placeholder. Fill in the real KFIN file identifier
    # (the way transactions use "mfsd201") when the KFIN
    # KYC status report is available, e.g.
    #
    # {
    #     "match": "mfsd???",
    #     "source": "KFIN",
    #     "report_type": "KYC_STATUS",
    #     "report_key": "KYC_STATUS"
    # },

]


# =========================================================
# FILE REGISTRY - INVALID EUIN
# =========================================================

INVALID_EUIN_FILE_PATTERNS = [

    # ---------- CAMS ----------

    {
        "match": "wbr68",
        "source": "CAMS",
        "report_type": "WBR68",
        "report_key": "INVALID_EUIN"
    },

    # ---------- KFINTECH ----------
    #
    # Placeholder, same rule as above, e.g.
    #
    # {
    #     "match": "mfsd???",
    #     "source": "KFIN",
    #     "report_type": "INVALID_EUIN",
    #     "report_key": "INVALID_EUIN"
    # },

]


# =========================================================
# COMBINED FILE REGISTRY
#
# The single place that decides which uploaded file is
# which WBR report. raw_ingestion.py and app.py both read
# it, so their filename rules cannot drift apart.
#
# ORDER MATTERS - the first match wins, so the more
# specific pattern must come first ("wbr36h" contains
# "wbr36"). BROKERAGE_FILE_PATTERNS already keeps that
# order internally and is placed first here.
# =========================================================

WBR_FILE_PATTERNS = (
    BROKERAGE_FILE_PATTERNS
    + KYC_STATUS_FILE_PATTERNS
    + INVALID_EUIN_FILE_PATTERNS
)


def identify_wbr_file(file_name):
    """
    Return the WBR_FILE_PATTERNS entry matching file_name,
    or None when the file is not a WBR report.

    The entry carries source, report_type and report_key;
    report_key selects the WBR_REPORTS spec that drives
    every layer.
    """

    name = str(file_name).lower()

    for pattern in WBR_FILE_PATTERNS:

        if pattern["match"] in name:

            return pattern

    return None


def get_report_spec(report_key):
    """
    Return the WBR_REPORTS entry for report_key.

    Raises rather than returning None: a file that matched
    the registry must have a spec, and a silent None would
    lose the upload.
    """

    spec = WBR_REPORTS.get(report_key)

    if spec is None:

        raise KeyError(
            f"Unknown WBR report key: {report_key}. "
            "Add it to WBR_REPORTS in mapping_wbr.py."
        )

    return spec

"""Column mapping for the CAMS WBR report family.

Pure data. No imports beyond the standard library, no database access, no logic, so
it can be imported and asserted in tests without a connection.

Every entry traces to a line in docs/cams-wbr-profile.md.

`source` holds the header EXACTLY as it appears in the file. Normalisation is applied
in code by bronze.cleaners.normalize_header, to BOTH this value and the incoming
header. Never pre-normalise here — the existing pipeline normalises headers with
" " -> "_" but aliases with only .lower().strip(), so every alias containing a space
is unmatchable, which is how 45 KFin columns are silently lost.

One explicit block per report. No merged alias lists: first-match-wins across merged
sources is what lets a newly added source steal an existing source's column.
"""

from __future__ import annotations

SOURCE_NAME = "CAMS_WBR"
SOURCE_VERSION = "2026-08-13"
PROFILE_DOC = "docs/cams-wbr-profile.md"

# Fallback date formats, tried after a column's declared `date_format`.
#
# Needed because a .xls date CELL is typed, and pandas renders it as an ISO datetime
# string under dtype=str — not in the display format a converted CSV shows. The first
# profile pass read LibreOffice-converted CSVs and therefore recorded the display
# format for four columns; reading the .xls directly gives '2026-07-16 00:00:00'.
#
# Keeping the declared per-column format first preserves the intent (and catches a
# provider switching a typed cell to text), while the chain makes the pipeline
# resilient to the same file arriving as .xls, .xlsx or .csv.
DATE_FALLBACKS = (
    "%Y-%m-%d %H:%M:%S",   # .xls / .xlsx typed date cell via dtype=str
    "%Y-%m-%d",            # ISO date
    "%d-%b-%Y",            # 01-Jan-2025  (rep_from_date / rep_to_date)
    "%m/%d/%Y",            # 7/16/2026    (display format in converted CSVs)
    "%d/%m/%Y",            # day-first, in case a provider switches
    "%Y%m%d",              # 20250928     (integer-rendered dates)
)

# Fixed namespace for deterministic uuid5 row ids. Never regenerate this value —
# changing it changes every id the pipeline has ever produced.
UUID_NAMESPACE = "3f2b6c48-7f4a-5d21-9e6b-1c8a4d0e5b72"


# =====================================================================
# FILE RECOGNITION
# =====================================================================
# Regex, not str.endswith. The existing CAMS routing requires the literal suffix
# "r2.csv", which makes it impossible to ever route an Excel file for that entity —
# a CAMS .xlsx is read and then silently discarded.

FILE_PATTERNS = {
    "wbr36_brokerage": {
        "pattern": r"^wbr36[-_ ].*brokerage",
        "formats": ["xls", "xlsx", "csv"],
        "report_variant": "STD",
        "required": False,
    },
    "wbr36h_brokerage": {
        # Must be tested BEFORE wbr36 — "wbr36h" also matches a loose "wbr36" prefix.
        # router.py sorts patterns by specificity for exactly this reason.
        "pattern": r"^wbr36h[-_ ].*brokerage",
        "formats": ["xls", "xlsx", "csv"],
        "report_variant": "H",
        "required": False,
    },
    "wbr56_kyc": {
        "pattern": r"^wbr56[-_ ].*kyc",
        "formats": ["xls", "xlsx", "csv"],
        "report_variant": "STD",
        "required": False,
    },
    "wbr68_invalid_euin": {
        "pattern": r"^wbr68[-_ ].*euin",
        "formats": ["xls", "xlsx", "csv"],
        "report_variant": "STD",
        "required": False,
    },
}


# =====================================================================
# FORMAT SPECIFICATIONS
# =====================================================================
# Declared, never sniffed. Delimiter detection from line 1 misreads any file whose
# header happens to contain the wrong character.

FORMAT_SPECS = {
    "xls": {
        # Legacy BIFF. Requires xlrd >= 2.0 (2.x dropped .xlsx, kept .xls).
        "engine": "xlrd",
        "sheet_name": 0,
        "all_sheets": False,
        "header_row": 0,
        "skiprows": 0,
        "notes": "All four sample files are OLE2 Composite Document, single sheet.",
    },
    "xlsx": {
        "engine": "openpyxl",
        "sheet_name": 0,
        "all_sheets": False,
        "header_row": 0,
        "skiprows": 0,
        "notes": "sheet_name is explicit. The pandas default of 0 silently ignores "
                 "every sheet after the first.",
    },
    "csv": {
        "encoding": "utf-8",
        "encoding_fallbacks": ["utf-8-sig", "latin1"],
        "delimiter": ",",
        "quotechar": '"',
        "header_row": 0,
        "skiprows": 0,
        "strip_nulls": True,
    },
}


# Provenance columns, injected by the bronze writer rather than read from the file.
# Shared by every entity so they are declared once.
_PROVENANCE = {
    "source_file_id": {
        "source": None, "type": "uuid", "nullable": False, "required": False,
        "identifier": False, "date_format": None, "trim": False, "case": None,
        "lookup": None, "layer": "bronze",
        "notes": "FK to audit_wbr.source_files. Populate it — the existing pipeline "
                 "declares this column on two gold tables and always leaves it NULL",
    },
    "row_number_in_file": {
        "source": None, "type": "integer", "nullable": False, "required": False,
        "identifier": False, "date_format": None, "trim": False, "case": None,
        "lookup": None, "layer": "bronze",
        "notes": "1-based data row index. Makes a rejected row traceable to a "
                 "spreadsheet line",
    },
    "report_variant": {
        "source": None, "type": "text", "nullable": False, "required": False,
        "identifier": False, "date_format": None, "trim": False, "case": "upper",
        "lookup": None, "layer": "bronze",
        "notes": "STD or H, from FILE_PATTERNS. WBR36 and WBR36H share 10 of 11 "
                 "product codes, so without this they overwrite each other",
    },
    "ingested_at": {
        "source": None, "type": "timestamptz", "nullable": False, "required": False,
        "identifier": False, "date_format": None, "trim": False, "case": None,
        "lookup": None, "layer": "bronze", "notes": "UTC",
    },
}


def _with_provenance(mapping: dict) -> dict:
    """Provenance first, then the file's own columns in output order."""
    return {**_PROVENANCE, **mapping}


# =====================================================================
# WBR36 / WBR36H — Brokerage summary by scheme
# =====================================================================
# 8 columns. Identical schema in both variants.
# Measures carry up to 8 decimal places (3950.45636848, -5327.04630385) and can be
# negative, so numeric(20,8) and no non-negative constraint. Rounding to 4 places as
# the existing round_decimal_columns() does would lose real precision here.

BROKERAGE_MAPPING = _with_provenance({
    "product_code": {
        "source": "product_code", "type": "text", "nullable": False, "required": True,
        "identifier": True, "date_format": None, "trim": True, "case": "upper",
        "lookup": None, "layer": "bronze",
        "notes": "CAMS scheme code: D104, FTI970, B153G, TSCFG. Unique within a file",
    },
    "product_name": {
        "source": "product_name", "type": "text", "nullable": True, "required": True,
        "identifier": False, "date_format": None, "trim": True, "case": None,
        "lookup": None, "layer": "bronze",
        "notes": "Long, includes parentheticals and 'erstwhile' clauses. Do not truncate",
    },
    "upfront": {
        "source": "upfront", "type": "numeric(20,8)", "nullable": True, "required": True,
        "identifier": False, "date_format": None, "trim": True, "case": None,
        "lookup": None, "layer": "bronze",
        "notes": "0 throughout the sample. Not sourceable from the existing pipeline",
    },
    "afe": {
        "source": "afe", "type": "numeric(20,8)", "nullable": True, "required": True,
        "identifier": False, "date_format": None, "trim": True, "case": None,
        "lookup": None, "layer": "bronze",
        "notes": "Additional Fee Expense. 0 throughout the sample",
    },
    "trailer_fee": {
        "source": "trailer_fee", "type": "numeric(20,8)", "nullable": True, "required": True,
        "identifier": False, "date_format": None, "trim": True, "case": None,
        "lookup": None, "layer": "bronze",
        "notes": "The only widely populated measure. CAN BE NEGATIVE (-5327.04630385)",
    },
    "trxn_charges": {
        "source": "trxn_charges", "type": "numeric(20,8)", "nullable": True, "required": True,
        "identifier": False, "date_format": None, "trim": True, "case": None,
        "lookup": None, "layer": "bronze",
        "notes": "Name collides with silver.transaction_master_new.trxn_charges but is "
                 "a different measure — brokerage-side, not investor-side",
    },
    "clawback": {
        "source": "clawback", "type": "numeric(20,8)", "nullable": True, "required": True,
        "identifier": False, "date_format": None, "trim": True, "case": None,
        "lookup": None, "layer": "bronze", "notes": "0 throughout the sample",
    },
    "incentives": {
        "source": "incentives", "type": "numeric(20,8)", "nullable": True, "required": True,
        "identifier": False, "date_format": None, "trim": True, "case": None,
        "lookup": None, "layer": "bronze",
        "notes": "Populated for a few schemes (361.74353686, 13.35629524)",
    },
})


# =====================================================================
# WBR56 — KYC status of Investor
# =====================================================================
# 40 columns. Three date columns in TWO different formats within the same file —
# declared per column, never inferred.

KYC_MAPPING = _with_provenance({
    "brok_dlr_code": {
        "source": "brok_dlr_code", "type": "text", "nullable": True, "required": True,
        "identifier": True, "date_format": None, "trim": True, "case": "upper",
        "lookup": None, "layer": "bronze", "notes": "ARN-266051 throughout the sample",
    },
    "folio": {
        "source": "folio", "type": "text", "nullable": False, "required": True,
        "identifier": True, "date_format": None, "trim": True, "case": "upper",
        "lookup": None, "layer": "bronze",
        "notes": "Two shapes: plain digits (1049217049) and slash-suffixed "
                 "(42213157/43). MUST stay text — numeric coercion is what creates "
                 "the trailing '.0' the existing pipeline has to strip back off",
    },
    "inv_name": {
        "source": "inv_name", "type": "text", "nullable": True, "required": True,
        "identifier": False, "date_format": None, "trim": True, "case": None,
        "lookup": None, "layer": "bronze",
        "notes": "Trailing double space in EVERY sample row. trim handles it",
    },
    "tax_no": {
        "source": "tax_no", "type": "text", "nullable": True, "required": True,
        "identifier": True, "date_format": None, "trim": True, "case": "upper",
        "lookup": None, "layer": "bronze", "notes": "PAN of the first holder",
    },
    "jname1": {
        "source": "jname1", "type": "text", "nullable": True, "required": True,
        "identifier": False, "date_format": None, "trim": True, "case": None,
        "lookup": None, "layer": "bronze", "notes": "",
    },
    "jointpan1": {
        "source": "jointpan1", "type": "text", "nullable": True, "required": True,
        "identifier": True, "date_format": None, "trim": True, "case": "upper",
        "lookup": None, "layer": "bronze", "notes": "",
    },
    "jname2": {
        "source": "jname2", "type": "text", "nullable": True, "required": True,
        "identifier": False, "date_format": None, "trim": True, "case": None,
        "lookup": None, "layer": "bronze", "notes": "Blank throughout the sample",
    },
    "jointpan2": {
        "source": "jointpan2", "type": "text", "nullable": True, "required": True,
        "identifier": True, "date_format": None, "trim": True, "case": "upper",
        "lookup": None, "layer": "bronze", "notes": "Blank throughout the sample",
    },
    "guardian": {
        "source": "guardian", "type": "text", "nullable": True, "required": True,
        "identifier": False, "date_format": None, "trim": True, "case": None,
        "lookup": None, "layer": "bronze", "notes": "Blank throughout the sample",
    },
    "guardian_panno": {
        "source": "guardian_panno", "type": "text", "nullable": True, "required": True,
        "identifier": True, "date_format": None, "trim": True, "case": "upper",
        "lookup": None, "layer": "bronze", "notes": "",
    },
    "address1": {
        "source": "address1", "type": "text", "nullable": True, "required": True,
        "identifier": False, "date_format": None, "trim": True, "case": None,
        "lookup": None, "layer": "bronze",
        "notes": "May contain commas and therefore arrive quoted",
    },
    "address2": {
        "source": "address2", "type": "text", "nullable": True, "required": True,
        "identifier": False, "date_format": None, "trim": True, "case": None,
        "lookup": None, "layer": "bronze", "notes": "",
    },
    "address3": {
        "source": "address3", "type": "text", "nullable": True, "required": True,
        "identifier": False, "date_format": None, "trim": True, "case": None,
        "lookup": None, "layer": "bronze", "notes": "",
    },
    "city": {
        "source": "city", "type": "text", "nullable": True, "required": True,
        "identifier": False, "date_format": None, "trim": True, "case": None,
        "lookup": None, "layer": "bronze",
        "notes": 'Can contain a comma: "MARYDEL, MARYDEL"',
    },
    "pincode": {
        "source": "pincode", "type": "text", "nullable": True, "required": True,
        "identifier": True, "date_format": None, "trim": True, "case": None,
        "lookup": None, "layer": "bronze",
        "notes": "Text, not integer — leading zeros and non-Indian postcodes",
    },
    "phone_res": {
        "source": "phone_res", "type": "text", "nullable": True, "required": True,
        "identifier": True, "date_format": None, "trim": True, "case": None,
        "lookup": None, "layer": "bronze", "notes": "Blank throughout the sample",
    },
    "phone_off": {
        "source": "phone_off", "type": "text", "nullable": True, "required": True,
        "identifier": True, "date_format": None, "trim": True, "case": None,
        "lookup": None, "layer": "bronze", "notes": "Blank throughout the sample",
    },
    "mobile_no": {
        "source": "mobile_no", "type": "text", "nullable": True, "required": True,
        "identifier": True, "date_format": None, "trim": True, "case": None,
        "lookup": None, "layer": "bronze",
        "notes": "Mixed: +919328266374 and 9825333209. Keep the + prefix; "
                 "normalise to E.164 in silver, do not strip in bronze",
    },
    "email": {
        "source": "email", "type": "text", "nullable": True, "required": True,
        "identifier": False, "date_format": None, "trim": True, "case": "lower",
        "lookup": None, "layer": "bronze", "notes": "",
    },
    "location": {
        "source": "location", "type": "text", "nullable": True, "required": True,
        "identifier": False, "date_format": None, "trim": True, "case": None,
        "lookup": None, "layer": "bronze",
        "notes": "Compound code/city: A1/Ahmedabad, PKD491/Palakkad, NSEDP/Mumbai. "
                 "Split in silver into location_code and location_city; keep raw",
    },
    "state": {
        "source": "state", "type": "text", "nullable": True, "required": True,
        "identifier": False, "date_format": None, "trim": True, "case": None,
        "lookup": None, "layer": "bronze",
        "notes": "Compound code/name: GU/Gujarat, OT/Others. Can be a bare '/' when "
                 "unknown — treat '/' as NULL in silver, not as a value",
    },
    "fax_res": {
        "source": "fax_res", "type": "text", "nullable": True, "required": True,
        "identifier": True, "date_format": None, "trim": True, "case": None,
        "lookup": None, "layer": "bronze", "notes": "Blank throughout the sample",
    },
    "fax_off": {
        "source": "fax_off", "type": "text", "nullable": True, "required": True,
        "identifier": True, "date_format": None, "trim": True, "case": None,
        "lookup": None, "layer": "bronze", "notes": "Blank throughout the sample",
    },
    "fh_kyc": {
        "source": "fh_kyc", "type": "text", "nullable": True, "required": True,
        "identifier": False, "date_format": None, "trim": True, "case": "upper",
        "lookup": "kyc_flag", "layer": "bronze",
        "notes": "'KYC OK' or blank. First holder",
    },
    "gu_kyc": {
        "source": "gu_kyc", "type": "text", "nullable": True, "required": True,
        "identifier": False, "date_format": None, "trim": True, "case": "upper",
        "lookup": "kyc_flag", "layer": "bronze", "notes": "Guardian",
    },
    "jh1_kyc": {
        "source": "jh1_kyc", "type": "text", "nullable": True, "required": True,
        "identifier": False, "date_format": None, "trim": True, "case": "upper",
        "lookup": "kyc_flag", "layer": "bronze", "notes": "Joint holder 1",
    },
    "jh2_kyc": {
        "source": "jh2_kyc", "type": "text", "nullable": True, "required": True,
        "identifier": False, "date_format": None, "trim": True, "case": "upper",
        "lookup": "kyc_flag", "layer": "bronze", "notes": "Joint holder 2",
    },
    "brok_name": {
        "source": "brok_name", "type": "text", "nullable": True, "required": True,
        "identifier": False, "date_format": None, "trim": True, "case": None,
        "lookup": None, "layer": "bronze",
        "notes": "'KMP MF SERVICES LLP' throughout the sample. A data column, not a "
                 "config constant. Not sourceable from the existing pipeline",
    },
    "rep_from_date": {
        "source": "rep_from_date", "type": "date", "nullable": True, "required": True,
        "identifier": False, "date_format": "%d-%b-%Y", "trim": True, "case": None,
        "lookup": None, "layer": "bronze",
        "notes": "01-Jan-2025. DIFFERENT format from rep_date in the same file",
    },
    "rep_to_date": {
        "source": "rep_to_date", "type": "date", "nullable": True, "required": True,
        "identifier": False, "date_format": "%d-%b-%Y", "trim": True, "case": None,
        "lookup": None, "layer": "bronze", "notes": "31-Dec-2025",
    },
    "rep_date": {
        "source": "rep_date", "type": "date", "nullable": True, "required": True,
        "identifier": False, "date_format": "%m/%d/%Y", "trim": True, "case": None,
        "lookup": None, "layer": "bronze",
        "notes": "7/16/2026 — US order, unlike the two above. This is exactly the "
                 "class of defect that makes inferred date parsing unsafe",
    },
    "amc_code": {
        "source": "amc_code", "type": "text", "nullable": False, "required": True,
        "identifier": True, "date_format": None, "trim": True, "case": "upper",
        "lookup": None, "layer": "bronze",
        "notes": "CAMS AMC code (B, P, T, G, L), not the AMFI code",
    },
    "fh_kyc_desc": {
        "source": "fh_kyc_desc", "type": "text", "nullable": True, "required": True,
        "identifier": False, "date_format": None, "trim": True, "case": "upper",
        "lookup": "kyc_status", "layer": "bronze",
        "notes": "'KYC VALIDATED' | 'KYC REGISTERED - New KYC' | blank. NOT tied to "
                 "the same holder as fh_kyc — a row can have fh_kyc set with a blank "
                 "fh_kyc_desc and carry jh1_kyc_desc instead. Map independently",
    },
    "gu_kyc_desc": {
        "source": "gu_kyc_desc", "type": "text", "nullable": True, "required": True,
        "identifier": False, "date_format": None, "trim": True, "case": "upper",
        "lookup": "kyc_status", "layer": "bronze", "notes": "",
    },
    "jh1_kyc_desc": {
        "source": "jh1_kyc_desc", "type": "text", "nullable": True, "required": True,
        "identifier": False, "date_format": None, "trim": True, "case": "upper",
        "lookup": "kyc_status", "layer": "bronze", "notes": "",
    },
    "jh2_kyc_desc": {
        "source": "jh2_kyc_desc", "type": "text", "nullable": True, "required": True,
        "identifier": False, "date_format": None, "trim": True, "case": "upper",
        "lookup": "kyc_status", "layer": "bronze", "notes": "",
    },
    "fh_g_aadharlink": {
        "source": "fh_g_aadharlink", "type": "text", "nullable": True, "required": True,
        "identifier": False, "date_format": None, "trim": True, "case": "upper",
        "lookup": "aadhaar_link", "layer": "bronze",
        "notes": "'Aadhar Linked' or blank. Note the provider's spelling of Aadhaar",
    },
    "jh1_aadharlink": {
        "source": "jh1_aadharlink", "type": "text", "nullable": True, "required": True,
        "identifier": False, "date_format": None, "trim": True, "case": "upper",
        "lookup": "aadhaar_link", "layer": "bronze", "notes": "",
    },
    "jh2_aadharlink": {
        "source": "jh2_aadharlink", "type": "text", "nullable": True, "required": True,
        "identifier": False, "date_format": None, "trim": True, "case": "upper",
        "lookup": "aadhaar_link", "layer": "bronze", "notes": "",
    },
    "country": {
        "source": "country", "type": "text", "nullable": True, "required": True,
        "identifier": False, "date_format": None, "trim": True, "case": None,
        "lookup": None, "layer": "bronze",
        "notes": "Full name, not a code: India, United States, Canada",
    },
})


# =====================================================================
# WBR68 — Invalid EUIN Report
# =====================================================================
# 31 columns. The most derivable of the four — 26 of 31 columns exist in
# silver.transaction_master_new by name or by rename.

EUIN_MAPPING = _with_provenance({
    "amc_code": {
        "source": "amc_code", "type": "text", "nullable": False, "required": True,
        "identifier": True, "date_format": None, "trim": True, "case": "upper",
        "lookup": None, "layer": "bronze", "notes": "B, G, L, T",
    },
    "arn_code": {
        "source": "arn_code", "type": "text", "nullable": True, "required": True,
        "identifier": True, "date_format": None, "trim": True, "case": "upper",
        "lookup": None, "layer": "bronze",
        "notes": "silver.transaction_master_new.brokcode is the equivalent",
    },
    "appln_no": {
        "source": "appln_no", "type": "text", "nullable": True, "required": True,
        "identifier": True, "date_format": None, "trim": True, "case": None,
        "lookup": None, "layer": "bronze", "notes": "Blank for some rows",
    },
    "folio_no": {
        "source": "folio_no", "type": "text", "nullable": False, "required": True,
        "identifier": True, "date_format": None, "trim": True, "case": "upper",
        "lookup": None, "layer": "bronze",
        "notes": "Same value as `folio` in every sample row. Both retained because "
                 "the output layout requires both, at positions 4 and 23",
    },
    "inv_name": {
        "source": "inv_name", "type": "text", "nullable": True, "required": True,
        "identifier": False, "date_format": None, "trim": True, "case": None,
        "lookup": None, "layer": "bronze", "notes": "Trailing double space",
    },
    "inv_pan": {
        "source": "inv_pan", "type": "text", "nullable": True, "required": True,
        "identifier": True, "date_format": None, "trim": True, "case": "upper",
        "lookup": None, "layer": "bronze", "notes": "",
    },
    "trade_date": {
        "source": "trade_date", "type": "date", "nullable": True, "required": True,
        "identifier": False, "date_format": "%m/%d/%Y", "trim": True, "case": None,
        "lookup": None, "layer": "bronze", "notes": "7/6/2026, 2/7/2025",
    },
    "sch_code": {
        "source": "sch_code", "type": "text", "nullable": True, "required": True,
        "identifier": True, "date_format": None, "trim": True, "case": "upper",
        "lookup": None, "layer": "bronze",
        "notes": "Short and numeric-looking (51, 201, 081G, SCFG). MUST stay text",
    },
    "sch_name": {
        "source": "sch_name", "type": "text", "nullable": True, "required": True,
        "identifier": False, "date_format": None, "trim": True, "case": None,
        "lookup": None, "layer": "bronze", "notes": "Long, with parentheticals",
    },
    "trxn_no": {
        "source": "trxn_no", "type": "text", "nullable": False, "required": True,
        "identifier": True, "date_format": None, "trim": True, "case": None,
        "lookup": None, "layer": "bronze",
        "notes": "Natural key with amc_code. Unique 9/9 in the sample",
    },
    "trxn_type": {
        "source": "trxn_type", "type": "text", "nullable": True, "required": True,
        "identifier": False, "date_format": None, "trim": True, "case": "upper",
        "lookup": None, "layer": "bronze",
        "notes": "Variable shape: P, PSCEF1, PSIPL30, P2SSCF. Not fixed width",
    },
    "trxn_desc": {
        "source": "trxn_desc", "type": "text", "nullable": True, "required": True,
        "identifier": False, "date_format": None, "trim": True, "case": None,
        "lookup": None, "layer": "bronze",
        "notes": "Long free text with instalment numbers. Size generously",
    },
    "amount": {
        "source": "amount", "type": "numeric(20,4)", "nullable": True, "required": True,
        "identifier": False, "date_format": None, "trim": True, "case": None,
        "lookup": None, "layer": "bronze", "notes": "999.95, 9999.5, 2000",
    },
    "subbrokcod": {
        "source": "subbrokcod", "type": "text", "nullable": True, "required": True,
        "identifier": True, "date_format": None, "trim": True, "case": "upper",
        "lookup": None, "layer": "bronze", "notes": "Blank throughout the sample",
    },
    "location": {
        "source": "location", "type": "text", "nullable": True, "required": True,
        "identifier": False, "date_format": None, "trim": True, "case": None,
        "lookup": None, "layer": "bronze", "notes": "Compound code/city, as in WBR56",
    },
    "euin": {
        "source": "euin", "type": "text", "nullable": True, "required": True,
        "identifier": True, "date_format": None, "trim": True, "case": "upper",
        "lookup": None, "layer": "bronze", "notes": "E064801, E338921",
    },
    "euin_valid": {
        "source": "euin_valid", "type": "text", "nullable": True, "required": True,
        "identifier": False, "date_format": None, "trim": True, "case": "upper",
        "lookup": "euin_valid", "layer": "bronze",
        "notes": "TWO non-valid values in the sample: N (8 rows) and F (1 row). A "
                 "filter of euin_valid = 'N' misses the F row. Use euin_valid <> 'Y'",
    },
    "email": {
        "source": "email", "type": "text", "nullable": True, "required": True,
        "identifier": False, "date_format": None, "trim": True, "case": "lower",
        "lookup": None, "layer": "bronze", "notes": "The distributor's email, not the investor's",
    },
    "posted_date": {
        "source": "posted_date", "type": "date", "nullable": True, "required": True,
        "identifier": False, "date_format": "%m/%d/%Y", "trim": True, "case": None,
        "lookup": None, "layer": "bronze", "notes": "Blank in the first sample row",
    },
    "cons_code": {
        "source": "cons_code", "type": "text", "nullable": True, "required": True,
        "identifier": True, "date_format": None, "trim": True, "case": "upper",
        "lookup": None, "layer": "bronze",
        "notes": "ARN-266051 throughout. Not sourceable from the existing pipeline",
    },
    "usertxn_no": {
        "source": "usertxn_no", "type": "text", "nullable": True, "required": True,
        "identifier": True, "date_format": None, "trim": True, "case": None,
        "lookup": None, "layer": "bronze", "notes": "Long numeric string. Keep as text",
    },
    "alt_folio": {
        "source": "alt_folio", "type": "text", "nullable": True, "required": True,
        "identifier": True, "date_format": None, "trim": True, "case": "upper",
        "lookup": None, "layer": "bronze", "notes": "Populated only for the Tata rows",
    },
    "folio": {
        "source": "folio", "type": "text", "nullable": True, "required": True,
        "identifier": True, "date_format": None, "trim": True, "case": "upper",
        "lookup": None, "layer": "bronze", "notes": "Duplicate of folio_no; layout needs both",
    },
    "subbrok_arn": {
        "source": "subbrok_arn", "type": "text", "nullable": True, "required": True,
        "identifier": True, "date_format": None, "trim": True, "case": "upper",
        "lookup": None, "layer": "bronze", "notes": "Blank throughout the sample",
    },
    "sys_reg_dt": {
        "source": "sys_reg_dt", "type": "date", "nullable": True, "required": True,
        "identifier": False, "date_format": "%m/%d/%Y", "trim": True, "case": None,
        "lookup": None, "layer": "bronze", "notes": "9/17/2025, 8/16/2018",
    },
    "reason": {
        "source": "reason", "type": "text", "nullable": True, "required": True,
        "identifier": False, "date_format": None, "trim": True, "case": None,
        "lookup": None, "layer": "bronze",
        "notes": "'Invalid EUIN' in every row — the report's selection criterion "
                 "echoed back as a column",
    },
    "user_code": {
        "source": "user_code", "type": "text", "nullable": True, "required": True,
        "identifier": True, "date_format": None, "trim": True, "case": "upper",
        "lookup": None, "layer": "bronze", "notes": "CMAGE24375, NSELM",
    },
    "sip_regn_date": {
        "source": "sip_regn_date", "type": "date", "nullable": True, "required": True,
        "identifier": False, "date_format": "%m/%d/%Y", "trim": True, "case": None,
        "lookup": None, "layer": "bronze",
        "notes": "MIXED FORMAT: 9/17/2025 in one row and 20250928 (%Y%m%d) in another. "
                 "cleaners.parse_dates falls back to %Y%m%d, then rejects",
        "date_format_fallbacks": ["%Y%m%d"],
    },
    "auto_trxn_no": {
        "source": "auto_trxn_no", "type": "text", "nullable": True, "required": True,
        "identifier": True, "date_format": None, "trim": True, "case": None,
        "lookup": None, "layer": "bronze", "notes": "6697788 for the Tata rows",
    },
    "folio_old": {
        "source": "folio_old", "type": "text", "nullable": True, "required": True,
        "identifier": True, "date_format": None, "trim": True, "case": "upper",
        "lookup": None, "layer": "bronze", "notes": "Blank throughout the sample",
    },
    "scheme_folio_number": {
        "source": "scheme_folio_number", "type": "text", "nullable": True, "required": True,
        "identifier": True, "date_format": None, "trim": True, "case": "upper",
        "lookup": None, "layer": "bronze", "notes": "Blank throughout the sample",
    },
})


# =====================================================================
# ENTITY REGISTRY
# =====================================================================
# natural_key generates the UNIQUE constraint in sql/002_tables.sql, which is what
# makes ON CONFLICT possible and gives the planner an index. 13 of the 14 tables in
# the existing pipeline have neither.
#
# date_columns and numeric_columns are DERIVED from the mapping below, never typed by
# hand. transform_transaction in the existing pipeline casts "trade_date",
# "load_amount" and "broker_percent"; the real columns are "traddate", "load" and
# "brokperc", so three of seven casts silently do nothing.

ENTITIES = {
    "wbr36_brokerage": {
        "mapping": BROKERAGE_MAPPING,
        "table": "brokerage_by_scheme",
        "natural_key": ["report_variant", "product_code"],
        "report_code": "WBR36",
        "chunksize": 2000,
    },
    "wbr36h_brokerage": {
        "mapping": BROKERAGE_MAPPING,
        "table": "brokerage_by_scheme",   # same table, distinguished by report_variant
        "natural_key": ["report_variant", "product_code"],
        "report_code": "WBR36H",
        "chunksize": 2000,
    },
    "wbr56_kyc": {
        "mapping": KYC_MAPPING,
        "table": "investor_kyc_status",
        "natural_key": ["amc_code", "folio"],
        "report_code": "WBR56",
        "chunksize": 2000,
    },
    "wbr68_invalid_euin": {
        "mapping": EUIN_MAPPING,
        "table": "invalid_euin",
        "natural_key": ["amc_code", "trxn_no"],
        "report_code": "WBR68",
        "chunksize": 2000,
    },
}


def date_columns(mapping: dict) -> list[str]:
    """Derived, so it cannot drift from the mapping."""
    return [k for k, v in mapping.items() if v["type"] == "date"]


def numeric_columns(mapping: dict) -> list[str]:
    return [k for k, v in mapping.items() if v["type"].startswith("numeric")
            or v["type"] == "integer"]


def identifier_columns(mapping: dict) -> list[str]:
    return [k for k, v in mapping.items() if v.get("identifier")]


def required_columns(mapping: dict) -> list[str]:
    return [k for k, v in mapping.items() if v.get("required")]


def source_to_target(mapping: dict) -> dict[str, str]:
    """Header-as-delivered -> target column. Only columns actually read from the file."""
    return {v["source"]: k for k, v in mapping.items() if v.get("source")}


# =====================================================================
# GOLD GRAIN DECLARATIONS
# =====================================================================
# Declared before implementing and asserted after every load by utils.audit.assert_grain.

GOLD_GRAIN = {
    "brokerage_by_scheme": {
        "grain": "one row per (report_period, report_variant, product_code)",
        "natural_key": ["report_period", "report_variant", "product_code"],
        "kind": "dimension",
        "max_row_ratio": 1.0,
        "derived_from": ["silver_wbr.brokerage_by_scheme"],
    },
    "investor_kyc_status": {
        "grain": "one row per (amc_code, folio)",
        "natural_key": ["amc_code", "folio"],
        "kind": "dimension",
        "max_row_ratio": 1.0,
        "derived_from": ["silver_wbr.investor_kyc_status"],
    },
    "invalid_euin": {
        "grain": "one row per (amc_code, trxn_no) — a transaction ledger",
        "natural_key": ["amc_code", "trxn_no"],
        "kind": "ledger",
        "max_row_ratio": 1.0,
        "derived_from": ["silver_wbr.invalid_euin"],
    },
}


# =====================================================================
# OUTPUT LAYOUTS
# =====================================================================
# Exact column order of each generated report. Must match the sample files
# column-for-column, in order — this is the contract with whoever consumes them.

OUTPUT_LAYOUTS = {
    "WBR36": {
        "file_stem": "WBR36-Brokerage summary by scheme",
        "source_table": "brokerage_by_scheme",
        "filter": {"report_variant": "STD"},
        "columns": [
            "product_code", "product_name", "upfront", "afe",
            "trailer_fee", "trxn_charges", "clawback", "incentives",
        ],
    },
    "WBR36H": {
        "file_stem": "WBR36H-Brokerage summary by scheme",
        "source_table": "brokerage_by_scheme",
        "filter": {"report_variant": "H"},
        "columns": [
            "product_code", "product_name", "upfront", "afe",
            "trailer_fee", "trxn_charges", "clawback", "incentives",
        ],
    },
    "WBR56": {
        "file_stem": "WBR56-KYC status of Investor",
        "source_table": "investor_kyc_status",
        "filter": {},
        "columns": [
            "brok_dlr_code", "folio", "inv_name", "tax_no", "jname1", "jointpan1",
            "jname2", "jointpan2", "guardian", "guardian_panno", "address1",
            "address2", "address3", "city", "pincode", "phone_res", "phone_off",
            "mobile_no", "email", "location", "state", "fax_res", "fax_off",
            "fh_kyc", "gu_kyc", "jh1_kyc", "jh2_kyc", "brok_name", "rep_from_date",
            "rep_to_date", "rep_date", "amc_code", "fh_kyc_desc", "gu_kyc_desc",
            "jh1_kyc_desc", "jh2_kyc_desc", "fh_g_aadharlink", "jh1_aadharlink",
            "jh2_aadharlink", "country",
        ],
    },
    "WBR68": {
        "file_stem": "WBR68-Invalid EUIN Report",
        "source_table": "invalid_euin",
        # Not euin_valid = 'N'. The sample contains an F row with the same reason.
        "filter": {"__euin_invalid__": True},
        "columns": [
            "amc_code", "arn_code", "appln_no", "folio_no", "inv_name", "inv_pan",
            "trade_date", "sch_code", "sch_name", "trxn_no", "trxn_type", "trxn_desc",
            "amount", "subbrokcod", "location", "euin", "euin_valid", "email",
            "posted_date", "cons_code", "usertxn_no", "alt_folio", "folio",
            "subbrok_arn", "sys_reg_dt", "reason", "user_code", "sip_regn_date",
            "auto_trxn_no", "folio_old", "scheme_folio_number",
        ],
    },
}

# Output date rendering, per report, to reproduce the provider's own formats.
OUTPUT_DATE_FORMATS = {
    "WBR56": {
        "rep_from_date": "%d-%b-%Y",
        "rep_to_date": "%d-%b-%Y",
        "rep_date": "%-m/%-d/%Y",
    },
    "WBR68": {
        "trade_date": "%-m/%-d/%Y",
        "posted_date": "%-m/%-d/%Y",
        "sys_reg_dt": "%-m/%-d/%Y",
        "sip_regn_date": "%-m/%-d/%Y",
    },
}


# =====================================================================
# VALIDATION CONTRACT
# =====================================================================

VALIDATION = {
    "assert_required_present": True,
    "assert_no_unmapped_columns": True,
    "assert_schema_match": True,
    "assert_lookups_resolve": True,
    "on_cast_failure": "reject",     # never silently "null"
    "on_missing_required": "abort",
}

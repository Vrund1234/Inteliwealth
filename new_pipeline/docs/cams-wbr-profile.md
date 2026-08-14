# CAMS WBR Report Profile

Step 0 output. Every entry in `config/mapping_cams_wbr.py` must trace to a line in this document.

Profiled 2026-08-13 against the four sample files in `/home/user/Inteliwealth-pipeline/files/gold/`.

---

## File format

All four files are **legacy BIFF `.xls`**, not `.xlsx`:

```
$ file "WBR36-Brokerage summary by scheme.xls"
Composite Document File V2 Document, Little Endian, Os: Windows, Version 4.10, Subject: Excel R
```

Consequences:

- `openpyxl` cannot read them. `xlrd` is required, and only `xlrd >= 2.0` on `.xls`
  (xlrd 2.x dropped `.xlsx` support and kept `.xls`, which is exactly what is needed here).
- `xlrd` is **not** installed in `python_scripts/venv`. This pipeline uses its own venv so the
  existing environment is untouched.
- LibreOffice is available at `/usr/bin/soffice` and is used only as an optional export path for
  writing `.xls` output. It is never required for reading.

| File | Bytes | Sheet rows | Columns |
|---|---|---|---|
| `WBR36-Brokerage summary by scheme.xls` | 36,864 | 141 | 8 |
| `WBR36H-Brokerage summary by scheme.xls` | 7,680 | 11 | 8 |
| `WBR56-KYC status of Investor.xls` | 78,848 | 101 | 40 |
| `WBR68-Invalid EUIN Report.xls` | 11,264 | 9 | 31 |

Header is row 0 in every file. Single sheet. No preamble rows. Headers are already
lowercase snake_case as delivered by CAMS — unusual and convenient, but the pipeline still
normalises both sides through one function rather than relying on it.

---

## WBR36 / WBR36H — Brokerage summary by scheme

Identical 8-column schema in both files.

```
product_code, product_name, upfront, afe, trailer_fee, trxn_charges, clawback, incentives
```

Sample rows:

```
D104,DSP Mid Cap Fund - Regular Plan - Growth,0,0,3950.45636848,0,0,0
K179,Kotak Arbitrage Fund - Growth (Regular Plan),0,0,-5327.04630385,0,0,0
P01,ICICI Prudential ELSS Tax Saver Fund - Growth,0,0,0,0,0,361.74353694
```

Observations:

- `product_code` is unique within each file (141/141 and 11/11).
- **10 of the 11 `WBR36H` product codes also appear in `WBR36`** (`K179`, `L103D`, `G201`,
  `TSCFG`, `PDFG`, …). The two files are therefore different *variants* of the same report, not a
  partition. `report_variant` (`STD` vs `H`) must be part of the natural key or the two files
  overwrite each other.
- The five brokerage measures carry up to 8 decimal places
  (`3950.45636848`, `-5327.04630385`). `numeric(20,8)` — rounding to 4 places as the existing
  pipeline does would lose real precision here.
- `trailer_fee` and `incentives` can be **negative** (clawback of previously paid brokerage).
  Do not add a non-negative constraint.
- Neither file carries a report date. The reporting period must come from ingestion metadata
  or the `--period` CLI argument. It is not inferable from the file contents.

**Natural key:** `(report_variant, product_code)` within a reporting period.

---

## WBR56 — KYC status of Investor

40 columns, 101 rows.

```
brok_dlr_code, folio, inv_name, tax_no, jname1, jointpan1, jname2, jointpan2, guardian,
guardian_panno, address1, address2, address3, city, pincode, phone_res, phone_off, mobile_no,
email, location, state, fax_res, fax_off, fh_kyc, gu_kyc, jh1_kyc, jh2_kyc, brok_name,
rep_from_date, rep_to_date, rep_date, amc_code, fh_kyc_desc, gu_kyc_desc, jh1_kyc_desc,
jh2_kyc_desc, fh_g_aadharlink, jh1_aadharlink, jh2_aadharlink, country
```

Observations:

- `folio` alone is unique (101/101). `(amc_code, folio)` is used as the key for safety, since
  folio numbers are only guaranteed unique per AMC.
- `folio` values arrive in two shapes: plain digits (`1049217049`) and slash-suffixed
  (`42213157/43`, `12825552/43`, `14098397/10`). Both are strings. **Do not** coerce to numeric —
  the existing pipeline's `.0`-stripping regex exists precisely because Excel coerced these.
- `inv_name` has **trailing double spaces** in every row (`"Radhika Doshi Kathpalia  "`). Trim.
- `location` is a compound `code/city` field (`A1/Ahmedabad`, `PKD491/Palakkad`, `M1/Chennai`,
  `NSEDP/Mumbai`). Split into `location_code` and `location_city` in silver; keep the raw value.
- `state` is a compound `code/name` field (`GU/Gujarat`, `KE/Kerala`, `OT/Others`) and can be a
  bare `/` when unknown — see the `MARYDEL` row. Treat `/` as NULL, not as a valid value.
- `city` can contain a comma and therefore arrives quoted: `"MARYDEL, MARYDEL"`.
- `country` is a full name, not a code: `India`, `United States`, `Canada`.
- KYC status columns come in pairs: a short flag and a description.
  - `fh_kyc` = `KYC OK` or blank
  - `fh_kyc_desc` = `KYC VALIDATED` or `KYC REGISTERED - New KYC` or blank
  - The `gu_` / `jh1_` / `jh2_` variants follow the same pattern and are mostly blank.
  - A row can have `fh_kyc = "KYC OK"` with a blank `fh_kyc_desc` and instead carry
    `jh1_kyc_desc` — see the `Naman Kamleshbhai Patel` row. The flag and description are not
    tied to the same holder position. Map them as independent columns; do not derive one from
    the other.
- `fh_g_aadharlink` = `Aadhar Linked` or blank. Same for `jh1_aadharlink`, `jh2_aadharlink`.
- `brok_name` is a constant across the sample (`KMP MF SERVICES LLP`) but is a data column, not
  a config value.
- Three date columns, three different formats in the same file:
  - `rep_from_date` = `01-Jan-2025`  → `%d-%b-%Y`
  - `rep_to_date`   = `31-Dec-2025`  → `%d-%b-%Y`
  - `rep_date`      = `7/16/2026`    → `%m/%d/%Y`
  This is the exact class of defect that made the existing pipeline's inferred date parsing
  unreliable. Every date format is declared per column in the mapping config.
- `amc_code` is a single letter or short code (`B`, `P`, `T`, `G`, `L`) matching the CAMS AMC
  code convention, not the AMFI code.

**Natural key:** `(amc_code, folio)`.

---

## WBR68 — Invalid EUIN Report

31 columns, 9 rows.

```
amc_code, arn_code, appln_no, folio_no, inv_name, inv_pan, trade_date, sch_code, sch_name,
trxn_no, trxn_type, trxn_desc, amount, subbrokcod, location, euin, euin_valid, email,
posted_date, cons_code, usertxn_no, alt_folio, folio, subbrok_arn, sys_reg_dt, reason,
user_code, sip_regn_date, auto_trxn_no, folio_old, scheme_folio_number
```

Observations:

- `trxn_no` alone is unique (9/9). `(amc_code, trxn_no)` used as the key.
- `euin_valid` takes **two distinct non-valid values** in the sample: `N` (8 rows) and `F`
  (1 row). Both carry `reason = "Invalid EUIN"`. A filter written as `euin_valid = 'N'` would
  miss the `F` row. The correct predicate is `euin_valid <> 'Y'`.
- `reason` is a constant `Invalid EUIN` across all rows — it is the report's selection criterion
  echoed back as a column.
- `folio_no` and `folio` hold the **same value** in every sample row. Both are retained because
  the output layout requires both, at positions 4 and 23.
- `alt_folio` is populated only for the Tata rows (`5012981336`); blank elsewhere.
- Dates are `%m/%d/%Y` (`7/6/2026`, `2/7/2025`, `11/11/2025`). `sip_regn_date` in the first row
  is `20250928` — an **8-digit `%Y%m%d` integer in the same column family as `%m/%d/%Y` dates**.
  Parse with a two-format fallback and reject anything matching neither.
- `trxn_type` is a short code that varies in shape: `P`, `PSCEF1`, `PSIPL30`, `P2SSCF`. Not a
  fixed-width code. Keep as text.
- `trxn_desc` is long free text including instalment numbers
  (`Net Systematic Purchase-NSE - Instalment No - 77 via Online`). Size the column generously.
- `amount` has 2 decimals (`999.95`, `9999.5`, `2000`).
- `subbrokcod` is blank in every sample row; `subbrok_arn` is also blank. Keep both, nullable.
- `cons_code` and `arn_code` both hold `ARN-266051` in every row.

**Natural key:** `(amc_code, trxn_no)`.

---

## Derivability from the existing pipeline

Checked every output column against `silver.transaction_master_new` (116 columns) and
`silver.investor_master` (150 columns) in the live `master_tables_db`.

| Report | Exact-name matches | Derivable by rename | Not sourceable from existing data |
|---|---|---|---|
| WBR68 | 10 of 31 | 16 more (`brokcode`→`arn_code`, `pan`→`inv_pan`, `traddate`→`trade_date`, `prodcode`→`sch_code`, `scheme`→`sch_name`, `trxnno`→`trxn_no`, `trxntype`→`trxn_type`, `trxn_nature`→`trxn_desc`, `subbrok`→`subbrokcod`, `postdate`→`posted_date`, `usrtrxno`→`usertxn_no`, `altfolio`→`alt_folio`, `sub_brk_arn`→`subbrok_arn`, `sys_regn_date`→`sys_reg_dt`, `usercode`→`user_code`, `application_no`→`appln_no`) | `cons_code`, `auto_trxn_no`, `sip_regn_date`. `reason` is a constant |
| WBR56 | 15 of 40 | 9 more (`broker_code`→`brok_dlr_code`, `folio_no`→`folio`, `pan_no`→`tax_no`, `joint_name_1`→`jname1`, `joint1_pan`→`jointpan1`, `joint_name_2`→`jname2`, `joint2_pan`→`jointpan2`, `guardian_name`→`guardian`, `guardian_pan`→`guardian_panno`, `fax_residence`→`fax_res`, `fax_office`→`fax_off`) | **13 columns**: `fh_kyc`, `gu_kyc`, `jh1_kyc`, `jh2_kyc`, all four `*_kyc_desc`, all three `*_aadharlink`, `brok_name`, `rep_from_date`, `rep_to_date` |
| WBR36 / WBR36H | 2 of 8 | `product_name` from `gold.scheme.scheme_name` | **5 measures**: `upfront`, `afe`, `trailer_fee`, `clawback`, `incentives` |

The existing pipeline holds a single `brokcomm` total and a `brokperc`. It has no breakdown into
upfront / AFE / trailer / clawback / incentives, and it stores CKYC *numbers* (`ckyc_no`,
`jh1_ckyc`, `jh2_ckyc`) rather than KYC *status descriptions*, and no Aadhaar-link status at all.

**Therefore the WBR files are treated as inputs as well as output templates.** The brokerage
measures and KYC status columns exist nowhere else, so ingesting the WBR reports is the only route
by which those columns can ever be populated. The pipeline round-trips: ingest WBR `.xls` →
bronze → silver → gold → regenerate the four report layouts, optionally enriched by the CAMS
R-series files (`*R2.csv`, `*R9.csv`, `*R49.csv`) for the columns those do carry.

This is recorded as an explicit assumption. If the intent was instead to derive all four reports
purely from the R-series files, WBR36, WBR36H and 13 columns of WBR56 are not achievable without a
new CAMS feed, and that needs to be raised with whoever owns the CAMS report subscription.

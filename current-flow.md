# Current Data Flow — Bronze → Silver → Gold

Reverse-engineered from the code as it stands on branch `development`
(commit `088447f`). Everything below describes what the code **does today**,
not what it is supposed to do. Gaps and dead columns are called out in
[Section 8](#8-known-gaps-in-the-current-flow).

- Entry point: `python_scripts/app.py` (Streamlit)
- Bronze writers: `raw_ingestion.py` → `etl_trans.py`, `etl_investor_master.py`, `etl_sip.py`
- Column dictionaries: `mapping.py`
- Bronze → Silver: `transformations/transform.py`
- Silver → Gold: `gold_loader.py` → `etl_gold_*.py`

---

## 1. Databases and engines

`utils/db.py` builds two engines. **The names are crossed** — read this before
touching anything:

| Variable | Constant used | Actual database | Contents |
|---|---|---|---|
| `engine` (commented "Project Database") | `MASTER_DATABASE` | `intelliwealth_master_19_aug_2026` | `bronze`, `silver`, `gold` schemas — the pipeline itself |
| `master_engine` (commented "Master Database") | `PROJECT_DATABASE` | `intelliwealthdb_19_aug` | application DB — `public.amc`, `public.arn`, `public.amfi_scheme_master`, `public.scheme_master`, `public.rta_amc_code`, `public.nav_master` |

So `engine` = pipeline DB, `master_engine` = reference/app DB. The comments say
the opposite. All ETL reads/writes of bronze/silver/gold go through `engine`;
only AMC-id, ARN-id and AMFI lookups go through `master_engine`.

`utils/triggers.py` (re)creates `bronze.update_updated_at()` and a
`BEFORE UPDATE` trigger on all six bronze/silver tables after every extract and
every transform.

### Table inventory (live, as restored)

| Layer | Table | Columns | Rows |
|---|---|---|---|
| bronze | `transaction_master_new` | 117 | 128,766 |
| bronze | `investor_master` | 151 | 3,542 |
| bronze | `sip_master_new` | 61 | 1,396 |
| bronze | `scheme_mapping` | 23 | 515 |
| bronze | `scheme_mapping_review` / `_audit` / `_override` / `scheme_name_alias` | 13 / 8 / 8 / 7 | 41 / — / — / — |
| bronze | `amc_master`, `state_code`, `occupation_code`, `category_code` | 3 / 4 / 4 / 4 | seeded from `sql_scripts/` |
| silver | `transaction_master_new` | 118 | 128,766 |
| silver | `investor_master` | 152 | 3,542 |
| silver | `sip_master_new` | 62 | 1,396 |
| gold | `amc` | 9 | 29 |
| gold | `scheme` | 22 | 515 |
| gold | `scheme_nav` | 8 | 51,061 |
| gold | `transactions` | 32 | 128,766 |
| gold | `holdings` | 34 | 1,790 |
| gold | `sip` | 29 | 1,396 |
| gold | `clients` | 37 | 593 |
| gold | `folio_nominees` | 14 | 1,616 |

Silver is column-identical to bronze **plus `scheme_id`** (and for SIP: silver
adds `ecsno`, drops `auto_trno`). Nothing is retyped: `amount`, `units`,
`purprice`, `traddate`, `postdate`, `rep_date` are `text` in **both** bronze and
silver. Numeric/date coercion happens only in pandas, and the values are written
back as text. Real typing appears for the first time in gold.

---

## 2. Upload → Bronze

### 2.1 What the uploader accepts

`app.py` (line ~106):

```python
st.file_uploader("Upload Files", type=["xlsx", "csv", "txt"], accept_multiple_files=True)
```

`.xls`, `.dbf` are **not** selectable in the UI even though `read_file()`
handles both.

### 2.2 File recognition

Both `app.py` (for the progress banner) and `raw_ingestion.extract_and_push()`
(for real routing) classify by filename only:

| Filename test | RTA | Target |
|---|---|---|
| ends with `r2.csv` / `r2.dbf` | CAMS | transactions |
| ends with `r9.csv` / `r9.dbf` | CAMS | investor master |
| ends with `r49.csv` / `r49.dbf` | CAMS | SIP |
| contains `mfsd201` | KFIN | transactions |
| contains `mfsd211` | KFIN | investor master |
| contains `mfsd243` | KFIN | SIP |
| anything else | — | printed as `Unknown file type` and **silently dropped** |

`source` is not read from the file — it is stamped as the literal `"CAMS"` or
`"KFIN"` by the branch that matched.

### 2.3 `read_file()` — physical parsing (`raw_ingestion.py`)

**CSV/TXT:** encoding probed in order `utf-8-sig, utf-8, utf-16, utf-16le,
utf-16be, latin1`; NUL bytes stripped; CRLF/CR normalised to LF; delimiter
sniffed by `csv.Sniffer` over the first 10 000 chars with a manual
tab/comma/semicolon/pipe fallback. Rows are split by `smart_split()`, which
honours single-quoted fields that contain the delimiter. Rows with **fewer**
fields than the header are right-padded with `""`; rows with **more** fields are
counted and skipped (never truncated). Object columns are stripped of outer
quotes/whitespace, and `"nan"`, `"None"`, `"<NA>"` become `""`.

**DBF:** written to a temp file, read with `dbfread.DBF`, every value cast to
stripped string, `nan`/`None`/`NaT` → `""`.

**Excel:** `pd.read_excel(file, dtype=str, keep_default_na=False)` — all text,
no NA inference. First sheet only.

Then, for every format: header names stripped of quotes/whitespace, blank-named
columns dropped, duplicate column names dropped keeping the first.

Files of the same kind are `pd.concat`-ed (all CAMS transaction files together,
all KFIN transaction files together) before the mapping step.

### 2.4 Column standardisation

Each loader lower-cases the incoming headers and replaces space, `-`, `/` with
`_` and strips `#` (`clean_columns()`), then walks its dictionary from
`mapping.py`. **The three loaders resolve multi-alias columns differently:**

| Loader | Resolution rule |
|---|---|
| `etl_investor_master.apply_investor_mapping` | **first alias present in the file wins**, whole column taken, remaining aliases ignored |
| `etl_trans.apply_transaction_mapping` | **per-row coalesce**: target starts NULL, every alias in the list fills only the rows still missing (blank/`nan`/`None`/`<NA>`/`NaT` count as missing) |
| `etl_sip.apply_sip_mapping` | first alias present wins; tries both `Alias Name` and `Alias_Name` spellings |

Special cases hard-coded outside the dictionary:

- **`postdate`** (transactions) is parsed inside the mapping step, per source:
  KFIN reads `td_prdt` with `format="%d/%m/%Y"`, CAMS reads `postdate` with
  `format="%m/%d/%Y"`. Both first strip `\xa0`/quotes and regex-extract just the
  `D/M/YYYY` portion so a trailing `12:00:00 AM` cannot turn the value into
  `NaT`. `format_dates()` then leaves any column that is already datetime64
  alone.
- **`occupation` / `occupation_description`** (investor): for CAMS the file's
  `occupation` column holds the *description*, so it is copied to
  `occupation_description` and `occupation` is forced to `None`; for KFIN
  `occ_code` → `occupation` and `occupation_description` is taken as-is.
- **`scheme_code` / `scheme_name`** (SIP): the alias list is overridden by
  source — KFIN uses `Scheme` / `Scheme Name`, CAMS uses `SCHEME_CODE` /
  `SCHEME`.
- **`periodicity`** (SIP) is upper-cased and remapped:
  `OM→MONTHLY, OW→WEEKLY, SM→SEMI_MONTHLY, TM→BI_MONTHLY, Q→QUARTERLY, O→ONE_TIME`.

### 2.5 Value cleaning before insert

- `normalize()` — every non-date column cast to string, `'`/`"` removed,
  trimmed, and `nan`/`None`/`<NA>`/`NaT` → `""`.
- `clean_identifier_columns()` — for a per-loader list of ID-ish columns
  (folios, PANs, phone numbers, pincodes, bank accounts, DP/client ids, CKYC,
  transaction/application numbers): trailing `.0` stripped by regex, blanks →
  `None`. This is what stops Excel's float coercion from turning folio
  `1049217049` into `1049217049.0`.
- `format_dates()` — per-loader `DATE_COLUMNS` coerced to `datetime.date`,
  failures → `None`.
  - transactions: `traddate, postdate, rep_date, ticob_posted_date, sys_regn_date, ca_initiated_date`
  - investor: `dob, report_date, rep_date, folio_date, jh1_dob, jh2_dob, guardian_dob, lastupdateddate, nominee_dob`
  - SIP: `from_date, to_date, cease_date, reg_date, pause_from_date, pause_to_date`, parsed with **source-specific formats** — CAMS `%m/%d/%Y %I:%M %p`, KFIN `%d/%m/%Y`
- `created_at` / `updated_at` = `pd.Timestamp.now(tz="Asia/Kolkata")`.

### 2.6 The `flag` column (bronze de-duplication)

For each table the loader reads the whole existing bronze table, normalises both
sides identically, joins every business column into a single `|`-separated key
(ignoring `flag`, `created_at`, `updated_at`, `source`) and sets
`flag = 1` where the key already exists, else `0`. **Nothing is ever updated or
deleted** — duplicates are appended with `flag = 1` and simply never travel
further, because Silver only reads `flag = 0`. Ordering: SIP additionally drops
exact duplicates *inside* the incoming batch before comparing; transactions and
investor do not (that block is commented out in `etl_trans.py`).

Finally the DataFrame is aligned to `information_schema` column order for the
target table, missing columns added as `None`, extras dropped, and appended with
`to_sql(..., if_exists="append", method="multi", chunksize=50000)`.

### 2.7 Bronze column dictionaries

`*` The "first match wins" note applies to investor and SIP; transactions
coalesce across all aliases as described in 2.4. Aliases are matched
**after** the header has been lower-cased and de-spaced, so `Address #1` in the
dictionary can only ever match a column literally named `address_1` — the
mixed-case entries are dead weight, not extra coverage.

Duplicate dictionary keys resolve to the **last** definition, Python-style. In
`INVESTOR_MASTER_MAPPING`, `occupation` is declared twice — the effective list
is `["occupation", "occpn", "occ_code"]`, and the earlier `["occ_code"]`
declaration plus its neighbouring `occupation_description` entry are shadowed.

#### bronze.transaction_master_new (116 target columns)

| bronze column | accepted source headers (first match wins*) |
|---|---|
| `source` | `source` |
| `prodcode` | `prodcode`, `fmcode` |
| `amc_code` | `amc_code`, `td_fund` |
| `folio_no` | `folio_no`, `td_acno` |
| `divopt` | `divopt` |
| `scheme` | `scheme`, `funddesc` |
| `trxnno` | `trxnno`, `td_trno` |
| `inv_name` | `inv_name`, `invname` |
| `trxnmode` | `trxnmode`, `trnmode` |
| `trxnstat` | `trxnstat`, `trnstat` |
| `trxntype` | `trxntype`, `td_trtype` |
| `trxnsubtyp` | `trxnsubtype`, `subtrtype`, `trxnsubtyp`, `trnsub` |
| `trxn_nature` | `trxn_nature`, `trdesc` |
| `trflag` | `trflag` |
| `traddate` | `traddate`, `navdate`, `td_trdt` |
| `postdate` | `postdate`, `td_prdt` |
| `rep_date` | `rep_date`, `crdate` |
| `sys_regn_date` | `sys_regn_date`, `sipregdt` |
| `units` | `units`, `td_units` |
| `amount` | `amount`, `td_amt` |
| `purprice` | `purprice`, `td_pop` |
| `load` | `load`, `load1` |
| `stt` | `stt` |
| `stamp_duty` | `stamp_duty` |
| `trxn_charges` | `trxn_charge`, `trcharges`, `trxn_charges` |
| `total_tax` | `total_tds`, `tdsamount`, `total_tax` |
| `brokcode` | `brokcode`, `td_agent` |
| `subbrok` | `subbrok`, `td_broker` |
| `usercode` | `usercode`, `branchcode` |
| `usrtrxno` | `usrtrxno`, `ihno`, `inwardnum1` |
| `pan` | `pan`, `pan1`, `Pan Number` |
| `client_id` | `client_id`, `clientid` |
| `dp_id` | `dp_id`, `dpid` |
| `tax_status` | `tax_status`, `status` |
| `chqno` | `chqno` |
| `siptrxnno` | `siptrxnno`, `sipregslno` |
| `targ_src_scheme` | `targ_src_s`, `prcode1`, `targ_src_scheme` |
| `scheme_type` | `scheme_type`, `assettype` |
| `ter_location` | `ter_location`, `citycateg5` |
| `euin` | `euin` |
| `euin_valid` | `euin_valid`, `evalid` |
| `euin_opted` | `euin_opted`, `edeclflag` |
| `sub_brk_arn` | `sub_brk_arn`, `subarncode` |
| `exchange_flag` | `exchange_f`, `td_trxnmod`, `exchange_flag`, `electrxnflag` |
| `remarks` | `remarks` |
| `altfolio` | `altfolio` |
| `common_account_number` | `can` |
| `ft_accno` | `ft_accno`, `ftaccno` |
| `rejtrnoor2` | `rejtrnoor2` |
| `to_product_code` | `targ_src_s`, `prcode1` |
| `reversal_c` | `reversal_c`, `reversal_code` |
| `td_fund` | `td_fund` |
| `funddesc` | `funddesc` |
| `td_purred` | `td_purred` |
| `folio_old` | `folio_old` |
| `old_folio` | `old_folio` |
| `scheme_folio_number` | `scheme_folio_number` |
| `time1` | `time1` |
| `crdate` | `crdate` |
| `crtime` | `crtime` |
| `purdate` | `purdate` |
| `puramt` | `puramt` |
| `purunits` | `purunits` |
| `brokperc` | `brokperc` |
| `brokcomm` | `brokcomm` |
| `application_no` | `application_no`, `td_appno` |
| `tax` | `tax` |
| `te_15h` | `te_15h` |
| `bank_name` | `bank_name` |
| `ac_no` | `ac_no`, `BankAccno` |
| `micr_no` | `micr_no` |
| `inv_iin` | `inv_iin` |
| `invid` | `invid` |
| `guardpanno` | `guardpanno` |
| `scanrefno` | `scanrefno` |
| `trxn_type_flag` | `trxn_type_flag` |
| `ticob_trtype` | `ticob_trtype` |
| `ticob_trno` | `ticob_trno` |
| `ticob_posted_date` | `ticob_posted_date` |
| `eligib_amt` | `eligib_amt` |
| `src_of_txn` | `src_of_txn` |
| `trxn_suffix` | `trxn_suffix` |
| `exch_dc_flag` | `exch_dc_flag` |
| `src_brk_code` | `src_brk_code` |
| `ca_initiated_date` | `ca_initiated_date` |
| `gst_state_code` | `gst_state_code` |
| `igst_amount` | `igst_amount` |
| `cgst_amount` | `cgst_amount` |
| `sgst_amount` | `sgst_amount` |
| `rev_remark` | `rev_remark` |
| `original_trxnno` | `original_trxnno` |
| `amc_ref_no` | `amc_ref_no` |
| `request_ref_no` | `request_ref_no` |
| `transmission_flag` | `transmission_flag` |
| `swflag` | `swflag` |
| `seq_no` | `seq_no` |
| `reinvest_flag` | `reinvest_flag` |
| `mult_brok` | `mult_brok` |
| `location` | `location` |
| `divper` | `divper` |
| `loadper` | `loadper` |
| `ihno` | `ihno` |
| `branchcode` | `branchcode` |
| `inwardno` | `inwardno` |
| `sipregslno` | `sipregslno` |
| `cleared` | `cleared` |
| `invstate` | `invstate` |
| `isctrno` | `isctrno` |
| `td_pop` | `td_pop` |
| `td_ptrno` | `td_ptrno` |
| `chqdate` | `chqdate` |
| `exchorgtrtype` | `exchorgtrtype` |
| `sfunddt` | `sfunddt` |
| `flag` | _(not from file)_ |
| `created_at` | _(not from file)_ |
| `updated_at` | _(not from file)_ |

#### bronze.investor_master (151 target columns)

| bronze column | accepted source headers (first match wins*) |
|---|---|
| `source` | `source` |
| `folio_no` | `foliochk`, `folio`, `folio_no` |
| `investor_name` | `inv_name`, `investor_name` |
| `joint_name_1` | `jnt_name1`, `jtname1`, `joint_name_1` |
| `joint_name_2` | `jnt_name2`, `jtname2`, `joint_name_2` |
| `address1` | `address1`, `address_1`, `add1`, `Address #1` |
| `address2` | `address2`, `address_2`, `add2`, `Address #2` |
| `address3` | `address3`, `address_3`, `add3`, `Address #3` |
| `city` | `city`, `City` |
| `state` | `state`, `State` |
| `country` | `country`, `Country` |
| `pincode` | `pincode`, `pin`, `Pincode` |
| `dob` | `dob`, `inv_dob`, `date_of_birth` |
| `mobile_no` | `mobile_no`, `mobile`, `mobile_number` |
| `email` | `email` |
| `phone_res` | `phone_res`, `rphone`, `phone_residence` |
| `phone_off` | `phone_off`, `ophone`, `phone_office` |
| `tax_status` | `tax_status`, `status` |
| `holding_nature` | `holding_nature` |
| `pan_no` | `pan_no`, `pan`, `pan_number` |
| `joint1_pan` | `joint1_pan` |
| `joint2_pan` | `joint2_pan` |
| `guardian_pan` | `guardian_pan`, `guard_pan`, `pangno`, `guardpanno` |
| `bank_name` | `bank_name`, `bname` |
| `bank_account_no` | `bank_account_no`, `bnkacno`, `ac_no`, `bankaccno` |
| `account_type` | `account_type`, `bnkactype`, `ac_type` |
| `branch` | `branch` |
| `ifsc_code` | `ifsc_code` |
| `bank_address1` | `bank_address1`, `bank_address_1`, `badd1`, `b_address1` |
| `bank_address2` | `bank_address2`, `bank_address_2`, `badd2`, `b_address2` |
| `bank_address3` | `bank_address3`, `bank_address_3`, `badd3`, `b_address3` |
| `bank_city` | `bank_city`, `bcity`, `b_city` |
| `bank_state` | `bank_state` |
| `bank_country` | `bank_country` |
| `nominee1_name` | `nominee1_name`, `nom_name`, `nominee` |
| `nominee1_relation` | `nominee1_relation`, `relation`, `nominee_relation` |
| `nominee1_address1` | `nominee1_address1`, `nom_addr1`, `nominee_address1` |
| `nominee1_address2` | `nominee1_address2`, `nom_addr2`, `nominee_address2` |
| `nominee1_address3` | `nominee1_address3`, `nom_addr3`, `nominee_address3` |
| `nominee1_city` | `nominee1_city`, `nom_city`, `nominee_city` |
| `nominee1_state` | `nominee1_state`, `nom_state`, `nominee_state` |
| `nominee1_pincode` | `nominee1_pincode`, `nom_pincode`, `nominee_pin_code` |
| `nominee1_phone` | `nominee1_phone`, `nom_ph_off`, `nom_ph_res`, `nominee_phone_residence` |
| `nominee1_email` | `nominee1_email`, `nom_email`, `nominee_email` |
| `nominee1_percentage` | `nominee1_percentage`, `nom_percentage`, `nominee_ratio` |
| `nominee2_name` | `nominee2_name`, `nom2_name`, `nominee2` |
| `nominee2_relation` | `nominee2_relation`, `nom2_relation` |
| `nominee2_address1` | `nominee2_address1`, `nom2_addr1` |
| `nominee2_address2` | `nominee2_address2`, `nom2_addr2` |
| `nominee2_address3` | `nominee2_address3`, `nom2_addr3` |
| `nominee2_city` | `nominee2_city`, `nom2_city` |
| `nominee2_state` | `nominee2_state`, `nom2_state` |
| `nominee2_pincode` | `nominee2_pincode`, `nom2_pincode`, `nominee2_pin_code` |
| `nominee2_phone` | `nominee2_phone`, `nom2_ph_off`, `nom2_ph_res`, `nominee2_phone_residence` |
| `nominee2_email` | `nominee2_email`, `nom2_email` |
| `nominee2_percentage` | `nominee2_percentage`, `nom2_percentage`, `nominee2_ratio` |
| `nominee3_name` | `nominee3_name`, `nom3_name`, `nominee3` |
| `nominee3_relation` | `nominee3_relation`, `nom3_relation` |
| `nominee3_address1` | `nominee3_address1`, `nom3_addr1` |
| `nominee3_address2` | `nominee3_address2`, `nom3_addr2` |
| `nominee3_address3` | `nominee3_address3`, `nom3_addr3` |
| `nominee3_city` | `nominee3_city`, `nom3_city` |
| `nominee3_state` | `nominee3_state`, `nom3_state` |
| `nominee3_pincode` | `nominee3_pincode`, `nom3_pincode` |
| `nominee3_phone` | `nominee3_phone`, `nom3_ph_off`, `nom3_ph_res`, `nominee3_phone_residence` |
| `nominee3_email` | `nominee3_email`, `nom3_email` |
| `nominee3_percentage` | `nominee3_percentage`, `nom3_percentage`, `nominee3_ratio` |
| `broker_code` | `broker_code`, `brokcode`, `td_agent`, `td_broker` |
| `dp_id` | `dp_id`, `dpid` |
| `demat_flag` | `demat_flag`, `demat`, `demat_folio_flag` |
| `ckyc_no` | `ckyc_no`, `fh_ckyc_no`, `fh_ckyc`, `fh_ckyc_n` |
| `jh1_ckyc` | `jh1_ckyc`, `jh1_ckyc_no`, `jh1_ckyc_n` |
| `jh2_ckyc` | `jh2_ckyc`, `jh2_ckyc_no`, `jh2_ckyc_n` |
| `guardian_ckyc_no` | `guardian_ckyc_no`, `g_ckyc_no`, `g_ckyc_n` |
| `guardian_name` | `guardian_name`, `guardian`, `guardianname`, `guard_name`, `GUARD_NAME` |
| `report_date` | `report_date`, `rep_date` |
| `report_time` | `report_time`, `time1` |
| `folio_date` | `folio_date`, `folio_dat`, `folio_dt`, `foliodate` |
| `occupation` | `occupation`, `occpn`, `occ_code` |
| `occupation_description` | `occupation_description`, `Occupation Description` |
| `product_code` | `product`, `product_code` |
| `scheme_name` | `scheme_name`, `SCH_NAME`, `SCHEME`, `Scheme Name`, `fund_description`, `Fund Description`, `funddesc` |
| `rep_date` | `rep_date` |
| `rupee_bal` | `rupee_bal` |
| `uin_no` | `uin_no` |
| `inv_iin` | `inv_iin` |
| `subbroker` | `subbroker`, `subbrok` |
| `brokcode` | `brokcode`, `broker_code` |
| `reinv_flag` | `reinv_flag`, `reinvest_f` |
| `b_pincode` | `b_pincode`, `bpin` |
| `nom_ph_off` | `nom_ph_off` |
| `nom2_ph_off` | `nom2_ph_off` |
| `nom3_ph_off` | `nom3_ph_off` |
| `tpa_linked` | `tpa_linked`, `tpa_link` |
| `g_ckyc_no` | `g_ckyc_no`, `g_ckyc_n`, `guardian_ckyc_no` |
| `jh1_dob` | `jh1_dob` |
| `jh2_dob` | `jh2_dob` |
| `guardian_dob` | `guardian_dob` |
| `amc_code` | `amc_code`, `Fund` |
| `gst_state_code` | `gst_state_code`, `gst_state_` |
| `folio_old` | `folio_old`, `old_folio` |
| `scheme_folio_number` | `scheme_folio_number`, `scheme_fol` |
| `fund` | `fund`, `td_fund` |
| `fund_description` | `fund_description`, `scheme_name` |
| `tpin` | `tpin` |
| `f_name` | `f_name` |
| `m_name` | `m_name` |
| `phone_res1` | `phone_res1`, `rphone1` |
| `phone_res2` | `phone_res2`, `rphone2` |
| `phone_off1` | `phone_off1`, `ophone1` |
| `phone_off2` | `phone_off2`, `ophone2` |
| `fax_residence` | `fax_residence`, `fax` |
| `fax_office` | `fax_office`, `faxoff` |
| `occ_code` | `occ_code`, `occpn` |
| `bank_phone` | `bank_phone`, `bphone` |
| `investor_id` | `investor_id`, `invid` |
| `client_id` | `client_id` |
| `dividend_option` | `dividend_option`, `divopt` |
| `mode_of_holding_description` | `holding_nature`, `mode_of_holding_description` |
| `mapin_id` | `mapin_id` |
| `pan2` | `pan2` |
| `pan3` | `pan3` |
| `category` | `category` |
| `categorydesc` | `categorydesc` |
| `statusdesc` | `statusdesc` |
| `kyc1flag` | `kyc1flag` |
| `kyc2flag` | `kyc2flag` |
| `kyc3flag` | `kyc3flag` |
| `lastupdateddate` | `lastupdateddate` |
| `commonaccno` | `commonaccno` |
| `holder_1_aadhaar_info` | `holder_1_aadhaar_info`, `aadhaar` |
| `holder_2_aadhaar_info` | `holder_2_aadhaar_info` |
| `holder_3_aadhaar_info` | `holder_3_aadhaar_info` |
| `guardian_aadhaar_info` | `guardian_aadhaar_info` |
| `joint_holder_1st_resi_phone_no` | `joint_holder_1st_resi_phone_no` |
| `joint_holder_2nd_resi_phone_no` | `joint_holder_2nd_resi_phone_no` |
| `joint_holder_1_contact_number` | `joint_holder_1_contact_number`, `jh1_mobile_no` |
| `joint_holder_2_contact_number` | `joint_holder_2_contact_number`, `jh2_mobile_no` |
| `joint_holder_1_email_id` | `joint_holder_1_email_id`, `jh1_email` |
| `joint_holder_2_email_id` | `joint_holder_2_email_id`, `jh2_email` |
| `investors_resi_faxno` | `investors_resi_faxno`, `fax` |
| `kycgflag` | `kycgflag` |
| `nominee_opt_out_flag` | `nominee_opt_out_flag` |
| `nominee_dob` | `nominee_dob` |
| `nominee_guardian_name` | `nominee_guardian_name` |
| `emailconcern` | `emailconcern` |
| `emailrelationship` | `emailrelationship` |
| `mobilerelationship` | `mobilerelationship` |
| `flag` | _(not from file)_ |
| `created_at` | _(not from file)_ |
| `updated_at` | _(not from file)_ |

#### bronze.sip_master_new (61 target columns)

| bronze column | accepted source headers (first match wins*) |
|---|---|
| `source` | `source` |
| `product_code` | `PRODUCT`, `Product Code` |
| `scheme_code` | `SCHEME_CODE`, `Scheme` |
| `scheme_name` | `SCHEME`, `Scheme Name` |
| `plan` | `Plan` |
| `folio_no` | `FOLIO_NO`, `Folio` |
| `folio_old` | `FOLIO_OLD` |
| `inv_name` | `INV_NAME`, `Investor Name` |
| `pan` | `PAN` |
| `inv_iin` | `INV_IIN`, `Ihno` |
| `inv_dp_id` | `InvDpId` |
| `inv_client_id` | `InvClientId` |
| `dp_inv_name` | `DP_InvName` |
| `aut_trntyp` | `AUT_TRNTYP`, `SipType` |
| `auto_trno` | `AUTO_TRNO` |
| `ft_sip_regno` | `FT_SIP_REGNO`, `RegSlno` |
| `auto_amount` | `AUTO_AMOUNT`, `Amount` |
| `no_of_installments` | `No Of Installments` |
| `periodicity` | `PERIODICITY`, `Frequency` |
| `period_day` | `PERIOD_DAY` |
| `payment_mode` | `PAYMENT_MODE`, `SIP Mode` |
| `reg_date` | `REG_DATE`, `RegistrationDate` |
| `from_date` | `FROM_DATE`, `Start Date` |
| `to_date` | `TO_DATE`, `End Date` |
| `cease_date` | `CEASE_DATE`, `TerminateDate` |
| `pause_from_date` | `PAUSE_FROM_DATE` |
| `pause_to_date` | `PAUSE_TO_DATE` |
| `target_scheme` | `TARGET_SCHEME`, `To Scheme` |
| `target_scheme_code` | `TARGET_SCHEME_CODE`, `ToProductCode` |
| `target_scheme_name` | `ToSchemeName` |
| `target_plan` | `To Plan` |
| `sub_arn_code` | `SUB_ARN_CODE`, `AgentCode` |
| `agent_name` | `AgentName` |
| `subbroker` | `SUBBROKER`, `Subbroker` |
| `euin` | `EUIN` |
| `zone` | `Zone` |
| `branch` | `Branch`, `BRANCH` |
| `ter_location` | `TER_LOCATION`, `Location` |
| `bank` | `BANK`, `ECSBankName` |
| `ac_type` | `AC_TYPE` |
| `instrm_no` | `INSTRM_NO`, `ECSNO` |
| `cheq_micr_no` | `CHEQ_MICR_NO` |
| `ecs_account_no` | `ECSAcno` |
| `ac_holder_name` | `AC_HOLDER_NAME`, `ECSHolderName` |
| `amc_code` | `AMC_CODE`, `Fund Code` |
| `user_code` | `USER_CODE` |
| `package_name` | `PACKAGE_NAME` |
| `special_product` | `SPECIAL_PRODUCT` |
| `subtrxndesc` | `SUBTRXNDESC`, `Trtype` |
| `remarks` | `REMARKS` |
| `top_up_frq` | `TOP_UP_FRQ` |
| `top_up_amt` | `TOP_UP_AMT` |
| `top_up_perc` | `TOP_UP_PERC` |
| `status` | `Status` |
| `modify_flag` | `ModifyFlag` |
| `umrn_code` | `umrncode` |
| `scheme_folio_number` | `SCHEME_FOLIO_NUMBER` |
| `request_ref_no` | `REQUEST_REF_NO` |
| `flag` | _(not from file)_ |
| `created_at` | _(not from file)_ |
| `updated_at` | _(not from file)_ |

---

## 3. Side branch: `bronze.scheme_mapping` (runs between Bronze and Silver)

Triggered from `app.py` right after extraction, **only if a transaction file was
uploaded**. `scheme_mapping.load_scheme_mapping()` reads
`SELECT DISTINCT ON (source, prodcode) source, amc_code, prodcode, scheme FROM
bronze.transaction_master_new`, renames to
`rta / rta_amc_code / rta_scheme_code / rta_scheme_name`, and tries to attach an
AMFI scheme code using reference data from `master_engine`:

- `public.amfi_scheme_master` (~16 k active schemes)
- `public.scheme_master` (~38 k codes incl. historical, `is_deleted = false`)
- `public.rta_amc_code` (RTA + RTA AMC code → `amc_slug`)
- `public.nav_master` (NAV fingerprinting)
- `bronze.scheme_mapping_override`, `bronze.scheme_name_alias` (curated)

Rules live in `scheme_matching/rules.py` as an ordered registry; every rule runs
on every row so the audit table and the top-3 review candidates can be
populated:

| Rule | Confidence |
|---|---|
| `OVERRIDE` (authoritative — displaces equal-confidence incumbents) | 100 |
| `ISIN_MATCH` | 100 |
| `PRODUCT_MATCH` | 100 |
| `STRUCT_EXACT` | 98 |
| `NAV_MATCH` | 97 |
| `STRUCT_TIEBREAK` | 95 |
| `CORE_FUZZY` | 90 |

Results are written with `UPSERT_MAPPING_SQL`
(`ON CONFLICT (rta, rta_scheme_code) DO UPDATE`). A row whose `verified_at` is
set is protected: `scheme_id`, `amfi_scheme_code`, `mapping_source`,
`mapping_confidence` and `mapping_status` are only overwritten when the incoming
candidate has **strictly higher** confidence. Ambiguous or low-confidence
matches go to `bronze.scheme_mapping_review` for human approval and are **not**
mapped until approved and promoted (`promote_approved_mappings.py`, also exposed
as a button in the Streamlit page). The same call also applies decisions made in
the review queue since the last run.

Net effect for the pipeline: `bronze.scheme_mapping` is the single lookup
`rta_scheme_code → scheme_id` that Silver consumes.

---

## 4. Bronze → Silver (`transformations/transform.py`, `load_silver()`)

Three independent passes, always in this order: investor master, transaction
master, SIP master. Each pass reads `SELECT * FROM bronze.<table> WHERE flag = 0`,
runs its transform function, rounds every float column to 4 decimals
(`round_decimal_columns`), and hands off to `append_new_rows()`.

### 4.1 `scheme_id` resolution — the one genuinely new column

`map_scheme_id(df, product_column)` loads `rta_scheme_code, scheme_id` from
`bronze.scheme_mapping` (no `is_active` filter, no status filter), upper-cases
and trims both sides, drops empty codes, keeps the first row per
`rta_scheme_code`, and maps:

| Silver table | Product column used | → |
|---|---|---|
| `investor_master` | `product_code` | `scheme_id` |
| `transaction_master_new` | `prodcode` | `scheme_id` |
| `sip_master_new` | `product_code` | `scheme_id` |

Unmatched stays NULL. Note the lookup is keyed on the **scheme code alone** —
`rta` is not part of the join here (unlike gold.scheme, which does key on
`rta|rta_scheme_code`).

### 4.2 `transform_investor_master()`

| Column(s) | Rule |
|---|---|
| all | `drop_duplicates()` on the full row; every object column `.str.strip()` |
| `scheme_id` | from `product_code` (4.1) |
| `state` | `.str.title()` |
| `gst_state_code` | `to_numeric`; filled from `state` via `bronze.state_code` (`state_name`→`state_id`) |
| `state` | back-filled from `gst_state_code` via the reverse lookup (`combine_first`, so a resolved code wins over the raw string) |
| `account_type` | `SAV/SAVINGS→Savings, CURRENT/CUR→Current, NRE→NRE, NRO→NRO`, else original |
| `tax_status` | `I/1/INDIVIDUAL→Individual, N→N`, else original |
| `holding_nature`, `mode_of_holding_description` | `SI/SINGLE→Single, AS→Anyone Or Survivor, JO/JOINT→Joint, EO→Either Or Survivor`, else Title Case |
| `pan_no`, `joint1_pan`, `joint2_pan`, `guardian_pan` | upper-case |
| `email`, `nominee1..3_email` | lower-case |
| `mobile_no`, `phone_res`, `phone_off` | spaces and `-` removed |
| `dob`, `report_date`, `folio_date` | `to_datetime(errors="coerce")` |
| all | whitespace-only strings → `pd.NA` |
| `occupation` | applied in `load_silver()` after the transform: `SERVICE→1, BUSINESS→2, PROFESSIONAL→3, AGRICULTURE→4, STUDENT→5, RETIRED→6, HOUSEWIFE→7, OTHERS→8, PRIVATE SECTOR→9, PUBLIC SECTOR→10, SELF EMPLOYED→11, NOT APPLICABLE→41`, then cast to `Int64` (anything unmapped becomes NULL) |

### 4.3 `transform_transaction()`

| Column(s) | Rule |
|---|---|
| all | full-row `drop_duplicates()`; object columns stripped |
| `scheme_id` | from `prodcode` (4.1) |
| `state` / `gst_state_code` | same two-way `bronze.state_code` resolution as investor |
| `source_system` | upper-case (column does not exist in the table — no-op) |
| `location` | Title Case |
| `bank_name` | 15-entry canonicalisation map (`HDFCBANK→HDFC Bank`, `SBI→State Bank Of India`, …), else Title Case |
| `tax_status` | `I/1/INDIVIDUAL→Individual, N→NRI, NRI - REPATRIATION→NRI - Repatriation`, else original |
| `pan` | upper-case |
| `email` | lower-case |
| `mobile`, `rphone`, `ophone` | spaces and `-` removed |
| `trade_date`, `post_date`, `report_date`, `purdate`, `chqdate`, `sys_regn_d` | `to_datetime(dayfirst=True).dt.date` — **only `purdate` and `chqdate` actually exist**; the bronze names are `traddate`, `postdate`, `rep_date`, `sys_regn_date`, so those four are no-ops |
| `units`, `amount`, `load_amount`, `broker_percent`, `broker_commission`, `purprice`, `stamp_duty` | `to_numeric(errors="coerce")` — `load_amount`, `broker_percent`, `broker_commission` do not exist (bronze has `load`, `brokperc`, `brokcomm`) |
| all | whitespace-only → `pd.NA` |

### 4.4 `transform_sip_master()`

| Column(s) | Rule |
|---|---|
| all | full-row `drop_duplicates()`; object columns stripped |
| `scheme_id` | from `product_code` (4.1) |
| `inv_iin, inv_dp_id, inv_client_id, ecsno, umrncode, instrm_no, cheq_micr_no, request_ref_no, ft_sip_regno` | float → int → string (kills `.0`), trimmed |
| `location, investor_name, agent_name, subbroker, scheme_name, to_scheme_name, ecs_bank_name, ecs_holder_name, dp_inv_name` | Title Case |
| `pan` | upper-case |
| `zone, branch, ihno, folio, agent_code, fund_code, product_code, to_product_code, ecsno, reg_slno, inv_dp_id, inv_client_id, umrncode` | upper-case + trim |
| `plan`, `to_plan` | `REGULAR→Regular, DIRECT→Direct`, else Title Case |
| `sip_type`, `frequency`, `trtype`, `status` | Title Case |
| `sip_mode` | `AUTO-DEBIT/AUTO DEBIT→Auto Debit, NACH→NACH, ECS→ECS`, else Title Case |
| `modify_flag` | `Y→Yes, N→No` |
| `ecs_acno` | spaces removed |
| `amount`, `no_of_installments` | `to_numeric` — the silver column is `auto_amount`, so `amount` is a no-op |
| all | whitespace-only → `pd.NA` |

Several of the columns above (`investor_name`, `to_scheme_name`, `ecs_bank_name`,
`ecs_holder_name`, `sip_mode`, `frequency`, `trtype`, `agent_code`, `fund_code`,
`reg_slno`, `ihno`, `folio`) are the *file's* names, not the bronze names
(`inv_name`, `target_scheme_name`, `bank`, `ac_holder_name`, `payment_mode`,
`periodicity`, `subtrxndesc`, `sub_arn_code`, `amc_code`, `ft_sip_regno`,
`inv_iin`, `folio_no`) — those rules are inert.

### 4.5 `append_new_rows()` — Silver de-duplication

1. Requires a `flag` column; keeps only `flag = 0` rows.
2. Drops duplicates **inside the batch**, comparing every column except
   `flag, created_at, updated_at, source, scheme_id`.
3. Reads the entire existing silver table, normalises both sides
   (`normalize_for_compare`: datetimes → `%Y-%m-%d`, everything else trimmed
   string, and the same five columns dropped), builds a `|`-joined row key, and
   sets `flag = 1` on rows already present, `0` otherwise.
4. `created_at = updated_at = pd.Timestamp.now()` (**naive local time**, unlike
   bronze's `Asia/Kolkata`-aware stamp).
5. Aligns to the silver table's column order, missing → `None`, appends.

`scheme_id` is deliberately excluded from the comparison so that back-filling a
previously unmapped scheme does not resurrect the whole row as "new".

Consequence, same as bronze: silver accumulates duplicate rows carrying
`flag = 1`. Gold readers mostly ignore `flag` (see 5), so those rows *do* reach
gold for some tables.

---

## 5. Silver → Gold (`gold_loader.load_gold()`)

Strict sequence, each step wrapped in its own `try/except` that prints and
continues — a failure never aborts the run, so a broken upstream table produces
silently stale downstream tables:

`amc → scheme → scheme_nav → transactions → holdings → sip → clients → folio_nominees`

Order matters: `scheme_nav` needs `gold.scheme`; `holdings` needs `gold.clients`
(which is populated **later** in the same run — see 8); `folio_nominees` needs
`gold.holdings`.

Incremental strategy differs per table:

| Gold table | How new rows are selected | Existing rows |
|---|---|---|
| `amc` | silver `flag = 0` | skipped if `amc_code` exists |
| `scheme` | all silver rows | **updated** in place on `(rta, scheme_code)` |
| `scheme_nav` | silver `created_at > MAX(gold.scheme_nav.created_at)` | skipped on `(scheme_id, nav_date)` |
| `transactions` | silver `created_at > MAX(gold.transactions.created_at)` | no comparison at all |
| `holdings` | all silver rows | skipped on `(rta, folio_number, scheme_id)` |
| `sip` | silver `created_at > MAX(gold.sip.created_at)` | checked on natural key |
| `clients` | all silver rows | skipped on `pan` |
| `folio_nominees` | all silver rows | skipped on `(holding_id, seq)` |

### 5.1 `gold.amc` — `etl_gold_amc.py`

Source: `silver.transaction_master_new WHERE flag = 0`.

| gold.amc column | Source |
|---|---|
| `amc_code` | `amc_code`, trimmed + upper |
| `name` | `bronze.amc_master.amc_name`, joined on `amc_code` |
| `short_name` | `NULL` (hard-coded) |
| `rta` | `source` |
| `logo_url` | `NULL` |
| `status` | `NULL` |
| `arn` | `brokcode` |
| `sub_arn` | `src_brk_code` |
| `created_at` | silver `created_at` of the winning row |

Batch de-duplicated on `amc_code` (first wins), rows with empty `amc_code`
dropped, string lengths clipped (`amc_code[:20]`, `name[:255]`, `rta[:20]`,
`arn`/`sub_arn[:50]`). Insert-only: any `amc_code` already in `gold.amc` is
dropped, so `arn`/`name` are **never refreshed** once first seen.

### 5.2 `gold.scheme` — `etl_gold_scheme.py`

Sources: `silver.transaction_master_new` (`source, amc_code, prodcode, scheme,
funddesc, scheme_type, brokcode, src_brk_code`) and `silver.investor_master`
(`source, amc_code, product_code, scheme_name, fund_description, categorydesc`).
Both are reduced to one row per `(source, amc_code, scheme_code)` and
left-joined on that triple.

| gold.scheme column | Source / rule |
|---|---|
| `id` | `uuid5(SCHEME_NAMESPACE, "<rta>|<scheme_code>")` — deterministic; duplicates raise |
| `rta` | `source` |
| `scheme_code`, `rta_scheme_code` | transaction `prodcode`, else investor `product_code` |
| `scheme_name` | `funddesc` → `scheme` → investor `scheme_name` → investor `fund_description` (first non-null) |
| `category` | AMFI `scheme_category` if `amfi_code` resolved, else `scheme_type` → investor `categorydesc` |
| `plan` | AMFI `plan_type` if resolved, else regex `\b(Direct|Regular)\b` on `scheme_name` |
| `isin` | forced `NULL`, and excluded from the UPDATE list |
| `amfi_code` | `bronze.scheme_mapping.amfi_scheme_code`, keyed on `rta|rta_scheme_code` |
| `plan_type`, `option_type`, `riskometer`, `status` | `public.amfi_scheme_master` via `amfi_code` |
| `category_id`, `benchmark_id`, `expense_ratio`, `exit_load_json`, `lock_in_months` | `NULL` (app-managed) |
| `amc_id` | `bronze.amc_master.amc_id` via `amc_code` (then `amc_code` is dropped) |
| `arn` | `brokcode` (upper, `[:50]`) |
| `sub_arn` | `src_brk_code` (upper, `[:50]`) |
| `created_at` | `datetime.now()` |

De-duplicated on `(rta, scheme_code)` after sorting. Load does a per-row
`UPDATE` for keys already present (every column above except `id`, `isin`,
`created_at`) and inserts the rest. Guards raise on unexpected or `_x`/`_y`
columns.

### 5.3 `gold.scheme_nav` — `etl_gold_scheme_nav.py`

Source: `silver.transaction_master_new WHERE purprice IS NOT NULL`, further
filtered to `created_at > MAX(gold.scheme_nav.created_at)`.

| gold.scheme_nav column | Source |
|---|---|
| `scheme_id` | `gold.scheme.id`, joined `source = rta` **and** `prodcode = scheme_code` |
| `nav_date` | `traddate` |
| `nav` | `purprice` |
| `repurchase_nav` | `NULL` |
| `source` | `source` |
| `arn` | `brokcode` |
| `sub_arn` | `src_brk_code` |

Rows missing `scheme_id`, `nav_date` or `nav` are dropped; batch de-duplicated on
`(scheme_id, nav_date)` keeping the **last**; pairs already in the table are
skipped. So one NAV per scheme per trade date, taken from whichever transaction
sorted last.

### 5.4 `gold.transactions` — `etl_gold_transaction.py`

Source: `silver.transaction_master_new`, all rows on first run, then
`created_at > MAX(gold.transactions.created_at)`. `flag` is selected but **not
filtered on**.

| gold.transactions column | Source / rule |
|---|---|
| `rta`, `source` | `source` (upper) |
| `rta_txn_no` | `trxnno` (`.0` stripped) — row dropped if NULL |
| `pan` | `pan` (upper, `.0` stripped) |
| `folio_number` | `folio_no` |
| `txn_type` | derived — see below |
| `txn_type_raw` | `trxntype` |
| `txn_desc` | `trxn_nature` |
| `txn_sub_type` | `trxnsubtyp` |
| `txn_date` | `traddate` |
| `post_date` | `postdate` |
| `amount`, `units` | `amount`, `units` |
| `nav` | `purprice` |
| `load_amount` | `load` |
| `stt`, `stamp_duty` | `stt`, `stamp_duty` |
| `gst` | `igst_amount + cgst_amount + sgst_amount`, each `fillna(0)` |
| `arn` | `brokcode` |
| `sub_arn` | `src_brk_code` |
| `euin` | `euin` |
| `sip_ref` | `siptrxnno` |
| `status` | `trxnstat` |
| `scheme_id` | taken **directly from silver** — no lookup, no UUID generation |
| `scheme_code` | `prodcode` (upper) |
| `client_id`, `amc_id`, `rta_txn_id`, `arn_id`, `sip_id`, `source_file_id` | `NULL` (app-managed) |
| `created_at` | `pd.Timestamp.utcnow()` (load time, **not** the silver timestamp) |

`txn_type` comes from `classify_transaction_fast()`, which lower-cases
`trxn_nature + trxntype + td_purred` and applies these tests **in order** — later
matches overwrite earlier ones, so the last matching label wins:

`OTHER` (default) → `PURCHASE` (`purchase|fresh purchase|additional purchase`, or
`trxntype == "PUR"`) → `REDEMPTION` (`redemption|redeem`, or `trxntype == "RED"`)
→ `SIP` (`sip|systematic`) → `SWITCH_IN` → `SWITCH_OUT` → `DIVIDEND` → `STP` →
`TRANSFER_IN` → `TRANSFER_OUT`.

Because the order is fixed, a "Systematic Transfer Plan purchase" ends as `STP`,
and a SIP purchase ends as `SIP` rather than `PURCHASE`.

Lengths are clipped before insert (`rta[:10]`, `rta_txn_no[:50]`, `pan[:10]`,
`folio_number[:40]`, `txn_type[:30]`, `txn_type_raw[:40]`, `txn_desc[:120]`,
`arn`/`sub_arn`/`euin[:20]`, `sip_ref[:50]`, `status[:10]`). There is **no**
duplicate check in `load_transactions()` — the timestamp window is the only
guard.

### 5.5 `gold.holdings` — `etl_gold_holdings.py`

The extract is one SQL statement over `silver.transaction_master_new` with two
helpers:

- `transaction_scheme` — latest (`traddate DESC`) row per
  `(source, PAN-or-folio-or-brokcode)` among rows that already have a
  `scheme_id`, exposed as `mapped_scheme_id`. Join is PAN-first, falling back to
  `COALESCE(folio_no, brokcode)` when PAN is blank.
- `investor_base` — `DISTINCT ON (source, folio_no)` from
  `silver.investor_master`, contributing holding nature, nominee 1, bank, demat
  and CKYC.

| gold.holdings column | Source / rule |
|---|---|
| `id` | `uuid4()` per row (random, not deterministic) |
| `rta` | `source` (upper) |
| `pan` | `pan` (cleaned) |
| `folio_number` | `folio_no`, falling back to `scheme_folio_number` |
| `scheme_id` | `mapped_scheme_id` from the CTE |
| `units` | `units` |
| `market_value` | `amount` |
| `as_on_date` | `rep_date` |
| `folio_date` | `traddate` |
| `arn` | `brokcode` |
| `subarn` | `src_brk_code` |
| `holding_nature` | `investor_master.holding_nature` |
| `nominee_name`, `nominee_relation`, `nominee_pct` | `investor_master.nominee1_*` |
| `kyc_status` | `"Verified"` when `investor_master.ckyc_no` non-blank, else `NULL` |
| `bank_name` | `investor_master.bank_name` |
| `bank_ac_last4` | last 4 chars of `investor_master.bank_account_no` |
| `demat_flag` | `investor_master.demat_flag` |
| `client_id` | `gold.clients.user_id` via PAN |
| `amc_id` | `public.amc.id` (master DB) via AMC code |
| `arn_id` | `public.arn.id` (master DB) via `brokcode` |
| `invested_amount` | `SUM(signed_amount)` per `(source, folio, prodcode)` |
| `avg_cost_nav` | `invested_amount / units` (units 0 → NULL) |
| `purchase_date`, `first_purchase_date` | `MIN(traddate)` over purchase rows of the group |
| `current_nav` | `df["nav"]` if present — **the column does not exist in silver**, so always NULL |
| `current_value` | `units * current_nav` → always NULL |
| `nav_date` | `df["nav_date"]` if present — does not exist → NULL |
| `unrealised_gain` | `current_value - invested_amount` → always NULL |
| `xirr` | Newton solve over the group's `(traddate, signed_amount)` cashflows |
| `source_file_id` | `NULL` |
| `last_synced_at`, `created_at` | `datetime.now(timezone.utc)` |

`signed_transaction_amount()` decides the sign from
`trxntype + " " + trxn_nature`: negative for
`REDEEM|REDEMPTION|SELL|SWITCH?OUT|WITHDRAW|REVERSAL|REV`, positive for
`PURCHASE|BUY|SIP|SWITCH?IN|DIVIDEND?REINVEST|REINVEST`, untouched otherwise.
`purchase_mask()` = `PURCHASE|BUY|SIP` in either field.

Rows without `rta`, `folio_number` or `scheme_id` are dropped, then
de-duplicated on `(rta, folio_number, scheme_id)` — one holding per folio per
scheme.

`load_holdings()` additionally calls **`remove_zero_net_holdings()`**: a SQL
aggregate over silver computes, per `(rta, folio, scheme_id)`,
`ROUND(SUM(purchase amounts),2) - ROUND(SUM(switch-out amounts),2)` and every
group whose net is exactly `0` is deleted from the batch — i.e. fully
switched-out positions never reach gold. `amount` is text in silver, so the
query regex-validates each value and casts to `numeric`, treating anything
non-numeric as `0`.

Live coverage on the restored data (1 790 rows): `invested_amount` 1 790,
`amc_id` 1 771, `xirr` 789, and `current_nav` / `current_value` /
`unrealised_gain` / `client_id` / `arn_id` **0**.

### 5.6 `gold.sip` — `etl_gold_sip.py`

Source: `silver.sip_master_new`, windowed on `created_at > MAX(gold.sip.created_at)`.

| gold.sip column | Source / rule |
|---|---|
| `rta` | `source` (upper) |
| `sip_reg_no` | `ft_sip_regno` |
| `folio_number` | `folio_no` (cleaned) |
| `scheme_code` | `scheme_code` |
| `scheme_name` | `scheme_name` |
| `amc_code` | `amc_code` (upper) |
| `isin` | `NULL` |
| `amount` | `auto_amount` |
| `frequency` | `periodicity` (upper) |
| `start_date` / `end_date` | `from_date` / `to_date` |
| `next_due_date` | `NULL` |
| `sip_day` | `period_day` |
| `mandate_id` | `umrn_code` |
| `status` | `status` (upper) |
| `registered_date` | `reg_date` |
| `ceased_date` | `cease_date` |
| `scheme_id` | straight from silver |
| `amc_id` | `public.amc.id` (master DB) via `amc_code` |
| `client_id` | `gold.clients.user_id` via PAN |
| `sip_type` | `aut_trntyp`: `SIP/S→SIP`, `STP/SO/SI→STP`, `SWP/WO→SWP`, any other non-null → `OTHER` |
| `registered_installments` | `no_of_installments` |
| `completed_installments` | count of SIP transactions for `(rta, folio, scheme_code)` that are **not** bounced |
| `bounced_installments` | count of SIP transactions matching `BOUNCE|BOUNCED|FAILED|FAILURE|REJECT|REJECTED` |
| `ceased_reason` | `remarks`, else `status` — only when `ceased_date` is set or status ∈ `CEASED/CANCELLED/EXPIRED` |
| `arn` / `sub_arn` | first non-null `brokcode` / `src_brk_code` among that `(rta, folio)`'s transactions |
| `arn_id` | `public.arn.id` via ARN |
| `created_at` | `datetime.now(timezone.utc)` |

A SIP transaction is one where `siptrxnno` or `sipregslno` is non-blank, or the
concatenation of `trxntype, trxnstat, trxnmode, trxnsubtyp, trxn_nature, remarks`
contains `SIP`. `transform_sip()` raises `ValueError` if the row count changes —
no row may be dropped in transform.

### 5.7 `gold.clients` — `etl_gold_clients.py`

Source: `silver.investor_master` left-joined to the latest transaction row and
the latest SIP row for the same `(source, folio_no)` (folio compared with
`REGEXP_REPLACE(..., '\.0$', '')`, source upper-cased on both sides).

| gold.clients column | Source / rule |
|---|---|
| `pan` | `investor_master.pan_no` → transaction `pan` → SIP `pan` (first non-null) |
| `full_name` | `investor_name` |
| `phone` | `phone_res` → `phone_off` |
| `mobile`, `mobile_isd` | `mobile_no` split by `normalize_mobile()` |
| `aadhaar` | `"Y"` when `holder_1_aadhaar_info` present, else NULL |
| `email` | `email` |
| `date_of_birth` | `dob` |
| `can` | KFIN only: `commonaccno`, falling back to transaction `common_account_number` |
| `occupation` | CAMS: `occupation`; KFIN: `occupation_description` |
| `investor_type` | from CAMS `tax_status` / KFIN `statusdesc` (fallback `categorydesc`), bucketed to `HUF`, `NRI`, `TRUST`, `INDIVIDUAL`, else NULL |
| `tax_status` | `tax_status` |
| `kyc_status` | CAMS: `Verified` if `ckyc_no` present else `Not Verified`; KFIN: `Verified` if `kyc1flag` ∈ `Y/YES/1/TRUE/VERIFIED` else `Not Verified` |
| `arn` / `sub_arn` | transaction `brokcode` / `src_brk_code` (upper, `[:50]`) |
| `arn_id` | `public.arn.id` (master DB, `is_deleted = false`) via `brokcode` |
| `onboarded_at` | CAMS: `folio_date`; KFIN: transaction `traddate` |
| `source` | `source` |
| `status`, `client_label`, `whatsapp_*`, `pan_verified_at`, `marital_status`, `anniversary_date`, `blood_group`, `equity_ucc`, `user_id`, `family_id`, `family_relation`, `gender`, `risk_profile`, `rm_id`, `branch_id` | `NULL` / app-managed (`pan_verified` = `False`) |
| `created_at` | `datetime.now()` |

Rows without PAN are dropped; batch de-duplicated on `pan` keeping the **last**;
PANs already in `gold.clients` are skipped. So one client per PAN, and existing
clients are never refreshed.

### 5.8 `gold.folio_nominees` — `etl_gold_folio_nominees.py`

Source: `SELECT * FROM silver.investor_master`, joined to `gold.holdings` on
`source = rta`, `folio_no = folio_number`, `pan_no = pan` (all upper/trimmed) to
obtain `holding_id`. Investor rows with no matching holding are skipped — no
nominee row can exist without a holding.

The three nominee slots are unpivoted into rows. A slot produces a row only if
its **name** is non-blank; `seq` is assigned 1..n over the *present* nominees
(so nominee 3 becomes `seq = 2` if nominee 2 is blank).

| gold.folio_nominees column | Source / rule |
|---|---|
| `holding_id` | `gold.holdings.id` |
| `seq` | 1-based position among non-blank nominees |
| `name` | `nominee{1,2,3}_name` |
| `relationship` | `nominee{1,2,3}_relation` |
| `percentage` | `nominee{1,2,3}_percentage` (`to_numeric`) |
| `dob` | `nominee_dob` (single column shared by all slots) |
| `guardian_name` | CAMS: `nominee_guardian_name`; KFIN: `guardian_name` |
| `is_minor` | `True` if a guardian name exists, else `True` when age from `nominee_dob` < 18 |
| `id_type`, `id_no`, `address` | `NULL` |

De-duplicated on `(holding_id, seq)` keeping the last.

---

## 6. Streamlit control flow (`app.py`)

1. **Upload** — multi-file uploader, `xlsx/csv/txt`.
2. **Extract** — detects types by filename → `extract_and_push()` (bronze) →
   `create_triggers()` → if a transaction file was present,
   `load_scheme_mapping()` and a summary of newly mapped / newly queued /
   ambiguous / still-unmatched schemes → bronze previews (`read_table`, newest
   100 rows per table) including `bronze.scheme_mapping`.
3. **Scheme review panel** — lists pending rows from
   `bronze.scheme_mapping_review`, offers "approve all single-candidate", a
   per-row candidate chooser, and "promote approved to `scheme_mapping`".
   Approvals only take effect on the **next** Extract run.
4. **Transform** — `load_silver()` → `create_triggers()` → `load_gold()` →
   `create_triggers()` → silver and gold previews.

`read_table()` orders by `created_at`, else `updated_at`, else `last_synced_at`,
limits to 100 rows, and renders those three timestamps in `Asia/Kolkata`.

---

## 7. The four WBR files — current status

`files/gold/` holds four CAMS WBR reports. Read with
`pd.read_excel(dtype=str)` they contain:

| File | Rows | Columns |
|---|---|---|
| `WBR36-Brokerage summary by scheme.xls` | 141 | `product_code, product_name, upfront, afe, trailer_fee, trxn_charges, clawback, incentives` |
| `WBR36H-Brokerage summary by scheme.xls` | 11 | identical 8 columns |
| `WBR56-KYC status of Investor.xls` | 101 | 40 cols: `brok_dlr_code, folio, inv_name, tax_no, jname1, jointpan1, jname2, jointpan2, guardian, guardian_panno, address1..3, city, pincode, phone_res, phone_off, mobile_no, email, location, state, fax_res, fax_off, fh_kyc, gu_kyc, jh1_kyc, jh2_kyc, brok_name, rep_from_date, rep_to_date, rep_date, amc_code, fh_kyc_desc, gu_kyc_desc, jh1_kyc_desc, jh2_kyc_desc, fh_g_aadharlink, jh1_aadharlink, jh2_aadharlink, country` |
| `WBR68-Invalid EUIN Report.xls` | 9 | 31 cols: `amc_code, arn_code, appln_no, folio_no, inv_name, inv_pan, trade_date, sch_code, sch_name, trxn_no, trxn_type, trxn_desc, amount, subbrokcod, location, euin, euin_valid, email, posted_date, cons_code, usertxn_no, alt_folio, folio, subbrok_arn, sys_reg_dt, reason, user_code, sip_regn_date, auto_trxn_no, folio_old, scheme_folio_number` |

### 7.1 WBR36 / WBR36H — wired in

`WBR36` and `WBR36H` now travel the full Bronze → Silver → Gold path as
`brokerage_summary`. Both reports share one table per layer; `source` carries
the RTA and `report_type` carries the report, because the two files repeat 10
of the same product codes.

| Piece | Where |
|---|---|
| Filename registry + column aliases | `mapping_wbr.py` (`BROKERAGE_FILE_PATTERNS`, `BROKERAGE_SUMMARY_MAPPING`) |
| Bronze loader | `etl_brokerage_summary.py` → `bronze.brokerage_summary` |
| Silver transform | `transform.transform_brokerage_summary()` → `silver.brokerage_summary` |
| Gold ETL | `etl_gold_brokerage_summary.py` → `gold.brokerage_summary` |
| DDL | `sql_scripts/brokerage_summary.sql` |

Notes specific to this feed:

- The mapping lives in `mapping_wbr.py`, **not** `mapping.py` — the three
  transactional feeds must not change when a WBR report is added. Adding
  KFINTECH is a filename pattern plus alias spellings in that one file.
- `app.py` and `raw_ingestion.py` both call
  `mapping_wbr.identify_brokerage_file()`, so for this feed the two filename
  rules cannot drift apart (gap 13 stands for the other three feeds).
- The uploader now accepts `.xls` and `.dbf` as well as `.xlsx`, `.csv`, `.txt`.
- `product_code` (`D104`, `FTI970`) is already
  `bronze.scheme_mapping.rta_scheme_code`, so `scheme_id` resolves through the
  existing `map_scheme_id()` with no report-specific rule — 152 of 152 rows
  matched on the sample files.
- Money columns are `TEXT` in bronze and `NUMERIC(20,8)` from silver on;
  `round_decimal_columns()` is deliberately skipped, because it rounds to 4
  decimals and the RTA reports to 8.
- Gold keys on `rta | report_type | scheme_code | report_from_date |
  report_to_date`, hashed into a deterministic `uuid5` `id`, and **updates** the
  row it already owns. Re-running the whole chain is idempotent at all three
  layers (verified: bronze all `flag = 1`, silver all `flag = 1`, gold 152
  updates / 0 inserts).
- Neither CAMS file carries a reporting period, so `report_from_date` /
  `report_to_date` / `rep_date` are NULL for CAMS and are part of the key as
  NULLs. Two different months of WBR36 are therefore distinguished only by
  their amounts — see gap 14.
- CAMS carries no broker column either, so `arn` / `sub_arn` fall back to the
  ARN already recorded against that scheme in `gold.scheme`.

### 7.2 WBR56 / WBR68 — still unconsumed

**These are inputs, and nothing in the pipeline consumes them yet.**

- `extract_and_push()` has no branch matching `wbr56` or `wbr68`, so such a
  file falls through to `print("Unknown file type")` and is discarded without
  error.
- No `bronze.*` table exists whose shape matches KYC-status or invalid-EUIN
  data.

Wiring them in requires, per file: an entry in a filename registry, a column
dictionary, a bronze loader with its own `DATE_COLUMNS` /
`IDENTIFIER_COLUMNS` / `flag` logic, a bronze table DDL, a `transform_*` +
`append_new_rows` pass in `transform.py` with a silver table, and a gold
`extract/transform/load` trio registered in `gold_loader.py` — i.e. the same
shape `brokerage_summary` now has.

---

## 8. Known gaps in the current flow

Ordered roughly by impact.

1. **`gold.holdings` valuation columns are always NULL.** `current_nav`,
   `nav_date`, `current_value` and `unrealised_gain` are read from
   `df["nav"]` / `df["nav_date"]`, which do not exist in
   `silver.transaction_master_new` (the NAV lives in `purprice`, and
   `gold.scheme_nav` already holds one NAV per scheme per date). Verified on the
   restored data: 0 of 1 790 rows populated.
2. **`gold.holdings.client_id` and `arn_id` are always NULL** here too, for two
   independent reasons. `client_id` maps PAN → `gold.clients.user_id`, but
   (a) `gold_loader` runs `holdings` **before** `clients`, and (b) `user_id` is
   itself never populated by `etl_gold_clients.py` — it is one of the
   app-managed `NULL` columns (0 of 593 rows have it). So the lookup cannot
   succeed even if the order were fixed. `arn_id` maps `brokcode` →
   `public.arn.id` in the master DB, which holds only 3 ARN rows, none matching
   the broker codes in this data. The same `user_id` problem makes
   `gold.sip.client_id` NULL.
3. **Silver `flag = 1` rows reach gold.** Only `gold.amc` filters on
   `flag = 0`. `scheme`, `scheme_nav`, `transactions`, `holdings`, `sip`,
   `clients` and `folio_nominees` read silver without the filter, relying instead
   on `created_at` windows or natural-key skips.
4. **Timestamp windows mix clocks.** Bronze stamps `Asia/Kolkata`-aware,
   silver stamps naive local (`pd.Timestamp.now()`), gold stamps UTC
   (`utcnow()` / `datetime.now(timezone.utc)`). `scheme_nav`, `transactions` and
   `sip` then compare a gold **UTC** watermark against silver's naive **IST**
   `created_at`, stripping tz info instead of converting. On the restored data
   the gap is visible: `MAX(silver.transaction_master_new.created_at)` is
   `2026-08-18 13:23:55` while `MAX(gold.transactions.created_at)` is
   `2026-08-18 07:58:46` — 5½ hours behind. Every silver row therefore looks
   newer than the watermark, so the next `load_gold()` re-selects rows that are
   already in gold. For `scheme_nav` and `sip` the natural-key skip absorbs it;
   `gold.transactions` has no such guard (see 12), so it would double-insert.
5. **Insert-only gold tables never refresh.** `amc`, `clients` and `holdings`
   skip any row whose natural key already exists, so corrected names, ARNs, KYC
   statuses or unit balances never propagate. Only `gold.scheme` does an UPDATE.
6. **`load_gold()` swallows every exception.** Each entity is wrapped in
   `try/except: print(e)`. A failed step leaves the previous table contents in
   place and the run still reports success.
7. **Dead transform rules.** In `transform_transaction()` the date list
   (`trade_date`, `post_date`, `report_date`, `sys_regn_d`) and the numeric list
   (`load_amount`, `broker_percent`, `broker_commission`) name columns that do
   not exist; the real names are `traddate/postdate/rep_date/sys_regn_date` and
   `load/brokperc/brokcomm`. `transform_sip_master()` has a dozen similar
   file-name-vs-bronze-name mismatches (4.4). Those columns therefore stay as
   text in silver and are only coerced later, in gold.
8. **`map_scheme_id()` ignores RTA.** Silver resolves `scheme_id` from
   `rta_scheme_code` alone, while `gold.scheme` keys on `rta|rta_scheme_code`.
   A scheme code reused by both RTAs would map to whichever row survived
   `drop_duplicates(subset=["rta_scheme_code"])`.
9. **`INVESTOR_MASTER_MAPPING` has duplicate keys.** `occupation` is defined
   twice; the first definition and the `occupation_description` entry beside it
   are shadowed by the later one.
10. **Mixed-case aliases in `mapping.py` can never match**, because headers are
    lower-cased and de-spaced before lookup (`Address #1` vs `address_1`).
11. **Whole-table reads for de-duplication.** Both bronze loaders and
    `append_new_rows()` `SELECT *` the entire target table on every run to build
    the row-key set; at 128 k transaction rows this is already the slowest part
    of a load and grows linearly.
12. **`gold.transactions` has no duplicate guard at all** — if the
    `created_at` window is ever wrong (see 4), rows are inserted twice.
13. **`app.py` and `raw_ingestion.py` classify filenames separately.** The two
    lists must be kept in sync by hand; `app.py` also omits the `.dbf` variants
    that `extract_and_push()` accepts. Does not apply to `brokerage_summary`,
    which routes both sides through
    `mapping_wbr.identify_brokerage_file()`.
14. **CAMS WBR36 / WBR36H carry no reporting period.** The gold natural key
    includes `report_from_date` / `report_to_date`, which are NULL for CAMS, so
    two periods of the same report collapse onto one key unless their amounts
    differ. If CAMS can be asked for a period-stamped export, the aliases are
    already in `BROKERAGE_SUMMARY_MAPPING`; otherwise the period has to be
    supplied at upload time.

# Data Mapping Guide — CAMS and KFintech files to Bronze, Silver and Gold

**Who this is for:** anyone who works with the CAMS and KFintech files in Excel and wants to know exactly where every column ends up in the database, and what happens to it on the way. No programming knowledge is assumed.

**Last checked against the code:** 17 August 2026.

---

## 1. How to read this document

Think of the database as three shelves, one behind the other.

| Shelf | Name | What is on it | Excel comparison |
|---|---|---|---|
| Front | **Bronze** | The uploaded file, copied in almost exactly as received. Only the column *names* are tidied. | The raw sheet you were emailed, pasted into a workbook |
| Middle | **Silver** | The same rows, cleaned: dates turned into real dates, "SAV" turned into "Savings", stray spaces removed, blanks turned into true blanks | The same sheet after you run Find & Replace and format the date column |
| Back | **Gold** | Rebuilt into business tables — one table for clients, one for schemes, one for holdings, one for transactions, one for SIPs, and so on. Columns are renamed to business language. | The pivot-ready master sheets you build *from* the cleaned sheet |

Two rules that apply everywhere:

1. **Nothing is thrown away going from Bronze to Silver.** Same number of columns, same rows (minus exact duplicates). Only values get tidied.
2. **Gold is a rebuild, not a copy.** Gold has fewer columns, different names, and some columns that no file supplies (they sit empty and wait for the application to fill them).

Throughout this document:

- **"RTA"** means the registrar — CAMS or KFintech. The system stores this as the word `CAMS` or `KFIN`.
- **"NULL"** means a genuinely empty cell (not a blank space, not the text "nan").
- **"Natural key"** means the column or combination of columns that identifies a row uniquely — like using PAN to identify a client.

---

## 2. The whole journey on one page

```
   You upload files in the web app
                │
                ▼
   ┌────────────────────────────────────────────────┐
   │  Step 1 — the app looks at the FILE NAME       │
   │  and decides what kind of file it is           │
   └────────────────────────────────────────────────┘
                │
    ┌───────────┼───────────┬──────────────┐
    ▼           ▼           ▼              ▼
 Transactions  Investors   SIPs        Unknown → ignored
    │           │           │
    ▼           ▼           ▼
 ┌──────────────────────────────────────┐
 │  BRONZE  (3 tables)                  │
 │  bronze.transaction_master_new       │
 │  bronze.investor_master              │
 │  bronze.sip_master_new               │
 └──────────────────────────────────────┘
                │  only rows marked flag = 0 (i.e. not already seen)
                ▼
 ┌──────────────────────────────────────┐
 │  SILVER  (same 3 tables, cleaned)    │
 │  silver.transaction_master_new       │
 │  silver.investor_master              │
 │  silver.sip_master_new               │
 └──────────────────────────────────────┘
                │
                ▼
 ┌───────────────────────────────────────────────────────────┐
 │  GOLD — business tables                                   │
 │  gold.amc          gold.scheme       gold.scheme_nav      │
 │  gold.clients      gold.holdings     gold.transactions    │
 │  gold.sip          gold.folio_nominees                    │
 ├───────────────────────────────────────────────────────────┤
 │  GOLD — report tables (used to regenerate CAMS WBR files) │
 │  gold.brokerage_by_scheme   (WBR36 / WBR36H)              │
 │  gold.investor_kyc_status   (WBR56)                       │
 │  gold.invalid_euin          (WBR68)                       │
 └───────────────────────────────────────────────────────────┘
                │
                ▼
        Excel / CSV report files written back out
```

The order the app runs things in: upload → Bronze → `load_silver()` → `load_gold()` → `load_wbr_gold()` → export the WBR report files.

---

## 3. Step 1 — which files the system accepts

The system decides what a file contains **purely from its file name**. It does not look inside first.

| If the file name… | It is treated as | Goes to Bronze table |
|---|---|---|
| ends with `r2.csv` | CAMS transactions | `bronze.transaction_master_new` |
| ends with `r9.csv` | CAMS investors / folios | `bronze.investor_master` |
| ends with `r49.csv` | CAMS SIP registrations | `bronze.sip_master_new` |
| contains `mfsd201` | KFintech transactions | `bronze.transaction_master_new` |
| contains `mfsd211` | KFintech investors / folios | `bronze.investor_master` |
| contains `mfsd243` | KFintech SIP registrations | `bronze.sip_master_new` |
| anything else | *Nothing.* The file is skipped and a message is printed | — |

**Practical consequences**

- Renaming a file changes what happens to it. A CAMS R2 file saved as `transactions_final.csv` will be silently ignored.
- `.csv`, `.txt` and Excel files are all readable. For text files the system works out the separator itself by looking at the first line: tab, then comma, then semicolon.
- Several files of the same type can be uploaded at once; they are stacked on top of each other before loading.
- CAMS files and KFintech files can be uploaded in the same batch. They end up in the same Bronze table, told apart by a `source` column holding `CAMS` or `KFIN`.

**Column-name tidy-up applied to every file, before any mapping happens:**

1. Leading and trailing spaces removed
2. Surrounding single and double quotes removed
3. Converted to lower case
4. Spaces, hyphens and slashes replaced with underscore `_`
5. `#` removed
6. Completely blank column headers dropped
7. If the same column name appears twice, only the first is kept

So a header written `Folio No` in the file becomes `folio_no` internally, and ` 'PAN' ` becomes `pan`.

**Value tidy-up applied to every cell:** single and double quotes removed, spaces trimmed from both ends, and the literal texts `nan`, `None`, `<NA>` turned into blanks.

---

## 4. Bronze layer — the three tables, column by column

### How to read the mapping tables below

The **"File column name(s) accepted"** column lists the header names the system looks for, **in order**. It takes the first one it finds in your file and ignores the rest. If none of them are present, the Bronze column is left empty for that file — it is never an error.

Remember the comparison happens *after* the tidy-up above, so `FOLIO_NO`, `Folio_No` and `folio_no` are all the same thing to the system.

---

### 4.1 `bronze.investor_master` — one row per folio (from CAMS R9 / KFIN MFSD211)

| Bronze column | File column name(s) accepted, in priority order | What it means |
|---|---|---|
| `source` | *(set by the system)* | `CAMS` or `KFIN` |
| **Core identifiers** | | |
| `folio_no` | `foliochk`, `folio`, `FOLIO`, `FOLIO_NO` | Folio number |
| `investor_name` | `inv_name`, `INV_NAME`, `investor_name` | First holder's name |
| `joint_name_1` | `jnt_name1`, `jtname1`, `JOINT_NAME_1` | Second holder |
| `joint_name_2` | `jnt_name2`, `jtname2`, `JOINT_NAME_2` | Third holder |
| **Address** | | |
| `address1` | `address1`, `add1` | Address line 1 |
| `address2` | `address2`, `add2` | Address line 2 |
| `address3` | `address3`, `add3` | Address line 3 |
| `city` | `city` | City |
| `state` | `state` | State |
| `country` | `country` | Country |
| `pincode` | `pincode`, `pin` | PIN code |
| **Personal** | | |
| `dob` | `dob`, `inv_dob` | Date of birth |
| `mobile_no` | `mobile_no`, `mobile` | Mobile number |
| `email` | `email` | Email |
| `phone_res` | `phone_res`, `rphone` | Residence phone |
| `phone_off` | `phone_off`, `ophone` | Office phone |
| **Tax and PAN** | | |
| `tax_status` | `tax_status`, `status` | Tax status code |
| `holding_nature` | `holding_nature` | Single / Joint / Either or Survivor |
| `pan_no` | `pan_no`, `pan` | First holder PAN |
| `joint1_pan` | `joint1_pan` | Second holder PAN |
| `joint2_pan` | `joint2_pan` | Third holder PAN |
| `guardian_pan` | `guardian_pan`, `guard_pan`, `pangno` | Guardian PAN |
| **Bank** | | |
| `bank_name` | `bank_name`, `bname` | Bank name |
| `bank_account_no` | `bank_account_no`, `bnkacno` | Account number |
| `account_type` | `account_type`, `bnkactype` | Savings / Current / NRE / NRO |
| `branch` | `branch` | Bank branch |
| `ifsc_code` | `ifsc_code` | IFSC |
| `bank_address1` | `bank_address1`, `badd1` | Bank address line 1 |
| `bank_address2` | `bank_address2`, `badd2` | Bank address line 2 |
| `bank_address3` | `bank_address3`, `badd3` | Bank address line 3 |
| `bank_city` | `bank_city`, `bcity` | Bank city |
| `bank_state` | `bank_state` | Bank state |
| `bank_country` | `bank_country` | Bank country |
| `bank_phone` | `bank_phone`, `bphone` | Bank phone |
| `b_pincode` | `b_pincode`, `bpin` | Bank PIN code |
| **Nominee 1** | | |
| `nominee1_name` | `nominee1_name`, `nom_name` | Nominee 1 name |
| `nominee1_relation` | `nominee1_relation`, `relation` | Relationship |
| `nominee1_address1` | `nominee1_address1`, `nom_addr1` | Address line 1 |
| `nominee1_address2` | `nominee1_address2`, `nom_addr2` | Address line 2 |
| `nominee1_address3` | `nominee1_address3`, `nom_addr3` | Address line 3 |
| `nominee1_city` | `nominee1_city`, `nom_city` | City |
| `nominee1_state` | `nominee1_state`, `nom_state` | State |
| `nominee1_pincode` | `nominee1_pincode`, `nom_pincode` | PIN code |
| `nominee1_phone` | `nominee1_phone`, `nom_ph_off`, `nom_ph_res` | Phone |
| `nominee1_email` | `nominee1_email`, `nom_email` | Email |
| `nominee1_percentage` | `nominee1_percentage`, `nom_percentage` | Share % |
| **Nominee 2** | | |
| `nominee2_name` | `nominee2_name`, `nom2_name` | Nominee 2 name |
| `nominee2_relation` | `nominee2_relation`, `nom2_relation` | Relationship |
| `nominee2_address1` | `nominee2_address1`, `nom2_addr1` | Address line 1 |
| `nominee2_address2` | `nominee2_address2`, `nom2_addr2` | Address line 2 |
| `nominee2_address3` | `nominee2_address3`, `nom2_addr3` | Address line 3 |
| `nominee2_city` | `nominee2_city`, `nom2_city` | City |
| `nominee2_state` | `nominee2_state`, `nom2_state` | State |
| `nominee2_pincode` | `nominee2_pincode`, `nom2_pincode` | PIN code |
| `nominee2_phone` | `nominee2_phone`, `nom2_ph_off`, `nom2_ph_res` | Phone |
| `nominee2_email` | `nominee2_email`, `nom2_email` | Email |
| `nominee2_percentage` | `nominee2_percentage`, `nom2_percentage` | Share % |
| **Nominee 3** | | |
| `nominee3_name` | `nominee3_name`, `nom3_name` | Nominee 3 name |
| `nominee3_relation` | `nominee3_relation`, `nom3_relation` | Relationship |
| `nominee3_address1` | `nominee3_address1`, `nom3_addr1` | Address line 1 |
| `nominee3_address2` | `nominee3_address2`, `nom3_addr2` | Address line 2 |
| `nominee3_address3` | `nominee3_address3`, `nom3_addr3` | Address line 3 |
| `nominee3_city` | `nominee3_city`, `nom3_city` | City |
| `nominee3_state` | `nominee3_state`, `nom3_state` | State |
| `nominee3_pincode` | `nominee3_pincode`, `nom3_pincode` | PIN code |
| `nominee3_phone` | `nominee3_phone`, `nom3_ph_off`, `nom3_ph_res` | Phone |
| `nominee3_email` | `nominee3_email`, `nom3_email` | Email |
| `nominee3_percentage` | `nominee3_percentage`, `nom3_percentage` | Share % |
| `nominee_opt_out_flag` | `nominee_opt_out_flag` | Investor opted out of nomination |
| `nominee_dob` | `nominee_dob` | Nominee date of birth |
| `nominee_guardian_name` | `nominee_guardian_name` | Nominee's guardian |
| **KYC and distributor** | | |
| `broker_code` | `broker_code`, `brokcode`, `td_agent`, `td_broker` | ARN / distributor code |
| `brokcode` | `brokcode`, `broker_code` | Same value, kept twice |
| `subbroker` | `subbroker`, `subbrok` | Sub-broker |
| `dp_id` | `dp_id` | Depository participant ID |
| `demat_flag` | `demat_flag`, `demat`, `Demat Folio flag` ⚠️ | Whether the folio is in demat form |
| `ckyc_no` | `ckyc_no`, `fh_ckyc_no`, `CKYC NO` | First holder CKYC number |
| `jh1_ckyc` | `jh1_ckyc` | Joint holder 1 CKYC |
| `jh2_ckyc` | `jh2_ckyc` | Joint holder 2 CKYC |
| `guardian_ckyc_no` | `guardian_ckyc_no`, `g_ckyc_no` | Guardian CKYC |
| `g_ckyc_no` | `g_ckyc_no` | Same value, kept twice |
| `guardian_name` | `guardian_name`, `guardian` | Guardian name |
| `kyc1flag` | `kyc1flag` | KYC status, first holder (KFintech) |
| `kyc2flag` | `kyc2flag` | KYC status, joint holder 1 (KFintech) |
| `kyc3flag` | `kyc3flag` | KYC status, joint holder 2 (KFintech) |
| `kycgflag` | `kycgflag` | KYC status, guardian (KFintech) |
| **Aadhaar** | | |
| `holder_1_aadhaar_info` | `holder_1_aadhaar_info` | Aadhaar link status, holder 1 |
| `holder_2_aadhaar_info` | `holder_2_aadhaar_info` | Aadhaar link status, holder 2 |
| `holder_3_aadhaar_info` | `holder_3_aadhaar_info` | Aadhaar link status, holder 3 |
| `guardian_aadhaar_info` | `guardian_aadhaar_info` | Aadhaar link status, guardian |
| **Product / scheme** | | |
| `product_code` | `product_code`, `product`, `prod` | Scheme code as the RTA writes it |
| `product` | `product`, `prod` | Same value, kept twice |
| `scheme_name` | `scheme_name`, `scheme`, `sch_name` | Scheme name |
| `fund` | `fund`, `td_fund` | Fund code |
| `fund_description` | `fund_description` | Fund name |
| `amc_code` | `amc_code` | AMC code |
| `closing_balance` | `closing_balance`, `clos_bal` | Units held |
| `rupee_balance` | `rupee_balance`, `rupee_bal` | Value held |
| `rupee_bal` | `rupee_bal` | Same value, kept twice |
| `reinv_flag` | `reinv_flag`, `reinvest_f` | Dividend reinvestment flag |
| `dividend_option` | `dividend_option`, `divopt` | Payout or reinvest |
| **Dates and reporting** | | |
| `report_date` | `report_date`, `rep_date`, `Report Date` | Date the report covers |
| `rep_date` | `rep_date` | Same value, kept twice |
| `report_time` | `report_time`, `time1` | Time on the report |
| `folio_date` | `folio_date` | Folio opening date |
| `lastupdateddate` | `lastupdateddate` | Last change at the RTA |
| `jh1_dob` | `jh1_dob` | Joint holder 1 date of birth |
| `jh2_dob` | `jh2_dob` | Joint holder 2 date of birth |
| `guardian_dob` | `guardian_dob` | Guardian date of birth |
| **Investor detail** | | |
| `investor_id` | `investor_id`, `invid` | RTA's internal investor ID |
| `client_id` | `client_id` | Depository client ID |
| `inv_iin` | `inv_iin` | Investor Identification Number |
| `uin_no` | `uin_no` | UIN |
| `mapin_id` | `mapin_id` | MAPIN |
| `commonaccno` | `commonaccno` | Common account number |
| `tpin` | `tpin` | T-PIN |
| `f_name` | `f_name` | Father's name |
| `m_name` | `m_name` | Mother's name |
| `pan2` | `pan2` | Additional PAN |
| `pan3` | `pan3` | Additional PAN |
| `occupation` | `occupation`, `occpn`, `occ_code` | Occupation |
| `occupation_description` | `occupation_description` | Occupation in words |
| `occ_code` | `occ_code`, `occpn` | Same value, kept twice |
| `category` | `category` | Investor category code |
| `categorydesc` | `categorydesc` | Category in words |
| `statusdesc` | `statusdesc` | Status in words |
| `mode_of_holding_description` | `mode_of_holding_description`, `holding_nature` | Holding mode in words |
| `gst_state_code` | `gst_state_code`, `gst_state_` | Numeric state code |
| `folio_old` | `folio_old`, `old_folio` | Previous folio number |
| `scheme_folio_number` | `scheme_folio_number`, `scheme_fol` | Folio number at scheme level |
| `tpa_linked` | `tpa_linked` | Third-party administrator link |
| **Extra contact columns** | | |
| `phone_res1` | `phone_res1`, `rphone1` | Residence phone 1 |
| `phone_res2` | `phone_res2`, `rphone2` | Residence phone 2 |
| `phone_off1` | `phone_off1`, `ophone1` | Office phone 1 |
| `phone_off2` | `phone_off2`, `ophone2` | Office phone 2 |
| `fax_residence` | `fax_residence`, `fax` | Residence fax |
| `fax_office` | `fax_office`, `faxoff` | Office fax |
| `investors_resi_faxno` | `investors_resi_faxno`, `fax` | Residence fax (duplicate) |
| `nom_ph_off` | `nom_ph_off` | Nominee 1 office phone |
| `nom2_ph_off` | `nom2_ph_off` | Nominee 2 office phone |
| `nom3_ph_off` | `nom3_ph_off` | Nominee 3 office phone |
| `joint_holder_1st_resi_phone_no` | *(same name)* | Joint holder 1 residence phone |
| `joint_holder_2nd_resi_phone_no` | *(same name)* | Joint holder 2 residence phone |
| `joint_holder_1_contact_number` | *(same name)* | Joint holder 1 contact |
| `joint_holder_2_contact_number` | *(same name)* | Joint holder 2 contact |
| `joint_holder_1_email_id` | *(same name)* | Joint holder 1 email |
| `joint_holder_2_email_id` | *(same name)* | Joint holder 2 email |
| `emailconcern` | `emailconcern` | Whose email is on record |
| `emailrelationship` | `emailrelationship` | Relationship of the email owner |
| `mobilerelationship` | `mobilerelationship` | Relationship of the mobile owner |
| **System columns** | | |
| `flag` | *(set by the system)* | `0` = new row, `1` = identical row already in Bronze |
| `created_at` | *(set by the system)* | When the row was loaded (India time) |
| `updated_at` | *(set by the system)* | Same as above on load |

⚠️ = see section 8, item 1. This heading never matches, because of how the name comparison works. Verified against the real KFintech MFSD211 file: `Demat Folio flag` is read and dropped, so `demat_flag` is empty for every KFintech folio. Column-by-column detail for all six files is in **document-mapping-detailed.md**.

**Special cleaning at Bronze for this table:** the following columns have a trailing `.0` stripped off (Excel turns `1234567` into `1234567.0` when it treats a number as decimal) and are then blanked if empty — all folio columns, all PIN code columns, bank account number, and every phone / mobile column.

---

### 4.2 `bronze.transaction_master_new` — one row per transaction (from CAMS R2 / KFIN MFSD201)

In this table the **first** accepted name is normally the CAMS spelling and the **second** is the KFintech spelling.

| Bronze column | File column name(s) accepted (CAMS first, KFIN second) | What it means |
|---|---|---|
| `source` | *(set by the system)* | `CAMS` or `KFIN` |
| **Core** | | |
| `amc_code` | `amc_code`, `fmcode` | AMC code |
| `folio_no` | `folio_no`, `td_acno` | Folio number |
| `prodcode` | `prodcode`, `smcode` | Scheme code |
| `scheme` | `scheme`, `schpln` | Scheme name |
| `inv_name` | `inv_name`, `invname` | Investor name |
| **The transaction itself** | | |
| `trxntype` | `trxntype`, `td_trtype` | Transaction type code |
| `trxnno` | `trxnno`, `td_trno` | Transaction number — **the unique reference** |
| `trxnmode` | `trxnmode`, `trnmode` | Physical / electronic |
| `trxnstat` | `trxnstat`, `trnstat` | Status |
| `trxnsubtyp` | `trxnsubtyp`, `trnsub` | Sub-type |
| `trxn_nature` | `trxn_nature`, `trdesc` | Transaction described in words |
| `trxn_type_flag` | `trxn_type_flag` | CAMS marker saying purchase / redemption / switch |
| `td_purred` | `td_purred` | KFintech marker: `P` purchase, `R` redemption, `D` dividend |
| `trflag` | `trflag` | Transaction flag |
| `application_no` | `application_no`, `td_appno` | Application number |
| `usercode` | `usercode`, `td_agent` | User / agent code |
| `usrtrxno` | `usrtrxno`, `unqno` | User's own transaction number |
| **Dates** | | |
| `traddate` | `traddate`, `td_trdt` | Trade date |
| `postdate` | `postdate`, `td_prdt` | Posting date |
| `time1` | `time1`, `crtime` | Time |
| `crdate` | `crdate` | Creation date |
| `purdate` | `purdate` | Purchase date |
| `sfunddt` | `sfunddt` | Fund settlement date |
| `chqdate` | `chqdate` | Cheque date |
| `ca_initiated_date` | `ca_initiated_date` | Corporate action date |
| `ticob_posted_date` | `ticob_posted_date` | Transfer-in posting date |
| **Money and units** | | |
| `purprice` | `purprice`, `td_nav` | NAV at which the deal was done |
| `units` | `units`, `td_units` | Units |
| `amount` | `amount`, `td_amt` | Amount |
| `puramt` | `puramt` | Purchase amount |
| `purunits` | `purunits` | Purchase units |
| `load` | `load`, `load1` | Load charged |
| `loadper` | `loadper` | Load percentage |
| `stt` | `stt` | Securities transaction tax |
| `stamp_duty` | `stamp_duty` | Stamp duty |
| `eligib_amt` | `eligib_amt` | Eligible amount |
| `trxn_charges` | `trxn_charges`, `trcharges` | Transaction charges |
| **Tax** | | |
| `tax` | `tax` | Tax |
| `total_tax` | `total_tax` | Total tax |
| `te_15h` | `te_15h` | Form 15H marker |
| `tax_status` | `tax_status` | Tax status |
| `gst_state_code` | `gst_state_code` | State code for GST |
| `igst_amount` | `igst_amount` | IGST |
| `cgst_amount` | `cgst_amount` | CGST |
| `sgst_amount` | `sgst_amount` | SGST |
| **Distributor** | | |
| `brokcode` | `brokcode`, `td_broker` | ARN of the distributor |
| `subbrok` | `subbrok`, `td_branch` | Sub-broker |
| `sub_brk_arn` | `sub_brk_arn` | Sub-broker's ARN |
| `brokperc` | `brokperc`, `brokper` | Brokerage percentage |
| `brokcomm` | `brokcomm` | Brokerage commission amount |
| `src_brk_code` | `src_brk_code` | Source broker |
| `mult_brok` | `mult_brok` | Multiple broker marker |
| `branchcode` | `branchcode` | Branch code |
| `euin` | `euin` | Employee Unique Identification Number |
| `euin_valid` | `euin_valid` | Whether that EUIN was valid (`Y` / `N` / `F`) |
| `euin_opted` | `euin_opted` | Whether EUIN was opted for |
| `ter_location` | `ter_location` | Terminal location code |
| `location` | `location` | Location / city |
| `ihno` | `ihno` | In-house number |
| `inwardno` | `inwardno` | Inward number |
| **Bank** | | |
| `chqno` | `chqno` | Cheque number |
| `bank_name` | `bank_name`, `chqbank` | Bank |
| `ac_no` | `ac_no` | Account number |
| `micr_no` | `micr_no` | MICR code |
| **Investor** | | |
| `pan` | `pan`, `pan1` | PAN |
| `inv_iin` | `inv_iin` | Investor Identification Number |
| `invid` | `invid` | RTA investor ID |
| `guardpanno` | `guardpanno` | Guardian PAN |
| `invstate` | `invstate` | Investor state |
| **Folio variants** | | |
| `altfolio` | `altfolio` | Alternate folio |
| `old_folio` | `old_folio` | Old folio |
| `folio_old` | `folio_old` | Old folio (second spelling) |
| `scheme_folio_number` | `scheme_folio_number` | Folio at scheme level |
| **Fund** | | |
| `td_fund` | `td_fund` | Fund code |
| `funddesc` | `funddesc` | Fund name |
| `scheme_type` | `scheme_type` | Equity / debt / hybrid etc. |
| `targ_src_scheme` | `targ_src_scheme` | Target or source scheme on a switch |
| **SIP** | | |
| `sys_regn_date` | `sys_regn_date`, `sipregdt` | SIP registration date |
| `sipregslno` | `sipregslno` | SIP registration serial |
| `siptrxnno` | `siptrxnno` | SIP transaction number |
| **Exchange / electronic** | | |
| `exchange_flag` | `exchange_flag`, `electrxnflag` | Came through an exchange |
| `exchorgtrtype` | `exchorgtrtype` | Original exchange transaction type |
| `exch_dc_flag` | `exch_dc_flag` | Demat / physical marker |
| `td_pop` | `td_pop` | Point of purchase |
| `td_ptrno` | `td_ptrno` | Partner transaction number |
| `isctrno` | `isctrno` | ISC transaction number |
| `cleared` | `cleared` | Cleared marker |
| **CAMS-specific** | | |
| `scanrefno` | `scanrefno` | Scan reference |
| `ticob_trtype` | `ticob_trtype` | Transfer-in type |
| `ticob_trno` | `ticob_trno` | Transfer-in number |
| `dp_id` | `dp_id` | Depository participant |
| `src_of_txn` | `src_of_txn` | Where the transaction came from |
| `trxn_suffix` | `trxn_suffix` | Transaction suffix |
| `reversal_code` | `reversal_code` | Reversal code |
| `rev_remark` | `rev_remark` | Reversal remark |
| `original_trxnno` | `original_trxnno` | Original transaction being reversed |
| `amc_ref_no` | `amc_ref_no` | AMC's reference |
| `request_ref_no` | `request_ref_no` | Request reference |
| `transmission_flag` | `transmission_flag` | Transmission marker |
| **Other** | | |
| `divopt` | `divopt` | Dividend option |
| `divper` | `divper` | Dividend percentage |
| `reinvest_flag` | `reinvest_flag` | Reinvestment marker |
| `remarks` | `remarks`, `nctremarks` | Remarks |
| `swflag` | `swflag` | Switch flag |
| `seq_no` | `seq_no` | Sequence number |
| **System columns** | | |
| `flag`, `created_at`, `updated_at` | *(set by the system)* | As described for investor master |

**Special cleaning at Bronze:** trailing `.0` removed from folio number, transaction number, user transaction number, application number, scheme folio number, both old-folio columns, MICR, account number, DP ID, transfer-in number, SIP transaction number, AMC reference and request reference.

**Note on duplicate removal:** unlike the other two tables, the "remove exact duplicate rows" step is switched off (commented out) for transactions. Only the `flag` marker distinguishes repeats.

---

### 4.3 `bronze.sip_master_new` — one row per SIP registration (from CAMS R49 / KFIN MFSD243)

This table has a genuine CAMS column and a genuine KFintech column for most fields, so both are shown separately.

| Bronze column | CAMS R49 header | KFintech MFSD243 header | What it means |
|---|---|---|---|
| `source` | *(set by system)* | *(set by system)* | `CAMS` or `KFIN` |
| **Product** | | | |
| `product_code` | `PRODUCT` | `Product Code` ⚠️ | Scheme code, e.g. `B331G` (CAMS) / `RMFLPIG` (KFIN) |
| `scheme_code` | `SCHEME_CODE` | `Scheme_code` | Short scheme code, e.g. `331G` / `LP` |
| `scheme_name` | `SCHEME_NAME` — ⚠️ the real R49 file writes plain `SCHEME`, which is dropped | `Scheme Name` | Full scheme name |
| `plan` | — | `Plan` | Regular or Direct |
| **Investor** | | | |
| `folio_no` | `FOLIO_NO` | `Folio` | Folio number |
| `folio_old` | `FOLIO_OLD` | — | Previous folio |
| `inv_name` | `INV_NAME` | `Investor Name` ⚠️ | Investor name |
| `pan` | `PAN` | `PAN` | PAN |
| `inv_iin` | `INV_IIN` | `Ihno` | Investor identification |
| `inv_dp_id` | — | `InvDpId` | Depository participant ID |
| `inv_client_id` | — | `InvClientId` | Depository client ID |
| `dp_inv_name` | — | `DP_InvName` | Name as held at the depository |
| **The SIP** | | | |
| `aut_trntyp` | `AUT_TRNTYP` | `SipType` | Type of systematic transaction |
| `auto_trno` | `AUTO_TRNO` | `RegSlno` | Registration serial number |
| `ft_sip_regno` | `FT_SIP_REGNO` | — | **SIP registration number — the unique reference** |
| `request_ref_no` | `REQUEST_REF_NO` | — | Used as the reference when the above is blank |
| `auto_amount` | `AUTO_AMOUNT` | `Amount` | Instalment amount |
| `no_of_installments` | — | `No Of Installments` ⚠️ | Number of instalments |
| `periodicity` | `PERIODICITY` | `Frequency` | Monthly / weekly etc. |
| `period_day` | `PERIOD_DAY` | — | Day of month the SIP debits |
| `payment_mode` | `PAYMENT_MODE` | `SIP Mode` ⚠️ | NACH / ECS / auto debit |
| `status` | `Status` | `Status` | SIP status |
| `modify_flag` | `ModifyFlag` | `ModifyFlag` | Whether the SIP has been modified |
| `umrn_code` | `umrncode` | `umrncode` | Mandate reference (UMRN) |
| `subtrxndesc` | `SUBTRXNDESC` | `Trtype` | Sub-transaction description |
| **Dates** | | | |
| `reg_date` | `REG_DATE` | `RegistrationDate` | Registration date |
| `from_date` | `FROM_DATE` | `Start Date` ⚠️ | SIP start date |
| `to_date` | `TO_DATE` | `End Date` ⚠️ | SIP end date |
| `cease_date` | `CEASE_DATE` | `TerminateDate` | Cessation date |
| `pause_from_date` | `PAUSE_FROM_DATE` | — | Pause start |
| `pause_to_date` | `PAUSE_TO_DATE` | — | Pause end |
| **Target scheme (for STP / switch SIPs)** | | | |
| `target_scheme` | `TARGET_SCHEME` (name) | `To Scheme` ⚠️ (short code) | Target scheme — **note the two RTAs put different things here** |
| `target_scheme_code` | `TARGET_SCHEME_CODE` | `ToProductCode` | Target scheme code |
| `target_scheme_name` | — | `ToSchemeName` | Target scheme full name |
| `target_plan` | — | `To Plan` ⚠️ | Target plan |
| **Distributor** | | | |
| `sub_arn_code` | `SUB_ARN_CODE` | `AgentCode` | ARN |
| `agent_name` | — | `AgentName` | Distributor name |
| `subbroker` | `SUBBROKER` | `Subbroker` | Sub-broker |
| `euin` | `EUIN` | — | EUIN |
| `zone` | — | `Zone` | Zone |
| `branch` | `BRANCH` | `Branch` | Branch — **the code notes these are different business fields in the two feeds but maps them together anyway** |
| `ter_location` | `TER_LOCATION` | `Location` | Location |
| **Bank** | | | |
| `bank` | `BANK` | `ECSBankName` | Bank |
| `ac_type` | `AC_TYPE` | — | Account type |
| `instrm_no` | `INSTRM_NO` | `ECSNO` | Instrument / ECS number |
| `cheq_micr_no` | `CHEQ_MICR_NO` | — | Cheque MICR |
| `ecs_account_no` | — | `ECSAcno` | ECS account number |
| `ac_holder_name` | `AC_HOLDER_NAME` | `ECSHolderName` | Account holder name |
| **AMC** | | | |
| `amc_code` | `AMC_CODE` | — | AMC code |
| `user_code` | `USER_CODE` | — | User code |
| `package_name` | `PACKAGE_NAME` | — | Package |
| `special_product` | `SPECIAL_PRODUCT` | — | Special product marker |
| `scheme_folio_number` | `SCHEME_FOLIO_NUMBER` | — | Scheme-level folio |
| **Top-up** | | | |
| `top_up_frq` | `TOP_UP_FRQ` | — | Top-up frequency |
| `top_up_amt` | `TOP_UP_AMT` | — | Top-up amount |
| `top_up_perc` | `TOP_UP_PERC` | — | Top-up percentage |
| `remarks` | `REMARKS` | — | Remarks |
| **System** | | | |
| `flag`, `created_at`, `updated_at` | *(set by system)* | | |

⚠️ = **these KFintech columns are currently never picked up.** See section 8, item 1. This is the most serious of the mapping problems, because for KFintech SIP files it leaves the scheme code, scheme name, investor name, number of instalments, payment mode, start date, end date, target scheme and target plan all empty.

**Date handling at Bronze for SIP is different per RTA:**

| RTA | Format the system expects | Example |
|---|---|---|
| CAMS | `MM/DD/YYYY HH:MM AM/PM` | `03/15/2024 12:00 AM` |
| KFintech | `DD/MM/YYYY` | `15/03/2024` |

Anything that does not match becomes an empty date. Applied to `from_date`, `to_date`, `cease_date`, `reg_date`, `pause_from_date`, `pause_to_date`.

**Numbers:** `auto_amount`, `no_of_installments`, `top_up_amt`, `top_up_perc` are converted to numbers; anything unreadable becomes empty.

---

### 4.4 The `flag` column — how repeat uploads are handled

Every Bronze table has a `flag` column set at load time:

- **`flag = 0`** — this row is new. Silver will pick it up.
- **`flag = 1`** — an identical row is already in Bronze. Silver will skip it.

"Identical" means every column matches except `flag`, `created_at`, `updated_at` and `source`. Comparison is done on trimmed text, and for SIP also on upper case, so `HDFC ` and `hdfc` count as the same for SIP but not for the other two tables.

**The row is still inserted either way.** Bronze keeps a full history of every delivery you ever uploaded. If you upload the same R2 file three times, Bronze will hold three copies, two of them marked `flag = 1`.

---

## 5. Silver layer — the same three tables, cleaned

Silver has the **same table names and the same columns** as Bronze. What changes is the content.

### 5.1 What gets picked up

1. Only Bronze rows with `flag = 0`.
2. Of those, only rows whose Bronze `created_at` is **later** than the newest `created_at` already in the matching Silver table.
3. Of those, only rows that are not already in Silver word for word (every column compared, ignoring `created_at`, `updated_at` and `flag`).

The `flag` column itself is dropped — it does not exist in Silver. `created_at` and `updated_at` in Silver are re-stamped with the time the Silver load ran.

### 5.2 Cleaning applied to all three tables

- Exact duplicate rows removed
- All text trimmed of leading and trailing spaces
- Cells containing only spaces turned into true blanks
- All decimal numbers rounded to 4 decimal places

### 5.3 Value standardisation — investor master

| Column | Before | After |
|---|---|---|
| `state` | Any capitalisation | Title Case, and cross-filled against `gst_state_code` using the `bronze.state_code` reference table — a missing state name is filled from the code, and a missing code from the name |
| `account_type` | `SAV`, `SAVINGS` | `Savings` |
| | `CUR`, `CURRENT` | `Current` |
| | `NRE` | `NRE` |
| | `NRO` | `NRO` |
| | anything else | left as-is |
| `tax_status` | `I`, `1`, `INDIVIDUAL` | `Individual` |
| | `N` | `N` |
| `holding_nature` and `mode_of_holding_description` | `SI`, `SINGLE` | `Single` |
| | `AS`, `ANYONE OR SURVIVOR` | `Anyone Or Survivor` |
| | `JO`, `JOINT` | `Joint` |
| | `EO`, `EITHER OR SURVIVOR` | `Either Or Survivor` |
| | anything else | Title Case |
| `occupation` | `SERVICE` | `1` |
| | `BUSINESS` | `2` |
| | `PROFESSIONAL` | `3` |
| | `AGRICULTURE` | `4` |
| | `STUDENT` | `5` |
| | `RETIRED` | `6` |
| | `HOUSEWIFE` | `7` |
| | `OTHERS` | `8` |
| | `PRIVATE SECTOR` | `9` |
| | `PUBLIC SECTOR` | `10` |
| | `SELF EMPLOYED` | `11` |
| | `NOT APPLICABLE` | `41` |
| | anything else | empty |
| PAN columns (`pan_no`, `joint1_pan`, `joint2_pan`, `guardian_pan`) | any case | UPPER CASE |
| Email columns | any case | lower case |
| Phone columns (`mobile_no`, `phone_res`, `phone_off`) | with spaces / hyphens | spaces and hyphens stripped out |
| `dob`, `report_date`, `folio_date` | text | real dates |

⚠️ Note: `occupation` becomes a **number** in Silver. The original word is lost unless the file also supplied `occupation_description`.

### 5.4 Value standardisation — transaction master

| Column | Rule |
|---|---|
| `state` / `gst_state_code` | Same two-way fill as investor master |
| `location` | Title Case |
| `bank_name` | `HDFCBANK`, `HDFC BANK`, `HDFC BANK LTD`, `HDFC BANK LIMITED` → `HDFC Bank`; `SBI`, `STATE BANK OF INDIA` → `State Bank Of India`; `ICICI BANK`, `ICICI BANK LIMITED` → `ICICI Bank`; `AXIS BANK`, `AXIS BANK LTD` → `Axis Bank`; `BANK OF BARODA`, `BANKOFBARODA` → `Bank Of Baroda`; `BANK OF INDIA` → `Bank Of India`; `KOTAK BANK`, `KOTAK MAHINDRA BANK LIMITED` → `Kotak Mahindra Bank`; anything else → Title Case |
| `tax_status` | `I`, `1`, `INDIVIDUAL` → `Individual`; `N` → `NRI`; `NRI - REPATRIATION` → `NRI - Repatriation` |
| `pan` | UPPER CASE |
| `email` | lower case |
| `mobile`, `rphone`, `ophone` | spaces and hyphens removed |
| `units`, `amount`, `load_amount`, `broker_percent`, `broker_commission`, `purprice`, `stamp_duty` | converted to numbers |

### 5.5 Value standardisation — SIP master

| Column | Rule |
|---|---|
| `location`, `investor_name`, `agent_name`, `subbroker`, `scheme_name`, `to_scheme_name`, `ecs_bank_name`, `ecs_holder_name`, `dp_inv_name` | Title Case |
| `zone`, `branch`, `ihno`, `folio`, `agent_code`, `fund_code`, `product_code`, `to_product_code`, `ecsno`, `reg_slno`, `inv_dp_id`, `inv_client_id`, `umrncode` | UPPER CASE |
| `pan` | UPPER CASE |
| `plan`, `to_plan` | `REGULAR` → `Regular`, `DIRECT` → `Direct`, anything else → Title Case |
| `sip_mode` | `AUTO-DEBIT`, `AUTO DEBIT` → `Auto Debit`; `NACH` → `NACH`; `ECS` → `ECS`; anything else → Title Case |
| `frequency`, `sip_type`, `trtype`, `status` | Title Case |
| `modify_flag` | `Y` → `Yes`, `N` → `No` |
| `ecs_acno` | spaces removed |
| `amount`, `no_of_installments` | converted to numbers |

---

## 6. Gold layer — the business tables

Gold is built by `gold_loader.py`, which runs the tables in this order, because later ones look up IDs created by earlier ones:

**AMC → Scheme → Scheme NAV → Transactions → Holdings → SIP → Clients → Folio Nominees**

---

### 6.1 `gold.amc` — one row per fund house

**Comes from:** `silver.transaction_master_new`, with names looked up in `bronze.amc_master`.
**Unique by:** `amc_code`.

| Gold column | Where it comes from | Notes |
|---|---|---|
| `amc_code` | `silver.transaction_master_new.amc_code` | Trimmed, UPPER CASE, max 20 characters. Blank codes dropped |
| `name` | `bronze.amc_master.amc_name`, matched on `amc_code` | Empty if the AMC is not in that reference table |
| `short_name` | *(always empty)* | Filled in by the application, not by the pipeline |
| `rta` | `silver.transaction_master_new.source` | `CAMS` or `KFIN` |
| `logo_url` | *(always empty)* | Application-managed |
| `status` | *(always empty)* | Application-managed |

**How repeat runs behave:** an AMC code already in `gold.amc` is skipped. Nothing is ever updated in place.

---

### 6.2 `gold.scheme` — one row per scheme per RTA

**Comes from:** `silver.transaction_master_new` and `silver.investor_master`, combined; plus the AMFI reference list in `public.scheme_master` and the AMC list in `bronze.amc_master`.
**Unique by:** `rta` + `scheme_code`.

| Gold column | Where it comes from | Notes |
|---|---|---|
| `id` | Calculated from `rta` + `scheme_code` | A fixed, repeatable ID. The same scheme always gets the same ID, on every run — this is what lets holdings and transactions point at it reliably |
| `rta` | `source` | `CAMS` or `KFIN` |
| `scheme_code` | `transaction_master_new.prodcode`, or `investor_master.product_code` | Trimmed, UPPER CASE |
| `scheme_name` | First non-empty of: `funddesc` → `scheme` → `scheme_name` → `fund_description` | Transaction file preferred over investor file |
| `category` | `transaction_master_new.scheme_type`, falling back to `investor_master.categorydesc` | |
| `plan` | Extracted from the scheme name | Looks for the word `Direct` or `Regular` inside the name. Empty if neither appears |
| `amfi_code` | `public.scheme_master.scheme_code` | Matched by normalising the scheme name — upper-cased, punctuation replaced with spaces, repeated spaces collapsed — and looking for an exact match. Empty when no match |
| `amc_id` | `bronze.amc_master.amc_id`, matched on `amc_code` | |
| `rta_scheme_code` | Same value as `scheme_code` | |
| `isin`, `category_id`, `plan_type`, `option_type`, `benchmark_id`, `expense_ratio`, `exit_load_json`, `lock_in_months`, `riskometer`, `status` | *(always empty)* | Application-managed |
| `created_at` | Load time | |

**Deduplication:** if the same `rta` + `scheme_code` appears more than once, the first one after sorting is kept. A scheme already in Gold is skipped on later runs.

---

### 6.3 `gold.scheme_nav` — NAV history

**Comes from:** `silver.transaction_master_new`, every row where a price is present.
**Unique by:** `scheme_id` + `nav_date` (within a batch).

| Gold column | Where it comes from | Notes |
|---|---|---|
| `scheme_id` | `gold.scheme.id`, matched on `source` + `prodcode` | Rows that do not match a scheme are dropped |
| `nav_date` | `traddate` | Rows with no date are dropped |
| `nav` | `purprice` | The NAV the transaction was done at. Rows with no price are dropped |
| `repurchase_nav` | *(always empty)* | Not in the feed |
| `source` | `source` | `CAMS` or `KFIN` |
| `created_at` | Load time | |

**Important caveat for business users:** this is not an official NAV history. It is the NAV *implied by the transactions we happen to hold*. Days on which nobody transacted have no NAV row.

---

### 6.4 `gold.transactions` — one row per transaction

**Comes from:** `silver.transaction_master_new`.
**Unique by:** `rta` + `rta_txn_no`.

| Gold column | Where it comes from | Notes |
|---|---|---|
| `rta` | `source` | UPPER CASE, max 10 characters |
| `rta_txn_no` | `trxnno` | The RTA's own transaction number. Rows without one are dropped |
| `pan` | `pan` | UPPER CASE, trailing `.0` removed, max 10 characters |
| `folio_number` | `folio_no` | Trailing `.0` removed, max 40 characters |
| `txn_type` | **Worked out from the text** — see the rules table below | |
| `txn_type_raw` | `trxntype` | The RTA's own code, kept as delivered |
| `txn_desc` | `trxn_nature` | The RTA's description |
| `txn_date` | `traddate` | |
| `post_date` | `postdate` | |
| `amount` | `amount` | |
| `units` | `units` | |
| `nav` | `purprice` | |
| `load_amount` | `load` | |
| `stt` | `stt` | |
| `stamp_duty` | `stamp_duty` | |
| `gst` | `igst_amount` + `cgst_amount` + `sgst_amount` | Missing components count as zero, so the total is never empty |
| `arn` | `brokcode` | Distributor code |
| `euin` | `euin` | |
| `sip_ref` | `siptrxnno` | |
| `status` | `trxnstat` | |
| `scheme_id` | `gold.scheme.id`, matched on `source` + `prodcode` | Empty when the scheme is not in `gold.scheme` |
| `source` | `source` | |
| `client_id`, `amc_id`, `txn_sub_type`, `rta_txn_id`, `arn_id`, `sip_id`, `source_file_id` | *(always empty)* | Application-managed |
| `created_at` | Load time (UTC) | |

**How `txn_type` is decided.** The system joins together the description (`trxn_nature`), the raw type (`trxntype`) and the KFintech marker (`td_purred`), lower-cases the lot, and then applies these tests **in this order** — the first one that matches wins:

| Order | If the combined text… | `txn_type` becomes |
|---|---|---|
| 1 | raw type is `swi` or `swin`, or contains "switch in" / "switchin" | `SWITCH_IN` |
| 2 | raw type is `swo` or `swout`, or contains "switch out" / "switchout" | `SWITCH_OUT` |
| 3 | contains "redemption" or "redeem", or raw type is `red` | `REDEMPTION` |
| 4 | contains "sip" or "systematic" | `SIP` |
| 5 | contains "stp" | `STP` |
| 6 | contains "dividend" | `DIVIDEND` |
| 7 | contains "transfer in" / "transfer-in" | `TRANSFER_IN` |
| 8 | contains "transfer out" / "transfer-out" | `TRANSFER_OUT` |
| 9 | contains "purchase", or raw type is `pur` | `PURCHASE` |
| 10 | none of the above | `OTHER` |

Because order matters, a "Systematic Purchase" is classified as `SIP`, not `PURCHASE` — test 4 comes before test 9.

---

### 6.5 `gold.holdings` — one row per position

This is the most transformed table in the whole pipeline. It is **not** a copy of anything: it is calculated by adding up transactions.

**Comes from:** `silver.transaction_master_new` joined to `silver.investor_master` on RTA + folio.
**Unique by:** `rta` + `folio_number` + `scheme_id` — "this folio's position in this scheme".

**Stage 1 — pick the transactions.** One row per RTA + transaction number. Where the same transaction was delivered more than once, the most recently loaded copy wins.

**Stage 2 — build the columns.**

| Working column | Where it comes from |
|---|---|
| `rta` | `source` |
| `pan` | `pan` — kept only if it is exactly 10 characters, otherwise emptied |
| `folio_number` | `folio_no`, falling back to `scheme_folio_number` |
| `units` | `units` |
| `market_value` | `amount` |
| `as_on_date` | `rep_date` |
| `folio_date` | `traddate`, falling back to `postdate`, then `crdate` |
| `arn` | `investor_master.broker_code` |
| `holding_nature` | `investor_master.holding_nature` |
| `nominee_name` | `investor_master.nominee1_name` |
| `nominee_relation` | `investor_master.nominee1_relation` |
| `nominee_pct` | `investor_master.nominee1_percentage` |
| `kyc_status` | `Verified` if `investor_master.ckyc_no` is present, otherwise empty |
| `bank_name` | `investor_master.bank_name` |
| `bank_ac_last4` | last 4 characters of `investor_master.bank_account_no` |
| `demat_flag` | `investor_master.demat_flag` |
| `scheme_id` | `gold.scheme.id`, matched on `source` + `prodcode` |

**Stage 3 — decide whether each transaction adds or removes units.** The units figure in the file cannot be trusted for direction (some dividend-reinvest rows carry a negative number, and all KFintech redemptions carry a positive one), so the direction comes from the transaction type:

| Direction | CAMS `trxn_type_flag` values | KFintech `td_purred` value |
|---|---|---|
| **Adds units (+)** | ADDITIONAL PURCHASE, ADDITIONAL PURCHASE SYSTEMATIC, FRESH PURCHASE, FRESH PURCHASE SYSTEMATIC, SWITCH IN, DIVIDEND REINVEST, BONUS, NFOAP, NFO FP, NFO SI, TI INTO NEW FOLIO, TI INTO EXISTING FOLIO, TICOB | `P` |
| **Removes units (−)** | PARTIAL REDEMPTION, FULL REDEMPTION, PARTIAL SWITCH OUT, FULL SWITCH OUT, TRANSFER OUT, TOCOB | `R` |
| **Money only, no unit change (0)** | DIVIDEND PAYOUT, DRO | `D` |
| **Unrecognised** | anything else — counted as 0 units and printed as a warning during the run | |

**Stage 4 — roll up to one row per position.**

| Gold column | How it is calculated |
|---|---|
| `id` | Calculated from `rta` + `folio_number` + `scheme_id`. Stable across runs |
| `rta`, `folio_number`, `scheme_id` | The three grouping columns |
| `units` | Sum of (absolute units × direction), rounded to 4 decimals |
| `invested_amount` | Sum of (absolute amount × direction), rounded to 4 decimals. This is money in minus money out — a **cost**, not a valuation |
| `avg_cost_nav` | `invested_amount ÷ units`, rounded to 6 decimals. Only calculated when units are above zero |
| `as_on_date` | The **latest** report date across the position's transactions |
| `folio_date` | The **earliest** trade date |
| `first_purchase_date` | The earliest trade date among transactions that *added* units |
| `pan`, `arn`, `holding_nature`, `nominee_name`, `nominee_relation`, `nominee_pct`, `kyc_status`, `bank_name`, `bank_ac_last4`, `demat_flag` | The most recent non-empty value, ordered by trade date |
| `market_value`, `current_nav`, `current_value`, `nav_date`, `unrealised_gain`, `xirr`, `purchase_date`, `client_id`, `amc_id`, `arn_id`, `source_file_id` | *(always empty)* — valuing a position needs a NAV as at a valuation date, which this module deliberately does not do |
| `last_synced_at`, `created_at` | Load time |

**How repeat runs behave:** this table is the only one that genuinely **updates** existing rows. A position already in Gold has its units, cost and dates rewritten, because those change every time a new transaction arrives. Its `id` and `created_at` never change.

A position can legitimately show zero or negative units: that means the redemptions in the data we hold meet or exceed the purchases we hold. The run prints a count of these.

---

### 6.6 `gold.sip` — one row per SIP registration

**Comes from:** `silver.sip_master_new`.
**Unique by:** `rta` + `sip_reg_no`.

| Gold column | Where it comes from | Notes |
|---|---|---|
| `rta` | `source` | UPPER CASE, max 10 characters |
| `sip_reg_no` | `ft_sip_regno`, falling back to `request_ref_no` | Trailing `.0` removed. Max 50 characters |
| `folio_number` | `folio_no` | Trailing `.0` removed, max 40 |
| `scheme_code` | `scheme_code` | Max 30 |
| `scheme_name` | `scheme_name` | Max 255 |
| `amc_code` | `amc_code` | Max 20 |
| `isin` | *(always empty)* | Not in the feed |
| `amount` | `auto_amount` | |
| `frequency` | `periodicity` | Trimmed, UPPER CASE, max 20 |
| `start_date` | `from_date` | |
| `end_date` | `to_date` | |
| `next_due_date` | *(always empty)* | Not in the feed |
| `sip_day` | `period_day` | Day of the month |
| `mandate_id` | `umrn_code` | Max 50 |
| `status` | `status` | UPPER CASE, max 20 |
| `registered_date` | `reg_date` | |
| `ceased_date` | `cease_date` | |
| `scheme_id`, `amc_id`, `client_id`, `sip_type`, `registered_installments`, `completed_installments`, `bounced_installments`, `ceased_reason`, `arn_id` | *(always empty)* | Application-managed |
| `created_at` | Load time | |

**How repeat runs behave:** a registration whose `rta` + `sip_reg_no` is already in Gold is skipped.

---

### 6.7 `gold.clients` — one row per investor

**Comes from:** `silver.investor_master`, with PAN topped up from the transaction and SIP tables.
**Unique by:** `pan`.

**How the PAN is chosen** — three sources, in this order of trust:

1. `investor_master.pan_no` — the PAN on the folio record
2. The highest PAN found on `silver.transaction_master_new` for the same folio number
3. The highest PAN found on `silver.sip_master_new` for the same folio number

Before comparing, the PAN is upper-cased, trimmed, cut to 10 characters, and the values `""`, `NAN`, `NONE`, `NULL` and `NON RESIDENT` are treated as blank.

| Gold column | Where it comes from | Notes |
|---|---|---|
| `full_name` | `investor_master.investor_name` | |
| `pan` | As described above | |
| `status` | Should be the fixed value `ACTIVE` | ⚠️ Currently lands empty — see section 8, item 2 |
| `pan_verified` | Fixed value `False` | |
| `pan_verified_at` | *(always empty)* | |
| `client_label`, `phone`, `mobile_isd`, `mobile`, `whatsapp_same_as_mobile`, `whatsapp_isd`, `whatsapp_no`, `aadhaar`, `email`, `date_of_birth`, `marital_status`, `anniversary_date`, `blood_group`, `equity_ucc`, `can`, `occupation`, `user_id`, `family_id`, `family_relation`, `gender`, `investor_type`, `tax_status`, `kyc_status`, `risk_profile`, `rm_id`, `branch_id`, `arn_id`, `onboarded_at`, `source` | *(always empty)* | All application-managed. Note this means **email, mobile and date of birth are NOT carried into `gold.clients`**, even though they exist in Silver |
| `created_at` | Load time | |

**Safety check:** the load fails deliberately if the number of Gold rows does not equal the number of Silver rows going in.

**How repeat runs behave:** a client whose PAN is already in Gold is skipped.

---

### 6.8 `gold.folio_nominees` — up to three nominee rows per holding

**Comes from:** `silver.investor_master`, unpivoted.
**Unique by:** `holding_id` + `seq`.

Each folio row in Silver produces **three** Gold rows, one for each nominee slot, whether or not the nominee exists.

| Gold column | Where it comes from |
|---|---|
| `holding_id` | `gold.holdings.id`, matched on RTA + folio number. Rows that find no matching holding are dropped |
| `seq` | `1`, `2` or `3` — which nominee slot |
| `name` | `nominee1_name` / `nominee2_name` / `nominee3_name`, max 255 |
| `relationship` | `nominee1_relation` / `nominee2_relation` / `nominee3_relation`, max 60 |
| `percentage` | `nominee1_percentage` / `nominee2_percentage` / `nominee3_percentage` |
| `dob`, `is_minor`, `guardian_name`, `id_type`, `id_no`, `address` | *(always empty)* |
| `created_at` | Load time |

⚠️ The match is on **folio only**, while a holding is identified by folio **and scheme**. A folio invested in five schemes has five holdings, so the same nominee is written five times, once against each. That is by design of the current key, but worth knowing when counting nominees.

---

## 7. Gold layer — the three report tables

These three are different in character. They are not business entities; they are **the CAMS WBR reports themselves**, rebuilt from Silver so the files can be regenerated at will. There is no WBR input file and no WBR Bronze or Silver stage.

Unlike the tables in section 6, these three **update rows in place** on a re-run rather than skipping them, and each one checks after loading that the row count still equals the number of distinct keys — if the table has started duplicating, the run stops.

### 7.1 `gold.brokerage_by_scheme` → files `WBR36` and `WBR36H`

**Comes from:** `silver.transaction_master_new`.
**Unique by:** `report_period` + `report_variant` + `product_code`.

| Gold column | Where it comes from | Output column in the file |
|---|---|---|
| `report_period` | The year, taken from: the `WBR_REPORT_PERIOD` setting if present, else the latest trade date in Silver, else the latest report date in Silver, else the current year | *(not in the file)* |
| `report_variant` | Always `STD` | *(not in the file)* |
| `product_code` | `prodcode` | `product_code` |
| `product_name` | `scheme` | `product_name` |
| `upfront` | *(always empty)* — no brokerage breakdown exists in the R2 file | `upfront` |
| `afe` | *(always empty)* — same reason | `afe` |
| `trailer_fee` | *(always empty)* — R2's `BROKCOMM` is per-transaction commission, which is a different figure from trail commission on AUM. Using it produced 1,036,504.67 against the provider's 3,139,008.5685 | `trailer_fee` |
| `trxn_charges` | *(always empty)* — the column exists but is 0 on all 90,536 R2 rows | `trxn_charges` |
| `clawback` | *(always empty)* — no such column in R2 | `clawback` |
| `incentives` | *(always empty)* — no such column in R2 | `incentives` |

**In plain terms:** we can reproduce the *list of schemes* on this report, in the right order, but none of the money columns. The `WBR36H` variant cannot be produced at all, because nothing in the R2 file says which schemes belong to it.

### 7.2 `gold.investor_kyc_status` → file `WBR56`

**Comes from:** `silver.investor_master`.
**Unique by:** `amc_code` + `folio`. Where a folio appears several times (once per scheme), the most recently reported row wins.

| Gold column | Silver source | Notes |
|---|---|---|
| `amc_code` | `amc_code` | Part of the key. **Rows with a blank AMC code are dropped and the count is printed** — this currently removes every KFintech folio, because the KFintech feed leaves `amc_code` blank |
| `folio` | `folio_no` | Part of the key |
| `brok_dlr_code` | `broker_code` | |
| `inv_name` | `investor_name` | |
| `tax_no` | `pan_no` | |
| `jname1` / `jointpan1` | `joint_name_1` / `joint1_pan` | |
| `jname2` / `jointpan2` | `joint_name_2` / `joint2_pan` | |
| `guardian` / `guardian_panno` | `guardian_name` / `guardian_pan` | |
| `address1`, `address2`, `address3`, `city`, `pincode`, `country` | Same-named Silver columns | |
| `location` | `city`, written as `/City` | The provider writes `GU/Gujarat` — a code and a name. Our reference table has a numeric state ID, not the provider's letter code, so the code half is left blank |
| `state` | `state`, written as `/State` | Same reason |
| `phone_res`, `phone_off`, `mobile_no`, `email` | Same-named Silver columns | |
| `fax_res` / `fax_off` | `fax_residence` / `fax_office` | |
| `fh_kyc` | `kyc1flag` | **KFintech folios only** — the CAMS R9 file carries a CKYC *number*, not a KYC *status* |
| `gu_kyc` | `kycgflag` | KFintech only |
| `jh1_kyc` | `kyc2flag` | KFintech only |
| `jh2_kyc` | `kyc3flag` | KFintech only |
| `fh_g_aadharlink` | `holder_1_aadhaar_info` | KFintech only — R9's Aadhaar column is blank on every CAMS folio |
| `jh1_aadharlink` | `holder_2_aadhaar_info` | KFintech only |
| `jh2_aadharlink` | `holder_3_aadhaar_info` | KFintech only |
| `fh_kyc_desc`, `gu_kyc_desc`, `jh1_kyc_desc`, `jh2_kyc_desc` | *(always empty)* | No KYC status description exists anywhere in the CAMS feed |
| `brok_name` | *(always empty)* | R9 carries the broker code only; there is no broker master to look the name up in |
| `rep_date` | `report_date` | |
| `rep_from_date` / `rep_to_date` | Earliest and latest `rep_date` across the whole delivery | The same pair on every row — it describes the reporting window, not the folio |

### 7.3 `gold.invalid_euin` → file `WBR68`

**Comes from:** `silver.transaction_master_new`, filtered.
**Unique by:** `amc_code` + `trxn_no`.

**The filter:** the EUIN is not blank **and** the validity marker is not blank **and** the validity marker is not `Y`.

That last part matters. A *blank* validity marker does not mean "invalid" — it means validity was not reported for that transaction. 43,894 Silver rows carry an EUIN with a blank marker, against 406 with an explicit non-`Y` verdict. Treating blank as invalid inflated the report from 406 rows to 44,299.

| Gold column | Silver source | Notes |
|---|---|---|
| `amc_code` | `amc_code` | Part of the key |
| `trxn_no` | `trxnno` | Part of the key |
| `arn_code` | `brokcode` | |
| `appln_no` | `application_no` | |
| `folio_no` / `folio` | `folio_no` — written to both columns | |
| `folio_old` | `old_folio` | |
| `alt_folio` | `altfolio` | A literal `0` is treated as "no alternate folio" and written as blank |
| `scheme_folio_number` | `scheme_folio_number` | |
| `inv_name` | `inv_name` | |
| `inv_pan` | `pan` | |
| `sch_code` | `prodcode`, **with the AMC letter stripped off the front** | `B51` → `51`, `G201` → `201`, `TSCFG` → `SCFG`. Only a genuine prefix is removed |
| `sch_name` | `scheme` | CAMS truncates this at 100 characters in R2 itself; nothing downstream can restore the rest |
| `trxn_type` | `trxntype` | |
| `trxn_desc` | *(always empty)* | R2 carries the type code but no description for it |
| `amount` | `amount` | |
| `subbrokcod` / `subbrok_arn` | `subbrok` / `sub_brk_arn` | |
| `location` | `location`, written as `/City` | The provider writes `PKD491/Palakkad` — a branch code and a city. R2's `TER_LOCATION` is a single letter (`T`, `B`), not that branch code, so pairing them gave `B/Palakkad`: plausible-looking and wrong. The code half is left blank |
| `user_code` / `usertxn_no` | `usercode` / `usrtrxno` | |
| `cons_code` | Copied from `arn_code` | An inference, not a mapping. In the provider's own file this column holds the same ARN on every row |
| `euin` / `euin_valid` | `euin` / `euin_valid` | |
| `reason` | Always the text `Invalid EUIN` | Constant by definition |
| `trade_date` / `posted_date` / `sys_reg_dt` | `traddate` / `postdate` / `sys_regn_date` | |
| `auto_trxn_no` | `siptrxnno` | A literal `0` is written as blank |
| `sip_regn_date` | *(always empty)* | There is no clean key from a transaction back to its SIP registration. Joining `siptrxnno` to the SIP table fans out to 359,518 pairs, which would multiply the report |
| `email` | *(always empty)* | The provider puts the **distributor's** email here, not the investor's. R2 has no distributor contact column. Filling it from the investor's email looked right and was wrong |

### 7.4 Date formats used when writing the report files back out

The provider is not internally consistent, and the export copies that inconsistency deliberately:

| Report | Column | Format written | Example |
|---|---|---|---|
| WBR56 | `rep_from_date` | `DD-Mon-YYYY` | `01-Jan-2025` |
| WBR56 | `rep_to_date` | `DD-Mon-YYYY` | `31-Mar-2025` |
| WBR56 | `rep_date` | `M/D/YYYY`, no leading zeros | `1/1/2025` |
| WBR68 | `trade_date`, `posted_date`, `sys_reg_dt`, `sip_regn_date` | `M/D/YYYY`, no leading zeros | `3/15/2025` |

Every other column is written as plain text or as a number.

---

## 8. Things worth confirming with the development team

These are observations from reading the current code, listed so the business team knows what to expect from the data. They are not part of the mapping design.

### 1. 55 columns are read out of the files and dropped

When the system reads your file it converts spaces and `#` in column headings to underscores (`Product Code` becomes `product_code`, `Address #1` becomes `address_1`), but the list of names it searches for still holds the untidied spelling. The two never meet, so the column is left empty.

Measured against the six real sample files, this drops:

| File | Columns dropped | The ones that hurt |
|---|---|---|
| CAMS R2 transactions | 1 of 80 | `REP_DATE` — and so `gold.holdings.as_on_date` is always empty |
| KFintech MFSD201 transactions | 1 of 59 | `CAN` |
| CAMS R9 folios | 8 of 101 | `AC_NO`, `AC_TYPE`, the bank address block, `GUARD_NAME`, `AADHAAR`, joint-holder emails and mobiles |
| KFintech MFSD211 folios | 35 of 121 | **`PAN Number`**, `Date of Birth`, `Mobile Number`, `Address #1/2/3`, `BankAccno`, `Demat Folio flag`, and **every first-nominee field** |
| CAMS R49 SIPs | 1 of 44 | `SCHEME` — so `gold.sip.scheme_name` is empty for CAMS SIPs |
| KFintech MFSD243 SIPs | 10 of 40 | `Start Date`, `End Date`, `Scheme`, `Fund Code`, `Product Code`, `No Of Installments`, `SIP Mode` |

Consequences worth naming: KFintech SIPs reach Gold with no start date, no end date and no scheme code; KFintech investors have no PAN unless it can be recovered from their transactions or SIPs; and `gold.holdings.bank_ac_last4` and `as_on_date` are empty for everybody.

45 of the 55 are fixed by one change — run the expected-heading list through the same tidy-up as the file headings before comparing. The remaining 10 need three list entries added (`SCHEME` for CAMS SIP scheme name, `Scheme` for KFintech SIP scheme code, `Fund Code` for KFintech SIP AMC code) and a decision on `CAN`, `AADHAAR` and CAMS `REMARKS`, which have no target column at all.

Full per-column detail is in **document-mapping-detailed.md** §8.

### 1b. Five KFintech transaction columns load into the wrong database column

| KFintech column | Holds | Currently loaded as | Should be |
|---|---|---|---|
| `fmcode` | `117EBRG` — a scheme code | `amc_code` | `prodcode` |
| `td_fund` | `117` — a fund-house code | `td_fund`, unused | `amc_code` |
| `smcode` | blank on 70% of rows | `prodcode` | nothing |
| `td_agent` | `ARN-266051` — the ARN | `usercode` | `brokcode` |
| `td_branch` | `AHMEDABAD` — a city | `subbrok` | `location` |

This is the root cause of the known "198 KFintech AMC codes with no name" problem: `gold.amc` is being filled with scheme codes. It also means most KFintech transactions never find their `scheme_id`, and half arrive with no ARN.

### 2. `gold.clients.status` is always empty instead of `ACTIVE`

The code intends to stamp every client row `ACTIVE`. Because of the order in which the columns are built, the value is written before the table has any rows and is then lost. Every client currently arrives with an empty status.

### 3. Clients and SIPs without an identifier collapse together

When checking whether a client is already in Gold, an empty PAN is treated as the text `""` rather than as "unknown". Once one blank-PAN client exists in `gold.clients`, every future client without a PAN is treated as a duplicate of it and dropped. The same pattern applies to SIPs with no registration number.

### 4. `gold.amc` can permanently skip rows

The AMC load only reads Silver rows created *after* the newest `created_at` in `gold.amc`. But `gold.amc.created_at` is stamped when the row is *written to Gold*, while the Silver value is stamped when the row was *written to Silver*. These are two different clocks, so Silver rows written during a Gold run can fall into a gap and never be picked up.

### 5. Duplicate SIP registrations in Silver

`silver.sip_master_new` currently holds roughly 6.5 rows for every distinct SIP registration. Almost all of the duplication is the frequency field being spelled differently between deliveries — `MONTHLY`, `Monthly` and `OM` for the same thing; `WEEKLY`, `Weekly` and `OW`. Standardising these values would collapse the duplication. Until then, SIP counts taken from Silver will overstate.

### 6. KFintech folios are dropped from the WBR56 report

The WBR56 table is keyed on AMC code plus folio, and the KFintech feed leaves `amc_code` blank on the investor file. Every KFintech folio is therefore excluded. The run prints how many rows this removes.

### 7. `bronze.investor_master` stores several values twice

`brokcode`, `g_ckyc_no`, `occ_code`, `product`, `rep_date`, `rupee_bal` and `investors_resi_faxno` each duplicate another column in the same table. Harmless, but worth knowing when counting columns or building a data dictionary.

---

## 9. Quick reference — where does a column end up?

| I care about… | Look in | Column |
|---|---|---|
| Who the investor is | `gold.clients` | `full_name`, `pan` |
| What they hold today | `gold.holdings` | `units`, `invested_amount`, `avg_cost_nav` |
| Every deal they ever did | `gold.transactions` | `txn_type`, `txn_date`, `amount`, `units`, `nav` |
| Their running SIPs | `gold.sip` | `amount`, `frequency`, `start_date`, `status` |
| Which scheme a code refers to | `gold.scheme` | `scheme_code`, `scheme_name`, `amfi_code` |
| Which fund house | `gold.amc` | `amc_code`, `name` |
| Nominees | `gold.folio_nominees` | `seq`, `name`, `relationship`, `percentage` |
| NAV on a date we transacted | `gold.scheme_nav` | `nav_date`, `nav` |
| Anything not in the list above | `silver.*` | The original column name from the file |
| Exactly what the RTA sent us | `bronze.*` | The original column name from the file, plus every past delivery |

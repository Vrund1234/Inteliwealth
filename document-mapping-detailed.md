# Detailed Column Guide — every column in every CAMS and KFintech file

This is the companion to `document-mapping.md`. That document explains the *shape* of the pipeline. This one goes column by column through the six files, in the order the columns actually appear in the file, and says for each one: what it holds, what a real value looks like, how often it is filled, which database column it becomes, and whether it survives all the way to the Gold layer.

**Everything here was measured against the six real sample files in `/home/user/Inteliwealth-pipeline/files/excel/` on 17 August 2026** — not read off the mapping list. Where the mapping list and the real file disagree, the real file wins and it is flagged.

| File profiled | Columns | Data rows |
|---|---|---|
| `10072026104746_216882305R2.csv` (CAMS transactions) | 80 | 90,536 |
| `MFSD201_WBTRN28912495_428923.csv` (KFintech transactions) | 59 | 38,230 |
| `10072026104907_216882541R9.csv` (CAMS folios) | 101 | 2,098 |
| `MFSD211_WBMST9217829_386513.csv` (KFintech folios) | 121 | 1,444 |
| `10072026105002_216882702R49.csv` (CAMS SIPs) | 44 | 738 |
| `MFSD243_WSREG8131655_1159890_0.csv` (KFintech SIPs) | 40 | 658 |

---

## 0. Read this bit first

### What happens to every single column, in order

Picture a column of numbers in Excel. Four things happen to it, always in this order:

**Step 1 — the heading gets tidied.** `AMC_CODE` becomes `amc_code`. `Product Code` becomes `product_code`. `Address #1` becomes `address_1`. Specifically: lower case, spaces and hyphens and slashes turn into `_`, and `#` is deleted.

**Step 2 — the system looks for that heading in its shopping list.** Each database column has a shopping list of headings it will accept. It walks the list top to bottom and takes the first heading it finds in your file. If it finds none, the database column stays empty. **This step is where columns get lost** — if the tidied heading is `address_1` but the shopping list only says `address1`, they never meet.

**Step 3 — the value gets cleaned.** Quotes stripped, spaces trimmed, and the texts `nan` / `None` / `<NA>` turned into true blanks. Some columns additionally get a trailing `.0` chopped off (Excel's fault), and date columns get parsed into real dates.

**Step 4 — Silver standardises the value, Gold renames and reshapes it.** Covered per column below.

### The three words in the "→ Gold" column of every table

| What it says | What it means |
|---|---|
| a column name, e.g. `gold.transactions.amount` | This value reaches the final business tables and reports |
| **stops at Silver** | The value is stored and queryable in Silver, but nothing in Gold reads it. Not lost — just not used yet |
| ⛔ **LOST** | The value never enters the database at all. It is in your file and it is dropped at Step 2 |

### How full is "filled"?

The percentage is how many of the sample file's rows had something in that column. `0%` in the table plus example values shown means "almost always blank, but a handful of rows have data" — usually fewer than 1 row in 200.

---

## 1. CAMS R2 — the transactions file

**File name pattern:** ends `R2.csv`  ·  **80 columns**  ·  **90,536 rows in the sample**
**Goes to:** `bronze.transaction_master_new` → `silver.transaction_master_new` → `gold.transactions`, `gold.holdings`, `gold.scheme`, `gold.scheme_nav`, `gold.amc`, `gold.invalid_euin`, `gold.brokerage_by_scheme`

One row = one thing that happened to one investor's money on one day: a purchase, a redemption, a switch, a dividend.

| # | File column | Example value | Filled | → Bronze | → Gold | What it is, plainly |
|---|---|---|---|---|---|---|
| 1 | `AMC_CODE` | `B`, `D`, `FTI` | 100% | `amc_code` | `gold.amc.amc_code`, `gold.invalid_euin.amc_code` | Short code for the fund house. `B` = Aditya Birla, `D` = DSP |
| 2 | `FOLIO_NO` | `1018057418` | 100% | `folio_no` | `gold.transactions.folio_number`, `gold.holdings.folio_number` | The investor's account number with that fund house |
| 3 | `PRODCODE` | `B02G`, `B104B` | 100% | `prodcode` | `gold.scheme.scheme_code`, and the link to every `scheme_id` | The scheme code. The letter in front is the AMC letter |
| 4 | `SCHEME` | `Aditya Birla Sun Life ELSS Tax Saver Fund…` | 100% | `scheme` | `gold.scheme.scheme_name` (2nd choice), `gold.invalid_euin.sch_name` | The scheme's full name. CAMS cuts this off at 100 characters |
| 5 | `INV_NAME` | `Prerak M Patel (Huf)` | 100% | `inv_name` | `gold.invalid_euin.inv_name` | Investor's name as printed on the transaction |
| 6 | `TRXNTYPE` | `P81ES`, `P43E`, `P239E` | 100% | `trxntype` | `gold.transactions.txn_type_raw`, feeds `txn_type` | CAMS's own code for the transaction. Not a fixed length |
| 7 | `TRXNNO` | `156064170` | 100% | `trxnno` | `gold.transactions.rta_txn_no`, `gold.invalid_euin.trxn_no` | **The unique receipt number.** This is what stops the same transaction being counted twice |
| 8 | `TRXNMODE` | `N`, `M`, `D` | 100% | `trxnmode` | stops at Silver | How the order arrived — electronic, manual, direct |
| 9 | `TRXNSTAT` | `Y`, `N` | 99% | `trxnstat` | `gold.transactions.status` | Did it go through? `Y` = yes |
| 10 | `USERCODE` | `CAMSWEB`, `MVJAYA9502` | 100% | `usercode` | `gold.invalid_euin.user_code` | Which login or channel keyed the transaction in |
| 11 | `USRTRXNO` | `4707888583` | 100% | `usrtrxno` | `gold.invalid_euin.usertxn_no` | The reference number the *sender* used |
| 12 | `TRADDATE` | `3/20/2019  12:00:00 AM` | 100% | `traddate` | `gold.transactions.txn_date`, `gold.holdings.folio_date` and `first_purchase_date`, `gold.scheme_nav.nav_date`, `gold.invalid_euin.trade_date` | **The date that counts** — the day the deal was struck at that day's NAV |
| 13 | `POSTDATE` | `3/20/2019  12:00:00 AM` | 100% | `postdate` | `gold.transactions.post_date`, `gold.invalid_euin.posted_date` | The day the RTA's books recorded it. Usually the same day, sometimes later |
| 14 | `PURPRICE` | `31.2`, `22.22` | 100% | `purprice` | `gold.transactions.nav`, `gold.scheme_nav.nav` | The NAV — the price of one unit on that day |
| 15 | `UNITS` | `801.282` | 100% | `units` | `gold.transactions.units`, and the sum behind `gold.holdings.units` | How many units changed hands. **The plus/minus sign here is unreliable** — see column 46 |
| 16 | `AMOUNT` | `25000`, `5000` | 100% | `amount` | `gold.transactions.amount`, the sum behind `gold.holdings.invested_amount`, `gold.invalid_euin.amount` | The rupee value |
| 17 | `BROKCODE` | `ARN-266051` | 100% | `brokcode` | `gold.transactions.arn`, `gold.invalid_euin.arn_code` and `cons_code` | The distributor's ARN — who earned the commission |
| 18 | `SUBBROK` | `ARN-76793`, `100315` | 1% | `subbrok` | `gold.invalid_euin.subbrokcod` | Sub-broker under the main distributor. Nearly always blank |
| 19 | `BROKPERC` | `0`, `0.25`, `0.8` | 100% | `brokperc` | stops at Silver | Commission rate as a percentage |
| 20 | `BROKCOMM` | `0`, `12.5`, `162.5` | 100% | `brokcomm` | stops at Silver | Commission in rupees on this one transaction. **Not** the trail commission the WBR36 report wants |
| 21 | `ALTFOLIO` | `0`, `5013981295` | 100% | `altfolio` | `gold.invalid_euin.alt_folio` | A second folio number for the same investor. `0` means "none" |
| 22 | `REP_DATE` | `7/10/2026  10:47:51 AM` | 100% | ⛔ **LOST** | ⛔ — and so `gold.holdings.as_on_date` is **always empty** | When CAMS produced the file. The mapping line for this is commented out in the code, so it never loads. See §8 item 1 |
| 23 | `TIME1` | *(blank in this file)* | 0% | `time1` | stops at Silver | Time of the transaction |
| 24 | `TRXNSUBTYP` | `N`, `A` | 100% | `trxnsubtyp` | stops at Silver | Sub-type — normal or additional |
| 25 | `APPLICATION_NO` | `WEBSITE`, `001037` | 20% | `application_no` | `gold.invalid_euin.appln_no` | The application form number, or the word `WEBSITE` |
| 26 | `TRXN_NATURE` | `Systematic - Instalment 5/11` | 100% | `trxn_nature` | `gold.transactions.txn_desc`, feeds `txn_type` | The transaction in words, including which SIP instalment it is |
| 27 | `TAX` | `0`, `139`, `5970` | 100% | `tax` | stops at Silver | Tax withheld |
| 28 | `TOTAL_TAX` | `0`, `139` | 100% | `total_tax` | stops at Silver | Total tax |
| 29 | `TE_15H` | `N` | 100% | `te_15h` | stops at Silver | Did the investor file Form 15H to avoid tax deduction? |
| 30 | `MICR_NO` | `632413604` | 16% | `micr_no` | stops at Silver | The 9-digit code at the bottom of a cheque |
| 31 | `REMARKS` | `(Reversal -Cheque dishonoured…)` | 0% | `remarks` | stops at Silver | Free text, almost always blank. Filled when something went wrong |
| 32 | `SWFLAG` | `NA`, `INTER`, `INTRA` | 99% | `swflag` | stops at Silver | For switches: within the same fund house (`INTRA`) or across (`INTER`) |
| 33 | `OLD_FOLIO` | `20034581` | 1% | `old_folio` | `gold.invalid_euin.folio_old` | The folio number before a renumbering |
| 34 | `SEQ_NO` | `11976303072` | 100% | `seq_no` | stops at Silver | CAMS's internal row counter |
| 35 | `REINVEST_FLAG` | `Z`, `Y`, `N` | 99% | `reinvest_flag` | stops at Silver | Are dividends being reinvested rather than paid out? |
| 36 | `MULT_BROK` | *(blank)* | 0% | `mult_brok` | stops at Silver | More than one broker on the same folio |
| 37 | `STT` | `0`, `0.83`, `25546` | 100% | `stt` | `gold.transactions.stt` | Securities Transaction Tax — a government levy on the sale |
| 38 | `LOCATION` | `Chennai`, `Ahmedabad` | 100% | `location` | `gold.invalid_euin.location` (written as `/Chennai`) | The city the business was booked in |
| 39 | `SCHEME_TYPE` | `ELSS`, `FOF`, `Balanced` | 100% | `scheme_type` | `gold.scheme.category` | What kind of fund it is |
| 40 | `TAX_STATUS` | `HUF`, `Individual` | 100% | `tax_status` | stops at Silver | Who the investor is for tax — individual, HUF, company |
| 41 | `LOAD` | `0`, `-12.5` | 100% | `load` | `gold.transactions.load_amount` | Entry or exit charge. Negative because it is deducted |
| 42 | `SCANREFNO` | `PANCA1$10027` | 32% | `scanrefno` | stops at Silver | Reference to the scanned paper form |
| 43 | `PAN` | `AAKHP6079G` | 99% | `pan` | `gold.transactions.pan`, `gold.holdings.pan`, `gold.invalid_euin.inv_pan`, and 2nd choice for `gold.clients.pan` | The investor's tax number — **the single most important identifier in the whole system** |
| 44 | `INV_IIN` | `0` | 100% | `inv_iin` | stops at Silver | Investor Identification Number. Always `0` here, so effectively unused |
| 45 | `TARG_SRC_SCHEME` | `B43N`, `B231G` | 12% | `targ_src_scheme` | stops at Silver | On a switch, the scheme on the other side of the move |
| 46 | `TRXN_TYPE_FLAG` | `Additional Purchase`, `Fresh Purchase`, `Additional Purchase Systematic` | 99% | `trxn_type_flag` | **Decides whether `gold.holdings` adds or subtracts the units** | The transaction in plain words. This is trusted over the sign on the units figure |
| 47 | `TICOB_TRTYPE` | *(blank)* | 0% | `ticob_trtype` | stops at Silver | Transfer-in-change-of-broker type |
| 48 | `TICOB_TRNO` | *(blank)* | 0% | `ticob_trno` | stops at Silver | Transfer-in reference |
| 49 | `TICOB_POSTED_DATE` | *(blank)* | 0% | `ticob_posted_date` | stops at Silver | Transfer-in date |
| 50 | `DP_ID` | *(blank)* | 0% | `dp_id` | stops at Silver | Depository participant ID, for demat holdings |
| 51 | `TRXN_CHARGES` | `0` | 100% | `trxn_charges` | `gold.brokerage_by_scheme.trxn_charges` — but **always 0** | Transaction charge. Zero on all 90,536 rows, which is why the WBR36 column comes out empty |
| 52 | `ELIGIB_AMT` | `0`, `5000` | 100% | `eligib_amt` | stops at Silver | Amount eligible for commission |
| 53 | `SRC_OF_TXN` | *(blank)* | 0% | `src_of_txn` | stops at Silver | Where the order came from |
| 54 | `TRXN_SUFFIX` | `- Instalment 5/11` | 96% | `trxn_suffix` | stops at Silver | The tail end of the description |
| 55 | `SIPTRXNNO` | `4882492`, `0` | 100% | `siptrxnno` | `gold.transactions.sip_ref`, `gold.invalid_euin.auto_trxn_no` | Links this transaction back to a SIP. `0` means "not a SIP" and is written out as blank |
| 56 | `TER_LOCATION` | `T`, `B` | 99% | `ter_location` | stops at Silver | A single-letter terminal code. **Not** the branch code the WBR68 report wants |
| 57 | `EUIN` | `E027648`, `NA` | 87% | `euin` | `gold.transactions.euin`, `gold.invalid_euin.euin` | The individual salesperson's licence number |
| 58 | `EUIN_VALID` | `Y`, `N` | 38% | `euin_valid` | `gold.invalid_euin.euin_valid` — **and the filter for the whole WBR68 report** | Was the EUIN valid on that day? Blank means "not checked", which is not the same as invalid |
| 59 | `EUIN_OPTED` | `Y`, `N` | 58% | `euin_opted` | stops at Silver | Did the investor choose to quote an EUIN? |
| 60 | `SUB_BRK_ARN` | `ARN-266051` | 0% | `sub_brk_arn` | `gold.invalid_euin.subbrok_arn` | The sub-broker's own ARN |
| 61 | `EXCH_DC_FLAG` | `D`, `B`, `Y` | 4% | `exch_dc_flag` | stops at Silver | Demat or physical |
| 62 | `SRC_BRK_CODE` | `ARN-76793` | 81% | `src_brk_code` | stops at Silver | The broker the business came from, before any transfer |
| 63 | `SYS_REGN_DATE` | `10/25/2018  12:00:00 AM` | 36% | `sys_regn_date` | `gold.invalid_euin.sys_reg_dt` | When the SIP behind this instalment was registered |
| 64 | `AC_NO` | `50100294712086` | 100% | `ac_no` | stops at Silver | The investor's bank account number |
| 65 | `BANK_NAME` | `HDFC BANK LTD` | 100% | `bank_name` | stops at Silver | Their bank. Silver tidies spellings — see `document-mapping.md` §5.4 |
| 66 | `REVERSAL_CODE` | `0`, `9`, `89` | 100% | `reversal_code` | stops at Silver | Why a transaction was reversed. `0` = not reversed |
| 67 | `EXCHANGE_FLAG` | `BSE` | 0% | `exchange_flag` | stops at Silver | Came through a stock exchange platform |
| 68 | `CA_INITIATED_DATE` | `6/20/2016  12:00:00 AM` | 0% | `ca_initiated_date` | stops at Silver | Corporate action date |
| 69 | `GST_STATE_CODE` | `24`, `27`, `09` | 55% | `gst_state_code` | stops at Silver | Numeric state code. Silver uses it to fill in a missing state name and vice versa |
| 70 | `IGST_AMOUNT` | `0`, `23.83` | 100% | `igst_amount` | added into `gold.transactions.gst` | Inter-state GST |
| 71 | `CGST_AMOUNT` | `0`, `10.36` | 100% | `cgst_amount` | added into `gold.transactions.gst` | Central GST |
| 72 | `SGST_AMOUNT` | `0`, `10.36` | 100% | `sgst_amount` | added into `gold.transactions.gst` | State GST |
| 73 | `REV_REMARK` | `Code VI : AMC Instructions Reversal…` | 0% | `rev_remark` | stops at Silver | Why it was reversed, in words |
| 74 | `ORIGINAL_TRXNNO` | `CAMSWEB-#-97047511620` | 0% | `original_trxnno` | stops at Silver | If this is a reversal, the transaction it reverses |
| 75 | `STAMP_DUTY` | `0`, `0.12` | 100% | `stamp_duty` | `gold.transactions.stamp_duty` | Government stamp duty, 0.005% on purchases since 2020 |
| 76 | `FOLIO_OLD` | `20034581` | 1% | `folio_old` | stops at Silver | Same as `OLD_FOLIO` at column 33; both are stored |
| 77 | `SCHEME_FOLIO_NUMBER` | `0029909694050` | 0% | `scheme_folio_number` | `gold.invalid_euin.scheme_folio_number`, and a fallback for `gold.holdings.folio_number` | Folio number at scheme level rather than fund-house level |
| 78 | `AMC_REF_NO` | `BSL_BD239500P01` | 2% | `amc_ref_no` | stops at Silver | The fund house's own reference |
| 79 | `REQUEST_REF_NO` | `632413604`, `CEO674010` | 35% | `request_ref_no` | stops at Silver | The reference of the request that created this transaction |
| 80 | `TRANSMISSION_FLAG` | `N`, `Y`, `M` | 30% | `transmission_flag` | stops at Silver | Units transferred after a death |

**Score for this file: 79 of 80 columns load. 1 is lost (`REP_DATE`).**

---

## 2. KFintech MFSD201 — the transactions file

**File name pattern:** contains `mfsd201`  ·  **59 columns**  ·  **38,230 rows in the sample**
**Goes to:** the same `bronze.transaction_master_new` as CAMS R2, so the two feeds sit side by side in one table, told apart by `source`.

This is the same *kind* of file as CAMS R2, but KFintech names everything differently. Where a column does the same job as a CAMS column, that is called out.

| # | File column | Example value | Filled | → Bronze | → Gold | What it is, plainly |
|---|---|---|---|---|---|---|
| 1 | `fmcode` | `117EBRG`, `101MDGP` | 100% | `amc_code` ⚠️ | `gold.amc.amc_code`, `gold.invalid_euin.amc_code` | **This is a scheme code, not a fund-house code**, but it is loaded into the AMC code column. See §8 item 2 — this is why 198 "AMC codes" in Gold have no name |
| 2 | `td_fund` | `117`, `101` | 100% | `td_fund` | stops at Silver | The fund-house number. **This is the column that should be feeding `amc_code`** |
| 3 | `td_acno` | `7086856242` | 100% | `folio_no` | `gold.transactions.folio_number`, `gold.holdings.folio_number` | Folio number. Same job as CAMS `FOLIO_NO` |
| 4 | `schpln` | `EBRG`, `MDGP` | 100% | `scheme` | `gold.scheme.scheme_name` (2nd choice) | A 4-letter scheme-and-plan code. Loaded into the column CAMS uses for the full scheme *name*, so it looks odd next to CAMS rows |
| 5 | `divopt` | `G`, `D`, `R` | 100% | `divopt` | stops at Silver | `G` growth, `D` dividend payout, `R` dividend reinvest |
| 6 | `funddesc` | `Mirae Asset Large and Midcap Fund…` | 100% | `funddesc` | `gold.scheme.scheme_name` (**1st choice**) | The scheme's full name. This is the name that actually reaches Gold |
| 7 | `td_purred` | `P`, `R`, `D` | 100% | `td_purred` | **Decides whether `gold.holdings` adds or subtracts units** | `P` purchase (adds), `R` redemption (subtracts), `D` dividend (no change). KFintech's equivalent of CAMS `TRXN_TYPE_FLAG` |
| 8 | `td_trno` | `7578537` | 100% | `trxnno` | `gold.transactions.rta_txn_no`, `gold.invalid_euin.trxn_no` | **The unique receipt number** |
| 9 | `smcode` | `1`, `0`, `786990` | 30% | `prodcode` ⚠️ | `gold.scheme.scheme_code`, and every `scheme_id` link | Loaded as the scheme code, but it is blank or meaningless on 70% of rows. **This is why KFintech transactions mostly fail to find their scheme.** See §8 item 2 |
| 10 | `chqno` | `99999`, `13` | 39% | `chqno` | stops at Silver | Cheque number |
| 11 | `invname` | `KAMLESH PATEL` | 100% | `inv_name` | `gold.invalid_euin.inv_name` | Investor's name |
| 12 | `trnmode` | `N`, `R` | 100% | `trxnmode` | stops at Silver | How the order arrived |
| 13 | `trnstat` | `Y` | 100% | `trxnstat` | `gold.transactions.status` | Did it go through? |
| 14 | `td_branch` | `AHMEDABAD`, `MUMBAI` | 99% | `subbrok` ⚠️ | `gold.invalid_euin.subbrokcod` | KFintech puts a **city** here. It is loaded into the sub-broker column, so `subbrok` holds ARNs for CAMS rows and city names for KFintech rows |
| 15 | `isctrno` | `7578537` | 100% | `isctrno` | stops at Silver | Investor service centre transaction number |
| 16 | `td_trdt` | `21/05/2019` | 100% | `traddate` | `gold.transactions.txn_date`, `gold.holdings.folio_date`, `gold.scheme_nav.nav_date`, `gold.invalid_euin.trade_date` | **Trade date.** Note the day-first format, opposite to CAMS |
| 17 | `td_prdt` | `06/06/2023` | 100% | `postdate` | `gold.transactions.post_date` | Posting date |
| 18 | `td_pop` | `52.444` | 99% | `td_pop` | stops at Silver | Price. Holds the same number as `td_nav` |
| 19 | `loadper` | `0.0000`, `2.2500` | 100% | `loadper` | stops at Silver | Load as a percentage |
| 20 | `td_units` | `190.680` | 100% | `units` | `gold.transactions.units`, the sum behind `gold.holdings.units` | Units. **All KFintech redemptions carry a positive number here**, which is exactly why the direction is taken from `td_purred` instead |
| 21 | `td_amt` | `10000.00` | 100% | `amount` | `gold.transactions.amount`, the sum behind `gold.holdings.invested_amount` | Rupee value |
| 22 | `load1` | `0.00`, `35.13` | 100% | `load` | `gold.transactions.load_amount` | Load charged in rupees |
| 23 | `td_agent` | `ARN-266051` | 100% | `usercode` ⚠️ | `gold.invalid_euin.user_code` | This is the **distributor ARN**, but it lands in the "who keyed it in" column. The ARN column instead gets column 24 |
| 24 | `td_broker` | `0`, `MFS76793`, `ARN-76793` | 50% | `brokcode` | `gold.transactions.arn`, `gold.invalid_euin.arn_code` | Broker code. Blank or `0` on half the rows, so **half of KFintech transactions reach Gold with no ARN** |
| 25 | `brokper` | `0` | 100% | `brokperc` | stops at Silver | Commission rate |
| 26 | `brokcomm` | `0` | 100% | `brokcomm` | stops at Silver | Commission amount |
| 27 | `invid` | *(blank)* | 0% | `invid` | stops at Silver | KFintech's investor ID |
| 28 | `crdate` | `10/07/2026` | 100% | `crdate` | 3rd-choice fallback for `gold.holdings.folio_date` | The date the file was created. Present on every row, which is why it is the last-resort date |
| 29 | `crtime` | `132059` | 100% | `time1` | stops at Silver | Time the file was created, as `HHMMSS` |
| 30 | `trnsub` | `N`, `L` | 100% | `trxnsubtyp` | stops at Silver | Sub-type |
| 31 | `td_appno` | `94570251`, `0` | 100% | `application_no` | `gold.invalid_euin.appln_no` | Application number |
| 32 | `unqno` | `EB7086856242SIN7578537` | 100% | `usrtrxno` | `gold.invalid_euin.usertxn_no` | A long unique string built from scheme + folio + type + transaction number |
| 33 | `trdesc` | `Systematic Investment`, `Redemption`, `S T P In` | 100% | `trxn_nature` | `gold.transactions.txn_desc`, feeds `txn_type` | The transaction in words |
| 34 | `td_trtype` | `SIN`, `RED`, `FUL` | 100% | `trxntype` | `gold.transactions.txn_type_raw` | Three-letter type code. `SIN` = systematic investment, `RED` = redemption |
| 35 | `purdate` | `26/03/2021` | 35% | `purdate` | stops at Silver | For a redemption, when the units being sold were bought |
| 36 | `puramt` | `2499.88` | 35% | `puramt` | stops at Silver | What those units originally cost |
| 37 | `purunits` | `32.367` | 35% | `purunits` | stops at Silver | How many units were originally bought |
| 38 | `trflag` | `TI`, `P`, `TO` | 99% | `trflag` | stops at Silver | Transfer in / transfer out / plain |
| 39 | `sfunddt` | `13/06/2023` | 13% | `sfunddt` | stops at Silver | When the money actually settled |
| 40 | `chqdate` | `21/05/2019` | 42% | `chqdate` | stops at Silver | Cheque date |
| 41 | `chqbank` | `KOTAK MAHINDRA BANK LTD` | 43% | `bank_name` | stops at Silver | The bank on the cheque |
| 42 | `td_nav` | `52.444` | 100% | `purprice` | `gold.transactions.nav`, `gold.scheme_nav.nav` | **The NAV.** Same job as CAMS `PURPRICE` |
| 43 | `td_ptrno` | `0`, `5049654` | 100% | `td_ptrno` | stops at Silver | Partner transaction number |
| 44 | `STT` | `0.00`, `1.12` | 100% | `stt` | `gold.transactions.stt` | Securities Transaction Tax |
| 45 | `IHNo` | `94570251` | 99% | `ihno` | stops at Silver | In-house reference number |
| 46 | `BranchCode` | `AH08`, `NS77` | 100% | `branchcode` | stops at Silver | KFintech branch code |
| 47 | `InwardNo` | `974206648` | 91% | `inwardno` | stops at Silver | Inward document number |
| 48 | `nctremarks` | `AUM Transfer from ARN-76793 to ARN-266051` | 77% | `remarks` | stops at Silver | Notes — very often records a change of distributor |
| 49 | `PAN1` | `AGNPP1463H` | 99% | `pan` | `gold.transactions.pan`, `gold.holdings.pan`, 2nd choice for `gold.clients.pan` | **The tax number.** Same job as CAMS `PAN` |
| 50 | `TrCharges` | *(blank)* | 0% | `trxn_charges` | stops at Silver | Transaction charges |
| 51 | `SipRegdt` | `26/05/2017` | 36% | `sys_regn_date` | `gold.invalid_euin.sys_reg_dt` | When the SIP behind this instalment was registered |
| 52 | `sipregslno` | `1019470`, `0` | 49% | `sipregslno` | stops at Silver | SIP registration serial number |
| 53 | `DivPer` | `1.0000000000` | 9% | `divper` | stops at Silver | Dividend rate per unit |
| 54 | `GuardPanNo` | `ALRPS2593L` | 0% | `guardpanno` | stops at Silver | Guardian's PAN, for a minor's folio |
| 55 | `CAN` | `19310RB01F` | 16% | ⛔ **LOST** | ⛔ | Common Account Number — the MF Utility identifier. There is no shopping list entry for it at all |
| 56 | `ExchOrgTrtype` | `SIN`, `RED` | 54% | `exchorgtrtype` | stops at Silver | Original type as the exchange sent it |
| 57 | `ElecTrxnFlag` | *(blank)* | 0% | `exchange_flag` | stops at Silver | Came through an exchange |
| 58 | `cleared` | `Y`, `P` | 37% | `cleared` | stops at Silver | Has the payment cleared? |
| 59 | `InvState` | `GUJARAT`, `MAHARASHTRA` | 99% | `invstate` | stops at Silver | The investor's state |

**Score for this file: 58 of 59 columns load. 1 is lost (`CAN`). But 4 of the 58 land in the wrong column** — see §8 item 2.

**Also worth knowing:** this file has **no** EUIN, EUIN validity, GST, stamp duty, scheme type, location, tax status, alternate folio or `TRXN_TYPE_FLAG`. So for KFintech transactions, `gold.transactions.gst` and `stamp_duty` are always empty, and no KFintech row can ever appear in the WBR68 invalid-EUIN report.

---

## 3. CAMS R9 — the folio / investor file

**File name pattern:** ends `R9.csv`  ·  **101 columns**  ·  **2,098 rows in the sample**
**Goes to:** `bronze.investor_master` → `silver.investor_master` → `gold.clients`, `gold.holdings`, `gold.folio_nominees`, `gold.scheme`, `gold.investor_kyc_status`

One row = one folio in one scheme, with everything CAMS knows about the person who owns it.

| # | File column | Example value | Filled | → Bronze | → Gold | What it is, plainly |
|---|---|---|---|---|---|---|
| 1 | `FOLIOCHK` | `1013497814` | 100% | `folio_no` | the join key to `gold.holdings`, `gold.investor_kyc_status.folio` | The folio number. `CHK` because it carries a check digit |
| 2 | `INV_NAME` | `Natvarbhai Shankerbhai Patel` | 100% | `investor_name` | `gold.clients.full_name`, `gold.investor_kyc_status.inv_name` | The first holder's name |
| 3 | `ADDRESS1` | `53 NIJRIPUNJ SOCIETY` | 100% | `address1` | `gold.investor_kyc_status.address1` | Address line 1 |
| 4 | `ADDRESS2` | `RADHASWAMI ROAD` | 97% | `address2` | `gold.investor_kyc_status.address2` | Address line 2 |
| 5 | `ADDRESS3` | `RANIP AHMEDABD` | 77% | `address3` | `gold.investor_kyc_status.address3` | Address line 3 |
| 6 | `CITY` | `AHMEDABAD` | 99% | `city` | `gold.investor_kyc_status.city` and `location` | City. Spelling varies — `AHMEDABAD` and `AHMEDBAD` both appear |
| 7 | `PINCODE` | `382480` | 99% | `pincode` | `gold.investor_kyc_status.pincode` | PIN code. Kept as text so a leading zero survives |
| 8 | `PRODUCT` | `B02G`, `B1024G` | 100% | `product_code` | `gold.scheme.scheme_code` (investor side) | The scheme code this folio row is about |
| 9 | `SCH_NAME` | `Aditya Birla Sun Life ELSS Tax Saver Fund…` | 100% | `scheme_name` | `gold.scheme.scheme_name` (3rd choice) | The scheme's name |
| 10 | `REP_DATE` | `7/10/2026  12:00:00 AM` | 100% | `report_date` **and** `rep_date` | `gold.investor_kyc_status.rep_date`, `rep_from_date`, `rep_to_date`; fallback for the WBR36 report year | The date CAMS produced the file. Loaded into two Bronze columns |
| 11 | `CLOS_BAL` | `5076.926` | 100% | `closing_balance` | stops at Silver ⚠️ | **The units the folio actually holds, as CAMS states them.** Nothing in Gold reads this — `gold.holdings.units` is calculated from transactions instead. Useful as a cross-check |
| 12 | `RUPEE_BAL` | `311875.56` | 100% | `rupee_balance` | stops at Silver ⚠️ | **The value the folio actually holds.** Same story — this is the market value `gold.holdings.market_value` is missing |
| 13 | `JNT_NAME1` | `Parav Chintan Patel` | 19% | `joint_name_1` | `gold.investor_kyc_status.jname1` | Second holder's name |
| 14 | `JNT_NAME2` | `Saleel Yogendra Bhatt` | 2% | `joint_name_2` | `gold.investor_kyc_status.jname2` | Third holder's name |
| 15 | `PHONE_OFF` | `079 27520071` | 5% | `phone_off` | `gold.investor_kyc_status.phone_off` | Office landline. Silver strips spaces and hyphens |
| 16 | `PHONE_RES` | `079-27550943` | 21% | `phone_res` | `gold.investor_kyc_status.phone_res` | Home landline |
| 17 | `EMAIL` | `despatchve@gmail.com` | 93% | `email` | `gold.investor_kyc_status.email` — **not** `gold.clients.email`, which stays empty | Email. Silver lower-cases it |
| 18 | `HOLDING_NATURE` | `SI`, `AS`, `ES` | 100% | `holding_nature` | `gold.holdings.holding_nature` | How the folio is held. `SI` single, `AS` anyone or survivor, `ES` either or survivor. Silver spells these out |
| 19 | `UIN_NO` | *(effectively blank)* | 0% | `uin_no` | stops at Silver | Unique Identification Number. Empty except on a couple of misaligned rows |
| 20 | `PAN_NO` | `ADRPP3032H` | 97% | `pan_no` | **`gold.clients.pan` (1st choice)**, `gold.investor_kyc_status.tax_no` | The first holder's tax number |
| 21 | `JOINT1_PAN` | `AMEPP9018M` | 19% | `joint1_pan` | `gold.investor_kyc_status.jointpan1` | Second holder's PAN |
| 22 | `JOINT2_PAN` | `AAYPB0139M` | 2% | `joint2_pan` | `gold.investor_kyc_status.jointpan2` | Third holder's PAN |
| 23 | `GUARD_PAN` | `AMRPP8730F` | 1% | `guardian_pan` | `gold.investor_kyc_status.guardian_panno` | Guardian's PAN, when the investor is a minor |
| 24 | `TAX_STATUS` | `Individual`, `HUF` | 99% | `tax_status` | stops at Silver | Who the investor is for tax purposes |
| 25 | `BROKER_CODE` | `ARN-266051` | 100% | `broker_code` | `gold.holdings.arn`, `gold.investor_kyc_status.brok_dlr_code` | The distributor who owns the relationship |
| 26 | `SUBBROKER` | `ARN-76793` | 1% | `subbroker` | stops at Silver | Sub-broker |
| 27 | `REINV_FLAG` | `Z`, `Y`, `N` | 99% | `reinv_flag` | stops at Silver | Dividend reinvestment setting |
| 28 | `BANK_NAME` | `BANK OF INDIA` | 100% | `bank_name` | `gold.holdings.bank_name` | The investor's bank |
| 29 | `BRANCH` | `Subhash Bridge` | 99% | `branch` | stops at Silver | Bank branch |
| 30 | `AC_TYPE` | `SB`, `CA`, `NRO` | 100% | ⛔ **LOST** | ⛔ | Account type — savings, current, NRO. The shopping list only accepts `account_type` or `bnkactype`, and CAMS writes `AC_TYPE`. See §8 item 3 |
| 31 | `AC_NO` | `203712100007454` | 100% | ⛔ **LOST** | ⛔ — and so `gold.holdings.bank_ac_last4` is **always empty for CAMS folios** | The bank account number. The list accepts `bank_account_no` or `bnkacno`, not `AC_NO` |
| 32 | `B_ADDRESS1` | `PB NO.703 MUNICIPAL…` | 18% | ⛔ **LOST** | ⛔ | Bank address line 1. List accepts `bank_address1` or `badd1` |
| 33 | `B_ADDRESS2` | `GIDC IND EST.MAKARPURA` | 14% | ⛔ **LOST** | ⛔ | Bank address line 2 |
| 34 | `B_ADDRESS3` | `SOCIETY AKOTA` | 7% | ⛔ **LOST** | ⛔ | Bank address line 3 |
| 35 | `B_CITY` | `Ahmedabad` | 96% | ⛔ **LOST** | ⛔ | Bank city. List accepts `bank_city` or `bcity` |
| 36 | `B_PINCODE` | `380054` | 32% | `b_pincode` | stops at Silver | Bank PIN code — this one *does* load, because `b_pincode` is on the list |
| 37 | `INV_DOB` | `4/10/1946  12:00:00 AM` | 95% | `dob` | stops at Silver ⚠️ | Date of birth. `gold.clients.date_of_birth` is left empty for the application to fill, so this does not reach Gold |
| 38 | `MOBILE_NO` | `+919512792006` | 91% | `mobile_no` | `gold.investor_kyc_status.mobile_no` — not `gold.clients.mobile` | Mobile number, usually with country code |
| 39 | `OCCUPATION` | `Retired`, `Business` | 83% | `occupation` | stops at Silver | Their job. **Silver replaces the word with a number** (`Business` → `2`) |
| 40 | `INV_IIN` | `0` | 100% | `inv_iin` | stops at Silver | Investor Identification Number. Always `0` |
| 41 | `NOM_NAME` | `LALITABEN PATEL` | 81% | `nominee1_name` | `gold.holdings.nominee_name`, `gold.folio_nominees` row 1 | First nominee's name |
| 42 | `RELATION` | `Spouse`, `Daughter` | 65% | `nominee1_relation` | `gold.holdings.nominee_relation`, `gold.folio_nominees` row 1 | How the nominee is related |
| 43 | `NOM_ADDR1` | `B 102 PANCHJANYA APPTS` | 24% | `nominee1_address1` | stops at Silver | Nominee address line 1 |
| 44 | `NOM_ADDR2` | `PANCHVATI 2ND LANE` | 21% | `nominee1_address2` | stops at Silver | Nominee address line 2 |
| 45 | `NOM_ADDR3` | `Usmanpur Ahmedabad` | 14% | `nominee1_address3` | stops at Silver | Nominee address line 3 |
| 46 | `NOM_CITY` | `AHMEDABAD` | 24% | `nominee1_city` | stops at Silver | Nominee city |
| 47 | `NOM_STATE` | `GU`, `Gujarat` | 14% | `nominee1_state` | stops at Silver | Nominee state — sometimes a code, sometimes a name |
| 48 | `NOM_PINCODE` | `380006` | 23% | `nominee1_pincode` | stops at Silver | Nominee PIN code |
| 49 | `NOM_PH_OFF` | *(blank)* | 0% | `nominee1_phone` **and** `nom_ph_off` | stops at Silver | Nominee office phone |
| 50 | `NOM_PH_RES` | *(blank)* | 0% | ⛔ **LOST** | ⛔ | Nominee home phone. `nominee1_phone` was already taken by column 49 |
| 51 | `NOM_EMAIL` | `pratik.a.vyas@gmail.com` | 12% | `nominee1_email` | stops at Silver | Nominee email |
| 52 | `NOM_PERCENTAGE` | `100`, `33` | 99% | `nominee1_percentage` | `gold.holdings.nominee_pct`, `gold.folio_nominees` row 1 | What share of the folio this nominee gets |
| 53 | `NOM2_NAME` | `TEJAL G PANTHAKI` | 7% | `nominee2_name` | `gold.folio_nominees` row 2 | Second nominee |
| 54 | `NOM2_RELATION` | `Daughter`, `Son` | 7% | `nominee2_relation` | `gold.folio_nominees` row 2 | Relationship |
| 55 | `NOM2_ADDR1` | `B-303 RUSHIN TOWER` | 2% | `nominee2_address1` | stops at Silver | Address line 1 |
| 56 | `NOM2_ADDR2` | `STAR BAZAAR LANE` | 2% | `nominee2_address2` | stops at Silver | Address line 2 |
| 57 | `NOM2_ADDR3` | `SATELLITE` | 1% | `nominee2_address3` | stops at Silver | Address line 3 |
| 58 | `NOM2_CITY` | `Ahmedabad` | 2% | `nominee2_city` | stops at Silver | City |
| 59 | `NOM2_STATE` | `GU`, `XX` | 0% | `nominee2_state` | stops at Silver | State |
| 60 | `NOM2_PINCODE` | `380015` | 2% | `nominee2_pincode` | stops at Silver | PIN code |
| 61 | `NOM2_PH_OFF` | *(blank)* | 0% | `nominee2_phone` **and** `nom2_ph_off` | stops at Silver | Office phone |
| 62 | `NOM2_PH_RES` | *(blank)* | 0% | ⛔ **LOST** | ⛔ | Home phone |
| 63 | `NOM2_EMAIL` | `genpmbhatt@gmail.com` | 1% | `nominee2_email` | stops at Silver | Email |
| 64 | `NOM2_PERCENTAGE` | `33`, `50` | 99% | `nominee2_percentage` | `gold.folio_nominees` row 2 | Share |
| 65 | `NOM3_NAME` | `MANISHA DOSHI AKKITHAM` | 1% | `nominee3_name` | `gold.folio_nominees` row 3 | Third nominee |
| 66 | `NOM3_RELATION` | `Daughter`, `Son` | 1% | `nominee3_relation` | `gold.folio_nominees` row 3 | Relationship |
| 67 | `NOM3_ADDR1` | *(blank)* | 0% | `nominee3_address1` | stops at Silver | Address line 1 |
| 68 | `NOM3_ADDR2` | *(blank)* | 0% | `nominee3_address2` | stops at Silver | Address line 2 |
| 69 | `NOM3_ADDR3` | *(blank)* | 0% | `nominee3_address3` | stops at Silver | Address line 3 |
| 70 | `NOM3_CITY` | *(blank)* | 0% | `nominee3_city` | stops at Silver | City |
| 71 | `NOM3_STATE` | *(blank)* | 0% | `nominee3_state` | stops at Silver | State |
| 72 | `NOM3_PINCODE` | *(blank)* | 0% | `nominee3_pincode` | stops at Silver | PIN code |
| 73 | `NOM3_PH_OFF` | *(blank)* | 0% | `nominee3_phone` **and** `nom3_ph_off` | stops at Silver | Office phone |
| 74 | `NOM3_PH_RES` | *(blank)* | 0% | ⛔ **LOST** | ⛔ | Home phone |
| 75 | `NOM3_EMAIL` | *(blank)* | 0% | `nominee3_email` | stops at Silver | Email |
| 76 | `NOM3_PERCENTAGE` | `34`, `33` | 99% | `nominee3_percentage` | `gold.folio_nominees` row 3 | Share |
| 77 | `IFSC_CODE` | `BKID0002037` | 96% | `ifsc_code` | stops at Silver | The bank branch's IFSC code |
| 78 | `DP_ID` | `IBKL0000036` | 0% | `dp_id` | stops at Silver | Depository participant ID |
| 79 | `DEMAT` | `N`, `Y` | 99% | `demat_flag` | `gold.holdings.demat_flag` | Is the folio held in demat form? |
| 80 | `GUARD_NAME` | `Prerit S Parihar` | 1% | ⛔ **LOST** | ⛔ — so `gold.investor_kyc_status.guardian` is **always empty for CAMS folios** | Guardian's name. The list accepts `guardian_name` or `guardian`, not `GUARD_NAME` |
| 81 | `BROKCODE` | `ARN-266051` | 99% | `brokcode` | stops at Silver | The distributor ARN again, in a second column |
| 82 | `FOLIO_DATE` | `3/14/2007  12:00:00 AM` | 100% | `folio_date` | stops at Silver | When the folio was opened |
| 83 | `AADHAAR` | `DELINKED`, `AVAILABLE` | 13% | ⛔ **LOST** | ⛔ | Aadhaar link status. There is no shopping list entry for a plain `AADHAAR` column |
| 84 | `TPA_LINKED` | `N` | 1% | `tpa_linked` | stops at Silver | Linked to a third-party administrator |
| 85 | `FH_CKYC_NO` | `10068324877994` | 5% | `ckyc_no` | `gold.holdings.kyc_status` — set to `Verified` **just because this is not blank** | First holder's central-KYC number. Note: this is an identity *number*, not a KYC *status*, which is why the WBR56 KYC columns cannot be filled from CAMS |
| 86 | `JH1_CKYC` | `50030741692145` | 0% | `jh1_ckyc` | stops at Silver | Second holder's CKYC number |
| 87 | `JH2_CKYC` | *(blank)* | 0% | `jh2_ckyc` | stops at Silver | Third holder's CKYC number |
| 88 | `G_CKYC_NO` | `0` | 0% | `guardian_ckyc_no` **and** `g_ckyc_no` | stops at Silver | Guardian's CKYC number |
| 89 | `JH1_DOB` | `10/19/1979  12:00:00 AM` | 18% | `jh1_dob` | stops at Silver | Second holder's date of birth |
| 90 | `JH2_DOB` | `10/24/1971  12:00:00 AM` | 2% | `jh2_dob` | stops at Silver | Third holder's date of birth |
| 91 | `GUARDIAN_DOB` | `9/28/1981  12:00:00 AM` | 11% | `guardian_dob` | stops at Silver | Guardian's date of birth |
| 92 | `AMC_CODE` | `B`, `D`, `FTI` | 99% | `amc_code` | `gold.scheme` (to find the AMC), `gold.investor_kyc_status.amc_code` | The fund house |
| 93 | `GST_STATE_CODE` | `24`, `19`, `08` | 81% | `gst_state_code` | stops at Silver | Numeric state code. Silver cross-fills it with the state name |
| 94 | `FOLIO_OLD` | `20034581` | 1% | `folio_old` | stops at Silver | Previous folio number |
| 95 | `SCHEME_FOLIO_NUMBER` | `0029909694050` | 0% | `scheme_folio_number` | stops at Silver | Folio number at scheme level |
| 96 | `COUNTRY` | `India`, `United States` | 99% | `country` | `gold.investor_kyc_status.country` | Country. A full name, not a code |
| 97 | `REMARKS` | *(effectively blank)* | 0% | ⛔ **LOST** | ⛔ | Free-text remarks. There is no `remarks` entry in the folio shopping list |
| 98 | `JH1_EMAIL` | `CHINTAN75@GMAIL.COM` | 15% | ⛔ **LOST** | ⛔ | Second holder's email. The list only has `joint_holder_1_email_id` |
| 99 | `JH2_EMAIL` | `SALEELBHATT@GMAIL.COM` | 1% | ⛔ **LOST** | ⛔ | Third holder's email |
| 100 | `JH1_MOBILE_NO` | `9824013413` | 16% | ⛔ **LOST** | ⛔ | Second holder's mobile. The list only has `joint_holder_1_contact_number` |
| 101 | `JH2_MOBILE_NO` | `9825504389` | 1% | ⛔ **LOST** | ⛔ | Third holder's mobile |

**Score for this file: 93 of 101 columns load (counting the several that load into two Bronze columns). 16 source columns are used by nothing** — the bank account block, the guardian's name, Aadhaar status, remarks, the joint holders' contact details, and the three nominee home-phone columns.

**One more thing about this file:** 2 of the 2,098 rows have one field too many, so from that point on the row's values sit in the wrong columns. That is why a scheme name occasionally shows up in `REP_DATE` and the word `PROFESSIONAL` in `INV_IIN`. Two rows out of 2,098 — worth knowing, not worth panicking about.

---

## 4. KFintech MFSD211 — the folio / investor file

**File name pattern:** contains `mfsd211`  ·  **121 columns**  ·  **1,444 rows in the sample**
**Goes to:** the same `bronze.investor_master` as CAMS R9.

**Read the ⛔ marks in this table carefully.** KFintech writes headings with spaces and `#` symbols, and the shopping lists were written for CAMS's underscore style. 35 columns are dropped — including the PAN, the date of birth, the mobile number, the whole address and every first-nominee detail.

| # | File column | Example value | Filled | → Bronze | → Gold | What it is, plainly |
|---|---|---|---|---|---|---|
| 1 | `Product Code` | `101EQGP`, `101MDGP` | 100% | `product_code` | `gold.scheme.scheme_code` (investor side) | The scheme code |
| 2 | `Fund` | `101`, `105` | 100% | `fund` | stops at Silver | The fund-house number |
| 3 | `Folio` | `17731026861` | 100% | `folio_no` | join key to `gold.holdings`, `gold.investor_kyc_status.folio` | Folio number |
| 4 | `Fund Description` | `Canara Robeco Large and Mid Cap Fund…` | 100% | `fund_description` | `gold.scheme.scheme_name` (4th choice) | The scheme's full name |
| 5 | `Investor Name` | `Venugopal Bontra` | 100% | `investor_name` | `gold.clients.full_name`, `gold.investor_kyc_status.inv_name` | The first holder's name |
| 6 | `Joint Name 1` | `Prerak Patel` | 15% | `joint_name_1` | `gold.investor_kyc_status.jname1` | Second holder |
| 7 | `Joint Name 2` | `MANISHA DOSHI AKKITHAM` | 0% | `joint_name_2` | `gold.investor_kyc_status.jname2` | Third holder |
| 8 | `Address #1` | `7 MANIKAMAL SOCIETY` | 100% | ⛔ **LOST** | ⛔ | Address line 1. The heading tidies to `address_1`; the list wants `address1` |
| 9 | `Address #2` | `SAL HOSPITAL ROAD` | 96% | ⛔ **LOST** | ⛔ | Address line 2 |
| 10 | `Address #3` | `THALTEJ` | 77% | ⛔ **LOST** | ⛔ | Address line 3 |
| 11 | `City` | `AHMEDABAD` | 100% | `city` | `gold.investor_kyc_status.city` and `location` | City |
| 12 | `Pincode` | `380054` | 99% | `pincode` | `gold.investor_kyc_status.pincode` | PIN code |
| 13 | `State` | `GUJARAT` | 99% | `state` | `gold.investor_kyc_status.state` | State. Silver title-cases it and cross-fills the numeric code |
| 14 | `Country` | *(blank)* | 0% | `country` | `gold.investor_kyc_status.country` | Country |
| 15 | `TPIN` | *(blank)* | 0% | `tpin` | stops at Silver | Telephone PIN |
| 16 | `Date of Birth` | `29/05/1975` | 92% | ⛔ **LOST** | ⛔ | Date of birth. Heading tidies to `date_of_birth`; the list wants `dob` or `inv_dob` |
| 17 | `F Name` | *(blank)* | 0% | `f_name` | stops at Silver | Father's name |
| 18 | `M Name` | *(blank)* | 0% | `m_name` | stops at Silver | Mother's name |
| 19 | `Phone Residence` | `9428819956` | 5% | ⛔ **LOST** | ⛔ | Home phone. Tidies to `phone_residence`; the list wants `phone_res` or `rphone` |
| 20 | `Phone Res#1` | *(blank)* | 0% | `phone_res1` | stops at Silver | Home phone 1 — this one loads, because `#` is deleted and the result matches |
| 21 | `Phone Res#2` | *(blank)* | 0% | `phone_res2` | stops at Silver | Home phone 2 |
| 22 | `Phone Office` | `9428819956` | 3% | ⛔ **LOST** | ⛔ | Office phone. Tidies to `phone_office`; the list wants `phone_off` |
| 23 | `Phone Off#1` | *(blank)* | 0% | `phone_off1` | stops at Silver | Office phone 1 |
| 24 | `Phone Off#2` | *(blank)* | 0% | `phone_off2` | stops at Silver | Office phone 2 |
| 25 | `Fax Residence` | `0265` | 0% | `fax_residence` | `gold.investor_kyc_status.fax_res` | Home fax |
| 26 | `Fax Office` | *(blank)* | 0% | `fax_office` | `gold.investor_kyc_status.fax_off` | Office fax |
| 27 | `Tax Status` | `I`, `N`, `H` | 99% | `tax_status` | stops at Silver | `I` individual, `N` NRI, `H` HUF. Silver spells `I` out as `Individual` |
| 28 | `Occ Code` | `2`, `1`, `7` | 100% | `occupation` **and** `occ_code` | stops at Silver | Occupation as a number. Already in the numeric form Silver converts CAMS words into |
| 29 | `Email` | `vbontra@gmail.com` | 94% | `email` | `gold.investor_kyc_status.email` | Email |
| 30 | `BankAccno` | `00491050113786` | 100% | ⛔ **LOST** | ⛔ — so `gold.holdings.bank_ac_last4` is empty for KFintech too | Bank account number. Tidies to `bankaccno`; the list wants `bank_account_no` or `bnkacno` |
| 31 | `Bank Name` | `HDFC Bank Ltd` | 100% | `bank_name` | `gold.holdings.bank_name` | The bank |
| 32 | `Account Type` | `SAV`, `NRE`, `SB` | 99% | `account_type` | stops at Silver | Savings, NRE, etc. Silver turns `SAV` into `Savings` |
| 33 | `Branch` | *(blank)* | 0% | `branch` | stops at Silver | Bank branch |
| 34 | `Bank Address #1` | `Bodakdev` | 96% | ⛔ **LOST** | ⛔ | Bank address line 1. Tidies to `bank_address_1` |
| 35 | `Bank Address #2` | `FLOOR SHOP NO12 TO 17…` | 21% | ⛔ **LOST** | ⛔ | Bank address line 2 |
| 36 | `Bank Address #3` | `VADODARA GUJARAT 391410` | 14% | ⛔ **LOST** | ⛔ | Bank address line 3 |
| 37 | `Bank City` | `AHMEDABAD` | 90% | `bank_city` | stops at Silver | Bank city |
| 38 | `Bank Phone` | *(blank)* | 0% | `bank_phone` | stops at Silver | Bank phone |
| 39 | `Bank State` | `GUJARAT` | 3% | `bank_state` | stops at Silver | Bank state |
| 40 | `Bank Country` | *(blank)* | 0% | `bank_country` | stops at Silver | Bank country |
| 41 | `Investor ID` | *(blank)* | 0% | `investor_id` | stops at Silver | KFintech's investor ID |
| 42 | `Broker Code` | `ARN-266051` | 100% | `broker_code` **and** `brokcode` | `gold.holdings.arn`, `gold.investor_kyc_status.brok_dlr_code` | The distributor's ARN |
| 43 | `Report Date` | `15/07/2026` | 100% | `report_date` | `gold.investor_kyc_status.rep_date` and the reporting window | When KFintech produced the file |
| 44 | `Report Time` | `152629` | 100% | `report_time` | stops at Silver | Time the file was produced |
| 45 | `PAN Number` | `AAVPB1269M` | 98% | ⛔ **LOST** | ⛔ — so `gold.clients.pan` and `gold.investor_kyc_status.tax_no` must be filled from the transaction and SIP files instead | **The tax number.** Tidies to `pan_number`; the list wants `pan_no` or `pan`. This is the single most damaging dropped column in the whole pipeline |
| 46 | `Mobile Number` | `+919714978899` | 93% | ⛔ **LOST** | ⛔ | Mobile number. Tidies to `mobile_number`; the list wants `mobile_no` or `mobile` |
| 47 | `Dividend Option` | `G`, `D`, `R` | 100% | `dividend_option` | stops at Silver | Growth, dividend payout, dividend reinvest |
| 48 | `Occupation Description` | `House Wife`, `SERVICE` | 100% | `occupation_description` | stops at Silver | The occupation in words |
| 49 | `Mode of Holding Description` | `SINGLE`, `ANYONE OR SURVIVOR` | 100% | `mode_of_holding_description` | stops at Silver ⚠️ | How the folio is held, in words. Note `gold.holdings.holding_nature` reads `holding_nature`, which KFintech does **not** supply — so that Gold column is empty for KFintech folios |
| 50 | `Mapin Id` | *(blank)* | 0% | `mapin_id` | stops at Silver | An old market identifier |
| 51 | `PAN2` | `AIAPP9113D` | 15% | `pan2` | stops at Silver | Second holder's PAN — loads, unlike the first holder's |
| 52 | `PAN3` | `AAWPD2914N` | 0% | `pan3` | stops at Silver | Third holder's PAN |
| 53 | `Category` | `11`, `21`, `22` | 100% | `category` | stops at Silver | Investor category as a number |
| 54 | `GuardianName` | `Vedant Maheshwari` | 1% | ⛔ **LOST** | ⛔ | Guardian's name. Tidies to `guardianname`, one underscore short of matching |
| 55 | `Nominee` | `Rachana Bontra` | 86% | ⛔ **LOST** | ⛔ — so `gold.holdings.nominee_name` and `gold.folio_nominees` row 1 are **empty for every KFintech folio** | The first nominee's name. The list wants `nominee1_name` or `nom_name` |
| 56 | `Client ID` | `17777369` | 0% | `client_id` | stops at Silver | Depository client ID |
| 57 | `DPID` | `IN301549` | 0% | ⛔ **LOST** | ⛔ | Depository participant ID. Tidies to `dpid`; the list wants `dp_id` |
| 58 | `CategoryDesc` | `RESIDENT INDIAN`, `NRI REPATRIABLE` | 99% | `categorydesc` | `gold.scheme.category` (fallback) | Category in words |
| 59 | `StatusDesc` | `INDIVIDUAL`, `HUF` | 99% | `statusdesc` | stops at Silver | Status in words |
| 60 | `IFSC Code` | `HDFC0000049` | 98% | `ifsc_code` | stops at Silver | Bank branch code |
| 61 | `Nominee2` | `PRITY T PATEL` | 5% | ⛔ **LOST** | ⛔ | Second nominee's name. The list wants `nominee2_name` or `nom2_name` |
| 62 | `Nominee3` | `NEELAM K PATEL` | 1% | ⛔ **LOST** | ⛔ | Third nominee's name |
| 63 | `Kyc1Flag` | `Y`, `M`, `D` | 98% | `kyc1flag` | **`gold.investor_kyc_status.fh_kyc`** | First holder's KYC status. **KFintech is the only source for this** — CAMS has nothing equivalent |
| 64 | `Kyc2Flag` | `Y`, `M`, `D` | 16% | `kyc2flag` | `gold.investor_kyc_status.jh1_kyc` | Second holder's KYC status |
| 65 | `Kyc3Flag` | `Y` | 2% | `kyc3flag` | `gold.investor_kyc_status.jh2_kyc` | Third holder's KYC status |
| 66 | `GuardPanNo` | `AILPM7451Q` | 1% | ⛔ **LOST** | ⛔ | Guardian's PAN. Tidies to `guardpanno`; the list wants `guardian_pan`, `guard_pan` or `pangno` |
| 67 | `LastUpdatedDate` | `16/06/2026` | 100% | `lastupdateddate` | stops at Silver | When KFintech last changed the record |
| 68 | `CommonAccNo` | `19310RB01F` | 6% | `commonaccno` | stops at Silver | MF Utility common account number |
| 69 | `Nominee Relation` | `OTHER`, `Husband` | 57% | ⛔ **LOST** | ⛔ | First nominee's relationship |
| 70 | `Nominee2 Relation` | `SON`, `Daughter` | 5% | `nominee2_relation` | `gold.folio_nominees` row 2 | Second nominee's relationship — this one loads, because the heading happens to carry the `2` |
| 71 | `Nominee3 Relation` | `Daughter`, `Son` | 1% | `nominee3_relation` | `gold.folio_nominees` row 3 | Third nominee's relationship |
| 72 | `Nominee Ratio` | `100`, `40` | 86% | ⛔ **LOST** | ⛔ | First nominee's share % |
| 73 | `Nominee2 Ratio` | `30`, `50` | 5% | ⛔ **LOST** | ⛔ | Second nominee's share %. The list wants `nominee2_percentage` or `nom2_percentage` |
| 74 | `Nominee3 Ratio` | `30`, `20` | 1% | ⛔ **LOST** | ⛔ | Third nominee's share % |
| 75 | `Holder 1 Aadhaar info` | `Y`, `N` | 99% | `holder_1_aadhaar_info` | **`gold.investor_kyc_status.fh_g_aadharlink`** | Is the first holder's Aadhaar linked? **KFintech only** |
| 76 | `Holder 2 Aadhaar info` | `N`, `Y` | 92% | `holder_2_aadhaar_info` | `gold.investor_kyc_status.jh1_aadharlink` | Second holder's Aadhaar link |
| 77 | `Holder 3 Aadhaar info` | `N`, `Y` | 88% | `holder_3_aadhaar_info` | `gold.investor_kyc_status.jh2_aadharlink` | Third holder's Aadhaar link |
| 78 | `Guardian Aadhaar info` | `N`, `Y` | 88% | `guardian_aadhaar_info` | stops at Silver | Guardian's Aadhaar link |
| 79 | `Nominee Address1` | `F.F.A-3 BHUMIKA APPART…` | 21% | ⛔ **LOST** | ⛔ | First nominee's address line 1 |
| 80 | `Nominee Address2` | `NR.BODYLINE HOSPITAL` | 18% | ⛔ **LOST** | ⛔ | Address line 2 |
| 81 | `Nominee Address3` | `VIKASH GRUH ROAD PALDI` | 11% | ⛔ **LOST** | ⛔ | Address line 3 |
| 82 | `Nominee City` | `AHMEDABAD` | 21% | ⛔ **LOST** | ⛔ | City |
| 83 | `Nominee State` | `GUJARAT` | 5% | ⛔ **LOST** | ⛔ | State |
| 84 | `Nominee Pin code` | `380007` | 86% | ⛔ **LOST** | ⛔ | PIN code |
| 85 | `Nominee phone residence` | *(blank)* | 0% | ⛔ **LOST** | ⛔ | Phone |
| 86 | `Nominee Email` | `TEJAS200@GMAIL.COM` | 9% | ⛔ **LOST** | ⛔ | Email |
| 87 | `Nominee2 Address1` | `22 AMBRISH SOCIETY` | 0% | `nominee2_address1` | stops at Silver | Second nominee's address line 1 |
| 88 | `Nominee2 Address2` | `RANIP` | 0% | `nominee2_address2` | stops at Silver | Address line 2 |
| 89 | `Nominee2 Address3` | `AHMEDABAD` | 0% | `nominee2_address3` | stops at Silver | Address line 3 |
| 90 | `Nominee2 City` | `AHMEDABAD` | 0% | `nominee2_city` | stops at Silver | City |
| 91 | `Nominee2 State` | `Gujarat` | 0% | `nominee2_state` | stops at Silver | State |
| 92 | `Nominee2 Pin code` | `382480` | 5% | ⛔ **LOST** | ⛔ | PIN code. Tidies to `nominee2_pin_code`; the list wants `nominee2_pincode` |
| 93 | `Nominee2 phone residence` | *(blank)* | 0% | ⛔ **LOST** | ⛔ | Phone |
| 94 | `Nominee2 Email` | `TEJAS200@GMAIL.COM` | 0% | `nominee2_email` | stops at Silver | Email |
| 95 | `Nominee3 Address1` | `B34 SHRINAGAR SOCIETY AKOTA` | 0% | `nominee3_address1` | stops at Silver | Third nominee's address line 1 |
| 96 | `Nominee3 Address2` | *(blank)* | 0% | `nominee3_address2` | stops at Silver | Address line 2 |
| 97 | `Nominee3 Address3` | *(blank)* | 0% | `nominee3_address3` | stops at Silver | Address line 3 |
| 98 | `Nominee3 City` | `VADODARA` | 0% | `nominee3_city` | stops at Silver | City |
| 99 | `Nominee3 State` | *(blank)* | 0% | `nominee3_state` | stops at Silver | State |
| 100 | `Nominee3 Pin code` | `390020` | 1% | ⛔ **LOST** | ⛔ | PIN code |
| 101 | `Nominee3 phone residence` | *(blank)* | 0% | ⛔ **LOST** | ⛔ | Phone |
| 102 | `CKYC NO` | `20018987247544` | 80% | `ckyc_no` | `gold.holdings.kyc_status` → `Verified` when present | First holder's central-KYC number |
| 103 | `JH1 CKYC` | `20077861793716` | 46% | `jh1_ckyc` | stops at Silver | Second holder's CKYC number |
| 104 | `JH2 CKYC` | `30016136575845` | 39% | `jh2_ckyc` | stops at Silver | Third holder's CKYC number |
| 105 | `Guardian CKYC NO` | `50069302007140` | 39% | `guardian_ckyc_no` | stops at Silver | Guardian's CKYC number |
| 106 | `Joint Holder 1st Resi Phone No` | *(blank)* | 0% | `joint_holder_1st_resi_phone_no` | stops at Silver | Second holder's home phone |
| 107 | `Joint Holder 2nd Resi Phone No` | *(blank)* | 0% | `joint_holder_2nd_resi_phone_no` | stops at Silver | Third holder's home phone |
| 108 | `Investors Resi FaxNo` | `0265` | 0% | `investors_resi_faxno` | stops at Silver | Home fax |
| 109 | `KycGFlag` | `Y`, `P` | 3% | `kycgflag` | `gold.investor_kyc_status.gu_kyc` | Guardian's KYC status |
| 110 | `Demat Folio flag` | `N` | 100% | ⛔ **LOST** | ⛔ — so `gold.holdings.demat_flag` is **always empty for KFintech folios** | Is the folio in demat form? Tidies to `demat_folio_flag`; the list wants `demat_flag`, `demat` or the spaced `Demat Folio flag`, which the tidy-up has already changed |
| 111 | `Nominee Opt Out flag` | `N`, `Y` | 90% | `nominee_opt_out_flag` | stops at Silver | Did the investor decline to nominate anyone? |
| 112 | `Nominee DOB` | `10/11/1951` | 19% | `nominee_dob` | stops at Silver | Nominee's date of birth |
| 113 | `Joint holder 1 contact number` | `9825461461` | 11% | `joint_holder_1_contact_number` | stops at Silver | Second holder's mobile |
| 114 | `Joint holder 1 Email id` | `prerakmpatel@gmail.com` | 11% | `joint_holder_1_email_id` | stops at Silver | Second holder's email |
| 115 | `Joint holder 2 contact number` | `9376211721` | 3% | `joint_holder_2_contact_number` | stops at Silver | Third holder's mobile |
| 116 | `Joint holder 2 Email id` | `ACCOUNTS@SANGATH.ORG` | 3% | `joint_holder_2_email_id` | stops at Silver | Third holder's email |
| 117 | `Nominee Guardian Name` | `KAMLESH PATEL` | 0% | `nominee_guardian_name` | stops at Silver | Guardian of a minor nominee |
| 118 | `emailconcern` | `Not Provided` | 2% | `emailconcern` | stops at Silver | Whose email address is on file |
| 119 | `emailrelationship` | `SELF`, `SPOUSE` | 4% | `emailrelationship` | stops at Silver | Relationship of the email's owner |
| 120 | `MobileRelationship` | `SELF`, `SPOUSE` | 3% | `mobilerelationship` | stops at Silver | Relationship of the mobile's owner |

**Score for this file: 86 of 121 columns load. 35 are lost.** The dropped set includes the PAN, date of birth, mobile number, the investor's whole postal address, the bank account number, the guardian's name and PAN, the depository ID, the demat flag, and every single first-nominee field.

**What this means in practice.** For a KFintech folio, `gold.clients.pan` cannot come from this file. It is filled instead from the transaction file (`PAN1`) or the SIP file (`PAN`), matched on folio number. That works — but it means any KFintech investor who has never transacted and has no SIP reaches Gold with no PAN at all, and because PAN is the key of `gold.clients`, those investors collide with each other.

---

## 5. CAMS R49 — the SIP file

**File name pattern:** ends `R49.csv`  ·  **44 columns**  ·  **738 rows in the sample**
**Goes to:** `bronze.sip_master_new` → `silver.sip_master_new` → `gold.sip`

One row = one standing instruction: a SIP, an STP or an SWP.

| # | File column | Example value | Filled | → Bronze | → Gold | What it is, plainly |
|---|---|---|---|---|---|---|
| 1 | `PRODUCT` | `B331G`, `B92` | 100% | `product_code` | stops at Silver ⚠️ | The scheme code. Note `gold.sip.scheme_code` reads `scheme_code`, not this — so this longer code does not reach Gold |
| 2 | `SCHEME` | `Aditya Birla Sun Life Savings Fund…` | 100% | ⛔ **LOST** | ⛔ — so `gold.sip.scheme_name` is **always empty for CAMS SIPs** | The scheme's full name. The list wants `SCHEME_NAME` or `Scheme Name`; CAMS writes plain `SCHEME`. See §8 item 4 |
| 3 | `FOLIO_NO` | `1014327918` | 100% | `folio_no` | `gold.sip.folio_number` | Folio number |
| 4 | `INV_NAME` | `Kashyap B Patel` | 100% | `inv_name` | stops at Silver | Investor's name |
| 5 | `AUT_TRNTYP` | `SO`, `P`, `DTP` | 100% | `aut_trntyp` | stops at Silver ⚠️ | What kind of standing instruction. `gold.sip.sip_type` is deliberately left empty, so this does not reach Gold |
| 6 | `AUTO_TRNO` | `3130`, `514308` | 100% | `auto_trno` | stops at Silver | The RTA's serial number for the registration |
| 7 | `AUTO_AMOUNT` | `150000`, `50000` | 100% | `auto_amount` | `gold.sip.amount` | The instalment amount |
| 8 | `FROM_DATE` | `1/10/2010 12:00 AM` | 99% | `from_date` | `gold.sip.start_date` | When the SIP starts |
| 9 | `TO_DATE` | `12/10/2025 12:00 AM` | 99% | `to_date` | `gold.sip.end_date` | When it is due to finish |
| 10 | `CEASE_DATE` | `7/12/2010 12:00 AM` | 43% | `cease_date` | `gold.sip.ceased_date` | When it actually stopped, if it stopped early |
| 11 | `PERIODICITY` | `OM`, `SM`, `OW` | 99% | `periodicity` | `gold.sip.frequency` | How often it debits. `OM` = once monthly, `SM` = twice monthly, `OW` = once weekly. **These short codes are not standardised anywhere**, which is the root of the duplicate-SIP problem |
| 12 | `PERIOD_DAY` | `10`, `28`, `7,14,21,28,1` | 98% | `period_day` | `gold.sip.sip_day` | Which day of the month it debits. Can be a **list** of days, which is why the Gold column often ends up empty — a list is not a number |
| 13 | `INV_IIN` | `0` | 100% | `inv_iin` | stops at Silver | Always `0` |
| 14 | `PAYMENT_MODE` | `TR`, `BD`, `STEP` | 98% | `payment_mode` | stops at Silver | How the money is collected — transfer, bank debit, step-up |
| 15 | `TARGET_SCHEME` | `ABSL Infrastructure Fund-PLAN-Regular-Growth` | 33% | `target_scheme` | stops at Silver | For an STP, the scheme money moves **into**. CAMS puts the full name here; KFintech puts a short code — the same Bronze column holds both |
| 16 | `REG_DATE` | `12/10/2009 12:00 AM` | 100% | `reg_date` | `gold.sip.registered_date` | When the instruction was registered |
| 17 | `SUBBROKER` | `100315`, `111808` | 0% | `subbroker` | stops at Silver | Sub-broker |
| 18 | `REMARKS` | `Customer opted Cancellation.` | 25% | `remarks` | stops at Silver | Why it stopped, usually |
| 19 | `TOP_UP_FRQ` | `Y` | 3% | `top_up_frq` | stops at Silver | Is the instalment set to increase over time? |
| 20 | `TOP_UP_AMT` | `0`, `1500` | 100% | `top_up_amt` | stops at Silver | By how much |
| 21 | `AC_TYPE` | `SB`, `CA`, `NRE` | 26% | `ac_type` | stops at Silver | Bank account type |
| 22 | `BANK` | `Bank of India` | 55% | `bank` | stops at Silver | The bank being debited |
| 23 | `BRANCH` | `Ellora Park`, `FORT` | 23% | `branch` | stops at Silver | Bank branch. **The mapping file itself warns that this is not the same thing as KFintech's `Branch`**, which is an office code — but both load into the same column anyway |
| 24 | `INSTRM_NO` | `250810100013257` | 28% | `instrm_no` | stops at Silver | The mandate or instrument number |
| 25 | `CHEQ_MICR_NO` | `390013011` | 27% | `cheq_micr_no` | stops at Silver | Cheque MICR code |
| 26 | `AC_HOLDER_NAME` | `Yogendra Bhatt` | 18% | `ac_holder_name` | stops at Silver | Whose bank account is being debited |
| 27 | `PAN` | `ALGPP2475B` | 97% | `pan` | 3rd choice for `gold.clients.pan` | The investor's tax number |
| 28 | `TOP_UP_PERC` | `0` | 100% | `top_up_perc` | stops at Silver | Top-up as a percentage |
| 29 | `EUIN` | `E027648` | 67% | `euin` | stops at Silver | The salesperson's licence number |
| 30 | `SUB_ARN_CODE` | `ARN-76793` | 0% | `sub_arn_code` | stops at Silver | Sub-distributor's ARN |
| 31 | `TER_LOCATION` | `B`, `T` | 1% | `ter_location` | stops at Silver | Terminal location letter |
| 32 | `SCHEME_CODE` | `331G`, `92`, `43N` | 100% | `scheme_code` | **`gold.sip.scheme_code`** | The short scheme code, without the AMC letter |
| 33 | `TARGET_SCHEME_CODE` | `293G`, `51` | 33% | `target_scheme_code` | stops at Silver | For an STP, the code of the scheme money moves into |
| 34 | `AMC_CODE` | `B`, `D`, `FTI` | 100% | `amc_code` | `gold.sip.amc_code` | The fund house |
| 35 | `USER_CODE` | `RVL`, `NSANDHYA` | 99% | `user_code` | stops at Silver | Who registered it |
| 36 | `PACKAGE_NAME` | *(blank)* | 0% | `package_name` | stops at Silver | Product package name |
| 37 | `SPECIAL_PRODUCT` | *(blank)* | 0% | `special_product` | stops at Silver | Special product marker |
| 38 | `SUBTRXNDESC` | `Systematic P/W/S`, `Automatic W/S` | 64% | `subtrxndesc` | stops at Silver | Sub-transaction description |
| 39 | `PAUSE_FROM_DATE` | `2/1/2023 12:00 AM` | 0% | `pause_from_date` | stops at Silver | If the SIP is paused, from when |
| 40 | `PAUSE_TO_DATE` | `2/1/2023 12:00 AM` | 0% | `pause_to_date` | stops at Silver | Until when |
| 41 | `FOLIO_OLD` | `19734401` | 1% | `folio_old` | stops at Silver | Previous folio number |
| 42 | `FT_SIP_REGNO` | `BSL_BSSTP-143223` | 2% | `ft_sip_regno` | **`gold.sip.sip_reg_no`, 1st choice** | The SIP registration number. **Only 2% filled**, so 98% of CAMS SIPs fall through to column 44 for their identity |
| 43 | `SCHEME_FOLIO_NUMBER` | `2019909146236` | 1% | `scheme_folio_number` | stops at Silver | Folio at scheme level |
| 44 | `REQUEST_REF_NO` | `592042`, `BD122876` | 98% | `request_ref_no` | **`gold.sip.sip_reg_no`, 2nd choice — in practice the real one** | The request reference. Because `FT_SIP_REGNO` is nearly always blank, this is what actually identifies a CAMS SIP in Gold |

**Score for this file: 43 of 44 columns load. 1 is lost (`SCHEME`).**

**Not in this file at all**, so always empty in Gold for CAMS SIPs: `status` (`gold.sip.status`), `umrncode` (`gold.sip.mandate_id`), `no_of_installments`, `plan`, `ModifyFlag`.

---

## 6. KFintech MFSD243 — the SIP file

**File name pattern:** contains `mfsd243`  ·  **40 columns**  ·  **658 rows in the sample**
**Goes to:** the same `bronze.sip_master_new` as CAMS R49.

**This is the worst-affected file of the six.** 10 of its 40 columns are dropped, and they include the start date, the end date and the scheme code.

| # | File column | Example value | Filled | → Bronze | → Gold | What it is, plainly |
|---|---|---|---|---|---|---|
| 1 | `Zone` | `CHANNEL`, `Exchange` | 99% | `zone` | stops at Silver | Sales zone |
| 2 | `Branch` | `NS77`, `WB99` | 99% | `branch` | stops at Silver | KFintech office code. Shares a Bronze column with CAMS's bank-branch **name** |
| 3 | `Location` | `MUMBAI`, `AHMEDABAD` | 99% | `ter_location` | stops at Silver | City. Shares a Bronze column with CAMS's single-letter terminal code |
| 4 | `Ihno` | `636055886` | 100% | `inv_iin` | stops at Silver | KFintech's in-house reference. Shares a column with CAMS's investor identification number |
| 5 | `Folio` | `477244685194` | 100% | `folio_no` | `gold.sip.folio_number` | Folio number |
| 6 | `Investor Name` | `KARTIK DONGA` | 100% | ⛔ **LOST** | ⛔ | Investor's name. Tidies to `investor_name`; the list wants `INV_NAME` or the spaced `Investor Name` |
| 7 | `RegistrationDate` | `03/07/2026` | 99% | `reg_date` | `gold.sip.registered_date` | When the SIP was registered |
| 8 | `Start Date` | `10/07/2026` | 100% | ⛔ **LOST** | ⛔ — so `gold.sip.start_date` is **always empty for KFintech SIPs** | When the SIP starts. Tidies to `start_date`; the list wants `FROM_DATE` or the spaced `Start Date` |
| 9 | `End Date` | `22/10/2026` | 100% | ⛔ **LOST** | ⛔ — so `gold.sip.end_date` is **always empty for KFintech SIPs** | When it finishes |
| 10 | `No Of Installments` | `16`, `25` | 100% | ⛔ **LOST** | ⛔ | How many instalments are planned. Only KFintech supplies this at all |
| 11 | `Amount` | `65000`, `50000` | 100% | `auto_amount` | `gold.sip.amount` | Instalment amount |
| 12 | `Scheme` | `LP`, `LF`, `EB` | 100% | ⛔ **LOST** | ⛔ — so `gold.sip.scheme_code` is **always empty for KFintech SIPs** | The short scheme code. The list wants `SCHEME_CODE` or `Scheme_code`; KFintech writes plain `Scheme` |
| 13 | `Plan` | `IG`, `RG`, `GP` | 100% | `plan` | stops at Silver | Which plan — institutional growth, regular growth, growth plan |
| 14 | `AgentCode` | `ARN-266051` | 100% | `sub_arn_code` | stops at Silver | The distributor's ARN |
| 15 | `AgentName` | *(blank)* | 0% | `agent_name` | stops at Silver | Distributor's name |
| 16 | `Subbroker` | `MFS76793`, `0` | 7% | `subbroker` | stops at Silver | Sub-broker |
| 17 | `Scheme Name` | `NIPPON INDIA LOW DURATION FUND…` | 100% | `scheme_name` | **`gold.sip.scheme_name`** | The scheme's full name. This one **does** load — the tidied heading `scheme_name` happens to match the CAMS entry on the list. It is why KFintech SIPs have a scheme name in Gold and CAMS SIPs do not |
| 18 | `PAN` | `AHNPD2091C` | 99% | `pan` | 3rd choice for `gold.clients.pan` | The investor's tax number |
| 19 | `SipType` | `Existing Folio with SIP`, `SIP status updated on 05-06-2020 by NS77` | 50% | `aut_trntyp` | stops at Silver | Notes about the SIP rather than a clean type code |
| 20 | `SIP Mode` | `Perpetual SIP`, `Normal SIP` | 66% | ⛔ **LOST** | ⛔ | Perpetual (no end date) or normal. Tidies to `sip_mode`; the list wants `PAYMENT_MODE` or the spaced `SIP Mode` |
| 21 | `Fund Code` | `RMF`, `117` | 100% | ⛔ **LOST** | ⛔ — so `gold.sip.amc_code` is **always empty for KFintech SIPs** | The fund-house code. There is no entry for it on the SIP list at all — the list only accepts `AMC_CODE` |
| 22 | `Product Code` | `RMFLPIG`, `117EBRG` | 100% | ⛔ **LOST** | ⛔ | The full scheme code. Tidies to `product_code`; the list wants `PRODUCT` or the spaced `Product Code` |
| 23 | `Frequency` | `Weekly`, `Monthly`, `MONTHLY` | 100% | `periodicity` | `gold.sip.frequency` | How often it debits. **Note the same word in two capitalisations in one file** — this is the direct cause of duplicate SIP rows |
| 24 | `Trtype` | `STP`, `SIP`, `SWP` | 100% | `subtrxndesc` | stops at Silver | What kind of instruction it is |
| 25 | `To Scheme` | `MT`, `MF`, `GF` | 32% | ⛔ **LOST** | ⛔ | For an STP, the scheme money moves into |
| 26 | `To Plan` | `GP`, `RG`, `DP` | 32% | ⛔ **LOST** | ⛔ | The plan of that target scheme |
| 27 | `TerminateDate` | `06/07/2026` | 46% | `cease_date` | `gold.sip.ceased_date` | When it stopped |
| 28 | `Status` | `Live STP`, `Expired`, `Live SIP` | 100% | `status` | **`gold.sip.status`** | Is it running? KFintech is the **only** source for this — CAMS R49 has no status column |
| 29 | `ToProductCode` | `RMFMTGP` | 32% | `target_scheme_code` | stops at Silver | Target scheme's full code |
| 30 | `ToSchemeName` | `NIPPON INDIA NIFTY 500 MOMENTUM 50 INDEX FUND` | 32% | `target_scheme_name` | stops at Silver | Target scheme's name |
| 31 | `ECSNO` | `380240016` | 83% | `instrm_no` | stops at Silver | The ECS mandate number |
| 32 | `ECSBankName` | `HDFC Bank Ltd` | 100% | `bank` | stops at Silver | The bank being debited |
| 33 | `ECSAcno` | `08881050008434` | 100% | `ecs_account_no` | stops at Silver | The account being debited |
| 34 | `ECSHolderName` | *(blank)* | 0% | `ac_holder_name` | stops at Silver | Whose account it is |
| 35 | `RegSlno` | `185100`, `187611` | 77% | `auto_trno` | stops at Silver | Registration serial number |
| 36 | `InvDpId` | *(blank)* | 0% | `inv_dp_id` | stops at Silver | Depository participant ID |
| 37 | `InvClientId` | *(blank)* | 0% | `inv_client_id` | stops at Silver | Depository client ID |
| 38 | `DP_InvName` | *(blank)* | 0% | `dp_inv_name` | stops at Silver | Name as held at the depository |
| 39 | `ModifyFlag` | *(blank)* | 0% | `modify_flag` | stops at Silver | Has the SIP been changed? |
| 40 | `umrncode` | `HDFC0000000000742016` | 3% | `umrn_code` | **`gold.sip.mandate_id`** | The bank mandate reference. KFintech only |

**Score for this file: 30 of 40 columns load. 10 are lost.**

**And there is a bigger problem hiding here.** `gold.sip` is keyed on RTA + SIP registration number. KFintech supplies neither `FT_SIP_REGNO` nor `REQUEST_REF_NO`, the two columns that feed that key. So **every KFintech SIP arrives in Gold with a blank registration number**, and because blank is treated as a value rather than as "unknown", the second KFintech SIP onwards is treated as a duplicate of the first and dropped.

---

## 7. The same thing, two names — quick translation table

Keep this next to you when you have a CAMS file open in one window and a KFintech file in another.

| The idea | CAMS transactions (R2) | KFintech transactions (MFSD201) | Database column |
|---|---|---|---|
| Fund house | `AMC_CODE` | `td_fund` (but `fmcode` is loaded instead ⚠️) | `amc_code` |
| Scheme code | `PRODCODE` | `fmcode` (but `smcode` is loaded instead ⚠️) | `prodcode` |
| Scheme name | `SCHEME` | `funddesc` | `scheme` / `funddesc` |
| Folio | `FOLIO_NO` | `td_acno` | `folio_no` |
| Investor name | `INV_NAME` | `invname` | `inv_name` |
| PAN | `PAN` | `PAN1` | `pan` |
| Receipt number | `TRXNNO` | `td_trno` | `trxnno` |
| Type code | `TRXNTYPE` | `td_trtype` | `trxntype` |
| Type in words | `TRXN_NATURE` | `trdesc` | `trxn_nature` |
| Buy or sell? | `TRXN_TYPE_FLAG` | `td_purred` | both, read together |
| Trade date | `TRADDATE` (month first) | `td_trdt` (day first) | `traddate` |
| Post date | `POSTDATE` | `td_prdt` | `postdate` |
| NAV | `PURPRICE` | `td_nav` | `purprice` |
| Units | `UNITS` | `td_units` | `units` |
| Amount | `AMOUNT` | `td_amt` | `amount` |
| Load charged | `LOAD` | `load1` | `load` |
| ARN | `BROKCODE` | `td_broker` | `brokcode` |
| Bank | `BANK_NAME` | `chqbank` | `bank_name` |

| The idea | CAMS folios (R9) | KFintech folios (MFSD211) | Database column |
|---|---|---|---|
| Folio | `FOLIOCHK` | `Folio` | `folio_no` |
| Investor name | `INV_NAME` | `Investor Name` | `investor_name` |
| PAN | `PAN_NO` | `PAN Number` ⛔ lost | `pan_no` |
| Date of birth | `INV_DOB` | `Date of Birth` ⛔ lost | `dob` |
| Mobile | `MOBILE_NO` | `Mobile Number` ⛔ lost | `mobile_no` |
| Address | `ADDRESS1/2/3` | `Address #1/2/3` ⛔ lost | `address1/2/3` |
| Bank account | `AC_NO` ⛔ lost | `BankAccno` ⛔ lost | `bank_account_no` — **empty for both** |
| First nominee | `NOM_NAME` | `Nominee` ⛔ lost | `nominee1_name` |
| Nominee share | `NOM_PERCENTAGE` | `Nominee Ratio` ⛔ lost | `nominee1_percentage` |
| Demat? | `DEMAT` | `Demat Folio flag` ⛔ lost | `demat_flag` |
| KYC status | *not supplied* | `Kyc1Flag` | `kyc1flag` |
| Aadhaar linked? | `AADHAAR` ⛔ lost | `Holder 1 Aadhaar info` | `holder_1_aadhaar_info` |
| Units held | `CLOS_BAL` | *not supplied* | `closing_balance` |
| Value held | `RUPEE_BAL` | *not supplied* | `rupee_balance` |
| Report date | `REP_DATE` | `Report Date` | `report_date` |

| The idea | CAMS SIPs (R49) | KFintech SIPs (MFSD243) | Database column |
|---|---|---|---|
| Registration number | `FT_SIP_REGNO` (2% filled) → `REQUEST_REF_NO` | *neither supplied* | `ft_sip_regno` / `request_ref_no` |
| Folio | `FOLIO_NO` | `Folio` | `folio_no` |
| Scheme code | `SCHEME_CODE` | `Scheme` ⛔ lost | `scheme_code` |
| Scheme name | `SCHEME` ⛔ lost | `Scheme Name` | `scheme_name` |
| Fund house | `AMC_CODE` | `Fund Code` ⛔ lost | `amc_code` |
| Amount | `AUTO_AMOUNT` | `Amount` | `auto_amount` |
| Frequency | `PERIODICITY` (`OM`, `OW`) | `Frequency` (`Monthly`, `MONTHLY`) | `periodicity` |
| Start date | `FROM_DATE` | `Start Date` ⛔ lost | `from_date` |
| End date | `TO_DATE` | `End Date` ⛔ lost | `to_date` |
| Registered on | `REG_DATE` | `RegistrationDate` | `reg_date` |
| Stopped on | `CEASE_DATE` | `TerminateDate` | `cease_date` |
| Status | *not supplied* | `Status` | `status` |
| Mandate reference | *not supplied* | `umrncode` | `umrn_code` |
| Debit day | `PERIOD_DAY` | *not supplied* | `period_day` |

---

## 8. Everything that is currently being dropped, in one list

This is the actionable summary. **55 columns across the six files are read out of your file and then thrown away.**

### Item 1 — CAMS R2 `REP_DATE`

The mapping list has the line for `rep_date` commented out. Consequence: `bronze.transaction_master_new.rep_date` is always empty, and because `gold.holdings.as_on_date` reads it, **every holding in Gold has an empty "as on" date**.

### Item 2 — KFintech transaction columns loaded into the wrong slots

| KFintech column | Currently becomes | Should almost certainly become |
|---|---|---|
| `fmcode` (`117EBRG` — a scheme code) | `amc_code` | `prodcode` |
| `td_fund` (`117` — a fund-house code) | `td_fund`, unused | `amc_code` |
| `smcode` (blank on 70% of rows) | `prodcode` | nothing |
| `td_agent` (`ARN-266051` — the ARN) | `usercode` | `brokcode` |
| `td_branch` (`AHMEDABAD` — a city) | `subbrok` | `location` |

Consequences: `gold.amc` fills up with scheme codes that have no matching AMC name (this is the known "198 KFintech codes with no name" issue); KFintech transactions mostly fail to find their `scheme_id`, so they appear in `gold.transactions` with no scheme; and half of KFintech transactions arrive with no ARN because the real ARN went into the wrong column.

### Item 3 — column names that never match because of the tidy-up

The tidy-up turns spaces and `#` into underscores or nothing, but the shopping lists still carry the untidied spelling. These 45 columns are the result.

**CAMS R9 (8 columns)**

| Column in your file | Should fill | Why it misses |
|---|---|---|
| `AC_NO` | `bank_account_no` | list has `bank_account_no`, `bnkacno` |
| `AC_TYPE` | `account_type` | list has `account_type`, `bnkactype` |
| `B_ADDRESS1/2/3` | `bank_address1/2/3` | list has `bank_address1`, `badd1` |
| `B_CITY` | `bank_city` | list has `bank_city`, `bcity` |
| `GUARD_NAME` | `guardian_name` | list has `guardian_name`, `guardian` |
| `AADHAAR` | *(no target exists)* | needs a new entry |
| `REMARKS` | *(no target exists)* | needs a new entry |
| `JH1_EMAIL`, `JH2_EMAIL` | `joint_holder_1/2_email_id` | list has only the long name |
| `JH1_MOBILE_NO`, `JH2_MOBILE_NO` | `joint_holder_1/2_contact_number` | list has only the long name |
| `NOM_PH_RES`, `NOM2_PH_RES`, `NOM3_PH_RES` | nominee home phone | the office-phone column already claimed the slot |

**KFintech MFSD211 (24 columns)** — `Address #1/2/3`, `Date of Birth`, `Phone Residence`, `Phone Office`, `BankAccno`, `Bank Address #1/2/3`, **`PAN Number`**, `Mobile Number`, `GuardianName`, `GuardPanNo`, `DPID`, `Demat Folio flag`, `Nominee`, `Nominee2`, `Nominee3`, `Nominee Relation`, `Nominee Ratio`, `Nominee2 Ratio`, `Nominee3 Ratio`, `Nominee Address1/2/3`, `Nominee City`, `Nominee State`, `Nominee Pin code`, `Nominee Email`, `Nominee2 Pin code`, `Nominee3 Pin code`.

**KFintech MFSD243 (8 columns)** — `Product Code`, `Investor Name`, `No Of Installments`, `SIP Mode`, `Start Date`, `End Date`, `To Scheme`, `To Plan`.

**KFintech MFSD201 (1 column)** — `CAN`, which has no target column at all.

The fix is the same in every case: run the shopping-list names through the *same* tidy-up as the file headings before comparing them. That single change recovers 45 of the 55 dropped columns without any mapping decisions being made.

### Item 4 — two genuine gaps in the shopping lists

| Column | File | Needs adding to the list for |
|---|---|---|
| `SCHEME` | CAMS R49 | `scheme_name` |
| `Scheme` | KFintech MFSD243 | `scheme_code` |
| `Fund Code` | KFintech MFSD243 | `amc_code` |

### Item 5 — data that is loaded but never used, and probably should be

| Column | Where it sits | Why it matters |
|---|---|---|
| `CLOS_BAL` | `silver.investor_master.closing_balance` | This is the units CAMS says the folio holds. `gold.holdings.units` is instead calculated by adding up transactions. Comparing the two would catch missing transactions immediately |
| `RUPEE_BAL` | `silver.investor_master.rupee_balance` | This is the market value CAMS says the folio holds — the exact figure `gold.holdings.market_value` is currently empty for |
| `INV_DOB` | `silver.investor_master.dob` | `gold.clients.date_of_birth` is left empty for the application to fill, but the RTA already told us |
| `EMAIL`, `MOBILE_NO` | `silver.investor_master` | Same — `gold.clients.email` and `mobile` are empty by design |

### Item 6 — identity columns that arrive blank

| Gold table | Key column | Problem |
|---|---|---|
| `gold.sip` | `sip_reg_no` | KFintech supplies nothing for it, so all KFintech SIPs share one blank key and only the first survives |
| `gold.clients` | `pan` | KFintech's `PAN Number` is dropped, so a KFintech investor with no transactions and no SIP has no PAN |
| `gold.investor_kyc_status` | `amc_code` | KFintech's folio file has no AMC code column at all, so every KFintech folio is excluded from the WBR56 report |

---

## 9. Code book — what the short values in the files mean

### Transaction direction

| CAMS `TRXN_TYPE_FLAG` | KFintech `td_purred` | Effect on units held |
|---|---|---|
| Fresh Purchase, Additional Purchase, Fresh/Additional Purchase Systematic, Switch In, Dividend Reinvest, Bonus, NFOAP, NFO FP, NFO SI, TI Into New/Existing Folio, TICOB | `P` | **adds** |
| Partial/Full Redemption, Partial/Full Switch Out, Transfer Out, TOCOB | `R` | **subtracts** |
| Dividend Payout, DRO | `D` | **no change** — money moves, units do not |

### How the folio is held (`HOLDING_NATURE` / `Mode of Holding Description`)

| Code | Means |
|---|---|
| `SI` / `SINGLE` | One holder only |
| `AS` / `ANYONE OR SURVIVOR` | Any one holder can act alone |
| `JO` / `JOINT` | All holders must sign together |
| `EO` / `ES` / `EITHER OR SURVIVOR` | Either of two holders can act |

### Tax status

| Code | Means |
|---|---|
| `I` / `1` / `Individual` | An individual person |
| `H` / `HUF` | Hindu Undivided Family |
| `N` | Non-resident Indian |

### SIP frequency (`PERIODICITY` / `Frequency`)

| CAMS code | KFintech word | Means |
|---|---|---|
| `OM` | `Monthly` / `MONTHLY` | Once a month |
| `SM` | — | Twice a month |
| `OW` | `Weekly` | Once a week |
| `OQ` | `Quarterly` | Once a quarter |

The two RTAs, and even the same file, spell these differently. That is why the same SIP appears several times in Silver — the rows look different to the computer even though they describe the same instruction.

### SIP payment mode

| CAMS code | Means |
|---|---|
| `TR` | Direct transfer |
| `BD` | Bank debit / ECS |
| `STEP` | Step-up SIP, instalment rises over time |

| KFintech word | Means |
|---|---|
| `Perpetual SIP` | No end date — runs until cancelled |
| `Normal SIP` | Has a fixed end date |

### Dividend option (`divopt` / `Dividend Option`)

| Code | Means |
|---|---|
| `G` | Growth — profits stay invested |
| `D` | Dividend paid out in cash |
| `R` | Dividend reinvested into more units |

### KFintech transaction type (`td_trtype`)

| Code | Means |
|---|---|
| `SIN` | Systematic investment (a SIP instalment) |
| `RED` | Redemption |
| `FUL` | Full switch or full redemption |
| `STP` | Systematic transfer |
| `SWP` | Systematic withdrawal |

### EUIN validity (`EUIN_VALID`)

| Value | Means |
|---|---|
| `Y` | The salesperson's licence was valid that day |
| `N` | It was not — the transaction appears in the WBR68 report |
| `F` | Also invalid, a different failure code — also appears in WBR68 |
| *blank* | **Not checked.** This is not the same as invalid, and treating it as invalid inflates the report from 406 rows to 44,299 |

---

## 10. One-paragraph summary for someone who reads nothing else

Six files come in. The CAMS files are mapped almost perfectly — 79 of 80 transaction columns, 93 of 101 folio columns, 43 of 44 SIP columns. The KFintech files are not: 35 of 121 folio columns and 10 of 40 SIP columns are read and then dropped, including the investor's PAN, date of birth, mobile number, address, every first-nominee field, and both SIP dates. Almost all of that is one small technical mismatch — headings are tidied before comparing, the list of expected headings is not — and fixing that one thing recovers 45 of the 55 dropped columns. Separately, five KFintech transaction columns are loaded into the wrong database columns, which is why KFintech schemes and AMCs do not link up properly in the Gold layer. None of this affects the CAMS side of the data, which is why the CAMS WBR reports come out well and the KFintech data looks thin.

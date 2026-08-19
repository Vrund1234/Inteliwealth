# KFintech WBR Mapping

How the four WBR reports are derived for KFintech, column by column, and what the KFIN feed
cannot fill. The companion to `cams-wbr-profile.md`, which profiles the provider's own CAMS
files; there is no KFintech equivalent of those files, so this document is a mapping rather
than a profile.

Written 2026-08-18 against the three KFintech files in
`/home/user/Inteliwealth-pipeline/files/excel/` and against `silver` as it stands. Every count
below was read off the file or the database, not estimated.

---

## Scope

Both RTAs land in the **same three gold tables**. There is no KFIN table, no KFIN column and no
KFIN branch in the schema:

| Report | Gold table | CAMS rows | KFIN rows |
|---|---|---|---|
| WBR36 / WBR36H | `gold.brokerage_by_scheme` | 332 | 377 |
| WBR56 | `gold.investor_kyc_status` | 1,190 | 967 |
| WBR68 | `gold.invalid_euin` | 406 | 0 |

`source` leads every natural key:

```
brokerage_by_scheme   (source, report_period, report_variant, product_code)
investor_kyc_status   (source, amc_code, folio)
invalid_euin          (source, amc_code, trxn_no)
```

Two reasons, and the second matters more than the first. Folio numbers are unique per AMC, not
per country, so a CAMS folio and a KFIN folio can collide. And the two feeds fill *different*
parts of the layout — KFIN fills the KYC and Aadhaar block that CAMS leaves empty — so merging
them on one key would let a NULL from one feed overwrite a real value from the other.

Exports are per RTA, one set of files each, with the RTA in the filename:

```
WBR56-KYC status of Investor-CAMS.csv
WBR56-KYC status of Investor-KFIN.csv
```

`WBR_SOURCES=CAMS` exports one RTA; `WBR_SOURCES=ALL` writes a single unfiltered file per
report under the provider's bare stem.

---

## Boundary: nothing outside the WBR layer was changed

Every KFIN decision below is made in `etl_gold_wbr.py`, reading `silver` as it stands.
`mapping.py`, the ingestion scripts and the other gold loaders are untouched.

That boundary has a cost, and it is paid in WBR56. The shared ingestion mapping does not carry
some MFSD211 columns into silver, so the WBR layer cannot read them however it is written.
Those columns are listed under **Ingestion gaps** below and each one is also recorded in
`UNAVAILABLE` in `etl_gold_wbr.py`, naming the MFSD211 heading, so the run prints the reason
instead of an unexplained blank column.

---

## Source files

| File | Rows | Columns | Feeds |
|---|---|---|---|
| `MFSD201_WBTRN28912495_428923.csv` | 38,230 | 59 | WBR36, WBR68 |
| `MFSD211_WBMST9217829_386513.csv` | 1,444 | 121 | WBR56 |
| `MFSD243_WSREG8131655_1159890_0.csv` | 658 | 40 | none of the four |

All three are comma-separated CSV with a single header row — not the legacy BIFF `.xls` the
CAMS WBR reference files use.

MFSD243 is listed because it was checked and rejected as a source: its `AgentName` column is
the only candidate anywhere in the KFIN feed for WBR56's `brok_name`, and it is blank on all
658 rows.

### Two MFSD201 layouts live in silver

`silver.transaction_master_new` holds 76,460 KFIN rows in two shapes:

| Layout | Rows | `prodcode` | `amc_code` | `scheme` | Broker in |
|---|---|---|---|---|---|
| Long headers | 49,787 | `105MDGP` | `105` | full scheme name | `brokcode` |
| Short headers (the MFSD201 above) | 26,673 | empty | `128TSGP` | `TSGP` (plan suffix) | `usercode` |

The short-header layout is what the current MFSD201 produces. Its `fmcode` column *is* the
product code, and the shared ingestion mapping sends `fmcode` to `amc_code`, so on those rows
the product code sits in the AMC column and `prodcode` is empty.

The WBR layer reads it back out rather than changing that mapping:

```python
product_code = prodcode  or  (amc_code if amc_code != td_fund else None)
```

`td_fund` is the true AMC code (15 distinct values: `101`, `105`, `128`, `RMF`, …). Comparing
against it is what tells `128TSGP` parked in the wrong column apart from a plain AMC code of
`128`. If the ingestion mapping is ever corrected, `prodcode` fills in and the fallback stops
firing on its own.

### MFSD201 column roles, as verified

| Column | Distinct | Sample | What it is |
|---|---|---|---|
| `td_fund` | 15 | `101`, `128`, `RMF` | AMC code |
| `fmcode` | 183 | `101EQGP`, `128TSGP` | product code |
| `schpln` | 160 | `03GP`, `TSGP` | plan suffix, **not** a scheme name |
| `funddesc` | — | `Axis ELSS Tax Saver Fund - Regular Growth` | scheme name |
| `smcode` | 194 | `0`, `1`, `1103863` | **not** a product code |
| `td_agent` | 1 | `ARN-266051` | the distributor ARN |
| `td_broker` | 14 | `0`, `1078`, `MFS76793` | sub-broker code |
| `td_branch` | 18 | `AHMEDABAD`, `BARODA` | branch city |

---

## WBR36 / WBR36H — Brokerage summary by scheme

`silver.transaction_master_new` → `gold.brokerage_by_scheme`, 377 KFIN schemes.

| Report column | KFIN source | Note |
|---|---|---|
| `product_code` | `prodcode`, else `amc_code` when it differs from `td_fund` | `128SCGP`, `RMFSCGP` |
| `product_name` | `funddesc`, falling back to `scheme` | `scheme` is the plan suffix on short-header rows |
| `upfront` | — | not delivered |
| `afe` | — | not delivered |
| `trailer_fee` | — | `brokcomm` is 0 on all 38,230 rows, and `brokper` with it. Even populated it would be per-transaction commission, not trail computed on AUM |
| `trxn_charges` | — | `TrCharges` is blank on all 38,230 rows |
| `clawback` | — | not delivered |
| `incentives` | — | not delivered |

Same shape as CAMS: the scheme list is derivable, the money is not. All five measures load as
NULL rather than 0, so "unknown" stays distinguishable from "genuinely nil".

Only the STD variant is produced. Nothing in MFSD201 marks which schemes belong to the H
variant, so `WBR36H-…-KFIN` is written empty with its header rather than skipped.

CAMS product codes (`D104`, `TSCFG`) and KFIN product codes (`128SCGP`, `RMFSCGP`) are
different code systems and do not currently collide, but they are kept apart by `source`
anyway, and `source_row` restarts at 1 per RTA because each RTA is its own file.

---

## WBR56 — KYC status of Investor

`silver.investor_master` → `gold.investor_kyc_status`, 967 KFIN folios from 1,444 silver rows
(one row per folio per scheme, deduplicated to folio grain, latest `report_date` wins).

**This is the report the KFIN feed contributes most to.** MFSD211 fills the entire KYC and
Aadhaar block that the CAMS R9 file cannot.

| Report column | KFIN source | Populated |
|---|---|---|
| `amc_code` | `fund` ← MFSD211 `Fund` | 1,444 / 1,444 |
| `folio` | `folio_no` ← `Folio` | 1,444 |
| `brok_dlr_code` | `broker_code` ← `Broker Code` | 1,444 (`ARN-266051`) |
| `inv_name` | `investor_name` ← `Investor Name` | 1,444 |
| `jname1` | `joint_name_1` ← `Joint Name 1` | 218 |
| `jname2` | `joint_name_2` ← `Joint Name 2` | 10 |
| `jointpan1` | `pan2` ← `PAN2` | 221 |
| `jointpan2` | `pan3` ← `PAN3` | 10 |
| `city` | `city` ← `City` | 1,444 |
| `pincode` | `pincode` ← `Pincode` | 1,431 |
| `state` | `state` ← `State`, as `/Gujarat` | 1,440 |
| `location` | `city`, as `/AHMEDABAD` | 1,444 |
| `email` | `email` ← `Email` | 1,365 |
| `fax_res` | `fax_residence` ← `Fax Residence` | 4 |
| `fh_kyc` | `kyc1flag` ← `Kyc1Flag` | 1,422 |
| `jh1_kyc` | `kyc2flag` ← `Kyc2Flag` | 233 |
| `jh2_kyc` | `kyc3flag` ← `Kyc3Flag` | 30 |
| `gu_kyc` | `kycgflag` ← `KycGFlag` | 51 |
| `fh_g_aadharlink` | `holder_1_aadhaar_info` ← `Holder 1 Aadhaar info` | 1,443 |
| `jh1_aadharlink` | `holder_2_aadhaar_info` ← `Holder 2 Aadhaar info` | 1,335 |
| `jh2_aadharlink` | `holder_3_aadhaar_info` ← `Holder 3 Aadhaar info` | 1,283 |
| `rep_date` | `report_date` ← `Report Date` | 1,444 |
| `rep_from_date` / `rep_to_date` | min/max of `rep_date` **per RTA** | — |

`location` and `state` are written compound, `code/name`, in the provider's own convention.
The code half is empty for both RTAs: `bronze.state_code` carries a numeric `state_id`, not the
provider's two-letter code, so `/Gujarat` reads as "name known, code unknown" rather than
inventing a code.

The reporting window is computed per RTA. The two feeds are delivered on their own dates —
CAMS 2025, KFIN 15-Jul-2026 — and one shared window would print the CAMS span on the KFIN file.

### Not delivered by KFintech

| Report column | Why |
|---|---|
| `fh_kyc_desc`, `gu_kyc_desc`, `jh1_kyc_desc`, `jh2_kyc_desc` | MFSD211 carries the flag but no description. `CategoryDesc` and `StatusDesc` describe the investor category and tax status, not the KYC verdict |
| `brok_name` | MFSD211 carries `Broker Code` only, and MFSD243's `AgentName` is blank on all 658 rows |
| `country` | `Country` is blank on all 1,444 rows |
| `fax_off` | `Fax Office` is blank on all 1,444 rows |

### Ingestion gaps

MFSD211 delivers these and `silver.investor_master` does not carry them, because the shared
column mapping has no alias for the heading. Fixing them is an ingestion change affecting every
consumer of silver — `gold.clients`, `gold.holdings`, `gold.folio_nominees` — and is
deliberately **not** done in the WBR layer.

| Report column | MFSD211 heading | Rows in file | Silver target that stays empty |
|---|---|---|---|
| `tax_no` | `PAN Number` | 1,423 | `pan_no` |
| `address1` | `Address #1` | 1,444 | `address1` |
| `address2` | `Address #2` | 1,399 | `address2` |
| `address3` | `Address #3` | 1,117 | `address3` |
| `mobile_no` | `Mobile Number` | 1,355 | `mobile_no` |
| `phone_res` | `Phone Residence` | 79 | `phone_res` |
| `phone_off` | `Phone Office` | 52 | `phone_off` |
| `guardian` | `GuardianName` | 20 | `guardian_name` |
| `guardian_panno` | `GuardPanNo` | 28 | `guardian_pan` |

The cause is header normalisation: headings are lower-cased and space, `-` and `#` collapsed to
`_` before the alias lookup, so `Address #1` arrives as `address_1` and `PAN Number` as
`pan_number`, neither of which appears in `INVESTOR_MASTER_MAPPING`. Adding those aliases would
fill all nine columns; that is a one-line change per column in `mapping.py` plus a re-ingest,
and re-ingesting appends rather than replaces.

---

## WBR68 — Invalid EUIN Report

**Not producible from the KFintech feed. Zero rows, by structure rather than by filter.**

MFSD201 has no `euin`, no `euin_valid` and no `euin_opted` column — 0 of 76,460 KFIN rows in
`silver.transaction_master_new` carry an EUIN of any kind. The verdict this report filters on
does not exist in the feed, so there is nothing to report as invalid.

This is recorded in `UNPRODUCIBLE` in `etl_gold_wbr.py`, not as 31 identical per-column
reasons, and it is printed on every run:

```
WBR68 / KFIN : no rows - MFSD201 carries no euin, euin_valid or euin_opted
column, so the invalid-EUIN verdict this report filters on does not exist in
the KFIN feed
```

The extract is deliberately **not** restricted to `source = 'CAMS'`. The EUIN filter already
excludes every KFIN row, and hard-coding the RTA would hide the day KFintech starts delivering
the column. The file `WBR68-Invalid EUIN Report-KFIN.csv` is still written, with its 31-column
header and no rows: an absent file reads as a failed run, an empty one with the right header
says "no rows qualified".

---

## Running it

```
python sql/wbr_gold_tables.sql       # via psql, or the loader in app.py
python etl_gold_wbr.py               # derives both RTAs into the three tables
python export_wbr.py                 # writes 8 report files, 4 per RTA
python -m pytest tests/test_wbr.py   # 45 tests, both RTAs
```

Every load prints, per report and per RTA, the row count and every layout column that came out
empty — split into columns the feed cannot source, with the reason, and columns present in the
feed but blank on every row of this delivery. The second list is the one that asks for a
person: it means something that normally arrives did not.

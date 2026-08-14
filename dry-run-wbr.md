# CAMS WBR Dry Run

How to run the pipeline end to end from the CAMS input files and check the four
WBR reports that come out of it.

The direction matters, and it was wrong in an earlier version of this document:

```
files/excel/*.csv          CAMS R2 / R9 / R49, KFIN MFSD201 / 211 / 243   INPUT
   -> bronze               raw, as delivered
   -> silver               typed, cleaned, deduplicated
   -> gold                 entities + the four WBR reports                OUTPUT
   -> python_scripts/output/*.xlsx|csv                                    OUTPUT
```

`files/gold/WBR*.xls` are the provider's own reports. They are **not** uploaded
and not ingested. They are the reference for what the derived output should look
like.

Four reports come from three tables. WBR36 and WBR36H both read
`gold.brokerage_by_scheme`, separated by `report_variant`.

---

## 0. Prerequisites

```bash
cd python_scripts
venv/bin/pip install -r requirements.txt
```

Confirm PostgreSQL is up, and that `utils/db.py` points at the database holding
the `bronze` / `silver` / `gold` schemas. That file is per-developer — it is in
`.gitignore` — so its committed values are not necessarily the working ones:

```sql
SELECT table_schema, count(*)
FROM information_schema.tables
WHERE table_schema IN ('bronze', 'silver', 'gold')
GROUP BY 1;
```

The commands below assume `master_tables_db`. Substitute what `db.py` resolves
`PROJECT_DATABASE` to, and set `PGPASSWORD` accordingly.

---

## 1. Create the WBR gold tables

```bash
cd python_scripts
PGPASSWORD=postgres psql -h localhost -U postgres -d master_tables_db \
  -f sql/wbr_gold_tables.sql
```

Creates `gold.brokerage_by_scheme`, `gold.investor_kyc_status` and
`gold.invalid_euin`, each with a `UNIQUE` constraint on its natural key — the
`ON CONFLICT` target for the upsert, and what keeps the grain from drifting:

| Table | Natural key |
|---|---|
| `brokerage_by_scheme` | `(report_period, report_variant, product_code)` |
| `investor_kyc_status` | `(amc_code, folio)` |
| `invalid_euin` | `(amc_code, trxn_no)` |

There is no bronze or silver stage for these three. They are built from
`silver.transaction_master_new` and `silver.investor_master`.

**Note:** the script begins with `DROP TABLE IF EXISTS` on those three tables, so
re-running it discards their contents and rebuilds them empty. The rows come back
from step 4.

---

## 2. Upload and run

```bash
cd python_scripts
venv/bin/streamlit run app.py
```

Opens on `http://localhost:8501`. Upload the CAMS and KFIN input files:

```
/home/user/Inteliwealth-pipeline/files/excel/10072026104746_216882305R2.csv
/home/user/Inteliwealth-pipeline/files/excel/10072026104907_216882541R9.csv
/home/user/Inteliwealth-pipeline/files/excel/10072026105002_216882702R49.csv
/home/user/Inteliwealth-pipeline/files/excel/MFSD201_WBTRN28912495_428923.csv
/home/user/Inteliwealth-pipeline/files/excel/MFSD211_WBMST9217829_386513.csv
/home/user/Inteliwealth-pipeline/files/excel/MFSD243_WSREG8131655_1159890_0.csv
```

**Click Extract.** Routing is by filename: `*R2.csv` and `MFSD201` are
transactions, `*R9.csv` and `MFSD211` investors, `*R49.csv` and `MFSD243` SIPs.
Expect `✔ Extraction Complete (Transactions: n, Investor: n, SIP: n)` and a
Bronze preview.

**Click Transform.** This runs silver, then the entity gold loaders, then the WBR
reports and their export. The WBR step runs whenever transaction or investor data
is present, because that is what the reports are made of.

Expect a **📥 CAMS WBR Reports** section with four blocks and XLSX/CSV download
buttons per report. The files also land in `python_scripts/output/`.

A WBR failure warns but does not fail the transform: the entity gold tables are
the primary deliverable and are already committed by that point, and the reports
rebuild any time with `etl_gold_wbr.py` then `export_wbr.py`.

---

## 3. Headless equivalent

```bash
cd python_scripts
venv/bin/python etl_gold_wbr.py     # silver -> the three WBR gold tables
venv/bin/python export_wbr.py       # gold  -> the four report files
```

Add `.xls` output if LibreOffice is installed — writing legacy BIFF from Python
needs the unmaintained `xlwt`, so the conversion is delegated to `soffice`:

```bash
venv/bin/python -c "
from export_wbr import export_wbr_reports
export_wbr_reports(formats=('xlsx', 'csv', 'xls'))"
```

`WBR_OUTPUT_DIR` changes where files are written, `WBR_REPORT_PERIOD` overrides
the period stamped on WBR36, `WBR_SOFFICE` picks the LibreOffice binary.

---

## 4. What to expect

Against the current six input files:

| | Rows | Source |
|---|---|---|
| `gold.brokerage_by_scheme` | 515 | schemes transacted, from `silver.transaction_master_new` |
| `gold.investor_kyc_status` | 1,190 | folios, from `silver.investor_master` |
| `gold.invalid_euin` | 406 | transactions with an EUIN explicitly not valid |
| WBR36 file | 515 | `report_variant = 'STD'` |
| WBR36H file | 0 | header only — see below |
| WBR56 file | 1,190 | |
| WBR68 file | 406 | |

Grain ratio must be 1.00 on all three tables. Two lines in the log are worth
reading every run:

```
report_period : 2026 (silver.transaction_master_new.traddate)
investor_kyc_status : 1446 of 3542 silver rows skipped for a missing amc_code or folio
```

The second is not a bug in the loader — `amc_code` is half the natural key and the
KFIN feed leaves it blank, so those folios cannot be written. It is printed on
every run rather than swallowed, because a quiet 40% loss is worse than a noisy
one. Fixing it means mapping an AMC code onto the KFIN rows during ingestion.

---

## 5. What the reports cannot contain

Every run prints this, per report, under "column(s) the feed cannot source". It is
not an error — the columns stay in the layout, in the provider's position, holding
NULL, because the layout is the contract with whoever consumes the file. The
reasons live next to `UNAVAILABLE` in `etl_gold_wbr.py`.

**WBR36 / WBR36H — all six measures are NULL.** `upfront`, `afe`, `trailer_fee`,
`trxn_charges`, `clawback`, `incentives`. Trail commission is computed by the RTA
on AUM over a period; R2 carries only per-transaction `BROKCOMM`, which totals
1,036,504.67 against the provider's 3,139,008.5685 for the same schemes and is
unrelated per product (D104: 15,931.82 vs 3,950.456368). `TRXN_CHARGES` is 0 on
all 90,536 R2 rows. What this report therefore delivers is the scheme list in the
provider's column order, and nothing about the money.

**WBR36H is empty.** Nothing in R2 marks which schemes belong to the H variant.
The file is written with its header rather than skipped, so that "no rows
qualified" is distinguishable from "the run failed".

**WBR56 — 12 columns NULL.** The four `*_kyc` statuses, their four `*_desc`
descriptions, three `*_aadharlink` columns, and `brok_name`. CAMS R9 carries
`FH_CKYC_NO` — a CKYC *number*, not a status — and its `AADHAAR` column is blank
on every CAMS folio. `silver.investor_master` does have `kyc1flag` and
`holder_1_aadhaar_info`, but only the KFIN feed populates them: 0 of the 101
folios in the provider's reference report, 1,422 of 1,444 KFIN rows. So a KFIN
delivery fills these in and a CAMS one does not.

**WBR68 — 3 columns NULL.** `trxn_desc` (R2 has `TRXNTYPE` but no description),
`sip_regn_date` (no clean key from a transaction to its SIP registration —
`SIPTRXNNO` to `sip_master_new.ft_sip_regno` fans out to 359,518 pairs), and
`email`. That last one is worth knowing: the provider writes the **distributor's**
email there, not the investor's — all 9 reference rows carry one address across 5
different folios. Filling it from `investor_master.email` looked right and was
wrong.

Columns that are merely blank in this particular delivery are printed under a
separate heading, so a genuine bug cannot hide among the expected gaps.

---

## 6. Verify against the provider's reports

`files/gold/WBR*.xls` is the reference. The useful check is WBR68, where the
derivation is most complete:

```bash
cd python_scripts
venv/bin/python -m pytest tests/test_wbr.py -q     # 30 passed
```

The suite asserts that all 9 of the provider's transactions appear in the derived
406, that column order matches the provider's file exactly for all four reports,
that two runs produce byte-identical files, and that the filter, the scheme-code
prefix, the `"0"`-means-absent rule and the email finding all still hold.

On the shared rows, **19 of 30 columns agree exactly**. The 11 that differ:

| Column | Provider | Derived | Why |
|---|---|---|---|
| `inv_name` | `Suresh Kumar V  ` | `Suresh Kumar V` | provider keeps trailing padding; silver trims |
| `sch_name` | full name | cut at 100 chars | CAMS truncates `SCHEME` in R2 itself — 6,945 rows sit at exactly 100, none longer |
| `trxn_type` | `P` | `P234ES` | 1 row; provider wrote the short form there |
| `trxn_desc` | `Systematic Purchase` | empty | no source |
| `amount` | `2000` | `1999.9` | 1 row; RTA restated it |
| `location` | `PKD491/Palakkad` | `/Palakkad` | R2's `TER_LOCATION` is a single letter, not the branch code |
| `euin_valid` | `F` | `N` | 1 row; `F` does not exist in R2 at all |
| `email` | distributor's | empty | see above |
| `posted_date` | empty | a date | 1 row; provider left it blank |
| `usertxn_no` | `9966684` | `9966692` | 2 rows; provider uses a different sequence |
| `sip_regn_date` | a date | empty | no clean key |

Full suite: `venv/bin/python -m pytest tests/ -q` → 145 passed, 5 failed, 7
errors. Those failures are pre-existing scheme_mapping ones
(`bronze.scheme_name_alias` missing in this database, plus baseline mapping
drift), unrelated to WBR.

---

## 7. Idempotency

Run step 3 twice. Gold stays at 515 / 1,190 / 406 and the exported files come out
byte-identical — the exporter's `ORDER BY source_row` is what guarantees that,
since a bare `SELECT *` returns heap order which changes after an `UPDATE`.

Row ids are `uuid5` over the natural key, so the same business row keeps the same
id across runs.

---

## 8. Files

| File | Role |
|---|---|
| `python_scripts/sql/wbr_gold_tables.sql` | DDL for the three report tables |
| `python_scripts/etl_gold_wbr.py` | derives the reports from silver; `UNAVAILABLE` records every gap and why |
| `python_scripts/export_wbr.py` | writes gold out as the four files |
| `python_scripts/mapping.py` | `WBR_OUTPUT_LAYOUTS`, `WBR_OUTPUT_DATE_FORMATS` — the provider's column order and date formats |
| `python_scripts/app.py` | upload, run, preview, download |
| `python_scripts/tests/test_wbr.py` | 30 tests over the derivation and the export |

`new_pipeline/` is a separate implementation that treats the WBR files as an input
feed. It is not part of this flow. Its
`new_pipeline/docs/cams-wbr-profile.md` remains the best column-by-column
description of what the provider's reports contain.

---

## 9. If the reports need to match the provider exactly

They cannot, from these inputs. WBR36's measures and WBR56's KYC payload are not
in the CAMS feed in any form, and no transformation of R2/R9/R49 produces them —
that is measured in section 5, not assumed. Closing the gap needs one of:

- the CAMS brokerage feed, for the WBR36 measures
- the CAMS KYC feed, for the WBR56 statuses and Aadhaar-link columns
- a broker master, for `brok_name` and the distributor `email`
- whatever field marks the WBR36H variant

Until then the derived reports carry the right shape, the right rows where the
rows are derivable, and empty columns everywhere else — with a printed reason for
each.

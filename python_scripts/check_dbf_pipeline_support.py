"""
check_dbf_pipeline_support.py — DBF -> bronze mapping coverage checker.

Why this exists
----------------
The backend now hands the automated pipeline (etl_pipeline.runner) real
.dbf report files pulled from S3, instead of the .csv exports the mapping
tables in mapping.py were originally written against. DBF field names are
truncated to 10 characters (dBase limit), so some fields land under a
different name than the CSV export used (e.g. "TRXN_NATURE" -> truncated
to "TRXN_NATUR" in the DBF, which doesn't match either alt-name
["trxn_nature", "trdesc"] in TRANSACTION_MASTER_MAPPING).

This script does NOT touch the database and does NOT run the automated
pipeline (etl_pipeline.runner) — it only exercises the read + column
mapping steps (raw_ingestion.read_file + apply_transaction_mapping /
apply_investor_mapping / apply_sip_mapping) against real sample .dbf
files, to answer: "for each target bronze column, did a source DBF field
actually get matched, and is it populated?"

Usage
-----
    cd python_scripts && source venv/bin/activate
    python3 check_dbf_pipeline_support.py [root_dir]

root_dir defaults to /home/user/Downloads/20_aug_all_report and is
expected to contain one subfolder per report type, each holding one or
more .dbf sample files (subfolder name only needs to contain "49", "9",
or "2" — checked in that order since "49" also contains "9").
"""

import contextlib
import io
import sys
from pathlib import Path

from raw_ingestion import read_file
from etl_trans import apply_transaction_mapping
from etl_investor_master import apply_investor_mapping
from etl_sip import apply_sip_mapping
from mapping import (
    TRANSACTION_MASTER_MAPPING,
    INVESTOR_MASTER_MAPPING,
    SIP_MASTER_MAPPING,
)

DEFAULT_ROOT = "/home/user/Downloads/20_aug_all_report"

# folder-name substring (checked in this order — "49" before "9" before
# "2", since "wbr_49" also contains "9") -> (label, mapping, apply_fn)
REPORT_TYPES = [
    ("49", "WBR49 / sip", SIP_MASTER_MAPPING, apply_sip_mapping),
    ("9", "WBR9 / investor", INVESTOR_MASTER_MAPPING, apply_investor_mapping),
    ("2", "WBR2 / transaction", TRANSACTION_MASTER_MAPPING, apply_transaction_mapping),
]

SKIP_TARGET_COLS = {"flag", "created_at", "updated_at", "source"}


def classify_folder(folder_name):
    lowered = folder_name.lower()
    for needle, label, mapping, apply_fn in REPORT_TYPES:
        if needle in lowered:
            return label, mapping, apply_fn
    return None


def summarize_coverage(raw_cols, mapping, mapped_df, total_rows):
    rows = []
    for target_col, source_cols in mapping.items():
        if target_col in SKIP_TARGET_COLS:
            continue

        non_null = (
            int(mapped_df[target_col].notna().sum())
            if target_col in mapped_df.columns
            else 0
        )
        matched_src = next(
            (s.lower().strip() for s in source_cols if s.lower().strip() in raw_cols),
            None,
        )

        if target_col == "postdate" and matched_src is None:
            # postdate is filled by hand-written date-parsing logic, not
            # the generic source_cols loop — "postdate" (CAMS) / "td_prdt"
            # (KFIN) are checked directly, so report that instead of a
            # false "no source column".
            matched_src = "(special-case: postdate/td_prdt)"

        if non_null == 0 and matched_src is None:
            status = "NO SOURCE COLUMN FOUND"
        elif non_null == 0:
            status = "SOURCE MATCHED BUT EMPTY"
        elif non_null < total_rows:
            status = "PARTIAL"
        else:
            status = "FULL"

        rows.append({
            "target_col": target_col,
            "matched_source": matched_src or "-",
            "populated": f"{non_null}/{total_rows}",
            "status": status,
        })
    return rows


def check_file(path, label, mapping, apply_fn):
    print(f"\n{'=' * 90}")
    print(f"FILE: {path}")
    print(f"REPORT TYPE: {label}")
    print("=" * 90)

    with open(path, "rb") as fh:
        buf = io.BytesIO(fh.read())
    buf.name = path.name  # read_file() dispatches on file.name

    quiet = io.StringIO()
    try:
        with contextlib.redirect_stdout(quiet):
            raw_df = read_file(buf)
            mapped_df = apply_fn(raw_df, mapping, "CAMS")
    except Exception as exc:
        print(f"  FAILED TO READ/MAP: {type(exc).__name__}: {exc}")
        print("  --- last output before failure ---")
        print("  " + "\n  ".join(quiet.getvalue().splitlines()[-30:]))
        return None

    total_rows = len(raw_df)
    print(f"  rows read: {total_rows}")
    print(f"  raw DBF columns ({len(raw_df.columns)}): {list(raw_df.columns)}")

    # raw_df columns aren't lowercased by read_file for DBF; apply_fn's
    # internal clean_columns() lowercases them before matching, so mirror
    # that here for an accurate "matched_source" check.
    raw_cols_lower = set(
        c.strip().lower().replace(" ", "_").replace("-", "_").replace("/", "_").replace("#", "")
        for c in raw_df.columns.astype(str)
    )
    coverage = summarize_coverage(raw_cols_lower, mapping, mapped_df, total_rows)

    no_source = [r for r in coverage if r["status"] == "NO SOURCE COLUMN FOUND"]
    empty_matched = [r for r in coverage if r["status"] == "SOURCE MATCHED BUT EMPTY"]
    partial = [r for r in coverage if r["status"] == "PARTIAL"]
    full = [r for r in coverage if r["status"] == "FULL"]

    def _print_group(title, group):
        if not group:
            return
        print(f"\n  {title} ({len(group)}):")
        for r in group:
            print(f"    {r['target_col']:<20} matched_source={r['matched_source']:<25} populated={r['populated']}")

    print(f"\n  --- coverage summary: {len(full)} full, {len(partial)} partial, "
          f"{len(empty_matched)} matched-but-empty, {len(no_source)} no-source-column "
          f"(of {len(coverage)} target columns) ---")
    _print_group("NO SOURCE COLUMN FOUND (real mapping gap — needs an alt-name added in mapping.py)", no_source)
    _print_group("SOURCE MATCHED BUT EMPTY (column found, but blank in this sample)", empty_matched)
    _print_group("PARTIAL (populated for some rows only)", partial)

    return {
        "path": str(path),
        "label": label,
        "rows": total_rows,
        "full": len(full),
        "partial": len(partial),
        "empty_matched": len(empty_matched),
        "no_source": len(no_source),
        "no_source_cols": [r["target_col"] for r in no_source],
    }


def main():
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(DEFAULT_ROOT)
    if not root.is_dir():
        print(f"Not a directory: {root}")
        sys.exit(1)

    results = []
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        classified = classify_folder(folder.name)
        if classified is None:
            print(f"Skipping folder (couldn't classify report type): {folder}")
            continue
        label, mapping, apply_fn = classified

        dbf_files = sorted(folder.glob("*.dbf"))
        if not dbf_files:
            print(f"Skipping folder (no .dbf files): {folder}")
            continue

        for path in dbf_files:
            result = check_file(path, label, mapping, apply_fn)
            if result:
                results.append(result)

    print(f"\n\n{'#' * 90}")
    print("OVERALL SUMMARY")
    print("#" * 90)
    if not results:
        print("No files were successfully checked.")
        return

    for r in results:
        print(f"\n{r['path']}  ({r['label']}, {r['rows']} rows)")
        print(f"  full={r['full']}  partial={r['partial']}  matched_but_empty={r['empty_matched']}  no_source_column={r['no_source']}")
        if r["no_source_cols"]:
            print(f"  missing target columns: {', '.join(r['no_source_cols'])}")


if __name__ == "__main__":
    main()

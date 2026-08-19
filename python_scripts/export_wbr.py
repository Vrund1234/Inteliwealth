import os
import shutil
import subprocess

from datetime import date, datetime
from decimal import Decimal

import pandas as pd
from sqlalchemy import text

from mapping import (
    WBR_OUTPUT_LAYOUTS,
    WBR_OUTPUT_DATE_FORMATS
)

from etl_gold_wbr import NATURAL_KEYS, SOURCES

from utils.db import engine


# =====================================================
# WBR REPORT EXPORTER  (CAMS AND KFINTECH)
# =====================================================
#
# Writes the derived gold tables out as the four WBR report files. The tables
# are the source of truth; the files are a projection of them.
#
# The gold tables hold both RTAs, keyed by source. A report is a per-RTA
# deliverable, so an export is filtered to one RTA and its filename carries the
# RTA — "WBR56-KYC status of Investor-KFIN". Exporting unfiltered would put CAMS
# and KFIN rows in one file under a layout the consumer expects to be one RTA\'s.
# Passing source=None restores the single mixed file, for a consumer that wants
# the whole table.
#
# Four layouts read three tables: WBR36 and WBR36H both read
# gold.brokerage_by_scheme and are separated by the report_variant filter in
# WBR_OUTPUT_LAYOUTS. Only STD is derivable from the CAMS feed, so the WBR36H
# file is written empty — with its header — rather than skipped. An absent file
# looks like a failed run; an empty one with the right columns says "no rows
# qualified", which is the truth.
#
# Column order, column names and date formats reproduce the provider's own
# layout exactly, because that layout is the contract with whoever consumes the
# reports.
#
# Formats:
#
#   xlsx  native, via openpyxl
#   csv   native
#   xls   optional, via LibreOffice. Writing legacy BIFF from Python needs xlwt,
#         which is unmaintained and cannot write the format reliably, so the
#         conversion is delegated to soffice when it is on PATH.


# =====================================================
# OUTPUT DIRECTORY
# =====================================================

OUTPUT_DIR = os.environ.get(
    "WBR_OUTPUT_DIR",
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "output"
    )
)

SOFFICE_BIN = os.environ.get("WBR_SOFFICE", "soffice")

DEFAULT_FORMATS = ("xlsx", "csv")


# =====================================================
# SOURCES
# =====================================================
#
# The RTAs exported by default, one set of files each. WBR_SOURCES overrides it
# ("CAMS", or "CAMS,KFIN"); WBR_SOURCES=ALL writes one unfiltered file per
# report instead.

def default_sources():

    override = os.environ.get("WBR_SOURCES", "").strip()

    if not override:
        return list(SOURCES)

    if override.upper() == "ALL":
        return [None]

    return [s.strip().upper() for s in override.split(",") if s.strip()]


# =====================================================
# FORMAT DATE
# =====================================================
#
# "%-m/%-d/%Y" (unpadded month and day) is a glibc extension and not portable,
# so the padded form is rendered and the padding stripped rather than relying on
# strftime to honour it.

def format_date(value, fmt):

    if value is None or value is pd.NaT:
        return ""

    if isinstance(value, str):
        return value

    if isinstance(value, (datetime, date)):

        if "%-" in fmt:

            rendered = value.strftime(
                fmt
                .replace("%-m", "%m")
                .replace("%-d", "%d")
            )

            month, day, rest = rendered.split("/", 2)

            return f"{int(month)}/{int(day)}/{rest}"

        return value.strftime(fmt)

    return value


# =====================================================
# FORMAT NUMBER
# =====================================================
#
# numeric columns come back from PostgreSQL as Decimal, and a naive str() gives
# "0.0" where the source file has "0" and "2000.0" where it has "2000". An
# integral value is written without a decimal point; anything else keeps its
# digits with trailing zeros stripped, so 3950.45636848 survives intact.

def format_number(value):

    if value is None or value is pd.NaT:
        return ""

    if isinstance(value, Decimal):

        if value == value.to_integral_value():
            return str(int(value))

        return format(value.normalize(), "f")

    if isinstance(value, float):

        if float(value).is_integer():
            return str(int(value))

        return repr(value)

    return value


# =====================================================
# FETCH
# =====================================================

def fetch(layout, source=None):

    table = layout["source_table"]

    conditions = []
    params = {}

    for key, value in layout["filter"].items():

        conditions.append(f'"{key}" = :{key}')
        params[key] = value

    if source:

        conditions.append('"source" = :source')
        params["source"] = source

    where = (
        f" WHERE {' AND '.join(conditions)}"
        if conditions else ""
    )

    # ORDER BY is not optional. A bare SELECT * returns heap order, which
    # changes after an UPDATE, and two consecutive runs then produce
    # byte-different files. source_row reproduces the provider's own row order;
    # the natural key is the tie-break, so the result is deterministic even for
    # rows with no recorded position.
    order_by = ", ".join(
        [
            "source_row NULLS LAST",
            *(f'"{c}"' for c in NATURAL_KEYS[table])
        ]
    )

    statement = text(
        f'SELECT * FROM gold."{table}"{where} ORDER BY {order_by}'
    )

    with engine.connect() as conn:

        return pd.read_sql(
            statement,
            conn,
            params=params
        )


# =====================================================
# PROJECT
# =====================================================
#
# Exact output columns, in the provider's order. A column the gold table does
# not carry is emitted empty rather than omitted: dropping it would change the
# layout and silently break the consumer. Those cases are printed by name so an
# unsourceable column is visible rather than invisible.

def project(df, layout, report_code):

    columns = layout["columns"]

    missing = [c for c in columns if c not in df.columns]

    if missing:

        print(
            f"{report_code} : {len(missing)} layout columns absent from gold "
            f"and emitted empty : {missing}"
        )

    out = pd.DataFrame(index=df.index)

    for column in columns:

        out[column] = (
            df[column]
            if column in df.columns
            else None
        )

    date_formats = WBR_OUTPUT_DATE_FORMATS.get(report_code, {})

    for column in out.columns:

        if column in date_formats:

            fmt = date_formats[column]

            out[column] = out[column].map(
                lambda v, f=fmt: format_date(v, f)
            )

        else:

            out[column] = out[column].map(format_number)

    return out.where(pd.notna(out), "")


# =====================================================
# WRITERS
# =====================================================

def write_xlsx(df, path):

    with pd.ExcelWriter(path, engine="openpyxl") as writer:

        df.to_excel(
            writer,
            index=False,
            sheet_name="Sheet1"
        )

    return path


def write_csv(df, path):

    df.to_csv(path, index=False)

    return path


def write_xls_via_soffice(xlsx_path):

    binary = shutil.which(SOFFICE_BIN)

    if not binary:

        print(
            f"{SOFFICE_BIN} not found on PATH. Skipping .xls output; the .xlsx "
            f"and .csv are already written."
        )

        return None

    try:

        subprocess.run(
            [
                binary,
                "--headless",
                "--convert-to", "xls",
                "--outdir", os.path.dirname(xlsx_path),
                xlsx_path
            ],
            check=True,
            capture_output=True,
            timeout=180
        )

    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:

        print(f"soffice conversion failed for {os.path.basename(xlsx_path)} : {e}")

        return None

    produced = f"{os.path.splitext(xlsx_path)[0]}.xls"

    return produced if os.path.exists(produced) else None


# =====================================================
# EXPORT ONE REPORT
# =====================================================

def export_report(
    report_code,
    formats=DEFAULT_FORMATS,
    output_dir=None,
    source=None
):

    layout = WBR_OUTPUT_LAYOUTS[report_code]

    df = project(
        fetch(layout, source=source),
        layout,
        report_code
    )

    out_dir = output_dir or OUTPUT_DIR

    os.makedirs(out_dir, exist_ok=True)

    # The RTA is in the filename, not only in the directory: the four stems are
    # the provider\'s own and two RTAs writing to one directory would otherwise
    # overwrite each other silently.
    stem = (
        f"{layout['file_stem']}-{source}"
        if source
        else layout["file_stem"]
    )

    written = []
    xlsx_path = None

    if "xlsx" in formats:

        xlsx_path = write_xlsx(
            df,
            os.path.join(out_dir, f"{stem}.xlsx")
        )

        written.append(xlsx_path)

    if "csv" in formats:

        written.append(
            write_csv(
                df,
                os.path.join(out_dir, f"{stem}.csv")
            )
        )

    if "xls" in formats:

        if xlsx_path is None:

            xlsx_path = write_xlsx(
                df,
                os.path.join(out_dir, f"{stem}.xlsx")
            )

        converted = write_xls_via_soffice(xlsx_path)

        if converted:
            written.append(converted)

    label = f"{report_code}/{source}" if source else report_code

    print(
        f"{label} : {len(df)} rows, {len(df.columns)} columns -> "
        f"{[os.path.basename(p) for p in written]}"
    )

    return {
        "report_code": report_code,
        "source": source,
        "rows": len(df),
        "columns": len(df.columns),
        "files": written
    }


# =====================================================
# EXPORT ALL FOUR REPORTS
# =====================================================
#
# One report failing does not stop the others: a missing table is worth
# reporting, not worth withholding the three files that can be produced.

def export_wbr_reports(
    report_codes=None,
    formats=DEFAULT_FORMATS,
    output_dir=None,
    sources=None
):

    codes = report_codes or list(WBR_OUTPUT_LAYOUTS)

    rtas = sources if sources is not None else default_sources()

    results = []

    print("=" * 80)
    print(
        "EXPORTING WBR REPORTS : "
        + ", ".join(r or "ALL SOURCES" for r in rtas)
    )
    print("=" * 80)

    for source in rtas:

      for code in codes:

        try:

            results.append(
                export_report(
                    code,
                    formats=formats,
                    output_dir=output_dir,
                    source=source
                )
            )

        except Exception as e:

            print(f"{code} export failed : {e}")

            results.append(
                {
                    "report_code": code,
                    "source": source,
                    "rows": 0,
                    "columns": 0,
                    "files": [],
                    "error": str(e)
                }
            )

    total_files = sum(len(r["files"]) for r in results)

    print("=" * 80)
    print(
        f"EXPORT COMPLETED : {len(results)} report files across "
        f"{len(rtas)} source(s), {total_files} files in "
        f"{output_dir or OUTPUT_DIR}"
    )
    print("=" * 80)

    return results


# =====================================================
# MAIN EXECUTION
# =====================================================

if __name__ == "__main__":

    export_wbr_reports()

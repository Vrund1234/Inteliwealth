"""Render gold tables back into the four WBR report files.

The tables are the source of truth; these files are a projection of them. Column
order, column names and date formats reproduce the provider's own layout exactly,
because that layout is the contract with whoever consumes the reports.

Output formats:
  .xlsx  native, via openpyxl
  .csv   native
  .xls   optional, via LibreOffice. Writing legacy BIFF from Python needs xlwt, which
         is unmaintained and cannot write the format reliably, so the conversion is
         delegated to soffice when it is available.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd
from sqlalchemy import text

from config.mapping_cams_wbr import GOLD_GRAIN, OUTPUT_DATE_FORMATS, OUTPUT_LAYOUTS
from config.settings import Settings
from utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class ExportResult:
    report_code: str
    rows: int
    files: list[Path]


def _format_date(value: object, fmt: str) -> object:
    """Render a date the way the provider does.

    '%-m/%-d/%Y' (no zero padding) is a glibc extension that is not portable, so the
    padded form is produced and the padding stripped, rather than relying on it.
    """
    if value is None or value is pd.NaT:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (datetime, date)):
        if "%-" in fmt:
            rendered = value.strftime(fmt.replace("%-m", "%m").replace("%-d", "%d"))
            month, day, rest = rendered.split("/", 2)
            return f"{int(month)}/{int(day)}/{rest}"
        return value.strftime(fmt)
    return value


def _format_number(value: object) -> object:
    """Render a number the way the provider does.

    numeric columns come back from PostgreSQL as Decimal, and a naive str() gives
    '0.0' where the source file has '0' and '2000.0' where it has '2000'. An integral
    value is written without a decimal point; anything else keeps its digits with
    trailing zeros stripped, so 3950.45636848 survives intact.
    """
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


def _fetch(engine, settings: Settings, layout: dict) -> pd.DataFrame:
    schema = settings.schemas.gold
    table = layout["source_table"]

    conditions = []
    params: dict[str, object] = {}
    for key, value in layout["filter"].items():
        if key == "__euin_invalid__":
            # Deliberately `<> 'Y'`, matching the report definition. A `= 'N'` filter
            # would drop the 'F' row the sample contains.
            conditions.append("(euin_valid IS NULL OR upper(euin_valid) <> 'Y')")
        else:
            conditions.append(f'"{key}" = :{key}')
            params[key] = value

    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""

    # ORDER BY is not optional. A bare SELECT * returns heap order, which changes after
    # an UPDATE — two consecutive runs produced byte-different CSVs before this was
    # added. source_row reproduces the provider's own row order; the natural key is the
    # tie-break so the result is deterministic even for rows with no recorded position.
    natural_key = GOLD_GRAIN[table]["natural_key"]
    order_by = ", ".join(
        ["source_row NULLS LAST", *(f'"{c}"' for c in natural_key)]
    )
    sql = text(f'SELECT * FROM "{schema}"."{table}"{where} ORDER BY {order_by}')

    with engine.connect() as conn:
        return pd.read_sql(sql, conn, params=params)


def _project(frame: pd.DataFrame, layout: dict, report_code: str) -> pd.DataFrame:
    """Exact output columns, in the provider's order.

    A column the gold table does not carry is emitted empty rather than omitted —
    dropping it would change the layout and silently break the consumer. Those cases
    are logged by name so an unsourceable column is visible, not invisible.
    """
    columns = layout["columns"]
    missing = [c for c in columns if c not in frame.columns]
    if missing:
        log.warning(
            "%s: %d layout columns absent from gold and emitted empty: %s",
            report_code, len(missing), missing,
        )

    out = pd.DataFrame(index=frame.index)
    for column in columns:
        out[column] = frame[column] if column in frame.columns else None

    date_formats = OUTPUT_DATE_FORMATS.get(report_code, {})
    for column in out.columns:
        if column in date_formats:
            fmt = date_formats[column]
            out[column] = out[column].map(lambda v, f=fmt: _format_date(v, f))
        else:
            out[column] = out[column].map(_format_number)

    return out.where(pd.notna(out), "")


def _write_xlsx(frame: pd.DataFrame, path: Path) -> Path:
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        frame.to_excel(writer, index=False, sheet_name="Sheet1")
    return path


def _write_csv(frame: pd.DataFrame, path: Path) -> Path:
    frame.to_csv(path, index=False)
    return path


def _write_xls_via_soffice(xlsx_path: Path, settings: Settings) -> Path | None:
    """Convert .xlsx to legacy .xls using LibreOffice, if it is installed."""
    binary = shutil.which(settings.runtime.soffice_bin)
    if not binary:
        log.warning(
            "%s not found on PATH — skipping .xls output. The .xlsx and .csv are "
            "already written.", settings.runtime.soffice_bin,
        )
        return None

    try:
        subprocess.run(
            [binary, "--headless", "--convert-to", "xls",
             "--outdir", str(xlsx_path.parent), str(xlsx_path)],
            check=True, capture_output=True, timeout=180,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        log.error("soffice conversion failed for %s: %s", xlsx_path.name, exc)
        return None

    produced = xlsx_path.with_suffix(".xls")
    return produced if produced.exists() else None


def export_report(
    engine,
    settings: Settings,
    report_code: str,
    formats: tuple[str, ...] = ("xlsx", "csv"),
) -> ExportResult:
    layout = OUTPUT_LAYOUTS[report_code]
    frame = _project(_fetch(engine, settings, layout), layout, report_code)

    out_dir = settings.paths.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = layout["file_stem"]

    written: list[Path] = []
    xlsx_path: Path | None = None

    if "xlsx" in formats:
        xlsx_path = _write_xlsx(frame, out_dir / f"{stem}.xlsx")
        written.append(xlsx_path)
    if "csv" in formats:
        written.append(_write_csv(frame, out_dir / f"{stem}.csv"))
    if "xls" in formats:
        if xlsx_path is None:
            xlsx_path = _write_xlsx(frame, out_dir / f"{stem}.xlsx")
        converted = _write_xls_via_soffice(xlsx_path, settings)
        if converted:
            written.append(converted)

    log.info(
        "exported %s: %d rows, %d columns -> %s",
        report_code, len(frame), len(frame.columns),
        [p.name for p in written],
    )
    return ExportResult(report_code=report_code, rows=len(frame), files=written)


def export_all(
    engine,
    settings: Settings,
    report_codes: list[str] | None = None,
    formats: tuple[str, ...] = ("xlsx", "csv"),
) -> list[ExportResult]:
    codes = report_codes or list(OUTPUT_LAYOUTS)
    return [export_report(engine, settings, code, formats) for code in codes]

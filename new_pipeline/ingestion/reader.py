"""File reading. Path in, raw DataFrame plus metadata out. No database access.

Differences from python_scripts/raw_ingestion.py that matter:

- Takes a pathlib.Path, not a Streamlit UploadedFile. That is what makes the pipeline
  runnable from a shell, from cron and from tests. The existing bronze writers can
  only be reached through the Streamlit UI because they require upload objects.
- Dispatches on the detected format, not on "everything that is not .csv goes to
  read_excel". The existing else-branch sends any unknown extension to read_excel,
  where a legacy .xls fails on a missing xlrd with an unhelpful ImportError.
- sheet_name is always explicit. The pandas default of 0 silently drops every sheet
  after the first.
- Returns a content hash so the same file is recognisable across runs and every
  bronze row can point back at the upload that produced it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from config.file_patterns import EXTENSION_TO_FORMAT
from config.mapping_cams_wbr import FORMAT_SPECS
from utils.logging import get_logger

log = get_logger(__name__)


class UnsupportedFormat(Exception):
    """The file's extension maps to no reader."""


class EmptyFile(Exception):
    """The file parsed but contained no data rows."""


@dataclass
class FileMetadata:
    """Provenance for one ingested file. Persisted to audit_wbr.source_files."""

    source_file_id: str
    file_name: str
    file_path: str
    sha256: str
    byte_size: int
    format: str
    rows_in_file: int
    columns_in_file: int
    entity: str | None = None
    report_variant: str | None = None
    period_from: object | None = None
    period_to: object | None = None
    ingested_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def as_row(self) -> dict:
        return {
            "source_file_id": self.source_file_id,
            "file_name": self.file_name,
            "file_path": self.file_path,
            "sha256": self.sha256,
            "byte_size": self.byte_size,
            "entity": self.entity,
            "report_variant": self.report_variant,
            "format": self.format,
            "rows_in_file": self.rows_in_file,
            "columns_in_file": self.columns_in_file,
            "period_from": self.period_from,
            "period_to": self.period_to,
            "ingested_at": self.ingested_at,
        }


def detect_format(path: Path) -> str:
    fmt = EXTENSION_TO_FORMAT.get(path.suffix.lower())
    if fmt is None:
        raise UnsupportedFormat(
            f"{path.name}: extension {path.suffix!r} maps to no reader. "
            f"Known: {sorted(EXTENSION_TO_FORMAT)}"
        )
    return fmt


def sha256_of(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_excel(path: Path, fmt: str) -> pd.DataFrame:
    spec = FORMAT_SPECS[fmt]

    # dtype=str + keep_default_na=False: every value arrives as the string the
    # provider wrote. Typing happens in silver, from the declared config type, so a
    # scheme code like "081G" is never guessed at and a folio like "42213157/43"
    # is never coerced.
    read_kwargs = {
        "dtype": str,
        "keep_default_na": False,
        "header": spec["header_row"],
        "skiprows": spec["skiprows"] or None,
        "engine": spec["engine"],
    }

    if spec["all_sheets"]:
        book = pd.ExcelFile(path, engine=spec["engine"])
        frames = []
        for sheet in book.sheet_names:
            part = book.parse(sheet_name=sheet, **{k: v for k, v in read_kwargs.items()
                                                   if k != "engine"})
            part["__sheet__"] = sheet
            frames.append(part)
        if not frames:
            raise EmptyFile(f"{path.name}: workbook has no sheets")
        return pd.concat(frames, ignore_index=True)

    try:
        return pd.read_excel(path, sheet_name=spec["sheet_name"], **read_kwargs)
    except ImportError as exc:
        raise UnsupportedFormat(
            f"{path.name}: reading {fmt} needs the {spec['engine']!r} package. "
            f"Install it in this pipeline's venv (see requirements.txt). "
            f"Original error: {exc}"
        ) from exc


def _read_csv(path: Path) -> pd.DataFrame:
    spec = FORMAT_SPECS["csv"]
    encodings = [spec["encoding"], *spec["encoding_fallbacks"]]

    last_error: Exception | None = None
    for encoding in encodings:
        try:
            frame = pd.read_csv(
                path,
                dtype=str,
                keep_default_na=False,
                sep=spec["delimiter"],
                quotechar=spec["quotechar"],
                header=spec["header_row"],
                skiprows=spec["skiprows"] or None,
                encoding=encoding,
                # Never silently drop a malformed line. The existing reader keeps a
                # truncated row while printing "Skipping bad row", which is both a
                # data corruption and a misleading message.
                on_bad_lines="error",
            )
            if encoding != spec["encoding"]:
                log.warning("%s: decoded with fallback encoding %s", path.name, encoding)
            break
        except UnicodeDecodeError as exc:
            last_error = exc
            continue
    else:
        raise UnsupportedFormat(f"{path.name}: undecodable with {encodings}: {last_error}")

    if spec["strip_nulls"]:
        for col in frame.columns:
            if frame[col].dtype == object:
                frame[col] = frame[col].str.replace("\x00", "", regex=False)

    return frame


def read_file(path: Path, source_file_id: str) -> tuple[pd.DataFrame, FileMetadata]:
    """Read one file into a raw frame with a 1-based row number preserved.

    The row number is attached here, before any filtering, so a row rejected in
    silver can still be pointed at a spreadsheet line.
    """
    if not path.is_file():
        raise FileNotFoundError(path)

    fmt = detect_format(path)
    frame = _read_excel(path, fmt) if fmt in {"xls", "xlsx"} else _read_csv(path)

    # Drop unnamed and blank header columns produced by trailing separators.
    frame = frame.loc[:, [c for c in frame.columns
                          if str(c).strip() and not str(c).startswith("Unnamed:")]]

    if frame.empty:
        raise EmptyFile(f"{path.name}: parsed 0 data rows")

    frame = frame.reset_index(drop=True)
    frame["__row_number__"] = frame.index + 1

    meta = FileMetadata(
        source_file_id=source_file_id,
        file_name=path.name,
        file_path=str(path),
        sha256=sha256_of(path),
        byte_size=path.stat().st_size,
        format=fmt,
        rows_in_file=len(frame),
        columns_in_file=len([c for c in frame.columns if c != "__row_number__"]),
    )

    log.info(
        "read %s: format=%s rows=%d cols=%d sha=%s",
        path.name, fmt, meta.rows_in_file, meta.columns_in_file, meta.sha256[:12],
    )
    return frame, meta

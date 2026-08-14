"""Bronze writer.

Bronze here means structurally conformed, semantically untouched: rename columns, fix
format artefacts, attach provenance. No business-value standardisation and no type
casting beyond what the provider already gave us — that is silver's job.

Note that the existing pipeline's "bronze" already does date parsing and value
mapping, which is why its name is misleading.

Behavioural difference that matters most: this writer UPSERTS on the natural key, so
re-uploading a file is idempotent. The existing bronze marks duplicates with flag=1
and inserts them anyway, so re-uploading the same six files takes
bronze.transaction_master_new from 128,766 rows to roughly 257,532.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from bronze.cleaners import (
    apply_case,
    blank_to_null,
    normalize_header,
    strip_float_artifacts,
    trim,
)
from config.mapping_cams_wbr import ENTITIES, source_to_target
from config.settings import Settings
from ingestion.reader import FileMetadata
from ingestion.validators import ValidationReport, validate_columns
from utils.audit import LoadSummary, Reject, utc_now, write_load_summary, write_rejects
from utils.logging import get_logger
from utils.upsert import upsert

log = get_logger(__name__)


class BronzeAborted(Exception):
    """Validation refused the file. Nothing was written."""


@dataclass
class BronzeResult:
    entity: str
    table: str
    rows_read: int
    rows_written: int
    rows_rejected: int
    report: ValidationReport


def _rename_to_targets(frame: pd.DataFrame, mapping: dict) -> pd.DataFrame:
    """Rename the file's columns to target names, both sides normalised identically."""
    wanted = {normalize_header(src): tgt for src, tgt in source_to_target(mapping).items()}

    renames: dict[str, str] = {}
    for column in frame.columns:
        if column == "__row_number__":
            continue
        target = wanted.get(normalize_header(column))
        if target:
            renames[column] = target

    keep = [*renames.keys(), "__row_number__"]
    return frame[keep].rename(columns=renames)


def _conform_values(frame: pd.DataFrame, mapping: dict) -> pd.DataFrame:
    """Trim, case-fold and strip float artefacts. No casting, no lookups."""
    out = frame.copy()

    for column in out.columns:
        if column == "__row_number__":
            continue
        spec = mapping[column]

        out[column] = blank_to_null(out[column])
        if spec.get("trim"):
            out[column] = trim(out[column])
        if spec.get("identifier"):
            out[column] = strip_float_artifacts(out[column])

        # A column that feeds a lookup keeps the provider's own casing. The generated
        # report has to reproduce 'KYC Not Verified' verbatim, and the standardised
        # form lives in the separate <column>_std column that silver adds.
        # lookups.resolve() uppercases internally for matching, so nothing is lost.
        if not spec.get("lookup"):
            out[column] = apply_case(out[column], spec.get("case"))
        # Re-run after transforms: trimming can turn " " into "", and the compound
        # "/" sentinel only becomes recognisable once trimmed.
        out[column] = blank_to_null(out[column])

    return out


def _attach_provenance(
    frame: pd.DataFrame,
    meta: FileMetadata,
    report_variant: str,
) -> pd.DataFrame:
    out = frame.copy()
    out["source_file_id"] = meta.source_file_id
    out["row_number_in_file"] = out["__row_number__"].astype(int)
    out["report_variant"] = report_variant
    out["ingested_at"] = meta.ingested_at
    return out.drop(columns=["__row_number__"])


def _reject_null_keys(
    frame: pd.DataFrame,
    natural_key: list[str],
    entity: str,
    meta: FileMetadata,
) -> tuple[pd.DataFrame, list[Reject]]:
    """A row with a NULL natural key cannot be upserted. Reject it, do not drop it."""
    mask = frame[natural_key].isna().any(axis=1)
    if not mask.any():
        return frame, []

    rejects = [
        Reject(
            entity=entity,
            rule="natural_key_not_null",
            reason=f"NULL in natural key {natural_key}",
            source_file_id=meta.source_file_id,
            row_number_in_file=int(row["row_number_in_file"]),
            payload={k: row.get(k) for k in natural_key},
        )
        for _, row in frame[mask].iterrows()
    ]
    return frame[~mask].copy(), rejects


def write_bronze(
    engine,
    settings: Settings,
    frame: pd.DataFrame,
    meta: FileMetadata,
    entity: str,
    report_variant: str,
) -> BronzeResult:
    """Conform one file into its bronze table."""
    spec = ENTITIES[entity]
    mapping = spec["mapping"]
    table = spec["table"]
    natural_key = spec["natural_key"]
    schema = settings.schemas.bronze

    summary = LoadSummary(
        source_file_id=meta.source_file_id,
        entity=entity,
        layer="bronze",
        rows_read=len(frame),
        started_at=utc_now(),
    )

    try:
        report = validate_columns(frame, mapping, entity, meta.file_name)

        if report.missing_required:
            raise BronzeAborted(
                f"{meta.file_name}: required columns absent: {report.missing_required}. "
                f"Nothing written."
            )
        if report.unmapped_in_file and settings.runtime.strict:
            raise BronzeAborted(
                f"{meta.file_name}: file carries columns absent from the mapping: "
                f"{report.unmapped_in_file}. Nothing written — add them to "
                f"config/mapping_cams_wbr.py or set WBR_STRICT=false to drop them."
            )
        if report.unmapped_in_file:
            log.warning(
                "%s: dropping %d unmapped columns: %s",
                meta.file_name, len(report.unmapped_in_file), report.unmapped_in_file,
            )

        conformed = _attach_provenance(
            _conform_values(_rename_to_targets(frame, mapping), mapping),
            meta,
            report_variant,
        )

        conformed, rejects = _reject_null_keys(conformed, natural_key, entity, meta)
        summary.rows_rejected = len(rejects)
        if rejects:
            write_rejects(engine, settings.schemas.audit, rejects)

        # Last row wins within a file, so a provider re-stating a key inside one
        # delivery does not abort the whole upsert.
        before = len(conformed)
        conformed = conformed.drop_duplicates(subset=natural_key, keep="last")
        if len(conformed) != before:
            log.warning(
                "%s: collapsed %d duplicate %s rows within the file",
                meta.file_name, before - len(conformed), natural_key,
            )

        result = upsert(
            engine=engine,
            schema=schema,
            table=table,
            df=conformed,
            conflict_columns=natural_key,
            chunksize=spec.get("chunksize", settings.runtime.chunksize),
        )

        summary.rows_written = result.written
        summary.ok(f"upserted into {schema}.{table}")
        return BronzeResult(
            entity=entity,
            table=f"{schema}.{table}",
            rows_read=summary.rows_read,
            rows_written=summary.rows_written,
            rows_rejected=summary.rows_rejected,
            report=report,
        )

    except Exception as exc:
        summary.failed(str(exc))
        raise
    finally:
        write_load_summary(engine, settings.schemas.audit, summary)

"""Silver transformer: bronze -> typed, standardised, rule-checked.

Casting is driven entirely by the mapping config's `type` field. There is no
hand-maintained column list anywhere in this module — the existing pipeline's
transform_transaction casts "trade_date", "load_amount" and "broker_percent" while
the real columns are "traddate", "load" and "brokperc", so three of seven casts
silently do nothing.

A cast failure on a declared column is a REJECTION, not a silent NaT/NaN.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from sqlalchemy import text

from bronze.cleaners import (
    normalize_msisdn,
    parse_date_value,
    parse_integer_value,
    parse_numeric_value,
    split_compound,
)
from config import lookups
from config.mapping_cams_wbr import DATE_FALLBACKS, ENTITIES
from config.settings import Settings
from silver.rules import run_rules
from utils.audit import LoadSummary, Reject, utc_now, write_load_summary, write_rejects
from utils.logging import get_logger
from utils.upsert import upsert

log = get_logger(__name__)


@dataclass
class SilverResult:
    entity: str
    table: str
    rows_read: int
    rows_written: int
    rows_rejected: int


def _read_bronze(engine, schema: str, table: str, source_file_id: str | None) -> pd.DataFrame:
    """Read one file's rows, or the whole table.

    Scoped by source_file_id when given, so a rerun of one file does not reprocess
    everything. The existing gold extractor computes a watermark and then issues
    SELECT * with no WHERE, filtering afterwards in pandas.
    """
    if source_file_id:
        sql = text(f'SELECT * FROM "{schema}"."{table}" WHERE source_file_id = :sfid')
        params = {"sfid": source_file_id}
    else:
        sql = text(f'SELECT * FROM "{schema}"."{table}"')
        params = {}

    with engine.connect() as conn:
        return pd.read_sql(sql, conn, params=params)


def _cast_column(series: pd.Series, spec: dict) -> tuple[pd.Series, pd.Series]:
    """Cast one column. Returns (values, failed_mask)."""
    declared = spec["type"]

    if declared == "date":
        # Declared format first, then the per-column fallbacks, then the shared chain.
        # A .xls typed date cell renders as '%Y-%m-%d %H:%M:%S' under dtype=str, which
        # is not the display format a converted CSV shows.
        fallbacks = (
            *spec.get("date_format_fallbacks", ()),
            *DATE_FALLBACKS,
        )
        parsed = series.map(
            lambda v: parse_date_value(v, spec["date_format"], fallbacks)
        )
    elif declared.startswith("numeric"):
        parsed = series.map(parse_numeric_value)
    elif declared == "integer":
        parsed = series.map(parse_integer_value)
    else:
        # text / uuid / timestamptz pass through — bronze already conformed them.
        return series, pd.Series(False, index=series.index)

    values = parsed.map(lambda pair: pair[0])
    failed = parsed.map(lambda pair: not pair[1])
    return values, failed


def _apply_lookups(frame: pd.DataFrame, mapping: dict) -> pd.DataFrame:
    """Standardise coded values into `<column>_std`, recording anything unrecognised.

    The raw column is kept alongside the standardised one so the generated report can
    reproduce the provider's own wording while downstream consumers get a stable code.
    """
    out = frame.copy()
    unresolved_per_row: list[list[str]] = [[] for _ in range(len(out))]

    for column, spec in mapping.items():
        lookup_name = spec.get("lookup")
        if not lookup_name or column not in out.columns:
            continue

        std_values: list[str | None] = []
        for position, raw in enumerate(out[column].tolist()):
            value, recognised = lookups.resolve(lookup_name, raw)
            std_values.append(value)
            if not recognised:
                unresolved_per_row[position].append(f"{column}={raw!r}")

        out[f"{column}_std"] = std_values

    out["__unresolved_lookups__"] = unresolved_per_row
    return out


def _derive_columns(frame: pd.DataFrame, entity: str) -> pd.DataFrame:
    """Entity-specific enrichment that belongs in silver, not bronze."""
    out = frame.copy()

    if "location" in out.columns:
        out["location_code"], out["location_city"] = split_compound(out["location"])

    if "state" in out.columns:
        out["state_code"], out["state_name"] = split_compound(out["state"])

    if entity == "wbr56_kyc":
        if "mobile_no" in out.columns:
            out["mobile_e164"] = normalize_msisdn(out["mobile_no"])
        # A single overall KYC verdict, derived from the four holder positions.
        flags = [c for c in ("fh_kyc_std", "gu_kyc_std", "jh1_kyc_std", "jh2_kyc_std")
                 if c in out.columns]
        if flags:
            out["kyc_ok_any"] = out[flags].notna().any(axis=1)

    if entity == "wbr68_invalid_euin":
        # Deliberately `<> 'Y'`, not `== 'N'`: the sample contains an 'F' row carrying
        # the same reason, which a `== 'N'` filter would drop from the report.
        out["euin_is_invalid"] = out["euin_valid"].map(
            lambda v: v is not None and str(v).upper() != "Y"
        )

    if entity in {"wbr36_brokerage", "wbr36h_brokerage"}:
        measures = ["upfront", "afe", "trailer_fee", "trxn_charges", "clawback", "incentives"]
        present = [c for c in measures if c in out.columns]
        out["total_brokerage"] = out[present].fillna(0).sum(axis=1)

    return out


def transform_entity(
    engine,
    settings: Settings,
    entity: str,
    source_file_id: str | None = None,
) -> SilverResult:
    spec = ENTITIES[entity]
    mapping = spec["mapping"]
    table = spec["table"]
    natural_key = spec["natural_key"]
    bronze_schema = settings.schemas.bronze
    silver_schema = settings.schemas.silver

    frame = _read_bronze(engine, bronze_schema, table, source_file_id)

    summary = LoadSummary(
        source_file_id=source_file_id or "ALL",
        entity=entity,
        layer="silver",
        rows_read=len(frame),
        started_at=utc_now(),
    )

    try:
        if frame.empty:
            log.info("silver %s: nothing in bronze to transform", entity)
            summary.ok("no bronze rows")
            return SilverResult(entity, f"{silver_schema}.{table}", 0, 0, 0)

        rejects: list[Reject] = []
        failed_any = pd.Series(False, index=frame.index)

        for column, column_spec in mapping.items():
            if column not in frame.columns:
                continue
            # Keep the original before overwriting: a reject whose payload shows the
            # post-cast None tells nobody which value failed.
            original = frame[column].copy()
            values, failed = _cast_column(frame[column], column_spec)
            frame[column] = values

            if failed.any():
                for index in frame.index[failed]:
                    rejects.append(
                        Reject(
                            entity=entity,
                            rule=f"cast_{column_spec['type']}",
                            reason=(
                                f"{column} could not be cast to {column_spec['type']}"
                                + (f" with format {column_spec['date_format']!r}"
                                   if column_spec["type"] == "date" else "")
                            ),
                            source_file_id=frame.at[index, "source_file_id"],
                            row_number_in_file=int(frame.at[index, "row_number_in_file"]),
                            payload={column: str(original.at[index])},
                        )
                    )
                failed_any |= failed

        frame = _apply_lookups(frame, mapping)
        frame = _derive_columns(frame, entity)

        # Business rules, row by row.
        for index, row in frame.iterrows():
            for rule_name, reason in run_rules(row.to_dict(), entity):
                rejects.append(
                    Reject(
                        entity=entity,
                        rule=rule_name,
                        reason=reason,
                        source_file_id=row.get("source_file_id"),
                        row_number_in_file=int(row["row_number_in_file"]),
                        payload={k: row.get(k) for k in natural_key},
                    )
                )
                failed_any.at[index] = True

        if rejects:
            write_rejects(engine, settings.schemas.audit, rejects)
        summary.rows_rejected = int(failed_any.sum())

        clean = frame.loc[~failed_any].drop(columns=["__unresolved_lookups__"])

        if settings.runtime.strict and summary.rows_rejected:
            log.error(
                "silver %s: %d of %d rows rejected — see %s.rejects",
                entity, summary.rows_rejected, len(frame), settings.schemas.audit,
            )

        clean = clean.drop_duplicates(subset=natural_key, keep="last")

        result = upsert(
            engine=engine,
            schema=silver_schema,
            table=table,
            df=clean,
            conflict_columns=natural_key,
            chunksize=spec.get("chunksize", settings.runtime.chunksize),
        )

        summary.rows_written = result.written
        summary.ok(f"upserted into {silver_schema}.{table}")
        return SilverResult(
            entity=entity,
            table=f"{silver_schema}.{table}",
            rows_read=len(frame),
            rows_written=result.written,
            rows_rejected=summary.rows_rejected,
        )

    except Exception as exc:
        summary.failed(str(exc))
        raise
    finally:
        write_load_summary(engine, settings.schemas.audit, summary)

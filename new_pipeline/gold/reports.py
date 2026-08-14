"""Gold report builders.

Three functions per entity — extract_ / transform_ / load_ — so the shape matches
python_scripts/etl_gold_<entity>.py and stays recognisable to anyone who knows the
existing pipeline.

Two deliberate departures:

1. Grain is declared in config.GOLD_GRAIN and asserted after every load. That single
   check would have caught the two largest existing defects: gold.holdings carries
   128,766 rows for 3,591 distinct positions, and gold.folio_nominees inherits that
   to reach 378,735 rows where roughly 10,000 are real.
2. Row ids are uuid5 over the natural key, so a reload produces the same ids.
   gold.holdings uses uuid4() and regenerates every id each run, which breaks every
   folio_nominees.holding_id reference.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import pandas as pd
from sqlalchemy import text

from config.mapping_cams_wbr import GOLD_GRAIN, UUID_NAMESPACE
from config.settings import Settings
from utils.audit import LoadSummary, assert_grain, stable_uuid, utc_now, write_load_summary
from utils.logging import get_logger
from utils.upsert import upsert

log = get_logger(__name__)

NAMESPACE = uuid.UUID(UUID_NAMESPACE)


@dataclass
class GoldResult:
    entity: str
    table: str
    rows_in: int
    rows_out: int
    grain_rows: int = 0
    grain_keys: int = 0
    grain_ratio: float = 0.0


def _read_silver(engine, schema: str, table: str) -> pd.DataFrame:
    with engine.connect() as conn:
        return pd.read_sql(text(f'SELECT * FROM "{schema}"."{table}"'), conn)


def _drop_internal(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.drop(
        columns=[c for c in frame.columns if c.startswith("__")], errors="ignore"
    )


# =====================================================================
# WBR36 / WBR36H — brokerage by scheme
# =====================================================================

def extract_brokerage(engine, settings: Settings) -> pd.DataFrame:
    frame = _read_silver(engine, settings.schemas.silver, "brokerage_by_scheme")
    log.info("gold brokerage: %d silver rows", len(frame))
    return frame


def transform_brokerage(frame: pd.DataFrame, report_period: str) -> pd.DataFrame:
    """One row per (report_period, report_variant, product_code).

    Aggregation is a genuine sum: a provider can deliver the same product code more
    than once inside a period across multiple files, and brokerage measures are
    additive. Grain is asserted after the load, so a mistake here fails the run rather
    than quietly inflating the table.
    """
    if frame.empty:
        return frame

    measures = ["upfront", "afe", "trailer_fee", "trxn_charges", "clawback", "incentives"]
    present = [c for c in measures if c in frame.columns]

    out = frame.copy()
    out["report_period"] = report_period

    grouped = (
        out.groupby(["report_period", "report_variant", "product_code"], dropna=False)
        .agg(
            product_name=("product_name", "last"),
            **{m: (m, "sum") for m in present},
            source_file_id=("source_file_id", "last"),
            # Earliest position in the delivered file, so the export can reproduce the
            # provider's row order. Without it the report's order is heap order, which
            # changes after an UPDATE and makes two runs produce different files.
            source_row=("row_number_in_file", "min"),
        )
        .reset_index()
    )

    grouped["total_brokerage"] = grouped[present].fillna(0).sum(axis=1)
    grouped["id"] = [
        stable_uuid(NAMESPACE, row.report_period, row.report_variant, row.product_code)
        for row in grouped.itertuples()
    ]
    grouped["created_at"] = utc_now()

    log.info(
        "gold brokerage: %d silver rows -> %d rows at declared grain",
        len(frame), len(grouped),
    )
    return grouped


def load_brokerage(engine, settings: Settings, frame: pd.DataFrame) -> GoldResult:
    return _load(engine, settings, "brokerage_by_scheme", frame)


# =====================================================================
# WBR56 — investor KYC status
# =====================================================================

def extract_kyc(engine, settings: Settings) -> pd.DataFrame:
    frame = _read_silver(engine, settings.schemas.silver, "investor_kyc_status")
    log.info("gold kyc: %d silver rows", len(frame))
    return frame


def transform_kyc(frame: pd.DataFrame) -> pd.DataFrame:
    """One row per (amc_code, folio).

    Latest delivery wins per folio — ordered by rep_date so the winner is the most
    recent statement, not an arbitrary row. The existing holdings extractor uses
    `DISTINCT ON (source, folio_no)` with no tie-break in ORDER BY, so which investor
    row survives is arbitrary and can change between runs.
    """
    if frame.empty:
        return frame

    out = _drop_internal(frame).copy()

    sort_columns = [c for c in ("rep_date", "ingested_at", "row_number_in_file")
                    if c in out.columns]
    if sort_columns:
        out = out.sort_values(["amc_code", "folio", *sort_columns])

    out = out.drop_duplicates(subset=["amc_code", "folio"], keep="last")

    out["id"] = [
        stable_uuid(NAMESPACE, row.amc_code, row.folio) for row in out.itertuples()
    ]
    out["created_at"] = utc_now()
    if "row_number_in_file" in out.columns:
        out["source_row"] = out["row_number_in_file"].astype("Int64")

    log.info("gold kyc: %d silver rows -> %d rows at declared grain", len(frame), len(out))
    return out


def load_kyc(engine, settings: Settings, frame: pd.DataFrame) -> GoldResult:
    return _load(engine, settings, "investor_kyc_status", frame)


# =====================================================================
# WBR68 — invalid EUIN ledger
# =====================================================================

def extract_euin(engine, settings: Settings) -> pd.DataFrame:
    frame = _read_silver(engine, settings.schemas.silver, "invalid_euin")
    log.info("gold euin: %d silver rows", len(frame))
    return frame


def transform_euin(frame: pd.DataFrame) -> pd.DataFrame:
    """One row per (amc_code, trxn_no). A ledger — no aggregation.

    Kept explicitly as a ledger rather than being collapsed to a position, which is
    the distinction the existing gold.holdings gets wrong.
    """
    if frame.empty:
        return frame

    out = _drop_internal(frame).copy()

    # `<> 'Y'`, never `== 'N'`: the sample carries an 'F' row with the same reason.
    if "euin_valid" in out.columns:
        keep = out["euin_valid"].map(lambda v: v is None or str(v).upper() != "Y")
        dropped = int((~keep).sum())
        if dropped:
            log.warning("gold euin: excluded %d rows with a valid EUIN", dropped)
        out = out[keep]

    sort_columns = [c for c in ("trade_date", "ingested_at") if c in out.columns]
    if sort_columns:
        out = out.sort_values(["amc_code", "trxn_no", *sort_columns])
    out = out.drop_duplicates(subset=["amc_code", "trxn_no"], keep="last")

    out["id"] = [
        stable_uuid(NAMESPACE, row.amc_code, row.trxn_no) for row in out.itertuples()
    ]
    out["created_at"] = utc_now()
    if "row_number_in_file" in out.columns:
        out["source_row"] = out["row_number_in_file"].astype("Int64")

    log.info("gold euin: %d silver rows -> %d rows at declared grain", len(frame), len(out))
    return out


def load_euin(engine, settings: Settings, frame: pd.DataFrame) -> GoldResult:
    return _load(engine, settings, "invalid_euin", frame)


# =====================================================================
# Shared load
# =====================================================================

def _load(engine, settings: Settings, table: str, frame: pd.DataFrame) -> GoldResult:
    grain = GOLD_GRAIN[table]
    natural_key = grain["natural_key"]
    schema = settings.schemas.gold

    summary = LoadSummary(
        source_file_id="ALL", entity=table, layer="gold",
        rows_read=len(frame), started_at=utc_now(),
    )

    try:
        if frame.empty:
            summary.ok("no rows")
            return GoldResult(table, f"{schema}.{table}", 0, 0)

        # Restrict to columns the table actually has, so a silver-side derived column
        # does not break the insert.
        with engine.connect() as conn:
            existing = [
                r[0] for r in conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = :s AND table_name = :t "
                        "ORDER BY ordinal_position"
                    ),
                    {"s": schema, "t": table},
                )
            ]
        if not existing:
            raise RuntimeError(
                f"{schema}.{table} does not exist — run sql/002_tables.sql first"
            )

        payload = frame[[c for c in existing if c in frame.columns]].copy()

        result = upsert(
            engine=engine, schema=schema, table=table, df=payload,
            conflict_columns=natural_key,
            chunksize=settings.runtime.chunksize,
        )

        rows, keys, ratio = assert_grain(
            engine, schema, table, natural_key, grain["max_row_ratio"]
        )

        summary.rows_written = result.written
        summary.ok(f"grain {rows}/{keys} ratio {ratio:.2f}")
        return GoldResult(
            entity=table, table=f"{schema}.{table}",
            rows_in=len(frame), rows_out=result.written,
            grain_rows=rows, grain_keys=keys, grain_ratio=ratio,
        )

    except Exception as exc:
        summary.failed(str(exc))
        raise
    finally:
        write_load_summary(engine, settings.schemas.audit, summary)


ENTITY_TO_GOLD_TABLE = {
    "wbr36_brokerage": "brokerage_by_scheme",
    "wbr36h_brokerage": "brokerage_by_scheme",
    "wbr56_kyc": "investor_kyc_status",
    "wbr68_invalid_euin": "invalid_euin",
}

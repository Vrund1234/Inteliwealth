"""Reject tracking and load accounting.

The existing pipeline drops 10,862 transactions between silver and gold with no
reject table and no counter, so a healthy run and a lossy one look identical. Every
row this pipeline refuses is written here with the rule that refused it and enough
context to find it in the source spreadsheet.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from utils.logging import get_logger

log = get_logger(__name__)


def new_uuid() -> str:
    return str(uuid.uuid4())


def stable_uuid(namespace: uuid.UUID, *parts: Any) -> str:
    """Deterministic id from a natural key.

    uuid5 over a fixed namespace, so re-running produces the same ids. The existing
    gold.holdings uses uuid4() and regenerates every id on each load, which breaks
    every folio_nominees.holding_id reference. Pattern copied from
    python_scripts/etl_gold_scheme.py:718, which does this correctly.
    """
    key = "|".join("" if p is None else str(p) for p in parts)
    return str(uuid.uuid5(namespace, key))


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Reject:
    """One refused row."""

    entity: str
    rule: str
    reason: str
    source_file_id: str | None = None
    row_number_in_file: int | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class LoadSummary:
    """One layer's accounting for one entity, for one file."""

    source_file_id: str
    entity: str
    layer: str
    rows_read: int = 0
    rows_written: int = 0
    rows_rejected: int = 0
    status: str = "RUNNING"
    message: str = ""
    started_at: datetime = field(default_factory=utc_now)

    def ok(self, message: str = "") -> "LoadSummary":
        self.status = "OK"
        self.message = message
        return self

    def failed(self, message: str) -> "LoadSummary":
        self.status = "FAILED"
        self.message = message[:2000]
        return self


def write_rejects(engine: Engine, schema: str, rejects: list[Reject]) -> int:
    if not rejects:
        return 0

    statement = text(
        f'INSERT INTO "{schema}".rejects '
        "(reject_id, entity, rule, reason, source_file_id, row_number_in_file, payload, created_at) "
        "VALUES (:reject_id, :entity, :rule, :reason, :source_file_id, "
        ":row_number_in_file, CAST(:payload AS jsonb), :created_at)"
    )
    now = utc_now()
    rows = [
        {
            "reject_id": new_uuid(),
            "entity": r.entity,
            "rule": r.rule,
            "reason": r.reason[:1000],
            "source_file_id": r.source_file_id,
            "row_number_in_file": r.row_number_in_file,
            "payload": json.dumps(r.payload, default=str)[:20000],
            "created_at": now,
        }
        for r in rejects
    ]
    with engine.begin() as conn:
        conn.execute(statement, rows)

    log.warning("recorded %d rejected rows", len(rows))
    return len(rows)


def write_file_metadata(engine: Engine, schema: str, meta: dict[str, Any]) -> None:
    """Record an ingested file. This is what source_file_id points at.

    The existing pipeline declares source_file_id on two gold tables and never
    populates it, so no row can be traced back to the upload that produced it.
    """
    statement = text(
        f'INSERT INTO "{schema}".source_files '
        "(source_file_id, file_name, file_path, sha256, byte_size, entity, "
        " report_variant, format, rows_in_file, columns_in_file, period_from, "
        " period_to, ingested_at) "
        "VALUES (:source_file_id, :file_name, :file_path, :sha256, :byte_size, :entity, "
        " :report_variant, :format, :rows_in_file, :columns_in_file, :period_from, "
        " :period_to, :ingested_at) "
        "ON CONFLICT (sha256) DO UPDATE SET ingested_at = EXCLUDED.ingested_at"
    )
    with engine.begin() as conn:
        conn.execute(statement, meta)


def write_load_summary(engine: Engine, schema: str, summary: LoadSummary) -> None:
    statement = text(
        f'INSERT INTO "{schema}".load_summary '
        "(load_id, source_file_id, entity, layer, rows_read, rows_written, "
        " rows_rejected, status, message, started_at, finished_at) "
        "VALUES (:load_id, :source_file_id, :entity, :layer, :rows_read, :rows_written, "
        " :rows_rejected, :status, :message, :started_at, :finished_at)"
    )
    with engine.begin() as conn:
        conn.execute(
            statement,
            {
                "load_id": new_uuid(),
                "source_file_id": summary.source_file_id,
                "entity": summary.entity,
                "layer": summary.layer,
                "rows_read": summary.rows_read,
                "rows_written": summary.rows_written,
                "rows_rejected": summary.rows_rejected,
                "status": summary.status,
                "message": summary.message,
                "started_at": summary.started_at,
                "finished_at": utc_now(),
            },
        )


def assert_grain(
    engine: Engine,
    schema: str,
    table: str,
    natural_key: list[str],
    max_ratio: float = 1.0,
) -> tuple[int, int, float]:
    """Fail if a table's row count exceeds its distinct natural key count.

    This single check would have caught the two largest defects in the existing
    pipeline on day one: gold.holdings carries 128,766 rows for 3,591 distinct
    (rta, folio_number, scheme_id) combinations, and gold.folio_nominees inherits
    that inflation to reach 378,735 rows where roughly 10,000 are real.
    """
    key_list = ", ".join(f'"{c}"' for c in natural_key)
    sql = text(
        f'SELECT count(*) AS rows, count(DISTINCT ({key_list})) AS keys '
        f'FROM "{schema}"."{table}"'
    )
    with engine.connect() as conn:
        row = conn.execute(sql).one()

    rows, keys = int(row[0]), int(row[1])
    ratio = (rows / keys) if keys else 0.0
    if keys and ratio > max_ratio:
        raise AssertionError(
            f"{schema}.{table}: grain violation — {rows} rows for {keys} distinct "
            f"{natural_key} (ratio {ratio:.2f} > {max_ratio}). The table is not at "
            f"its declared grain."
        )
    log.info(
        "%s.%s grain ok: %d rows / %d keys (ratio %.2f)", schema, table, rows, keys, ratio
    )
    return rows, keys, ratio

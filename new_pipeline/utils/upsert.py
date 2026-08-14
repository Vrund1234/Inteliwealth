"""The shared upsert helper. Used by all three layers.

This replaces the pattern used 14 times in the existing pipeline: pull the entire
destination table into pandas, build a string key per row, anti-join, then
to_sql(if_exists="append"). Measured cost of that approach on the existing data is
roughly 2.5 GB of reads per run, it is not atomic, and it cannot express an update —
gold silently skips a corrected row, silver silently inserts a second one.

Here the database does the work: INSERT ... ON CONFLICT (natural_key) DO UPDATE,
inside one transaction, with bound parameters. Requires a UNIQUE constraint on the
conflict target, which sql/002_tables.sql creates for every table.
"""

from __future__ import annotations

from typing import Iterable, Sequence

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from utils.logging import get_logger

log = get_logger(__name__)


class UpsertResult:
    __slots__ = ("table", "attempted", "written")

    def __init__(self, table: str, attempted: int, written: int) -> None:
        self.table = table
        self.attempted = attempted
        self.written = written

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<UpsertResult {self.table} attempted={self.attempted} written={self.written}>"


def _quote_ident(name: str) -> str:
    """Quote an identifier for interpolation into DDL/DML.

    Identifiers cannot be bound as parameters in PostgreSQL, so they are the one
    thing that must be interpolated. Rejecting anything outside a safe character
    set is what makes that interpolation safe. All values still go through bound
    parameters.
    """
    if not name or not all(ch.isalnum() or ch == "_" for ch in name):
        raise ValueError(f"unsafe SQL identifier: {name!r}")
    return f'"{name}"'


def upsert(
    engine: Engine,
    schema: str,
    table: str,
    df: pd.DataFrame,
    conflict_columns: Sequence[str],
    update_columns: Iterable[str] | None = None,
    chunksize: int = 2000,
) -> UpsertResult:
    """Insert rows, updating on conflict against `conflict_columns`.

    All rows in one transaction: either the whole batch lands or none of it does.
    The existing pipeline autocommits per chunk, so a failure at chunk 7 of 26
    leaves 6 chunks committed with no marker and no rollback path.
    """
    if df.empty:
        log.info("%s.%s: nothing to write", schema, table)
        return UpsertResult(f"{schema}.{table}", 0, 0)

    missing = [c for c in conflict_columns if c not in df.columns]
    if missing:
        raise ValueError(
            f"{schema}.{table}: conflict columns absent from frame: {missing}"
        )

    columns = list(df.columns)
    if update_columns is None:
        update_columns = [c for c in columns if c not in conflict_columns]
    update_columns = [c for c in update_columns if c in columns]

    q_schema = _quote_ident(schema)
    q_table = _quote_ident(table)
    col_list = ", ".join(_quote_ident(c) for c in columns)
    placeholders = ", ".join(f":{c}" for c in columns)
    conflict_list = ", ".join(_quote_ident(c) for c in conflict_columns)

    if update_columns:
        assignments = ", ".join(
            f"{_quote_ident(c)} = EXCLUDED.{_quote_ident(c)}" for c in update_columns
        )
        action = f"DO UPDATE SET {assignments}"
    else:
        # Every column is part of the key; there is nothing to update.
        action = "DO NOTHING"

    statement = text(
        f"INSERT INTO {q_schema}.{q_table} ({col_list})\n"
        f"VALUES ({placeholders})\n"
        f"ON CONFLICT ({conflict_list}) {action}"
    )

    records = df.astype(object).where(pd.notna(df), None).to_dict(orient="records")

    written = 0
    with engine.begin() as conn:
        for start in range(0, len(records), chunksize):
            batch = records[start : start + chunksize]
            conn.execute(statement, batch)
            written += len(batch)

    log.info("%s.%s: upserted %d rows", schema, table, written)
    return UpsertResult(f"{schema}.{table}", len(records), written)

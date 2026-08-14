#!/usr/bin/env python
"""CLI entry point.

Runnable from a shell, from cron and from tests, with a non-zero exit status on
failure. The existing pipeline's bronze writers can only be reached through the
Streamlit UI because they take UploadedFile objects rather than paths, so there is no
way to run ingestion from the command line at all.

Examples
--------
    # create schemas, tables and audit tables
    python main.py migrate

    # end to end over a directory of WBR files
    python main.py run --input /home/user/Inteliwealth-pipeline/files/gold --period 2025

    # one layer at a time
    python main.py bronze --input <dir>
    python main.py silver
    python main.py gold --period 2025 --formats xlsx,csv,xls

    # what would be routed, without touching the database
    python main.py plan --input <dir>
"""

from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

# Make the package importable when invoked as `python main.py` from any directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from bronze.writer import BronzeAborted, write_bronze          # noqa: E402
from config.mapping_cams_wbr import ENTITIES, OUTPUT_LAYOUTS   # noqa: E402
from config.settings import Settings, load_settings            # noqa: E402
from gold.loader import load_gold                              # noqa: E402
from ingestion.reader import read_file                         # noqa: E402
from ingestion.router import Routed, discover, route_all       # noqa: E402
from silver.transformer import transform_entity                # noqa: E402
from utils.audit import write_file_metadata                    # noqa: E402
from utils.db import get_engine, ping                          # noqa: E402
from utils.logging import get_logger, setup_logging            # noqa: E402

log = get_logger("main")


# ---------------------------------------------------------------------
# migrate
# ---------------------------------------------------------------------

def cmd_migrate(settings: Settings) -> int:
    from sqlalchemy import text

    engine = get_engine(settings)
    log.info("connected to %s", ping(engine))

    scripts = sorted(settings.paths.sql_dir.glob("*.sql"))
    if not scripts:
        log.error("no .sql files in %s", settings.paths.sql_dir)
        return 1

    for script in scripts:
        log.info("applying %s", script.name)
        statement = script.read_text()
        with engine.begin() as conn:
            conn.execute(text(statement))

    log.info("migrate: applied %d scripts", len(scripts))
    return 0


# ---------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------

def cmd_plan(settings: Settings, input_dir: Path) -> int:
    routed, unrouted = route_all(discover(input_dir))

    print(f"\ninput: {input_dir}")
    print(f"routed ({len(routed)}):")
    for item in routed:
        print(f"  {item.path.name}")
        print(f"      entity={item.entity} format={item.fmt} variant={item.report_variant}")

    if unrouted:
        print(f"\nUNROUTED ({len(unrouted)}):")
        for item in unrouted:
            print(f"  {item.path.name}: {item.reason}")

    # Unrouted files are a failure, not a warning. The existing pipeline prints
    # "Unknown file type" and reports the extraction as successful.
    return 1 if unrouted else 0


# ---------------------------------------------------------------------
# bronze
# ---------------------------------------------------------------------

def _ingest_one(engine, settings: Settings, item: Routed) -> int:
    source_file_id = str(uuid.uuid4())
    frame, meta = read_file(item.path, source_file_id)
    meta.entity = item.entity
    meta.report_variant = item.report_variant

    write_file_metadata(engine, settings.schemas.audit, meta.as_row())

    result = write_bronze(
        engine=engine,
        settings=settings,
        frame=frame,
        meta=meta,
        entity=item.entity,
        report_variant=item.report_variant,
    )
    log.info(
        "bronze %s: read=%d written=%d rejected=%d -> %s",
        result.entity, result.rows_read, result.rows_written,
        result.rows_rejected, result.table,
    )
    return result.rows_written


def cmd_bronze(settings: Settings, input_dir: Path) -> int:
    engine = get_engine(settings)
    log.info("connected to %s", ping(engine))

    routed, unrouted = route_all(discover(input_dir))
    if unrouted and settings.runtime.strict:
        for item in unrouted:
            log.error("refusing to proceed: %s: %s", item.path.name, item.reason)
        return 1
    if not routed:
        log.error("no recognised files in %s", input_dir)
        return 1

    failures = 0
    for item in routed:
        try:
            _ingest_one(engine, settings, item)
        except BronzeAborted as exc:
            log.error("ABORTED %s: %s", item.path.name, exc)
            failures += 1
        except Exception:
            log.exception("FAILED %s", item.path.name)
            failures += 1

    return 1 if failures else 0


# ---------------------------------------------------------------------
# silver
# ---------------------------------------------------------------------

def cmd_silver(settings: Settings, entities: list[str] | None) -> int:
    engine = get_engine(settings)
    requested = entities or list(ENTITIES)

    # wbr36_brokerage and wbr36h_brokerage share one table, and the transformer reads
    # the whole table, so running both would process the same 152 rows twice.
    # Deduplicate by target table, keeping the first entity that reaches each one.
    targets: list[str] = []
    seen_tables: set[str] = set()
    for entity in requested:
        table = ENTITIES[entity]["table"]
        if table in seen_tables:
            log.debug("silver: %s shares table %s, already queued", entity, table)
            continue
        seen_tables.add(table)
        targets.append(entity)

    failures = 0
    for entity in targets:
        try:
            result = transform_entity(engine, settings, entity)
            log.info(
                "silver %s: read=%d written=%d rejected=%d -> %s",
                result.entity, result.rows_read, result.rows_written,
                result.rows_rejected, result.table,
            )
        except Exception:
            log.exception("silver %s FAILED", entity)
            failures += 1

    return 1 if failures else 0


# ---------------------------------------------------------------------
# gold
# ---------------------------------------------------------------------

def cmd_gold(settings: Settings, period: str, formats: tuple[str, ...],
             export: bool) -> int:
    engine = get_engine(settings)
    result = load_gold(
        engine=engine, settings=settings, report_period=period,
        export=export, formats=formats,
    )
    print("\n" + result.describe())
    return 0


# ---------------------------------------------------------------------
# run — everything
# ---------------------------------------------------------------------

def cmd_run(settings: Settings, input_dir: Path, period: str,
            formats: tuple[str, ...]) -> int:
    for step in (
        lambda: cmd_bronze(settings, input_dir),
        lambda: cmd_silver(settings, None),
        lambda: cmd_gold(settings, period, formats, export=True),
    ):
        code = step()
        if code != 0:
            # Fail fast. The existing orchestrator catches each entity's exception and
            # continues, so dependent entities join stale data while the UI reports
            # success.
            log.error("pipeline stopped: a step returned %d", code)
            return code
    return 0


# ---------------------------------------------------------------------

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="new_pipeline",
        description="CAMS WBR report pipeline (bronze_wbr / silver_wbr / gold_wbr).",
    )
    parser.add_argument("--log-level", default=None,
                        help="DEBUG|INFO|WARNING|ERROR (default from WBR_LOG_LEVEL)")

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("migrate", help="apply sql/*.sql")

    for name, helptext in (("plan", "show what would be routed, no database access"),
                           ("bronze", "ingest files into bronze_wbr")):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("--input", type=Path, default=None,
                       help="directory of WBR files (default from WBR_INPUT_DIR)")

    p_silver = sub.add_parser("silver", help="bronze_wbr -> silver_wbr")
    p_silver.add_argument("--entity", action="append", choices=sorted(ENTITIES),
                          help="repeatable; default is every entity")

    p_gold = sub.add_parser("gold", help="silver_wbr -> gold_wbr, then export reports")
    p_gold.add_argument("--period", default="UNSPECIFIED",
                        help="reporting period label, e.g. 2025 or 2025-H1. WBR36 "
                             "carries no date column, so this cannot be inferred")
    p_gold.add_argument("--formats", default="xlsx,csv",
                        help="comma-separated: xlsx,csv,xls (xls needs LibreOffice)")
    p_gold.add_argument("--no-export", action="store_true",
                        help="load the tables but do not write report files")

    p_run = sub.add_parser("run", help="bronze -> silver -> gold -> export")
    p_run.add_argument("--input", type=Path, default=None)
    p_run.add_argument("--period", default="UNSPECIFIED")
    p_run.add_argument("--formats", default="xlsx,csv")

    sub.add_parser("reports", help="list the report layouts this pipeline emits")

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    settings = load_settings()
    setup_logging(args.log_level or settings.runtime.log_level)

    log.debug("database: %s", settings.db.masked_url())

    input_dir = getattr(args, "input", None) or settings.paths.input_dir
    formats = tuple(
        f.strip() for f in getattr(args, "formats", "xlsx,csv").split(",") if f.strip()
    )

    if args.command == "migrate":
        return cmd_migrate(settings)
    if args.command == "plan":
        return cmd_plan(settings, input_dir)
    if args.command == "bronze":
        return cmd_bronze(settings, input_dir)
    if args.command == "silver":
        return cmd_silver(settings, args.entity)
    if args.command == "gold":
        return cmd_gold(settings, args.period, formats, export=not args.no_export)
    if args.command == "run":
        return cmd_run(settings, input_dir, args.period, formats)
    if args.command == "reports":
        for code, layout in OUTPUT_LAYOUTS.items():
            print(f"{code:8s} {len(layout['columns']):>3d} columns  "
                  f"from {layout['source_table']}  ->  {layout['file_stem']}")
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())

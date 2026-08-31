"""Cron entry point: reserve a batch, load it, report each file's outcome.

    cd python_scripts && venv/bin/python -m etl_pipeline.runner
    cd python_scripts && venv/bin/python -m etl_pipeline.runner --dry-run

Bronze runs one file at a time -- that is what makes the per-file counts exact,
and processing sequentially gives the right answer when two files in one batch
share rows: the first file's rows insert as new, the second's are flagged as
duplicates against them. Silver and gold run once per batch, because both are
whole-layer rebuilds.
"""

import argparse
import sys
import traceback
from datetime import datetime, timezone

from etl_investor_master import process_investor_master
from etl_sip import process_sip
from etl_trans import process_transactions
from gold_loader import load_gold
from raw_ingestion import read_file
from scheme_mapping import load_scheme_mapping
from transformations.transform import load_silver

from . import config, dispatch, file_logger, layer_stats, pipeline_lock, s3_client
from .api_client import EtlHandoffClient, EtlHandoffFatalError
from .logging_repo import (
    is_already_processed,
    log_event,
    mark_processed,
    new_run_id,
)

# The runner calls these directly rather than extract_and_push(): one file at a
# time, so each file's counts are its own. extract_and_push() stays the
# Streamlit entry point. Two callers, one set of loaders, no forked ETL logic.
#
# Each loader takes cams=/kfin= keyword arguments, which is why the vendor from
# dispatch.resolve() is used to build the call below.
BRONZE_LOADER_BY_DTYPE = {
    "transaction": process_transactions,
    "investor": process_investor_master,
    "sip": process_sip,
}


def _now():
    return datetime.now(timezone.utc)


def _base_fields(item):
    """The item's identifying columns, shared by every log row about it."""
    return {
        "handoff_id": item.get("handoff_id"),
        "rta": item.get("rta"),
        "arn_code": item.get("arn_code"),
        "report_code": item.get("report_code"),
        "filename": item.get("filename"),
        "content_hash": item.get("content_hash"),
        "payload_format": item.get("payload_format"),
        "file_size": item.get("file_size"),
        "source_s3_uri": item.get("source_s3_uri"),
        "report_date": s3_client.report_date_from_uri(item.get("source_s3_uri")),
    }


def ingest_file(item, run_id, log):
    """Download, read and bronze-load one reserved file.

    Returns a record the reporting step consumes:
      {"item", "dtype", "ok", "skipped", "counts", "failure_reason", "error"}

    Never raises: one bad file must not cost the rest of the batch the work
    they have already done.
    """
    fields = _base_fields(item)
    started = _now()
    record = {"item": item, "dtype": None, "ok": False, "skipped": False,
              "counts": None, "failure_reason": None, "error": None}

    resolved = dispatch.resolve(item.get("rta"), item.get("report_code"))
    if resolved is None:
        # Any code raw_ingestion.py has no rule for. Logged loudly and
        # reported FAILED rather than dropped silently as "Unknown file type"
        # -- after three attempts the backend marks it ABANDONED, where a
        # human will see it.
        message = (
            f"report_code {item.get('report_code')!r} for RTA "
            f"{item.get('rta')!r} is not in the dispatch table"
        )
        log.warning("%s (%s)", message, item.get("filename"))
        record["failure_reason"] = dispatch.UNSUPPORTED_REPORT_CODE_REASON
        record["error"] = message
        log_event(run_id, "BRONZE", "SKIPPED", comment=message,
                  started_at=started, ended_at=_now(), **fields)
        return record

    dtype, vendor = resolved
    record["dtype"] = dtype

    if is_already_processed(item.get("content_hash")):
        # Cross-run idempotency: these exact bytes have already been loaded.
        message = "content_hash already processed by an earlier run"
        log.info("%s: %s", item.get("filename"), message)
        record["skipped"] = True
        record["ok"] = True
        log_event(run_id, "BRONZE", "SKIPPED_DUPLICATE", comment=message,
                  started_at=started, ended_at=_now(), **fields)
        return record

    try:
        # The API's `filename`, NEVER the S3 key's basename -- raw_ingestion
        # dispatches on it, and the key does not follow the naming convention.
        buffer = s3_client.download_as_file(item["source_s3_uri"], item["filename"])
    except Exception as exc:
        record["failure_reason"] = dispatch.DOWNLOAD_FAILED
        record["error"] = f"{type(exc).__name__}: {exc}"
        log.error("%s: download failed: %s", item.get("filename"), record["error"])
        log_event(run_id, "BRONZE", "FAILED", comment=record["error"],
                  started_at=started, ended_at=_now(), **fields)
        return record

    try:
        df = read_file(buffer)
    except Exception as exc:
        record["failure_reason"] = dispatch.failure_reason_for(exc)
        record["error"] = f"{type(exc).__name__}: {exc}"
        log.error("%s: read failed: %s", item.get("filename"), record["error"])
        log_event(run_id, "BRONZE", "FAILED", comment=record["error"],
                  started_at=started, ended_at=_now(), **fields)
        return record

    try:
        counts = BRONZE_LOADER_BY_DTYPE[dtype](**{vendor: df})
    except Exception as exc:
        record["failure_reason"] = dispatch.EXTRACT_FAILED
        record["error"] = f"{type(exc).__name__}: {exc}"
        log.error("%s: bronze load failed: %s", item.get("filename"), record["error"])
        log_event(run_id, "BRONZE", "FAILED", comment=record["error"],
                  total_records=len(df), started_at=started, ended_at=_now(), **fields)
        return record

    record["ok"] = True
    record["counts"] = counts
    log.info(
        "%s -> bronze.%s: total=%s new=%s duplicate=%s",
        item.get("filename"), dispatch.SILVER_TABLE_BY_DTYPE[dtype],
        counts["total"], counts["new"], counts["duplicate"],
    )
    log_event(run_id, "BRONZE", "COMPLETED", entity=dtype,
              started_at=started, ended_at=_now(),
              **layer_stats.bronze_counts(counts), **fields)
    return record


def _run_scheme_mapping(run_id, log):
    """Mirrors app.py:305-314. promote_approved() is NEVER called here.

    Fuzzy matches queue in bronze.scheme_mapping_review for a human exactly as
    they do today: a bad fuzzy match silently corrupts scheme_id across gold,
    so mapping stays a decision someone makes.
    """
    started = _now()
    try:
        summary = load_scheme_mapping()
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        log.error("scheme mapping failed: %s", message)
        log_event(run_id, "SCHEME_MAPPING", "FAILED", comment=message,
                  started_at=started, ended_at=_now())
        return None

    comment = (
        f"newly_queued={summary.get('newly_queued')} "
        f"tier1={summary.get('queued_tier1')} tier2={summary.get('queued_tier2')} "
        f"ambiguous={summary.get('ambiguous')} "
        f"no_candidate={summary.get('no_candidate')} "
        f"still_unmatched={summary.get('still_unmatched')}"
    )
    log.info("scheme mapping: %s", comment)
    log_event(run_id, "SCHEME_MAPPING", "COMPLETED", comment=comment,
              started_at=started, ended_at=_now(),
              **layer_stats.scheme_mapping_counts(summary))
    return summary


def _run_silver(run_id, log):
    started = _now()
    try:
        results = load_silver()
    except Exception as exc:
        # load_silver() itself does not raise today (append_new_rows swallows),
        # but a failure inside safe_read or a transform_* could. Treat it as
        # every table failing, so no file is reported COMPLETED on a silver
        # rebuild that did not happen.
        message = f"{type(exc).__name__}: {exc}"
        log.error("silver load raised: %s", message)
        results = {
            table: {"table": table, "status": "FAILED", "total": 0,
                    "inserted": 0, "updated": 0, "error": message}
            for table in dispatch.SILVER_TABLE_BY_DTYPE.values()
        }

    for table, result in results.items():
        log.info("silver.%s: %s inserted=%s updated=%s",
                 table, result["status"], result["inserted"], result["updated"])
        log_event(run_id, "SILVER", result["status"], entity=table,
                  comment=result.get("error"), started_at=started, ended_at=_now(),
                  **layer_stats.silver_counts(result))
    return results


def _run_gold(run_id, log):
    started = _now()
    try:
        results = load_gold()
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        log.error("gold load raised: %s", message)
        results = {
            entity: {"entity": entity, "status": "FAILED", "total": 0,
                     "inserted": 0, "updated": 0, "error": message}
            for entity in dispatch.GOLD_ENTITY_DEPENDENCIES
        }

    for entity, result in results.items():
        log.info("gold.%s: %s inserted=%s updated=%s",
                 entity, result["status"], result["inserted"], result["updated"])
        log_event(run_id, "GOLD", result["status"], entity=entity,
                  comment=result.get("error"), started_at=started, ended_at=_now(),
                  **layer_stats.gold_counts(result))
    return results


def _report(client, run_id, log, records, failed):
    """PATCH each file's outcome. Returns the number of reports that failed.

    A reporting failure is logged rather than aborting: the remaining files'
    work is already done, and a 409 (a double report, or a reservation another
    runner now owns) is expected rather than exceptional.
    """
    failures = 0

    for record in records:
        item = record["item"]
        fields = _base_fields(item)
        counts = record["counts"] or {}

        if record["failure_reason"] is not None:
            status, reason = "FAILED", record["failure_reason"]
            error_message = record["error"]
        elif record["skipped"]:
            # A cross-run duplicate contributed no rows to this run's silver or
            # gold rebuild, so a downstream failure in this run cannot be its
            # fault. Its bytes are already loaded; report it done.
            status, reason, error_message = "COMPLETED", None, None
        else:
            status, reason = layer_stats.file_outcome(
                record["dtype"], record["ok"], failed
            )
            error_message = None
            if status == "FAILED":
                error_message = (
                    f"a downstream layer consuming '{record['dtype']}' reported "
                    "an error in this run"
                )

        started = _now()
        outcome = client.report_outcome(
            item["handoff_id"], status,
            rows_extracted=counts.get("total"),
            failure_reason=reason,
            error_message=error_message,
        )

        if not outcome["ok"]:
            failures += 1
            log.error("report for %s failed: %s", item.get("filename"),
                      outcome["reason"])
        else:
            log.info("reported %s for %s", status, item.get("filename"))
            # Recorded only once the file has actually made it through, so a
            # FAILED file is retried rather than skipped as a duplicate.
            if status == "COMPLETED" and not record["skipped"]:
                mark_processed(item.get("content_hash"), item["handoff_id"],
                               rows_extracted=counts.get("total"))

        log_event(
            run_id, "REPORT",
            status if outcome["ok"] else "FAILED",
            entity=record["dtype"], comment=error_message or outcome["reason"],
            api_request=outcome.get("api_request"),
            api_response=outcome.get("api_response"),
            http_status=outcome.get("http_status"),
            started_at=started, ended_at=_now(),
            **layer_stats.bronze_counts(record["counts"]), **fields,
        )

    return failures


def run_once(client=None, limit=None, dry_run=False, run_id=None, log=None):
    """One cron invocation. Returns a summary dict."""
    run_id = run_id or new_run_id()
    log = log or file_logger.setup(run_id)
    client = client or EtlHandoffClient()
    started = _now()

    summary = {"run_id": run_id, "dry_run": dry_run, "reserved": 0,
               "completed": 0, "failed": 0, "skipped": 0, "report_failures": 0}

    log_event(run_id, "RUN", "STARTED", started_at=started,
              comment="dry-run" if dry_run else None)

    if dry_run:
        # GET /pending is not feature-flag gated and reserves nothing, so this
        # never burns an attempt_count.
        pending = client.peek_pending()
        log.info("dry run: %s file(s) pending", len(pending))
        for row in pending:
            log.info("  would process %s %s (%s)", row.get("rta"),
                     row.get("report_code"), row.get("handoff_filename"))
        summary["reserved"] = len(pending)
        log_event(run_id, "RUN", "COMPLETED", started_at=started, ended_at=_now(),
                  total_records=len(pending), comment="dry-run")
        return summary

    reserve_started = _now()
    items = client.reserve(limit=limit)
    log.info("reserved %s file(s)", len(items))
    log_event(run_id, "RESERVE", "COMPLETED", total_records=len(items),
              api_request=getattr(client, "last_request", None),
              api_response=getattr(client, "last_response", None),
              http_status=getattr(client, "last_status", None),
              started_at=reserve_started, ended_at=_now())
    summary["reserved"] = len(items)

    if not items:
        log_event(run_id, "RUN", "COMPLETED", started_at=started, ended_at=_now(),
                  total_records=0, comment="queue drained")
        return summary

    # --- bronze, one file at a time --------------------------------------
    records = [ingest_file(item, run_id, log) for item in items]

    ingested = [r for r in records if r["ok"] and not r["skipped"]]

    # --- scheme mapping, silver, gold: once per batch ---------------------
    if any(r["dtype"] == "transaction" for r in ingested):
        _run_scheme_mapping(run_id, log)
    else:
        log.info("no transaction file ingested; skipping scheme mapping")

    silver_results = _run_silver(run_id, log) if ingested else {}
    gold_results = _run_gold(run_id, log) if ingested else {}
    failed = dispatch.failed_dtypes(silver_results, gold_results)
    if failed:
        log.warning("layers failed for dtype(s): %s", sorted(failed))

    # --- report each file -------------------------------------------------
    summary["report_failures"] = _report(client, run_id, log, records, failed)
    summary["skipped"] = sum(1 for r in records if r["skipped"])
    summary["failed"] = sum(
        1 for r in records
        if r["failure_reason"] is not None
        or (not r["skipped"] and r["dtype"] in failed)
    )
    summary["completed"] = len(records) - summary["failed"]

    log.info("run complete: %s", summary)
    log_event(run_id, "RUN", "COMPLETED", started_at=started, ended_at=_now(),
              total_records=len(records),
              total_processed=summary["completed"],
              total_duplicate=summary["skipped"],
              comment=f"report_failures={summary['report_failures']}")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(prog="etl_pipeline.runner")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="List what would be processed via GET /pending. Reserves nothing, "
             "so no attempt_count is spent.",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help=f"Files to reserve. Defaults to ETL_BATCH_LIMIT "
             f"({config.ETL_BATCH_LIMIT}); the API caps it at 50.",
    )
    args = parser.parse_args(argv)

    run_id = new_run_id()
    log = file_logger.setup(run_id)

    connection = pipeline_lock.try_acquire()
    if connection is None:
        # Not an error: overlapping cron invocations are expected and safe.
        log.info("another run is in progress; exiting")
        return 0

    try:
        run_once(limit=args.limit, dry_run=args.dry_run, run_id=run_id, log=log)
        return 0
    except EtlHandoffFatalError as exc:
        # A 403 (missing grant / must_change_password / incomplete org) or a
        # 503 (handoff disabled). Retrying can never resolve either.
        log.error("fatal: %s", exc)
        return 1
    except Exception as exc:
        log.error("run failed: %s\n%s", exc, traceback.format_exc())
        return 1
    finally:
        pipeline_lock.release(connection)


if __name__ == "__main__":
    sys.exit(main())

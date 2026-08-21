from datetime import datetime, timedelta, timezone

import pandas as pd

from etl_pipeline import config, dedup, hold_groups, logging_repo, pipeline_lock
from etl_pipeline.api_client import EtlHandoffClient
from etl_pipeline.s3_client import download_as_file
from raw_ingestion import read_file
from etl_trans import process_transactions
from etl_investor_master import process_investor_master
from etl_sip import process_sip
from transformations.transform import load_silver
import gold_loader


# Which silver table each dtype (see DISPATCH above) lands in, so a group's
# SILVER log row can be matched back to load_silver()'s per-table result.
SILVER_TABLE_BY_DTYPE = {
    "transaction": "transaction_master_new",
    "investor": "investor_master",
    "sip": "sip_master_new",
}


DISPATCH = {
    ("CAMS", "WBR2"): ("transaction", "cams"),
    ("CAMS", "WBR9"): ("investor", "cams"),
    ("CAMS", "WBR49"): ("sip", "cams"),
    ("KFIN", "MFSD211"): ("transaction", "kfin"),
    ("KFIN", "MFSD201"): ("investor", "kfin"),
    ("KFIN", "MFSD243"): ("sip", "kfin"),
}


def run_once():
    lock_conn = pipeline_lock.try_acquire()
    if lock_conn is None:
        print("Another pipeline run is already in progress. Exiting.")
        return

    run_id = logging_repo.new_run_id()

    try:
        client = EtlHandoffClient()
        _discover_and_reserve(client, run_id)
        completed = _run_bronze_for_ready_groups(client, run_id)
        if completed:
            silver_result = load_silver()
            _report_silver_outcomes(run_id, completed, silver_result)
            gold_result = gold_loader.load_gold()
            _report_gold_outcomes(client, run_id, completed, gold_result)
        _check_hold_timeouts()
    finally:
        pipeline_lock.release(lock_conn)


def _discover_and_reserve(client, run_id):
    pending = client.peek_pending(limit=config.ETL_PEEK_LIMIT)
    coarse_groups = hold_groups.group_pending_items(pending)
    target_ids = set(hold_groups.ready_handoff_ids(coarse_groups))

    for key, group in coarse_groups.items():
        if group["missing"]:
            members = {
                item["report_code"]: {"handoff_id": item["id"], "bronze_done": False}
                for item in group["items"]
            }
            logging_repo.upsert_group_hold(
                key, group["rta"], group["arn_code"], None,
                group["required"], members, "HOLDING",
            )
            for item in group["items"]:
                logging_repo.log_event(
                    run_id=run_id, handoff_id=item["id"], group_key=key,
                    rta=item["rta"], arn_code=item.get("arn_code"),
                    report_code=item["report_code"], filename=None,
                    source_s3_uri=None, content_hash=None,
                    layer="HOLD", status="HOLDING",
                )

    if not target_ids:
        return

    reserved, calls, seen = [], 0, set()
    while (target_ids - seen) and calls < config.ETL_MAX_RESERVATION_CALLS_PER_RUN:
        batch = client.reserve(limit=config.ETL_BATCH_LIMIT)
        calls += 1
        if not batch:
            break
        for item in batch:
            seen.add(item["handoff_id"])
        reserved.extend(batch)

    real_groups = hold_groups.regroup_by_authoritative_key(reserved)
    for key, group in real_groups.items():
        members = {
            code: {**item, "bronze_done": False}
            for code, item in group["members"].items()
        }
        status = "READY" if hold_groups.is_group_complete(group) else "HOLDING"
        logging_repo.upsert_group_hold(
            key, group["rta"], group["arn_code"], group["s3_date"],
            group["required"], members, status,
        )


def _run_bronze_for_ready_groups(client, run_id):
    completed = []

    for hold in logging_repo.get_groups_with_status(["READY", "PROCESSING"]):
        key = hold["group_key"]
        members = hold["members"]
        required = set(hold["required_report_codes"])

        if not required <= set(members.keys()):
            continue  # still incomplete, left as-is for a later run

        files_by_type = {}
        group_failed = False

        for report_code, item in list(members.items()):
            if item.get("bronze_done"):
                continue

            dup = dedup.is_already_processed(item.get("content_hash"))
            if dup is not None:
                item["bronze_done"] = True
                item["rows"] = dup["rows_extracted"]
                item["skipped_duplicate"] = True
                continue

            dtype, vendor = DISPATCH[(hold["rta"], report_code)]
            started = datetime.now(timezone.utc)
            try:
                buffer = download_as_file(item["source_s3_uri"], item["filename"])
                df = read_file(buffer)
            except Exception as exc:
                outcome = client.report_outcome(
                    item["handoff_id"], "FAILED",
                    failure_reason="DOWNLOAD_FAILED", error_message=str(exc)[:2000],
                )
                logging_repo.log_event(
                    run_id=run_id, handoff_id=item["handoff_id"], group_key=key,
                    rta=hold["rta"], arn_code=hold["arn_code"], report_code=report_code,
                    filename=item.get("filename"), source_s3_uri=item.get("source_s3_uri"),
                    content_hash=item.get("content_hash"), layer="BRONZE", status="FAILED",
                    error_message=str(exc)[:2000], started_at=started,
                    ended_at=datetime.now(timezone.utc),
                    api_response=outcome.get("api_response"),
                )
                group_failed = True
                del members[report_code]
                continue

            files_by_type.setdefault(dtype, {}).setdefault(vendor, []).append(df)
            item["_rows"] = len(df)
            item["_started_at"] = started
            item["_dtype"] = dtype

        if group_failed:
            logging_repo.upsert_group_hold(
                key, hold["rta"], hold["arn_code"], hold["s3_date"],
                hold["required_report_codes"], members, "HOLDING",
            )
            continue

        bronze_counts = _load_bronze(files_by_type)
        ended = datetime.now(timezone.utc)

        for report_code, item in members.items():
            if item.get("skipped_duplicate"):
                logging_repo.log_event(
                    run_id=run_id, handoff_id=item["handoff_id"], group_key=key,
                    rta=hold["rta"], arn_code=hold["arn_code"], report_code=report_code,
                    filename=item.get("filename"), source_s3_uri=item.get("source_s3_uri"),
                    content_hash=item.get("content_hash"), layer="BRONZE",
                    status="SKIPPED_DUPLICATE", total_processed=item.get("rows"),
                )
                continue

            if "_rows" not in item:
                continue  # was already bronze_done from an earlier run

            item["bronze_done"] = True
            rows = item.pop("_rows")
            item["rows"] = rows
            dtype = item["dtype"] = item.pop("_dtype")
            logging_repo.log_event(
                run_id=run_id, handoff_id=item["handoff_id"], group_key=key,
                rta=hold["rta"], arn_code=hold["arn_code"], report_code=report_code,
                filename=item.get("filename"), source_s3_uri=item.get("source_s3_uri"),
                content_hash=item.get("content_hash"), layer="BRONZE", status="COMPLETED",
                total_records_in_report=rows, total_processed=bronze_counts.get(dtype, 0),
                started_at=item.pop("_started_at", None), ended_at=ended,
            )

        logging_repo.upsert_group_hold(
            key, hold["rta"], hold["arn_code"], hold["s3_date"],
            hold["required_report_codes"], members, "PROCESSING",
        )
        completed.append((key, hold["rta"], hold["arn_code"], members))

    return completed


def _load_bronze(files_by_type):
    counts = {}
    for dtype, by_vendor in files_by_type.items():
        cams_df = pd.concat(by_vendor["cams"], ignore_index=True) if by_vendor.get("cams") else None
        kfin_df = pd.concat(by_vendor["kfin"], ignore_index=True) if by_vendor.get("kfin") else None
        rows = (len(cams_df) if cams_df is not None else 0) + (len(kfin_df) if kfin_df is not None else 0)
        if dtype == "transaction":
            process_transactions(cams=cams_df, kfin=kfin_df)
        elif dtype == "investor":
            process_investor_master(cams=cams_df, kfin=kfin_df)
        elif dtype == "sip":
            process_sip(cams=cams_df, kfin=kfin_df)
        counts[dtype] = rows
    return counts


def _report_silver_outcomes(run_id, completed, silver_result):
    # load_silver() is a single table-wide bronze -> silver promotion (it
    # sweeps every flag=0 row, not just this run's), so its result is logged
    # once per completed group here rather than once per file -- the same
    # "GROUP" convention _report_gold_outcomes uses for the gold layer.
    # Each group has exactly one report_code (see hold_groups.group_key),
    # so it has exactly one dtype and therefore one silver table to report.
    for key, rta, arn_code, members in completed:
        real_members = {c: i for c, i in members.items() if not i.get("skipped_duplicate")}
        if not real_members:
            continue

        primary = next(iter(real_members.values()))
        report_code, dtype = next(iter(
            (c, i.get("dtype") or DISPATCH.get((rta, c), (None,))[0])
            for c, i in real_members.items()
        ))
        table = SILVER_TABLE_BY_DTYPE.get(dtype)
        result = silver_result.get(table) if table else None

        if result is None:
            status = "SKIPPED"
            rows_loaded = 0
            error_message = None
        else:
            status = {"ok": "COMPLETED", "skipped": "SKIPPED", "error": "FAILED"}.get(
                result["status"], result["status"].upper()
            )
            rows_loaded = result["rows_loaded"]
            error_message = result["error"]

        logging_repo.log_event(
            run_id=run_id, handoff_id=primary["handoff_id"], group_key=key,
            rta=rta, arn_code=arn_code, report_code="GROUP",
            filename=None, source_s3_uri=None, content_hash=None,
            layer="SILVER", status=status,
            total_processed=rows_loaded, error_message=error_message,
        )


def _report_gold_outcomes(client, run_id, completed, gold_result):
    gold_total_rows = sum(r["rows_loaded"] for r in gold_result.values())
    gold_errors = [f"{k}: {r['error']}" for k, r in gold_result.items() if r["status"] == "error"]
    gold_status = "FAILED" if gold_errors else "COMPLETED"
    gold_error_message = "; ".join(gold_errors)[:2000] if gold_errors else None

    for key, rta, arn_code, members in completed:
        real_members = {c: i for c, i in members.items() if not i.get("skipped_duplicate")}

        # Report each member's outcome first so their PATCH responses are on
        # hand for the group-level GOLD log row below (GOLD is logged
        # per-group, not per-file — see README's "Known simplification").
        outcomes = []
        for report_code, item in members.items():
            if item.get("skipped_duplicate"):
                continue
            if gold_status == "COMPLETED":
                outcome = client.report_outcome(item["handoff_id"], "COMPLETED", rows_extracted=item.get("rows", 0))
                dedup.mark_processed(item.get("content_hash"), item["handoff_id"], item.get("rows", 0))
            else:
                outcome = client.report_outcome(
                    item["handoff_id"], "FAILED",
                    failure_reason="TRANSFORM_FAILED", error_message=gold_error_message,
                )
            outcomes.append({
                "handoff_id": item["handoff_id"],
                "report_code": report_code,
                "ok": outcome.get("ok"),
                "reason": outcome.get("reason"),
                "api_response": outcome.get("api_response"),
            })

        if real_members:
            primary = next(iter(real_members.values()))
            logging_repo.log_event(
                run_id=run_id, handoff_id=primary["handoff_id"], group_key=key,
                rta=rta, arn_code=arn_code, report_code="GROUP",
                filename=None, source_s3_uri=None, content_hash=None,
                layer="GOLD", status=gold_status,
                total_processed=gold_total_rows, error_message=gold_error_message,
                api_response=outcomes,
            )

        logging_repo.upsert_group_hold(
            key, rta, arn_code, None, list(members.keys()), members,
            "COMPLETED" if gold_status == "COMPLETED" else "HOLDING",
        )


def _check_hold_timeouts():
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=config.ETL_HOLD_TIMEOUT_MINUTES)
    for hold in logging_repo.get_groups_with_status(["HOLDING"]):
        first_seen = hold["first_seen_at"]
        if first_seen.tzinfo is None:
            first_seen = first_seen.replace(tzinfo=timezone.utc)
        if first_seen < cutoff:
            logging_repo.upsert_group_hold(
                hold["group_key"], hold["rta"], hold["arn_code"], hold["s3_date"],
                hold["required_report_codes"], hold["members"], "TIMED_OUT",
            )


if __name__ == "__main__":
    run_once()

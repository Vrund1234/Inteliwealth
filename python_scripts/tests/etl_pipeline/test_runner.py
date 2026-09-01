"""run_once() against a fake API client and stubbed loaders. Nothing here
touches S3 or the network; the DB is only touched through logging_repo."""

import logging
import uuid
from datetime import date

import pandas as pd
import pytest
from sqlalchemy import text

from etl_pipeline import file_logger, runner
from etl_pipeline.logging_repo import new_run_id
from utils.db import engine


# ---- fakes ---------------------------------------------------------------

class FakeClient:
    def __init__(self, items=(), fail_report_for=()):
        self.items = list(items)
        self.fail_report_for = set(fail_report_for)
        self.reported = []
        self.peeked = False
        self.last_request = {"method": "POST", "path": "/etl-handoff/reservations",
                             "body": {"runner": "w1", "limit": 10}}
        self.last_response = {"data": {"items": []}}
        self.last_status = 200

    def reserve(self, limit=None):
        return self.items

    def peek_pending(self, limit=None):
        self.peeked = True
        return self.items

    def report_outcome(self, handoff_id, status, rows_extracted=None,
                       failure_reason=None, error_message=None):
        self.reported.append({
            "handoff_id": handoff_id, "status": status,
            "rows_extracted": rows_extracted, "failure_reason": failure_reason,
            "error_message": error_message,
        })
        ok = handoff_id not in self.fail_report_for
        return {"ok": ok, "reason": None if ok else "http_409", "fatal": False,
                "api_request": {}, "api_response": {}, "http_status": 200 if ok else 409}


def _item(report_code="WBR2", rta="CAMS", **overrides):
    item = {
        "handoff_id": str(uuid.uuid4()),
        "rta": rta,
        "report_code": report_code,
        "arn_code": "ARN-266051",
        "filename": f"{report_code}.csv",
        "payload_format": "csv",
        "content_hash": "sha256:" + uuid.uuid4().hex,
        "file_size": 1024,
        "source_s3_uri": f"s3://bucket/arn_ARN-266051/2026-08-25/msg_x/{report_code}.csv",
    }
    item.update(overrides)
    return item


ALL_GOLD = ("amc", "scheme", "scheme_nav", "transactions",
            "holdings", "sip", "clients", "folio_nominees")


def _gold_all_completed():
    return {
        entity: {"entity": entity, "status": "COMPLETED", "total": 3,
                 "inserted": 2, "updated": 1, "error": None}
        for entity in ALL_GOLD
    }


def _silver_all_completed():
    return {
        table: {"table": table, "status": "COMPLETED", "total": 3,
                "inserted": 2, "updated": 1, "error": None}
        for table in ("investor_master", "transaction_master_new", "sip_master_new")
    }


@pytest.fixture(autouse=True)
def isolate_logs(tmp_path, monkeypatch):
    """Keep test runs out of the real logs/ directory.

    run_once() falls back to file_logger.setup(run_id), which writes to
    config.ETL_LOG_DIR -- the production log file. Without this, every test
    run appended synthetic "inserted=2 updated=1" lines to the same file an
    operator reads to diagnose a real run.
    """
    monkeypatch.setattr(runner.config, "ETL_LOG_DIR", tmp_path / "logs")
    yield
    logger = logging.getLogger(file_logger.LOGGER_NAME)
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)


@pytest.fixture
def run_id():
    value = new_run_id()
    yield value
    with engine.begin() as conn:
        conn.execute(text(
            "DELETE FROM pipeline.etl_pipeline_log WHERE run_id = CAST(:r AS uuid)"
        ), {"r": value})


@pytest.fixture
def hashes():
    seen = []
    yield seen
    if seen:
        with engine.begin() as conn:
            conn.execute(text(
                "DELETE FROM pipeline.etl_processed_files WHERE content_hash = ANY(:h)"
            ), {"h": seen})


@pytest.fixture
def stub_layers(monkeypatch):
    """Replace S3, read_file and all five loaders with recording stubs."""
    calls = {"bronze": [], "silver": 0, "gold": 0, "mapping": 0}

    monkeypatch.setattr(runner.s3_client, "download_as_file",
                        lambda uri, filename: object())
    monkeypatch.setattr(runner, "read_file", lambda buffer: pd.DataFrame(
        [{"AMC_CODE": "X"}] * 3
    ))

    def bronze(dtype):
        def loader(**kwargs):
            calls["bronze"].append(dtype)
            return {"total": 3, "new": 2, "duplicate": 1}
        return loader

    monkeypatch.setattr(runner, "BRONZE_LOADER_BY_DTYPE", {
        "transaction": bronze("transaction"),
        "investor": bronze("investor"),
        "sip": bronze("sip"),
    })

    def silver():
        calls["silver"] += 1
        return _silver_all_completed()

    def gold():
        calls["gold"] += 1
        return _gold_all_completed()

    def mapping():
        calls["mapping"] += 1
        return {"approved_found": 1, "newly_mapped": 1, "already_mapped": 0,
                "still_unmatched": 0, "newly_queued": 0, "queued_tier1": 0,
                "queued_tier2": 0, "ambiguous": 0, "no_candidate": 0}

    monkeypatch.setattr(runner, "load_silver", silver)
    monkeypatch.setattr(runner, "load_gold", gold)
    monkeypatch.setattr(runner, "load_scheme_mapping", mapping)
    return calls


def _log_rows(run_id, layer=None):
    query = ("SELECT * FROM pipeline.etl_pipeline_log "
             "WHERE run_id = CAST(:r AS uuid)")
    params = {"r": run_id}
    if layer:
        query += " AND layer = :l"
        params["l"] = layer
    with engine.begin() as conn:
        return [dict(r._mapping) for r in conn.execute(text(query), params)]


# ---- empty queue ---------------------------------------------------------

def test_an_empty_queue_completes_without_running_any_layer(run_id, stub_layers):
    summary = runner.run_once(client=FakeClient([]), run_id=run_id)

    assert summary["reserved"] == 0
    assert stub_layers["silver"] == 0
    assert stub_layers["gold"] == 0
    assert stub_layers["mapping"] == 0


def test_an_empty_queue_still_writes_a_run_row(run_id, stub_layers):
    runner.run_once(client=FakeClient([]), run_id=run_id)

    layers = {row["layer"] for row in _log_rows(run_id)}
    assert "RUN" in layers
    assert "RESERVE" in layers


# ---- happy path ----------------------------------------------------------

def test_each_file_is_loaded_into_its_own_bronze_table(run_id, hashes, stub_layers):
    items = [_item("WBR2"), _item("WBR9"), _item("WBR49")]
    hashes.extend(i["content_hash"] for i in items)

    runner.run_once(client=FakeClient(items), run_id=run_id)

    assert stub_layers["bronze"] == ["transaction", "investor", "sip"]


def test_silver_and_gold_run_exactly_once_for_the_whole_batch(
    run_id, hashes, stub_layers
):
    items = [_item("WBR2"), _item("MFSD201", rta="KFIN")]
    hashes.extend(i["content_hash"] for i in items)

    runner.run_once(client=FakeClient(items), run_id=run_id)

    assert stub_layers["silver"] == 1
    assert stub_layers["gold"] == 1


def test_every_file_is_reported_completed(run_id, hashes, stub_layers):
    items = [_item("WBR2"), _item("WBR9")]
    hashes.extend(i["content_hash"] for i in items)
    client = FakeClient(items)

    runner.run_once(client=client, run_id=run_id)

    assert [r["status"] for r in client.reported] == ["COMPLETED", "COMPLETED"]
    assert all(r["rows_extracted"] == 3 for r in client.reported)


def test_a_bronze_row_is_logged_per_file_with_its_counts(run_id, hashes, stub_layers):
    items = [_item("WBR2")]
    hashes.extend(i["content_hash"] for i in items)

    runner.run_once(client=FakeClient(items), run_id=run_id)

    rows = _log_rows(run_id, "BRONZE")
    assert len(rows) == 1
    assert rows[0]["total_records"] == 3
    assert rows[0]["total_processed"] == 2
    assert rows[0]["total_duplicate"] == 1
    assert rows[0]["report_code"] == "WBR2"
    assert str(rows[0]["handoff_id"]) == items[0]["handoff_id"]


def test_the_report_date_comes_from_the_s3_uri(run_id, hashes, stub_layers):
    items = [_item("WBR2")]
    hashes.extend(i["content_hash"] for i in items)

    runner.run_once(client=FakeClient(items), run_id=run_id)

    assert _log_rows(run_id, "BRONZE")[0]["report_date"] == date(2026, 8, 25)


def test_silver_and_gold_rows_are_run_scoped_not_per_file(run_id, hashes, stub_layers):
    items = [_item("WBR2")]
    hashes.extend(i["content_hash"] for i in items)

    runner.run_once(client=FakeClient(items), run_id=run_id)

    for layer, expected in (("SILVER", 3), ("GOLD", 8)):
        rows = _log_rows(run_id, layer)
        assert len(rows) == expected
        assert all(row["handoff_id"] is None for row in rows)
        assert all(row["entity"] for row in rows)


def test_the_file_name_passed_to_s3_is_the_api_filename(
    run_id, hashes, stub_layers, monkeypatch
):
    # NEVER the S3 key's basename: raw_ingestion dispatches on it.
    seen = {}

    def capture(uri, filename):
        seen["filename"] = filename
        return object()

    monkeypatch.setattr(runner.s3_client, "download_as_file", capture)
    items = [_item("MFSD201", rta="KFIN", filename="MFSD201.dbf",
                   source_s3_uri="s3://b/2026-08-25/msg/W0I7582.dbf")]
    hashes.extend(i["content_hash"] for i in items)

    runner.run_once(client=FakeClient(items), run_id=run_id)

    assert seen["filename"] == "MFSD201.dbf"


# ---- scheme mapping ------------------------------------------------------

def test_scheme_mapping_runs_when_a_transaction_file_was_ingested(
    run_id, hashes, stub_layers
):
    items = [_item("WBR2")]
    hashes.extend(i["content_hash"] for i in items)

    runner.run_once(client=FakeClient(items), run_id=run_id)

    assert stub_layers["mapping"] == 1


def test_scheme_mapping_is_skipped_without_a_transaction_file(
    run_id, hashes, stub_layers
):
    items = [_item("WBR9")]
    hashes.extend(i["content_hash"] for i in items)

    runner.run_once(client=FakeClient(items), run_id=run_id)

    assert stub_layers["mapping"] == 0


def test_promote_approved_is_never_called(run_id, hashes, stub_layers, monkeypatch):
    # Auto-approving a fuzzy match silently corrupts scheme_id across gold.
    import promote_approved_mappings

    def forbidden(*args, **kwargs):
        raise AssertionError("promote_approved must never be called by the runner")

    monkeypatch.setattr(promote_approved_mappings, "promote_approved", forbidden)
    items = [_item("WBR2")]
    hashes.extend(i["content_hash"] for i in items)

    runner.run_once(client=FakeClient(items), run_id=run_id)


# ---- idempotency ---------------------------------------------------------

def test_an_already_processed_hash_is_skipped_and_reported_completed(
    run_id, hashes, stub_layers
):
    from etl_pipeline.logging_repo import mark_processed

    item = _item("WBR2")
    hashes.append(item["content_hash"])
    mark_processed(item["content_hash"], item["handoff_id"], rows_extracted=99)
    client = FakeClient([item])

    runner.run_once(client=client, run_id=run_id)

    assert stub_layers["bronze"] == []
    assert client.reported[0]["status"] == "COMPLETED"
    statuses = {row["status"] for row in _log_rows(run_id, "BRONZE")}
    assert statuses == {"SKIPPED_DUPLICATE"}


def test_a_processed_file_is_recorded_for_the_next_run(run_id, hashes, stub_layers):
    from etl_pipeline.logging_repo import is_already_processed

    item = _item("WBR2")
    hashes.append(item["content_hash"])

    runner.run_once(client=FakeClient([item]), run_id=run_id)

    assert is_already_processed(item["content_hash"]) is True


# ---- unsupported codes ---------------------------------------------------

def test_an_unknown_report_code_is_reported_unsupported_format(
    run_id, hashes, stub_layers
):
    # A code raw_ingestion.py has no rule for would be silently dropped as
    # "Unknown file type"; the runner makes it loud and terminal instead.
    # (MFSD307 used to be this example and is now supported.)
    item = _item("MFSD999", rta="KFIN")
    client = FakeClient([item])

    runner.run_once(client=client, run_id=run_id)

    assert stub_layers["bronze"] == []
    assert client.reported[0]["status"] == "FAILED"
    assert client.reported[0]["failure_reason"] == "UNSUPPORTED_FORMAT"
    assert {row["status"] for row in _log_rows(run_id, "BRONZE")} == {"SKIPPED"}


# ---- failure paths -------------------------------------------------------

def test_a_download_failure_is_reported_download_failed(
    run_id, hashes, stub_layers, monkeypatch
):
    def boom(uri, filename):
        raise RuntimeError("NoSuchKey")

    monkeypatch.setattr(runner.s3_client, "download_as_file", boom)
    item = _item("WBR2")
    client = FakeClient([item])

    runner.run_once(client=client, run_id=run_id)

    assert client.reported[0]["failure_reason"] == "DOWNLOAD_FAILED"
    assert "NoSuchKey" in client.reported[0]["error_message"]


def test_an_unsupported_format_from_read_file_is_reported_as_such(
    run_id, hashes, stub_layers, monkeypatch
):
    def boom(buffer):
        raise ValueError("Unsupported file format: WBR2.zzz")

    monkeypatch.setattr(runner, "read_file", boom)
    client = FakeClient([_item("WBR2")])

    runner.run_once(client=client, run_id=run_id)

    assert client.reported[0]["failure_reason"] == "UNSUPPORTED_FORMAT"


def test_a_parse_error_from_read_file_is_conversion_failed(
    run_id, hashes, stub_layers, monkeypatch
):
    def boom(buffer):
        raise ValueError("Unable to decode uploaded file.")

    monkeypatch.setattr(runner, "read_file", boom)
    client = FakeClient([_item("WBR2")])

    runner.run_once(client=client, run_id=run_id)

    assert client.reported[0]["failure_reason"] == "CONVERSION_FAILED"


def test_a_bronze_loader_raising_is_extract_failed(
    run_id, hashes, stub_layers, monkeypatch
):
    def boom(**kwargs):
        raise RuntimeError("bronze exploded")

    monkeypatch.setitem(runner.BRONZE_LOADER_BY_DTYPE, "transaction", boom)
    client = FakeClient([_item("WBR2")])

    runner.run_once(client=client, run_id=run_id)

    assert client.reported[0]["failure_reason"] == "EXTRACT_FAILED"


def test_a_failing_file_does_not_stop_the_rest_of_the_batch(
    run_id, hashes, stub_layers, monkeypatch
):
    good = _item("WBR9")
    bad = _item("WBR2")
    hashes.append(good["content_hash"])
    calls = []

    def selective(buffer):
        calls.append(1)
        if len(calls) == 1:
            raise ValueError("Unable to decode uploaded file.")
        return pd.DataFrame([{"AMC_CODE": "X"}] * 3)

    monkeypatch.setattr(runner, "read_file", selective)
    client = FakeClient([bad, good])

    runner.run_once(client=client, run_id=run_id)

    assert len(client.reported) == 2
    assert client.reported[0]["status"] == "FAILED"
    assert client.reported[1]["status"] == "COMPLETED"


# ---- downstream failure fan-out -----------------------------------------

def test_a_failed_gold_entity_fails_only_the_files_it_depends_on(
    run_id, hashes, stub_layers, monkeypatch
):
    # gold.folio_nominees reads investor only, so a WBR9 file fails and a
    # WBR2 file does not.
    def gold():
        results = _gold_all_completed()
        results["folio_nominees"]["status"] = "FAILED"
        results["folio_nominees"]["error"] = "nominees exploded"
        return results

    monkeypatch.setattr(runner, "load_gold", gold)
    txn = _item("WBR2")
    inv = _item("WBR9")
    hashes.extend([txn["content_hash"], inv["content_hash"]])
    client = FakeClient([txn, inv])

    runner.run_once(client=client, run_id=run_id)

    by_id = {r["handoff_id"]: r for r in client.reported}
    assert by_id[txn["handoff_id"]]["status"] == "COMPLETED"
    assert by_id[inv["handoff_id"]]["status"] == "FAILED"
    assert by_id[inv["handoff_id"]]["failure_reason"] == "TRANSFORM_FAILED"


def test_a_failed_clients_entity_fails_every_file(
    run_id, hashes, stub_layers, monkeypatch
):
    def gold():
        results = _gold_all_completed()
        results["clients"]["status"] = "FAILED"
        return results

    monkeypatch.setattr(runner, "load_gold", gold)
    items = [_item("WBR2"), _item("WBR9"), _item("WBR49")]
    hashes.extend(i["content_hash"] for i in items)
    client = FakeClient(items)

    runner.run_once(client=client, run_id=run_id)

    assert {r["status"] for r in client.reported} == {"FAILED"}


def test_a_failed_silver_table_fails_only_its_own_dtype(
    run_id, hashes, stub_layers, monkeypatch
):
    def silver():
        results = _silver_all_completed()
        results["sip_master_new"]["status"] = "FAILED"
        return results

    monkeypatch.setattr(runner, "load_silver", silver)
    sip = _item("WBR49")
    inv = _item("WBR9")
    hashes.append(inv["content_hash"])
    client = FakeClient([sip, inv])

    runner.run_once(client=client, run_id=run_id)

    by_id = {r["handoff_id"]: r for r in client.reported}
    assert by_id[sip["handoff_id"]]["status"] == "FAILED"
    assert by_id[inv["handoff_id"]]["status"] == "COMPLETED"


def test_a_failed_file_is_not_recorded_as_processed(
    run_id, hashes, stub_layers, monkeypatch
):
    # Otherwise the next attempt would be skipped as a duplicate and the file
    # could never succeed.
    from etl_pipeline.logging_repo import is_already_processed

    def gold():
        results = _gold_all_completed()
        results["clients"]["status"] = "FAILED"
        return results

    monkeypatch.setattr(runner, "load_gold", gold)
    item = _item("WBR2")
    hashes.append(item["content_hash"])

    runner.run_once(client=FakeClient([item]), run_id=run_id)

    assert is_already_processed(item["content_hash"]) is False


# ---- reporting -----------------------------------------------------------

def test_a_report_row_is_logged_per_file(run_id, hashes, stub_layers):
    items = [_item("WBR2"), _item("WBR9")]
    hashes.extend(i["content_hash"] for i in items)

    runner.run_once(client=FakeClient(items), run_id=run_id)

    rows = _log_rows(run_id, "REPORT")
    assert len(rows) == 2
    assert all(row["handoff_id"] is not None for row in rows)


def test_a_409_on_the_report_is_logged_and_does_not_abort(
    run_id, hashes, stub_layers
):
    items = [_item("WBR2"), _item("WBR9")]
    hashes.extend(i["content_hash"] for i in items)
    client = FakeClient(items, fail_report_for=[items[0]["handoff_id"]])

    summary = runner.run_once(client=client, run_id=run_id)

    assert len(client.reported) == 2
    assert summary["report_failures"] == 1


# ---- dry run -------------------------------------------------------------

def test_dry_run_peeks_and_reserves_nothing(run_id, stub_layers):
    client = FakeClient([_item("WBR2")])

    summary = runner.run_once(client=client, run_id=run_id, dry_run=True)

    assert client.peeked is True
    assert client.reported == []
    assert stub_layers["bronze"] == []
    assert stub_layers["silver"] == 0
    assert summary["dry_run"] is True


# ---- main / locking ------------------------------------------------------

def test_main_exits_zero_when_another_run_holds_the_lock(monkeypatch, tmp_path):
    # Not an error condition -- cron must not treat it as one.
    monkeypatch.setattr(runner.pipeline_lock, "try_acquire", lambda: None)
    monkeypatch.setattr(runner.config, "ETL_LOG_DIR", tmp_path / "logs")

    assert runner.main([]) == 0


def test_main_releases_the_lock_even_when_the_run_raises(monkeypatch, tmp_path):
    released = []
    monkeypatch.setattr(runner.pipeline_lock, "try_acquire", lambda: "conn")
    monkeypatch.setattr(runner.pipeline_lock, "release", released.append)
    monkeypatch.setattr(runner.config, "ETL_LOG_DIR", tmp_path / "logs")

    def boom(**kwargs):
        raise RuntimeError("run exploded")

    monkeypatch.setattr(runner, "run_once", boom)

    assert runner.main([]) == 1
    assert released == ["conn"]

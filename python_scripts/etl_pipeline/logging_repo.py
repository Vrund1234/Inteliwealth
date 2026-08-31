"""Reads and writes the pipeline schema created by
sql_scripts/etl_pipeline_schema_2026-08-31.sql."""

import json
import uuid

from sqlalchemy import text

from utils.db import engine

# Column-name substrings that must never be stored in plain text. Matched
# case-insensitively against every dict key in an api_request/api_response
# payload, however deeply nested.
_SECRET_KEYS = ("password", "token", "authorization", "secret", "credential")

# The documented maximum for error_message; applied to `comment` too so a
# stack trace cannot bloat the table.
MAX_COMMENT = 2000

# Exactly the nullable columns of pipeline.etl_pipeline_log. An unknown key is
# a caller bug and raises rather than being silently dropped.
_OPTIONAL_FIELDS = (
    "handoff_id", "entity", "rta", "arn_code", "report_code", "filename",
    "report_date", "source_s3_uri", "content_hash", "payload_format",
    "file_size", "total_records", "total_processed", "total_duplicate",
    "comment", "api_request", "api_response", "http_status",
    "started_at", "ended_at", "duration_ms",
)

_INSERT = text("""
    INSERT INTO pipeline.etl_pipeline_log (
        log_id, run_id, handoff_id, layer, entity, status,
        rta, arn_code, report_code, filename, report_date,
        source_s3_uri, content_hash, payload_format, file_size,
        total_records, total_processed, total_duplicate, comment,
        api_request, api_response, http_status,
        started_at, ended_at, duration_ms
    ) VALUES (
        CAST(:log_id AS uuid), CAST(:run_id AS uuid),
        CAST(:handoff_id AS uuid), :layer, :entity, :status,
        :rta, :arn_code, :report_code, :filename, :report_date,
        :source_s3_uri, :content_hash, :payload_format, :file_size,
        :total_records, :total_processed, :total_duplicate, :comment,
        CAST(:api_request AS jsonb), CAST(:api_response AS jsonb), :http_status,
        :started_at, :ended_at, :duration_ms
    )
""")


def new_run_id():
    """One cron invocation's id. Every row this run writes carries it."""
    return str(uuid.uuid4())


def redact(value):
    """Replace every secret-looking value with "***", recursively.

    A second, independent pass on top of api_client's own masking: the bearer
    token and the runner password must never reach this table or the log
    files, and a future caller that hands log_event() a raw body must not be
    able to change that.
    """
    if isinstance(value, dict):
        return {
            key: ("***" if any(s in str(key).lower() for s in _SECRET_KEYS)
                  else redact(item))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    return value


def _json_or_none(value):
    if value is None:
        return None
    # default=str: a datetime or UUID inside an API payload must not raise.
    # Losing the log row would lose the only record of what happened.
    return json.dumps(redact(value), default=str)


def log_event(run_id, layer, status, **fields):
    """Write one row and return its log_id.

    `layer` is RUN|RESERVE|BRONZE|SCHEME_MAPPING|SILVER|GOLD|REPORT and
    `status` is STARTED|COMPLETED|FAILED|SKIPPED|SKIPPED_DUPLICATE.

    Per-file rows (RESERVE, BRONZE, REPORT) carry a handoff_id. Run-scoped
    rows (RUN, SCHEME_MAPPING, SILVER, GOLD) leave it NULL and are keyed by
    run_id + entity, because silver and gold are whole-layer rebuilds and
    attributing their counts to one file would be a fiction.
    """
    unknown = set(fields) - set(_OPTIONAL_FIELDS)
    if unknown:
        raise TypeError(f"log_event: unknown field(s) {sorted(unknown)}")

    params = {name: fields.get(name) for name in _OPTIONAL_FIELDS}
    log_id = str(uuid.uuid4())
    params.update({"log_id": log_id, "run_id": run_id, "layer": layer, "status": status})

    if params["comment"] is not None:
        params["comment"] = str(params["comment"])[:MAX_COMMENT]

    if (params["duration_ms"] is None
            and params["started_at"] is not None
            and params["ended_at"] is not None):
        delta = params["ended_at"] - params["started_at"]
        params["duration_ms"] = int(delta.total_seconds() * 1000)

    params["api_request"] = _json_or_none(params["api_request"])
    params["api_response"] = _json_or_none(params["api_response"])

    with engine.begin() as conn:
        conn.execute(_INSERT, params)
    return log_id


def is_already_processed(content_hash):
    """True when this exact file has been loaded by an earlier run.

    content_hash is the API's SHA-256 of the file bytes, stable across RTA
    re-sends. It is nullable on the item, and a file without one must be
    processed rather than skipped.
    """
    if not content_hash:
        return False
    with engine.begin() as conn:
        found = conn.execute(text(
            "SELECT 1 FROM pipeline.etl_processed_files WHERE content_hash = :h"
        ), {"h": content_hash}).scalar()
    return found is not None


def mark_processed(content_hash, handoff_id, rows_extracted=None):
    """Record that this file's bytes have been loaded.

    ON CONFLICT DO NOTHING rather than DO UPDATE: the first run to load these
    bytes is the one that actually did the work, and processed_at should stay
    pointing at it.
    """
    if not content_hash:
        return
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO pipeline.etl_processed_files
                (content_hash, handoff_id, rows_extracted)
            VALUES (:h, CAST(:handoff_id AS uuid), :rows)
            ON CONFLICT (content_hash) DO NOTHING
        """), {"h": content_hash, "handoff_id": handoff_id, "rows": rows_extracted})

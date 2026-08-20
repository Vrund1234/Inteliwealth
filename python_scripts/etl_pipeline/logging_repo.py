import json
import uuid

from sqlalchemy import text

from utils.db import engine


def new_run_id():
    return str(uuid.uuid4())


def log_event(run_id, handoff_id, group_key, rta, arn_code, report_code, filename,
              source_s3_uri, content_hash, layer, status,
              total_records_in_report=None, total_processed=None, error_message=None,
              started_at=None, ended_at=None, api_response=None):
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO pipeline.etl_pipeline_log (
                    log_id, run_id, handoff_id, group_key, rta, arn_code, report_code,
                    filename, source_s3_uri, content_hash, layer, status,
                    total_records_in_report, total_processed, error_message,
                    started_at, ended_at, api_response
                ) VALUES (
                    :log_id, :run_id, :handoff_id, :group_key, :rta, :arn_code, :report_code,
                    :filename, :source_s3_uri, :content_hash, :layer, :status,
                    :total_records_in_report, :total_processed, :error_message,
                    :started_at, :ended_at, CAST(:api_response AS jsonb)
                )
                """
            ),
            {
                "log_id": str(uuid.uuid4()),
                "run_id": run_id,
                "handoff_id": handoff_id,
                "group_key": group_key,
                "rta": rta,
                "arn_code": arn_code,
                "report_code": report_code,
                "filename": filename,
                "source_s3_uri": source_s3_uri,
                "content_hash": content_hash,
                "layer": layer,
                "status": status,
                "total_records_in_report": total_records_in_report,
                "total_processed": total_processed,
                "error_message": error_message,
                "started_at": started_at,
                "ended_at": ended_at,
                "api_response": json.dumps(api_response, default=str) if api_response is not None else None,
            },
        )


def upsert_group_hold(group_key, rta, arn_code, s3_date, required_report_codes, members, status):
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO pipeline.etl_report_group_hold (
                    group_key, rta, arn_code, s3_date, required_report_codes, members, status
                ) VALUES (
                    :group_key, :rta, :arn_code, :s3_date,
                    CAST(:required AS jsonb), CAST(:members AS jsonb), :status
                )
                ON CONFLICT (group_key) DO UPDATE
                SET rta = EXCLUDED.rta,
                    arn_code = EXCLUDED.arn_code,
                    s3_date = COALESCE(EXCLUDED.s3_date, pipeline.etl_report_group_hold.s3_date),
                    required_report_codes = EXCLUDED.required_report_codes,
                    members = EXCLUDED.members,
                    status = EXCLUDED.status,
                    last_updated_at = now(),
                    completed_at = CASE WHEN EXCLUDED.status = 'COMPLETED' THEN now()
                                        ELSE pipeline.etl_report_group_hold.completed_at END
                """
            ),
            {
                "group_key": group_key,
                "rta": rta,
                "arn_code": arn_code,
                "s3_date": s3_date,
                "required": json.dumps(sorted(required_report_codes)),
                "members": json.dumps(members, default=str),
                "status": status,
            },
        )


def get_groups_with_status(statuses):
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT group_key, rta, arn_code, s3_date, required_report_codes,
                       members, status, first_seen_at
                FROM pipeline.etl_report_group_hold
                WHERE status = ANY(:statuses)
                """
            ),
            {"statuses": list(statuses)},
        ).mappings().all()
    return [dict(row) for row in rows]

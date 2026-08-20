from sqlalchemy import text

from utils.db import engine


def is_already_processed(content_hash):
    if not content_hash:
        return None
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT handoff_id, rows_extracted FROM pipeline.etl_processed_files "
                "WHERE content_hash = :h"
            ),
            {"h": content_hash},
        ).fetchone()
    if row is None:
        return None
    return {"handoff_id": str(row[0]), "rows_extracted": row[1]}


def mark_processed(content_hash, handoff_id, rows_extracted):
    if not content_hash:
        return
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO pipeline.etl_processed_files (content_hash, handoff_id, rows_extracted)
                VALUES (:h, :hid, :rows)
                ON CONFLICT (content_hash) DO UPDATE
                SET handoff_id = EXCLUDED.handoff_id,
                    rows_extracted = EXCLUDED.rows_extracted,
                    processed_at = now()
                """
            ),
            {"h": content_hash, "hid": handoff_id, "rows": rows_extracted},
        )

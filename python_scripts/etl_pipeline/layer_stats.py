"""Normalize each layer's counts into the three columns
pipeline.etl_pipeline_log actually stores.

  total_records    -- "total data"
  total_processed  -- "total processed data"
  total_duplicate  -- "total duplicate data"

What "duplicate" means differs by layer and is defined here, once:

  Bronze  rows whose row_hash matched something already in bronze (flag = 1).
  Silver  rows that took the ON CONFLICT DO UPDATE path -- already present
          under the table's natural key.
  Gold    the same, per gold entity.
"""

from . import dispatch

_EMPTY = {"total_records": None, "total_processed": None, "total_duplicate": None}


def _triple(total, processed, duplicate):
    return {
        "total_records": total,
        "total_processed": processed,
        "total_duplicate": duplicate,
    }


def bronze_counts(result):
    """From process_transactions/_investor_master/_sip's {total, new, duplicate}."""
    if not result:
        return dict(_EMPTY)
    return _triple(result.get("total"), result.get("new"), result.get("duplicate"))


def silver_counts(result):
    """From append_new_rows' {total, inserted, updated}.

    `total` is the rows handed to the upsert -- bronze rows with flag = 0 that
    survived the transform.
    """
    if not result:
        return dict(_EMPTY)
    return _triple(result.get("total"), result.get("inserted"), result.get("updated"))


def gold_counts(result):
    """From load_gold()'s per-entity {total, inserted, updated}.

    `total` is len(<entity>_gold_df), i.e. the rows transform_* produced.
    """
    if not result:
        return dict(_EMPTY)
    return _triple(result.get("total"), result.get("inserted"), result.get("updated"))


def scheme_mapping_counts(summary):
    """From load_scheme_mapping()'s nine-key summary.

    Mapped onto the same three columns so the SCHEME_MAPPING row is queryable
    alongside every other layer: approvals found is the total, approvals
    actually applied is the processed count, and approvals skipped because the
    scheme was already mapped is the duplicate count. The queue-side keys
    (newly_queued, tiers, ambiguous, no_candidate) go into `comment`, which the
    runner builds -- they are not counts of rows processed.
    """
    if not summary:
        return dict(_EMPTY)
    return _triple(
        summary.get("approved_found"),
        summary.get("newly_mapped"),
        summary.get("already_mapped"),
    )


def file_outcome(dtype, bronze_ok, failed_dtypes):
    """(status, failure_reason) for one reserved file.

    A file is COMPLETED when its own bronze load succeeded AND no silver table
    or gold entity that consumes its dtype reported an error. A bronze failure
    outranks a downstream one: it is the more specific and more actionable
    reason, and a file that never reached bronze cannot meaningfully be blamed
    on the transform.
    """
    if not bronze_ok:
        return ("FAILED", dispatch.EXTRACT_FAILED)
    if dtype is not None and dtype in failed_dtypes:
        return ("FAILED", dispatch.TRANSFORM_FAILED)
    return ("COMPLETED", None)

import sys
from datetime import date, datetime
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PKG_ROOT))

from etl_pipeline import hold_groups  # noqa: E402


def test_s3_date_from_uri_parses_partition_date():
    uri = "s3://bucket/mailback/org_abc/arn_ARN-266051/2026-08-19/msg_123/processed/WBR2.csv"
    assert hold_groups.s3_date_from_uri(uri) == date(2026, 8, 19)


def test_s3_date_from_uri_raises_on_missing_date():
    try:
        hold_groups.s3_date_from_uri("s3://bucket/no/date/here.csv")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_required_report_codes_is_self_requirement_for_known_code():
    # Each report code requires only itself now — no more "hold for siblings".
    assert hold_groups.required_report_codes("CAMS", "WBR2") == {"WBR2"}
    assert hold_groups.required_report_codes("CAMS", "WBR9") == {"WBR9"}
    assert hold_groups.required_report_codes("CAMS", "WBR49") == {"WBR49"}
    assert hold_groups.required_report_codes("KFIN", "MFSD201") == {"MFSD201"}
    assert hold_groups.required_report_codes("KFIN", "MFSD211") == {"MFSD211"}
    assert hold_groups.required_report_codes("KFIN", "MFSD243") == {"MFSD243"}


def test_required_report_codes_unrecognized_pair_returns_empty():
    assert hold_groups.required_report_codes("CAMS", "NOT_A_REAL_CODE") == set()
    assert hold_groups.required_report_codes("UNKNOWN", "WBR2") == set()


def _pending_item(id_, rta, report_code, arn_code, created_at):
    return {"id": id_, "rta": rta, "report_code": report_code,
            "arn_code": arn_code, "created_at": created_at}


def test_group_pending_items_each_report_code_is_its_own_ready_group():
    # Previously WBR2+WBR9+WBR49 for the same arn+date formed ONE group that
    # needed all three present. Now each forms its OWN group, ready alone.
    now = datetime(2026, 8, 19, 10, 0, 0)
    items = [
        _pending_item("h1", "CAMS", "WBR2", "ARN-1", now),
        _pending_item("h2", "CAMS", "WBR9", "ARN-1", now),
        # WBR49 deliberately absent — must NOT block h1/h2
    ]
    groups = hold_groups.group_pending_items(items)
    assert len(groups) == 2  # one group per report_code, not one combined group
    for group in groups.values():
        assert group["missing"] == set()
    ready_ids = hold_groups.ready_handoff_ids(groups)
    assert set(ready_ids) == {"h1", "h2"}


def test_group_pending_items_unrecognized_report_code_never_ready():
    now = datetime(2026, 8, 19, 10, 0, 0)
    items = [_pending_item("h1", "CAMS", "NOT_A_REAL_CODE", "ARN-1", now)]
    groups = hold_groups.group_pending_items(items)
    group = next(iter(groups.values()))
    assert group["missing"] == set()  # required is empty, so "missing" is empty too...
    assert hold_groups.ready_handoff_ids(groups) == []  # ...but never ready, since required itself is empty


def _reserved_item(handoff_id, rta, report_code, arn_code, s3_date_str, filename):
    return {
        "handoff_id": handoff_id, "rta": rta, "report_code": report_code,
        "arn_code": arn_code, "filename": filename, "payload_format": "csv",
        "content_hash": f"hash-{handoff_id}", "file_size": 100,
        "source_s3_uri": f"s3://bucket/mailback/org_x/arn_{arn_code}/{s3_date_str}/msg_1/processed/{filename}",
    }


def test_regroup_by_authoritative_key_single_file_is_immediately_complete():
    items = [
        _reserved_item("h1", "CAMS", "WBR2", "ARN-1", "2026-08-19", "WBR2.csv"),
    ]
    groups = hold_groups.regroup_by_authoritative_key(items)
    assert len(groups) == 1
    group = next(iter(groups.values()))
    assert hold_groups.is_group_complete(group)
    assert group["s3_date"] == date(2026, 8, 19)


def test_regroup_by_authoritative_key_splits_different_report_codes_even_on_same_date():
    items = [
        _reserved_item("h1", "CAMS", "WBR2", "ARN-1", "2026-08-19", "WBR2.csv"),
        _reserved_item("h2", "CAMS", "WBR9", "ARN-1", "2026-08-19", "WBR9.csv"),
    ]
    groups = hold_groups.regroup_by_authoritative_key(items)
    assert len(groups) == 2  # WBR2 and WBR9 no longer merge into one group
    assert all(hold_groups.is_group_complete(g) for g in groups.values())  # each complete on its own


def test_regroup_by_authoritative_key_splits_on_real_date_mismatch():
    items = [
        _reserved_item("h1", "CAMS", "WBR2", "ARN-1", "2026-08-19", "WBR2.csv"),
        _reserved_item("h2", "CAMS", "WBR2", "ARN-1", "2026-08-20", "WBR2.csv"),
    ]
    groups = hold_groups.regroup_by_authoritative_key(items)
    assert len(groups) == 2
    assert all(hold_groups.is_group_complete(g) for g in groups.values())  # each is still self-complete

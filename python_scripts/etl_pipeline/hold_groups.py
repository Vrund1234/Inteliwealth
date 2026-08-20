import re
from datetime import date, datetime

# Every report code CAMS/KFIN are known to send. This is NOT a "must arrive
# together" set any more — each report code processes independently the
# moment it arrives (see the 2026-08-20 file-decoupling plan). Kept only to
# recognize which (rta, report_code) pairs are real, so a typo'd/unknown
# report_code never becomes falsely "ready".
KNOWN_REPORT_CODES = {
    "CAMS": {"WBR2", "WBR9", "WBR49"},
    "KFIN": {"MFSD201", "MFSD211", "MFSD243"},
}

_S3_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def s3_date_from_uri(source_s3_uri):
    match = _S3_DATE_RE.search(source_s3_uri)
    if not match:
        raise ValueError(f"No YYYY-MM-DD partition date found in {source_s3_uri!r}")
    return datetime.strptime(match.group(1), "%Y-%m-%d").date()


def _as_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value)).date()


def coarse_group_key(rta, report_code, arn_code, created_at):
    day = _as_date(created_at)
    return f"{rta}|{report_code}|{arn_code or ''}|{day.isoformat()}"


def group_key(rta, report_code, arn_code, s3_date):
    day = s3_date if isinstance(s3_date, date) else s3_date_from_uri(s3_date)
    return f"{rta}|{report_code}|{arn_code or ''}|{day.isoformat()}"


def required_report_codes(rta, report_code):
    """A group's requirement is just its own report_code — each file type
    processes independently, no longer held for its siblings. Returns
    {report_code} if it's a recognized code for that RTA, else an empty set
    (an unrecognized code never becomes "ready")."""
    known = KNOWN_REPORT_CODES.get((rta or "").upper(), set())
    return {report_code} if report_code in known else set()


def group_pending_items(pending_items):
    """
    pending_items: list of dicts shaped like GET /pending's EtlHandoffRead —
    must have 'id', 'rta', 'report_code', 'arn_code', 'created_at'.

    Returns {coarse_group_key: {rta, arn_code, required, present, missing, items}}.

    Each group is scoped to a single (rta, report_code, arn_code, date) —
    WBR2/WBR9/WBR49 for the same distributor+date land in three separate
    groups, each complete the moment its own single file is present.
    """
    groups = {}
    for item in pending_items:
        rta = item["rta"]
        report_code = item["report_code"]
        arn_code = item.get("arn_code")
        key = coarse_group_key(rta, report_code, arn_code, item["created_at"])
        group = groups.setdefault(key, {
            "rta": rta,
            "arn_code": arn_code,
            "required": required_report_codes(rta, report_code),
            "present": set(),
            "items": [],
        })
        group["present"].add(item["report_code"])
        group["items"].append(item)

    for group in groups.values():
        group["missing"] = group["required"] - group["present"]

    return groups


def ready_handoff_ids(groups):
    """handoff ids (peek item['id']) belonging to coarse groups with nothing missing."""
    ids = []
    for group in groups.values():
        if group["required"] and not group["missing"]:
            ids.extend(item["id"] for item in group["items"])
    return ids


def regroup_by_authoritative_key(reserved_items):
    """
    reserved_items: list of dicts shaped like POST /reservations' EtlHandoffItem —
    must have 'handoff_id', 'rta', 'report_code', 'arn_code', 'source_s3_uri',
    plus whatever else the caller wants carried through (filename, content_hash, ...).

    Returns {group_key: {rta, arn_code, s3_date, required, members: {report_code: item}}}.
    Each group is scoped to a single report_code (see group_pending_items).
    """
    groups = {}
    for item in reserved_items:
        s3_date = s3_date_from_uri(item["source_s3_uri"])
        key = group_key(item["rta"], item["report_code"], item.get("arn_code"), s3_date)
        group = groups.setdefault(key, {
            "rta": item["rta"],
            "arn_code": item.get("arn_code"),
            "s3_date": s3_date,
            "required": required_report_codes(item["rta"], item["report_code"]),
            "members": {},
        })
        group["members"][item["report_code"]] = item
    return groups


def is_group_complete(group):
    return bool(group["required"]) and group["required"] <= set(group["members"].keys())

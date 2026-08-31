"""Fetch a reserved file's bytes from S3 as something raw_ingestion.read_file()
already knows how to open."""

import re
from datetime import date
from io import BytesIO

import boto3

from . import config

_S3_URI_RE = re.compile(r"^s3://([^/]+)/(.+)$")

# The RTA feed's own date, taken from the partition segment of the key:
#   .../arn_ARN-266051/2026-08-25/msg_.../processed/W0I7582.dbf
# First match wins. This is NOT the run's date -- created_at covers that.
_DATE_SEGMENT_RE = re.compile(r"/(\d{4})-(\d{2})-(\d{2})(?:/|$)")


def parse_s3_uri(uri):
    match = _S3_URI_RE.match(uri or "")
    if not match:
        raise ValueError(f"Not a valid s3:// URI: {uri!r}")
    return match.group(1), match.group(2)


def report_date_from_uri(uri):
    """The report's date, or None when the URI carries no date segment.

    Never raises: an unparseable or impossible date is logged as NULL rather
    than failing a file whose bytes are perfectly good.
    """
    match = _DATE_SEGMENT_RE.search(uri or "")
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def _client():
    kwargs = {
        "aws_access_key_id": config.AWS_ACCESS_KEY_ID,
        "aws_secret_access_key": config.AWS_SECRET_ACCESS_KEY,
        "region_name": config.AWS_REGION,
    }
    # Only passed when a real MinIO/localstack endpoint is configured; boto3
    # must never receive endpoint_url="".
    if config.AWS_S3_ENDPOINT_URL:
        kwargs["endpoint_url"] = config.AWS_S3_ENDPOINT_URL
    return boto3.client("s3", **kwargs)


def download_as_file(source_s3_uri, filename):
    """The object's bytes as a BytesIO whose .name is `filename`.

    raw_ingestion.read_file() dispatches on file.name.lower().endswith(...) and
    calls .seek(0) / .read() -- the contract a Streamlit UploadedFile
    satisfies. A named BytesIO satisfies the same contract, so S3 bytes reach
    the existing reader with no change to raw_ingestion.py and no temp file of
    our own. (read_file() writes its own temp file for the DBF branch; that is
    unchanged.)

    `filename` MUST be the API's `filename` field, never the S3 key's
    basename. The API normalises filename to "<REPORT_CODE>.<ext>", which is
    what raw_ingestion's suffix rules expect; the MFSD201 item's key is
    "W0I7582.dbf", which matches no rule and would be silently dropped as
    "Unknown file type" at raw_ingestion.py:736.
    """
    bucket, key = parse_s3_uri(source_s3_uri)
    payload = _client().get_object(Bucket=bucket, Key=key)["Body"].read()
    buffer = BytesIO(payload)
    buffer.name = filename
    buffer.seek(0)
    return buffer

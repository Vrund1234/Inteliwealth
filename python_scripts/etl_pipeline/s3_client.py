import re
from io import BytesIO

import boto3

from . import config

_S3_URI_RE = re.compile(r"^s3://([^/]+)/(.+)$")


def parse_s3_uri(uri):
    match = _S3_URI_RE.match(uri)
    if not match:
        raise ValueError(f"Not a valid s3:// URI: {uri!r}")
    return match.group(1), match.group(2)


def _client():
    kwargs = {
        "aws_access_key_id": config.AWS_ACCESS_KEY_ID,
        "aws_secret_access_key": config.AWS_SECRET_ACCESS_KEY,
        "region_name": config.AWS_REGION,
    }
    if config.AWS_S3_ENDPOINT_URL:
        kwargs["endpoint_url"] = config.AWS_S3_ENDPOINT_URL
    return boto3.client("s3", **kwargs)


def download_as_file(source_s3_uri, filename):
    bucket, key = parse_s3_uri(source_s3_uri)
    body = _client().get_object(Bucket=bucket, Key=key)["Body"].read()
    buffer = BytesIO(body)
    buffer.name = filename
    buffer.seek(0)
    return buffer

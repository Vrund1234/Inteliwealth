"""s3_client against a mocked boto3. AWS credentials for the bucket have not
been supplied, so nothing here may touch the network."""

from datetime import date

import pytest

from etl_pipeline import s3_client
from etl_pipeline.s3_client import download_as_file, parse_s3_uri, report_date_from_uri

URI = (
    "s3://iw-rta-reports/arn_ARN-266051/2026-08-25/"
    "msg_0f3a/processed/W0I7582.dbf"
)


class FakeBody:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return self._payload


class FakeS3:
    def __init__(self, payload=b"file-bytes"):
        self.payload = payload
        self.calls = []

    def get_object(self, Bucket, Key):
        self.calls.append({"Bucket": Bucket, "Key": Key})
        return {"Body": FakeBody(self.payload)}


@pytest.fixture
def fake_s3(monkeypatch):
    created = {}
    client = FakeS3()

    def fake_client(service, **kwargs):
        created["service"] = service
        created["kwargs"] = kwargs
        return client

    monkeypatch.setattr(s3_client.boto3, "client", fake_client)
    client.created = created
    return client


# ---- URI parsing ---------------------------------------------------------

def test_a_well_formed_uri_splits_into_bucket_and_key():
    assert parse_s3_uri(URI) == (
        "iw-rta-reports",
        "arn_ARN-266051/2026-08-25/msg_0f3a/processed/W0I7582.dbf",
    )


@pytest.mark.parametrize("bad", [
    "", "https://example.com/x", "s3://", "s3://bucket-only", "s3:///no-bucket",
    None,
])
def test_a_malformed_uri_raises_valueerror(bad):
    with pytest.raises(ValueError):
        parse_s3_uri(bad)


# ---- report date ---------------------------------------------------------

def test_the_report_date_comes_from_the_partition_segment():
    assert report_date_from_uri(URI) == date(2026, 8, 25)


def test_the_first_date_segment_wins():
    uri = "s3://b/2026-08-25/nested/2026-01-01/file.csv"

    assert report_date_from_uri(uri) == date(2026, 8, 25)


def test_a_uri_with_no_date_segment_gives_none():
    assert report_date_from_uri("s3://bucket/flat/file.csv") is None


def test_an_impossible_date_gives_none_rather_than_raising():
    assert report_date_from_uri("s3://bucket/2026-13-45/file.csv") is None


def test_report_date_of_none_is_none():
    assert report_date_from_uri(None) is None


# ---- download ------------------------------------------------------------

def test_download_fetches_the_parsed_bucket_and_key(fake_s3):
    download_as_file(URI, "MFSD201.dbf")

    assert fake_s3.calls == [{
        "Bucket": "iw-rta-reports",
        "Key": "arn_ARN-266051/2026-08-25/msg_0f3a/processed/W0I7582.dbf",
    }]


def test_the_buffer_is_named_with_the_api_filename_not_the_s3_key(fake_s3):
    # raw_ingestion.read_file() and extract_and_push() both dispatch on
    # file.name. The S3 key here is W0I7582.dbf, which matches no rule and
    # would be dropped as "Unknown file type" at raw_ingestion.py:736.
    buffer = download_as_file(URI, "MFSD201.dbf")

    assert buffer.name == "MFSD201.dbf"


def test_the_buffer_is_readable_from_the_start(fake_s3):
    buffer = download_as_file(URI, "MFSD201.dbf")

    assert buffer.read() == b"file-bytes"


def test_the_buffer_supports_the_uploadedfile_contract(fake_s3):
    # read_file() calls .seek(0) then .read(); it must be re-readable.
    buffer = download_as_file(URI, "MFSD201.dbf")
    buffer.read()
    buffer.seek(0)

    assert buffer.read() == b"file-bytes"


def test_credentials_and_region_come_from_config(fake_s3, monkeypatch):
    monkeypatch.setattr(s3_client.config, "AWS_ACCESS_KEY_ID", "AKIATEST")
    monkeypatch.setattr(s3_client.config, "AWS_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setattr(s3_client.config, "AWS_REGION", "ap-south-1")
    monkeypatch.setattr(s3_client.config, "AWS_S3_ENDPOINT_URL", None)

    download_as_file(URI, "MFSD201.dbf")

    kwargs = fake_s3.created["kwargs"]
    assert fake_s3.created["service"] == "s3"
    assert kwargs["aws_access_key_id"] == "AKIATEST"
    assert kwargs["region_name"] == "ap-south-1"
    assert "endpoint_url" not in kwargs


def test_a_custom_endpoint_is_passed_only_when_set(fake_s3, monkeypatch):
    monkeypatch.setattr(s3_client.config, "AWS_S3_ENDPOINT_URL", "http://minio:9000")

    download_as_file(URI, "MFSD201.dbf")

    assert fake_s3.created["kwargs"]["endpoint_url"] == "http://minio:9000"


def test_a_get_object_failure_propagates(fake_s3, monkeypatch):
    # The runner maps this to DOWNLOAD_FAILED; swallowing it here would lose
    # the reason.
    def boom(Bucket, Key):
        raise RuntimeError("NoSuchKey")

    monkeypatch.setattr(fake_s3, "get_object", boom)

    with pytest.raises(RuntimeError):
        download_as_file(URI, "MFSD201.dbf")


def test_a_downloaded_buffer_is_readable_by_raw_ingestion(fake_s3, monkeypatch):
    # The whole point of the named BytesIO: raw_ingestion.read_file() must
    # accept it unmodified. A one-row CSV is enough to prove the contract.
    import raw_ingestion

    monkeypatch.setattr(
        fake_s3, "get_object",
        lambda Bucket, Key: {"Body": FakeBody(b"AMC_CODE,FOLIO_NO\nTESTAMC,ZZ01\n")},
    )
    buffer = download_as_file("s3://b/2026-08-25/WBR2.csv", "WBR2.csv")

    df = raw_ingestion.read_file(buffer)

    assert len(df) == 1
    assert "AMC_CODE" in [str(c).upper() for c in df.columns]

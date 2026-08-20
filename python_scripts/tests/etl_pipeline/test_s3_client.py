import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

PKG_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PKG_ROOT))

from etl_pipeline import s3_client  # noqa: E402


def test_parse_s3_uri():
    bucket, key = s3_client.parse_s3_uri("s3://my-bucket/a/b/c.csv")
    assert bucket == "my-bucket"
    assert key == "a/b/c.csv"


def test_parse_s3_uri_rejects_non_s3_scheme():
    try:
        s3_client.parse_s3_uri("https://not-s3/x")
        assert False, "expected ValueError"
    except ValueError:
        pass


@patch("etl_pipeline.s3_client.boto3.client")
def test_download_as_file_returns_named_bytesio(mock_boto_client):
    mock_s3 = MagicMock()
    mock_s3.get_object.return_value = {"Body": MagicMock(read=MagicMock(return_value=b"a,b\n1,2\n"))}
    mock_boto_client.return_value = mock_s3

    buffer = s3_client.download_as_file("s3://bucket/path/WBR2.csv", "WBR2.csv")

    assert buffer.name == "WBR2.csv"
    assert buffer.read() == b"a,b\n1,2\n"
    mock_s3.get_object.assert_called_once_with(Bucket="bucket", Key="path/WBR2.csv")

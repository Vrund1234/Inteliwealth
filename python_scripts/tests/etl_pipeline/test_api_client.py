import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

PKG_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PKG_ROOT))

from etl_pipeline.api_client import EtlHandoffClient  # noqa: E402


def _client():
    return EtlHandoffClient(
        base_url="http://test/api/v1", email="e@x.com", password="pw", runner="runner-1"
    )


def _resp(status_code=200, json_body=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body or {}
    resp.raise_for_status = MagicMock()
    if status_code >= 400 and status_code != 409:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    return resp


@patch("etl_pipeline.api_client.requests.post")
def test_login_called_lazily_and_token_cached(mock_post):
    mock_post.return_value = _resp(json_body={"data": {"access_token": "tok-1"}})
    client = _client()
    assert client._token is None
    client._login()
    assert client._token == "tok-1"
    mock_post.assert_called_once_with(
        "http://test/api/v1/auth/login",
        json={"email": "e@x.com", "password": "pw"},
        timeout=30,
    )


@patch("etl_pipeline.api_client.requests.request")
@patch("etl_pipeline.api_client.requests.post")
def test_call_relogs_in_on_401(mock_post, mock_request):
    mock_post.return_value = _resp(json_body={"data": {"access_token": "tok-new"}})
    mock_request.side_effect = [_resp(status_code=401), _resp(status_code=200, json_body={"items": []})]

    client = _client()
    client._token = "tok-old"
    response = client._call("GET", "/etl-handoff/pending")

    assert response.status_code == 200
    assert mock_post.call_count == 1
    assert mock_request.call_count == 2


@patch("etl_pipeline.api_client.requests.request")
def test_peek_pending_returns_items(mock_request):
    # Real wire shape: every response is wrapped by the backend's
    # ResponseEnvelopeMiddleware — {"success", "status_code", "message", "data"}.
    mock_request.return_value = _resp(
        json_body={"success": True, "status_code": 200, "message": "OK",
                   "data": [{"id": "h1", "rta": "CAMS", "report_code": "WBR2"}]}
    )
    client = _client()
    client._token = "tok"
    items = client.peek_pending(limit=200)
    assert items == [{"id": "h1", "rta": "CAMS", "report_code": "WBR2"}]


@patch("etl_pipeline.api_client.requests.request")
def test_reserve_returns_items(mock_request):
    # "data" is an EtlHandoffReservation object; items live under data.items.
    mock_request.return_value = _resp(
        json_body={"success": True, "status_code": 200, "message": "Reserved 1 file(s) for the ETL",
                   "data": {"contract_version": 1, "reserved_by": "runner-1",
                            "reserved_at": "2026-08-20T00:00:00Z",
                            "items": [{"handoff_id": "h1"}]}}
    )
    client = _client()
    client._token = "tok"
    items = client.reserve(limit=10)
    assert items == [{"handoff_id": "h1"}]


@patch("etl_pipeline.api_client.requests.request")
def test_report_outcome_409_returns_reclaimed(mock_request):
    body = {"success": False, "status_code": 409, "message": "already reserved by another runner"}
    mock_request.return_value = _resp(status_code=409, json_body=body)
    client = _client()
    client._token = "tok"
    result = client.report_outcome("h1", "COMPLETED", rows_extracted=5)
    assert result == {"ok": False, "reason": "reservation_reclaimed", "api_response": body}


@patch("etl_pipeline.api_client.requests.request")
def test_report_outcome_success(mock_request):
    body = {"success": True, "status_code": 200, "message": "OK", "data": {"status": "COMPLETED"}}
    mock_request.return_value = _resp(status_code=200, json_body=body)
    client = _client()
    client._token = "tok"
    result = client.report_outcome("h1", "COMPLETED", rows_extracted=5)
    assert result == {"ok": True, "reason": None, "api_response": body}


@patch("etl_pipeline.api_client.requests.request")
def test_report_outcome_422_does_not_raise_and_captures_body(mock_request):
    # A validation error (e.g. an invalid failure_reason) must never crash the
    # whole run — it's captured for the caller to log, not raised.
    body = {
        "success": False, "status_code": 422, "message": "Unprocessable Entity",
        "detail": [{"loc": ["body", "failure_reason"], "msg": "failure_reason must be one of [...]"}],
    }
    mock_request.return_value = _resp(status_code=422, json_body=body)
    client = _client()
    client._token = "tok"
    result = client.report_outcome("h1", "FAILED", failure_reason="DOWNLOAD_ERROR")
    assert result == {"ok": False, "reason": "http_422", "api_response": body}

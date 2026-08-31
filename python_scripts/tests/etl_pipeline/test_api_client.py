"""EtlHandoffClient against a mocked `requests`. No live backend is needed and
none is available -- the api container publishes no host port.

Token lifetime is asserted with synthetic JWTs whose `exp` is set per case;
the client only ever DECODES the claim (never verifies the signature), so an
unsigned payload is enough."""

import base64
import json
import time

import pytest

from etl_pipeline import api_client
from etl_pipeline.api_client import (
    EtlHandoffApiError,
    EtlHandoffClient,
    EtlHandoffFatalError,
)


# ---- helpers -------------------------------------------------------------

def _jwt(expires_in_seconds):
    """An unsigned JWT-shaped string whose exp is `expires_in_seconds` away."""
    payload = {"exp": int(time.time()) + expires_in_seconds, "user_id": "u"}
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=")
    return "header." + raw.decode() + ".signature"


class FakeResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text else json.dumps(payload)

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def _envelope(data, status_code=200):
    return {"success": True, "status_code": status_code, "message": "OK", "data": data}


def _tokens(access_ttl=1800, refresh_ttl=604800):
    return _envelope({
        "access_token": _jwt(access_ttl),
        "refresh_token": _jwt(refresh_ttl),
        "token_type": "bearer",
    })


class Recorder:
    """Stands in for requests.request / requests.post, returning queued
    responses and recording every call."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        if not self.responses:
            raise AssertionError(f"unexpected extra request: {method} {url}")
        return self.responses.pop(0)


@pytest.fixture
def client():
    return EtlHandoffClient(
        base_url="http://api.test/api/v1",
        email="de-runner@intelliwealth.com",
        password="de-runner@123",
        runner="de-etl-worker-1",
        timeout=5,
        refresh_margin_seconds=300,
    )


def _install(monkeypatch, responses):
    recorder = Recorder(responses)
    monkeypatch.setattr(api_client.requests, "request", recorder)
    monkeypatch.setattr(
        api_client.requests, "post",
        lambda url, **kwargs: recorder("POST", url, **kwargs),
    )
    return recorder


# ---- login ---------------------------------------------------------------

def test_login_posts_the_credentials_and_stores_both_tokens(monkeypatch, client):
    recorder = _install(monkeypatch, [FakeResponse(200, _tokens())])

    client.login()

    assert recorder.calls[0]["url"] == "http://api.test/api/v1/auth/login"
    assert recorder.calls[0]["json"] == {
        "email": "de-runner@intelliwealth.com",
        "password": "de-runner@123",
    }
    assert client._access_token
    assert client._refresh_token


def test_a_failed_login_is_fatal(monkeypatch, client):
    _install(monkeypatch, [FakeResponse(401, {"message": "Could not validate credentials"})])

    with pytest.raises(EtlHandoffFatalError):
        client.login()


def test_the_first_call_logs_in_lazily(monkeypatch, client):
    recorder = _install(monkeypatch, [
        FakeResponse(200, _tokens()),
        FakeResponse(200, _envelope([])),
    ])

    client.peek_pending()

    assert recorder.calls[0]["url"].endswith("/auth/login")
    assert recorder.calls[1]["url"].endswith("/etl-handoff/pending")


def test_every_call_carries_the_bearer_token(monkeypatch, client):
    recorder = _install(monkeypatch, [
        FakeResponse(200, _tokens()),
        FakeResponse(200, _envelope([])),
    ])

    client.peek_pending()

    assert recorder.calls[1]["headers"]["Authorization"].startswith("Bearer ")


# ---- proactive refresh ---------------------------------------------------

def test_a_token_inside_the_refresh_margin_is_refreshed_before_the_call(
    monkeypatch, client
):
    # 100s of life left, margin is 300 -> refresh first.
    recorder = _install(monkeypatch, [
        FakeResponse(200, _envelope({"access_token": _jwt(100),
                                     "refresh_token": _jwt(604800),
                                     "token_type": "bearer"})),
        FakeResponse(200, _tokens()),
        FakeResponse(200, _envelope([])),
    ])
    client.login()

    client.peek_pending()

    assert recorder.calls[1]["url"].endswith("/auth/refresh")
    assert recorder.calls[2]["url"].endswith("/etl-handoff/pending")


def test_a_healthy_token_is_not_refreshed(monkeypatch, client):
    recorder = _install(monkeypatch, [
        FakeResponse(200, _tokens()),          # login: 1800s of life
        FakeResponse(200, _envelope([])),
    ])
    client.login()

    client.peek_pending()

    assert len(recorder.calls) == 2
    assert not any("/auth/refresh" in call["url"] for call in recorder.calls)


def test_refresh_sends_only_the_refresh_token_and_no_authorization_header(
    monkeypatch, client
):
    # app/modules/auth/schemas.py:28-29 -- the refresh route takes the token in
    # the body and no bearer header.
    recorder = _install(monkeypatch, [
        FakeResponse(200, _tokens()),
        FakeResponse(200, _tokens()),
    ])
    client.login()
    held = client._refresh_token

    assert client.refresh() is True
    call = recorder.calls[1]
    assert call["url"].endswith("/auth/refresh")
    assert call["json"] == {"refresh_token": held}
    assert "headers" not in call


def test_refresh_adopts_the_rotated_refresh_token(monkeypatch, client):
    # The second response carries a distinguishable refresh token: two
    # _tokens() calls in the same second would mint byte-identical JWTs, which
    # would make this assertion vacuous rather than meaningful.
    _install(monkeypatch, [
        FakeResponse(200, _tokens()),
        FakeResponse(200, _tokens(refresh_ttl=604801)),
    ])
    client.login()
    first = client._refresh_token

    client.refresh()

    assert client._refresh_token != first


def test_a_rejected_refresh_returns_false_rather_than_raising(monkeypatch, client):
    _install(monkeypatch, [
        FakeResponse(200, _tokens()),
        FakeResponse(401, {"message": "Could not validate credentials"}),
    ])
    client.login()

    assert client.refresh() is False


def test_refresh_without_a_stored_token_returns_false(client):
    assert client.refresh() is False


# ---- reactive 401 --------------------------------------------------------

def test_a_401_triggers_refresh_then_replays_the_request_once(monkeypatch, client):
    recorder = _install(monkeypatch, [
        FakeResponse(200, _tokens()),                              # login
        FakeResponse(401, {"message": "Could not validate credentials"}),  # call
        FakeResponse(200, _tokens()),                              # refresh
        FakeResponse(200, _envelope([])),                          # replay
    ])
    client.login()

    assert client.peek_pending() == []
    assert [c["url"].rsplit("/", 1)[-1] for c in recorder.calls] == [
        "login", "pending", "refresh", "pending",
    ]


def test_a_401_with_a_dead_refresh_token_falls_back_to_a_full_login(
    monkeypatch, client
):
    recorder = _install(monkeypatch, [
        FakeResponse(200, _tokens()),                              # login
        FakeResponse(401, {"message": "..."}),                     # call
        FakeResponse(401, {"message": "..."}),                     # refresh fails
        FakeResponse(200, _tokens()),                              # login again
        FakeResponse(200, _envelope([])),                          # replay
    ])
    client.login()

    assert client.peek_pending() == []
    assert recorder.calls[3]["url"].endswith("/auth/login")


def test_a_second_401_after_reauthenticating_raises(monkeypatch, client):
    _install(monkeypatch, [
        FakeResponse(200, _tokens()),
        FakeResponse(401, {"message": "..."}),
        FakeResponse(200, _tokens()),
        FakeResponse(401, {"message": "..."}),
    ])
    client.login()

    with pytest.raises(EtlHandoffApiError):
        client.peek_pending()


def test_the_runner_never_calls_logout(monkeypatch, client):
    # Logout denylists the token's jti, revoking a token a concurrent run may
    # still be using (app/modules/auth/service.py:264-272).
    recorder = _install(monkeypatch, [
        FakeResponse(200, _tokens()),
        FakeResponse(200, _envelope([])),
    ])
    client.login()
    client.peek_pending()

    assert not any("/auth/logout" in call["url"] for call in recorder.calls)


# ---- reserve / peek ------------------------------------------------------

def test_reserve_posts_the_runner_and_limit_and_unwraps_items(monkeypatch, client):
    items = [{"handoff_id": "abc", "report_code": "WBR2"}]
    recorder = _install(monkeypatch, [
        FakeResponse(200, _tokens()),
        FakeResponse(200, _envelope({
            "contract_version": 1,
            "reserved_by": "de-etl-worker-1",
            "reserved_at": "2026-08-31T10:00:00Z",
            "items": items,
        })),
    ])
    client.login()

    assert client.reserve(limit=3) == items
    assert recorder.calls[1]["json"] == {"runner": "de-etl-worker-1", "limit": 3}


def test_peek_pending_unwraps_the_bare_data_list(monkeypatch, client):
    rows = [{"id": "abc"}]
    recorder = _install(monkeypatch, [
        FakeResponse(200, _tokens()),
        FakeResponse(200, _envelope(rows)),
    ])
    client.login()

    assert client.peek_pending(limit=7) == rows
    assert recorder.calls[1]["params"] == {"limit": 7}


@pytest.mark.parametrize("status_code", [403, 503])
def test_a_403_or_503_on_reserve_is_fatal(monkeypatch, client, status_code):
    _install(monkeypatch, [
        FakeResponse(200, _tokens()),
        FakeResponse(status_code, {"message": "nope"}),
    ])
    client.login()

    with pytest.raises(EtlHandoffFatalError):
        client.reserve()


def test_a_422_on_reserve_raises_an_api_error_carrying_the_body(monkeypatch, client):
    _install(monkeypatch, [
        FakeResponse(200, _tokens()),
        FakeResponse(422, {"detail": [{"loc": ["body", "limit"], "msg": "too big"}]}),
    ])
    client.login()

    with pytest.raises(EtlHandoffApiError) as excinfo:
        client.reserve()
    assert "too big" in str(excinfo.value)


# ---- report_outcome ------------------------------------------------------

def test_report_outcome_patches_the_expected_payload(monkeypatch, client):
    recorder = _install(monkeypatch, [
        FakeResponse(200, _tokens()),
        FakeResponse(200, _envelope({"id": "abc", "status": "COMPLETED"})),
    ])
    client.login()

    result = client.report_outcome("abc", "COMPLETED", rows_extracted=42)

    assert result["ok"] is True
    assert result["http_status"] == 200
    assert recorder.calls[1]["method"] == "PATCH"
    assert recorder.calls[1]["url"].endswith("/etl-handoff/abc")
    assert recorder.calls[1]["json"] == {
        "runner": "de-etl-worker-1", "status": "COMPLETED", "rows_extracted": 42,
    }


def test_report_outcome_truncates_the_error_message_to_2000_chars(monkeypatch, client):
    recorder = _install(monkeypatch, [
        FakeResponse(200, _tokens()),
        FakeResponse(200, _envelope({})),
    ])
    client.login()

    client.report_outcome("abc", "FAILED", failure_reason="UNKNOWN",
                          error_message="x" * 5000)

    assert len(recorder.calls[1]["json"]["error_message"]) == 2000


def test_report_outcome_never_raises_on_a_409(monkeypatch, client):
    # Either a double report or a reclaimed reservation. Expected, not
    # exceptional -- the remaining files must still be reported.
    _install(monkeypatch, [
        FakeResponse(200, _tokens()),
        FakeResponse(409, {"message": "not reserved by this runner"}),
    ])
    client.login()

    result = client.report_outcome("abc", "COMPLETED")

    assert result["ok"] is False
    assert result["reason"] == "http_409"
    assert result["fatal"] is False


@pytest.mark.parametrize("status_code", [403, 503])
def test_report_outcome_flags_403_and_503_as_fatal(monkeypatch, client, status_code):
    _install(monkeypatch, [
        FakeResponse(200, _tokens()),
        FakeResponse(status_code, {"message": "nope"}),
    ])
    client.login()

    result = client.report_outcome("abc", "COMPLETED")

    assert result["ok"] is False
    assert result["fatal"] is True


def test_report_outcome_captures_a_non_json_body(monkeypatch, client):
    _install(monkeypatch, [
        FakeResponse(200, _tokens()),
        FakeResponse(502, None, text="<html>bad gateway</html>"),
    ])
    client.login()

    result = client.report_outcome("abc", "COMPLETED")

    assert result["api_response"]["raw_text"].startswith("<html>")


def test_report_outcome_survives_a_transport_error(monkeypatch, client):
    _install(monkeypatch, [FakeResponse(200, _tokens())])
    client.login()

    def boom(method, url, **kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(api_client.requests, "request", boom)

    result = client.report_outcome("abc", "COMPLETED")

    assert result["ok"] is False
    assert result["reason"] == "transport_error"
    assert result["fatal"] is False


# ---- redaction -----------------------------------------------------------

def test_the_recorded_login_request_never_carries_the_password(monkeypatch, client):
    _install(monkeypatch, [FakeResponse(200, _tokens())])

    client.login()

    serialized = json.dumps(client.last_request)
    assert "de-runner@123" not in serialized
    assert client.last_request["body"]["password"] == "***"


def test_the_recorded_refresh_request_never_carries_the_token(monkeypatch, client):
    _install(monkeypatch, [FakeResponse(200, _tokens()), FakeResponse(200, _tokens())])
    client.login()

    client.refresh()

    assert client.last_request["body"] == {"refresh_token": "***"}


def test_no_recorded_request_ever_carries_an_authorization_header(
    monkeypatch, client
):
    _install(monkeypatch, [
        FakeResponse(200, _tokens()),
        FakeResponse(200, _envelope([])),
    ])
    client.login()

    client.peek_pending()

    assert "headers" not in client.last_request
    assert "Bearer" not in json.dumps(client.last_request)


# ---- token decoding ------------------------------------------------------

def test_an_undecodable_token_disables_proactive_refresh_only(monkeypatch, client):
    # A token whose exp can't be read must not crash the run; the reactive
    # 401 path still covers expiry.
    recorder = _install(monkeypatch, [
        FakeResponse(200, _envelope({"access_token": "not-a-jwt",
                                     "refresh_token": _jwt(604800),
                                     "token_type": "bearer"})),
        FakeResponse(200, _envelope([])),
    ])
    client.login()

    assert client.peek_pending() == []
    assert len(recorder.calls) == 2

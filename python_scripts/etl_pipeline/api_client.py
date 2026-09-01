"""Client for the intelli-wealth-backend ETL handoff API.

Every route is wrapped by the backend's ResponseEnvelopeMiddleware:
{"success", "status_code", "message", "data": <payload>}. The payload is
always read out of "data".
"""

import base64
import json
import time

import requests

from . import config

# The documented maximum for the outcome payload's error_message.
MAX_ERROR_MESSAGE = 2000


class EtlHandoffFatalError(Exception):
    """The whole run must stop. A 403 (missing grant, must_change_password, or
    an incomplete-org gate) or a 503 (ETL_HANDOFF_ENABLED=false) can never be
    resolved by retrying, and neither can a failed login."""


class EtlHandoffApiError(Exception):
    """This call failed. The run may continue with the next file."""


def _decode_exp(token):
    """The `exp` claim of a JWT, or None if it cannot be read.

    Deliberately NOT a verification: the claim is used only to schedule a
    refresh, so there is nothing to gain from checking a signature the runner
    has no key for -- and PyJWT is not installed. A token this cannot decode
    simply disables proactive refresh; the reactive 401 path still covers it.
    """
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload_b64))["exp"])
    except Exception:
        return None


class EtlHandoffClient:
    def __init__(self, base_url=None, email=None, password=None, runner=None,
                 timeout=None, refresh_margin_seconds=None):
        self.base_url = (base_url or config.INTELLIWEALTH_API_BASE).rstrip("/")
        self.email = email if email is not None else config.INTELLIWEALTH_RUNNER_EMAIL
        self.password = (
            password if password is not None else config.INTELLIWEALTH_RUNNER_PASSWORD
        )
        self.runner = runner or config.ETL_RUNNER_NAME
        self.timeout = timeout or config.ETL_HTTP_TIMEOUT_SECONDS
        self.refresh_margin_seconds = (
            config.ETL_TOKEN_REFRESH_MARGIN_SECONDS
            if refresh_margin_seconds is None
            else refresh_margin_seconds
        )

        self._access_token = None
        self._refresh_token = None
        self._access_expires_at = None

        # The most recent exchange, for pipeline.etl_pipeline_log. The request
        # NEVER includes headers -- that is how the bearer token is kept out of
        # the log table -- and secret body values are replaced before they are
        # stored, not after.
        self.last_request = None
        self.last_response = None
        self.last_status = None

    # -- recording ---------------------------------------------------------

    def _record(self, method, path, body, response):
        self.last_request = {"method": method, "path": path, "body": body}
        self.last_status = response.status_code
        try:
            self.last_response = response.json()
        except ValueError:
            self.last_response = {"raw_text": (response.text or "")[:2000]}
        return self.last_response

    # -- authentication ----------------------------------------------------

    def login(self):
        response = requests.post(
            f"{self.base_url}/auth/login",
            json={"email": self.email, "password": self.password},
            timeout=self.timeout,
        )
        # The password is replaced HERE, at the only place it is ever recorded.
        body = self._record("POST", "/auth/login",
                            {"email": self.email, "password": "***"}, response)
        if response.status_code >= 400:
            raise EtlHandoffFatalError(
                f"login failed: HTTP {response.status_code} {json.dumps(body)[:500]}"
            )
        self._adopt(body["data"])

    def refresh(self):
        """Exchange the refresh token for a new pair. True on success.

        Rotation is non-destructive -- refresh_tokens() never revokes the old
        one -- and the refresh token lives 7 days, so this yields an
        effectively unbounded session without ever re-sending the password.
        """
        if not self._refresh_token:
            return False
        response = requests.post(
            f"{self.base_url}/auth/refresh",
            json={"refresh_token": self._refresh_token},
            timeout=self.timeout,
        )
        body = self._record("POST", "/auth/refresh", {"refresh_token": "***"}, response)
        if response.status_code >= 400:
            return False
        self._adopt(body["data"])
        return True

    def _adopt(self, data):
        self._access_token = data["access_token"]
        self._refresh_token = data.get("refresh_token") or self._refresh_token
        self._access_expires_at = _decode_exp(self._access_token)

    def _ensure_token(self):
        """Proactive refresh, run before EVERY request.

        A long silver/gold rebuild sits between the reserve call and the PATCH
        calls, so checking only once per run would leave a stale token in hand
        for the most expensive request of the batch.
        """
        if self._access_token is None:
            self.login()
            return
        if self._access_expires_at is None:
            return
        if self._access_expires_at - time.time() <= self.refresh_margin_seconds:
            if not self.refresh():
                self.login()

    # -- transport ---------------------------------------------------------

    def _send(self, method, path, **kwargs):
        return requests.request(
            method,
            f"{self.base_url}{path}",
            headers={"Authorization": f"Bearer {self._access_token}"},
            timeout=self.timeout,
            **kwargs,
        )

    def _call(self, method, path, **kwargs):
        """One authenticated request, with a single re-authenticate-and-replay.

        A 401 is ambiguous by construction -- an expired token, a bad signature
        and a wrong SECRET_KEY all return the same message with no
        distinguishing code -- so it is NEVER string-matched. Any 401 means
        "re-authenticate".
        """
        self._ensure_token()
        response = self._send(method, path, **kwargs)
        if response.status_code == 401:
            if not self.refresh():
                self.login()
            response = self._send(method, path, **kwargs)
        return response

    def _checked(self, method, path, body, **kwargs):
        response = self._call(method, path, **kwargs)
        payload = self._record(method, path, body, response)
        if response.status_code in (403, 503):
            # 403: a missing etl_handoff.* grant, a must_change_password flag,
            # or an incomplete-org gate. 503: the handoff is switched off.
            # Neither can be resolved by retrying.
            raise EtlHandoffFatalError(
                f"{method} {path}: HTTP {response.status_code} "
                f"{json.dumps(payload)[:500]}"
            )
        if response.status_code >= 400:
            raise EtlHandoffApiError(
                f"{method} {path}: HTTP {response.status_code} "
                f"{json.dumps(payload)[:500]}"
            )
        return payload["data"]

    # -- routes ------------------------------------------------------------

    def peek_pending(self, limit=None):
        """Files the ETL could take right now. Reserves nothing, and is NOT
        feature-flag gated -- so --dry-run works even with the handoff off."""
        limit = config.ETL_PEEK_LIMIT if limit is None else limit
        return self._checked("GET", "/etl-handoff/pending", None,
                             params={"limit": limit})

    def queue_status(self):
        """Queue depth and lag. Also not feature-flag gated."""
        return self._checked("GET", "/etl-handoff/status", None)

    def reserve(self, limit=None):
        """Reserve a batch. Returns the item list, empty when the queue is
        drained. attempt_count increments HERE, not on the outcome report."""
        limit = config.ETL_BATCH_LIMIT if limit is None else limit
        body = {"runner": self.runner, "limit": limit}
        data = self._checked("POST", "/etl-handoff/reservations", body, json=body)
        return data["items"]

    def report_outcome(self, handoff_id, status, rows_extracted=None,
                       failure_reason=None, error_message=None):
        """Report one file. NEVER raises.

        A reporting failure for one file must not abort the remaining files --
        their bronze/silver/gold work is already done. 403 and 503 are still
        flagged fatal so the caller can stop after finishing the batch.
        """
        body = {"runner": self.runner, "status": status}
        if rows_extracted is not None:
            body["rows_extracted"] = rows_extracted
        if failure_reason is not None:
            body["failure_reason"] = failure_reason
        if error_message is not None:
            body["error_message"] = str(error_message)[:MAX_ERROR_MESSAGE]

        path = f"/etl-handoff/{handoff_id}"
        try:
            response = self._call("PATCH", path, json=body)
        except Exception as exc:
            self.last_request = {"method": "PATCH", "path": path, "body": body}
            self.last_response = {"error": f"{type(exc).__name__}: {exc}"}
            self.last_status = None
            return {"ok": False, "reason": "transport_error", "fatal": False,
                    "api_request": self.last_request,
                    "api_response": self.last_response, "http_status": None}

        payload = self._record("PATCH", path, body, response)
        if response.status_code >= 400:
            return {
                "ok": False,
                "reason": f"http_{response.status_code}",
                # 403/503 are run-level problems, not per-file ones.
                "fatal": response.status_code in (403, 503),
                "api_request": self.last_request,
                "api_response": payload,
                "http_status": response.status_code,
            }
        return {"ok": True, "reason": None, "fatal": False,
                "api_request": self.last_request, "api_response": payload,
                "http_status": response.status_code}

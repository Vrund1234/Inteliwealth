import requests

from . import config


class EtlHandoffClient:
    def __init__(self, base_url=None, email=None, password=None, runner=None):
        self.base_url = (base_url or config.INTELLIWEALTH_API_BASE).rstrip("/")
        self.email = email or config.INTELLIWEALTH_RUNNER_EMAIL
        self.password = password or config.INTELLIWEALTH_RUNNER_PASSWORD
        self.runner = runner or config.ETL_RUNNER_NAME
        self._token = None

    def _login(self):
        response = requests.post(
            f"{self.base_url}/auth/login",
            json={"email": self.email, "password": self.password},
            timeout=30,
        )
        response.raise_for_status()
        self._token = response.json()["data"]["access_token"]
        return self._token

    def _call(self, method, path, **kwargs):
        if self._token is None:
            self._login()

        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self._token}"
        response = requests.request(
            method, f"{self.base_url}{path}", headers=headers, timeout=30, **kwargs
        )

        if response.status_code == 401:
            self._login()
            headers["Authorization"] = f"Bearer {self._token}"
            response = requests.request(
                method, f"{self.base_url}{path}", headers=headers, timeout=30, **kwargs
            )

        return response

    def peek_pending(self, limit=200):
        response = self._call("GET", "/etl-handoff/pending", params={"limit": limit})
        response.raise_for_status()
        # Every endpoint is wrapped by the backend's ResponseEnvelopeMiddleware:
        # {"success", "status_code", "message", "data": <payload>}. Here "data"
        # is the bare list of pending items.
        return response.json()["data"]

    def reserve(self, limit=50):
        response = self._call(
            "POST", "/etl-handoff/reservations",
            json={"runner": self.runner, "limit": limit},
        )
        response.raise_for_status()
        # "data" is an EtlHandoffReservation object; the reserved items live
        # one level deeper, under "data"."items".
        return response.json()["data"]["items"]

    def report_outcome(self, handoff_id, status, rows_extracted=None,
                        failure_reason=None, error_message=None):
        payload = {"runner": self.runner, "status": status}
        if rows_extracted is not None:
            payload["rows_extracted"] = rows_extracted
        if failure_reason is not None:
            payload["failure_reason"] = failure_reason
        if error_message is not None:
            payload["error_message"] = error_message[:2000]

        response = self._call("PATCH", f"/etl-handoff/{handoff_id}", json=payload)

        # Always capture the body — including on a 4xx/5xx — so the caller can
        # log the backend's actual response (e.g. a 422 validation message)
        # instead of losing it to an uncaught HTTPError. This is deliberately
        # NOT response.raise_for_status(): a bad PATCH for one file must never
        # crash the whole run_once() batch; it's reported per-file instead.
        try:
            body = response.json()
        except ValueError:
            body = {"raw_text": response.text[:2000]}

        if response.status_code == 409:
            return {"ok": False, "reason": "reservation_reclaimed", "api_response": body}

        if response.status_code >= 400:
            return {"ok": False, "reason": f"http_{response.status_code}", "api_response": body}

        return {"ok": True, "reason": None, "api_response": body}

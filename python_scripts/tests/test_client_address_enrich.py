"""enrich_client_address fills blanks on addresses gold already holds. It is
the half of the dedup fix that keeps data: without it, the first RTA feed to
describe an address wins permanently and every later pincode or country is
discarded."""

import pandas as pd
import pytest

import etl_gold_client_address as mod


class _FakeResult:
    def __init__(self, rowcount):
        self.rowcount = rowcount


class _FakeConn:
    def __init__(self, sink):
        self.sink = sink

    def execute(self, statement, payload):
        self.sink.append((statement, payload))
        return _FakeResult(len(payload))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeEngine:
    def __init__(self):
        self.calls = []

    def begin(self):
        return _FakeConn(self.calls)


def test_nothing_to_enrich_touches_the_database_at_all(monkeypatch):
    engine = _FakeEngine()
    monkeypatch.setattr(mod, "engine", engine)
    assert mod.enrich_client_address(pd.DataFrame()) == 0
    assert mod.enrich_client_address(None) == 0
    assert engine.calls == []


def test_payload_carries_the_match_key_and_every_enriched_field(monkeypatch):
    engine = _FakeEngine()
    monkeypatch.setattr(mod, "engine", engine)

    rows = pd.DataFrame([{
        "client_id": "c-1",
        "address_key": "RAMDASNIKHADKI",
        "area": None,
        "city": "NADIAD",
        "state": "Gujarat",
        "country": None,
        "pincode": "387001",
        "mobile_no": "+918128356506",
        "whatsapp_no": None,
        "seq": 99,          # must NOT reach the payload
        "is_main": True,    # must NOT reach the payload
    }])

    assert mod.enrich_client_address(rows) == 1

    (_, payload), = engine.calls
    assert payload == [{
        "client_id": "c-1",
        "address_key": "RAMDASNIKHADKI",
        "area": None,
        "city": "NADIAD",
        "state": "Gujarat",
        "country": None,
        "pincode": "387001",
        "mobile_no": "+918128356506",
        "whatsapp_no": None,
    }]


def test_nan_becomes_none_so_coalesce_sees_a_real_null(monkeypatch):
    """A pandas NaN bound as a parameter is not SQL NULL; COALESCE would treat
    it as a value and the blank would be 'filled' with nothing."""
    engine = _FakeEngine()
    monkeypatch.setattr(mod, "engine", engine)

    rows = pd.DataFrame([{
        "client_id": "c-1", "address_key": "K",
        "area": float("nan"), "city": pd.NA, "state": None,
        "country": "India", "pincode": float("nan"),
        "mobile_no": None, "whatsapp_no": None,
    }])
    mod.enrich_client_address(rows)

    (_, payload), = engine.calls
    assert payload[0]["area"] is None
    assert payload[0]["city"] is None
    assert payload[0]["pincode"] is None
    assert payload[0]["country"] == "India"


def test_the_update_never_overwrites_a_populated_field(monkeypatch):
    """COALESCE(column, :param) -- the existing value wins. Reversing these
    arguments would make the newest feed authoritative, which is a different
    product decision, not a refactor."""
    engine = _FakeEngine()
    monkeypatch.setattr(mod, "engine", engine)
    rows = pd.DataFrame([{
        "client_id": "c", "address_key": "K", "area": None, "city": "X",
        "state": None, "country": None, "pincode": None,
        "mobile_no": None, "whatsapp_no": None,
    }])
    mod.enrich_client_address(rows)
    statement = str(engine.calls[0][0])
    for field in ("area", "city", "state", "country",
                  "pincode", "mobile_no", "whatsapp_no"):
        assert f"{field} = COALESCE({field}," in " ".join(statement.split())


def test_update_is_scoped_to_live_rows_of_one_client(monkeypatch):
    engine = _FakeEngine()
    monkeypatch.setattr(mod, "engine", engine)
    rows = pd.DataFrame([{
        "client_id": "c", "address_key": "K", "area": None, "city": "X",
        "state": None, "country": None, "pincode": None,
        "mobile_no": None, "whatsapp_no": None,
    }])
    mod.enrich_client_address(rows)
    statement = " ".join(str(engine.calls[0][0]).split())
    assert "WHERE client_id = :client_id" in statement
    assert "AND address_key = :address_key" in statement
    assert "AND is_deleted = false" in statement

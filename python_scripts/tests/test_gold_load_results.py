"""load_gold() must report a per-entity {status, total, inserted, updated}
without changing what it does. app.py:665 discards this return value, and the
eight per-entity try/except blocks must keep swallowing -- a gold failure that
propagated would break the Streamlit Transform button."""

import pandas as pd
import pytest

import gold_loader
from gold_loader import GOLD_ENTITIES, load_gold


def test_every_entity_is_named():
    assert GOLD_ENTITIES == (
        "amc",
        "scheme",
        "scheme_nav",
        "transactions",
        "holdings",
        "sip",
        "clients",
        "folio_nominees",
    )


def test_a_raising_entity_is_reported_failed_and_does_not_propagate(monkeypatch):
    def boom():
        raise RuntimeError("extract exploded")

    monkeypatch.setattr(gold_loader, "extract_amc", boom)

    results = load_gold()

    assert results["amc"]["status"] == "FAILED"
    assert "extract exploded" in results["amc"]["error"]


def test_a_raising_entity_still_prints_its_original_message(monkeypatch, capsys):
    def boom():
        raise RuntimeError("extract exploded")

    monkeypatch.setattr(gold_loader, "extract_amc", boom)

    load_gold()

    printed = capsys.readouterr().out
    assert "AMC Gold Failed" in printed
    assert "extract exploded" in printed


def test_a_loader_returning_false_is_reported_failed(monkeypatch):
    # etl_gold_scheme.load_scheme() and etl_gold_clients.load_clients() catch
    # their own insert errors and return False. load_gold()'s own except never
    # sees those, so the return value is the only signal they failed.
    monkeypatch.setattr(gold_loader, "extract_amc",
                        lambda: pd.DataFrame([{"amc_code": "X"}]))
    monkeypatch.setattr(gold_loader, "transform_amc",
                        lambda df: pd.DataFrame([{"amc_code": "X"}]))
    monkeypatch.setattr(gold_loader, "load_amc", lambda df: False)

    results = load_gold()

    assert results["amc"]["status"] == "FAILED"
    assert results["amc"]["total"] == 1


def test_an_entity_with_no_source_data_is_skipped(monkeypatch):
    monkeypatch.setattr(gold_loader, "extract_amc", lambda: pd.DataFrame())

    results = load_gold()

    assert results["amc"]["status"] == "SKIPPED"
    assert results["amc"]["total"] == 0


def test_counts_come_from_the_upserts_that_entity_performed(monkeypatch):
    def fake_load(df):
        # Stand in for a real loader: record one upsert through the same
        # collector load_gold() arms.
        from utils import db
        db._record_upsert("gold", "amc", 4, 4, 3)
        return True

    monkeypatch.setattr(gold_loader, "extract_amc",
                        lambda: pd.DataFrame([{"amc_code": "X"}] * 4))
    monkeypatch.setattr(gold_loader, "transform_amc",
                        lambda df: pd.DataFrame([{"amc_code": "X"}] * 4))
    monkeypatch.setattr(gold_loader, "load_amc", fake_load)

    results = load_gold()

    assert results["amc"]["total"] == 4
    assert results["amc"]["inserted"] == 3
    assert results["amc"]["updated"] == 1


def test_every_entity_has_a_result_even_when_untouched():
    results = load_gold()

    assert set(results) == set(GOLD_ENTITIES)
    for entity, result in results.items():
        assert result["entity"] == entity
        assert result["status"] in {"COMPLETED", "SKIPPED", "FAILED"}
        assert isinstance(result["inserted"], int)
        assert isinstance(result["updated"], int)

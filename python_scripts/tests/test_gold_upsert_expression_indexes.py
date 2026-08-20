import sys
import uuid
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PKG_ROOT))

import pandas as pd  # noqa: E402
from sqlalchemy import text  # noqa: E402
from utils.db import engine  # noqa: E402
from etl_gold_transaction import load_transactions  # noqa: E402
from etl_gold_holdings import load_holdings  # noqa: E402
from etl_gold_sip import load_sip  # noqa: E402
from etl_gold_clients import load_clients  # noqa: E402


def test_load_transactions_do_nothing_on_conflict_never_duplicates():
    rta_txn_no = f"TEST-{uuid.uuid4().hex[:8]}"
    df = pd.DataFrame([{
        "rta": "CAMS", "rta_txn_no": rta_txn_no, "pan": None, "folio_number": "F1",
        "txn_type": "PURCHASE", "txn_type_raw": None, "txn_desc": None,
        "txn_date": None, "post_date": None, "amount": 100.0, "units": 5.0,
        "nav": None, "load_amount": None, "stt": None, "stamp_duty": None, "gst": None,
        "arn": None, "euin": None, "sip_ref": None, "status": None,
        "client_id": None, "amc_id": None, "scheme_id": None, "txn_sub_type": None,
        "rta_txn_id": None, "arn_id": None, "sip_id": None, "source": "TEST",
        "source_file_id": None, "created_at": pd.Timestamp.now(),
        "scheme_code": None, "sub_arn": None,
    }])
    try:
        r1 = load_transactions(df.copy())
        assert r1["status"] == "ok"
        assert r1["rows_loaded"] == 1

        r2 = load_transactions(df.copy())  # identical row again
        assert r2["status"] == "ok"
        assert r2["rows_loaded"] == 0  # DO NOTHING — nothing new landed

        with engine.connect() as conn:
            count = conn.execute(
                text("SELECT COUNT(*) FROM gold.transactions WHERE rta_txn_no = :t"),
                {"t": rta_txn_no},
            ).scalar()
        assert count == 1
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM gold.transactions WHERE rta_txn_no = :t"), {"t": rta_txn_no})


def test_load_holdings_updates_existing_on_conflict():
    # load_holdings upper-cases rta/folio_number/scheme_id before upserting —
    # use already-uppercase values so the test's own lookups match.
    folio_number = f"TEST-{uuid.uuid4().hex[:8]}".upper()
    scheme_id = str(uuid.uuid4()).upper()
    base = {
        "id": str(uuid.uuid4()), "rta": "CAMS", "pan": None, "folio_number": folio_number,
        "units": 10.0, "market_value": 1000.0, "as_on_date": None, "folio_date": None,
        "arn": "ARN-1", "holding_nature": None, "nominee_name": None, "nominee_relation": None,
        "nominee_pct": None, "kyc_status": None, "bank_name": None, "bank_ac_last4": None,
        "demat_flag": None, "client_id": None, "amc_id": None, "scheme_id": scheme_id,
        "purchase_date": None, "arn_id": None, "avg_cost_nav": None, "invested_amount": None,
        "current_nav": None, "current_value": None, "nav_date": None, "unrealised_gain": None,
        "xirr": None, "first_purchase_date": None, "source_file_id": None,
        "last_synced_at": None, "subarn": None, "created_at": pd.Timestamp.now(),
    }
    df = pd.DataFrame([base])
    try:
        r1 = load_holdings(df.copy())
        assert r1["status"] == "ok"

        df2 = pd.DataFrame([{**base, "id": str(uuid.uuid4()), "units": 20.0, "market_value": 2200.0}])
        r2 = load_holdings(df2)
        assert r2["status"] == "ok"

        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT units, market_value FROM gold.holdings WHERE folio_number = :f AND scheme_id = :s"),
                {"f": folio_number, "s": scheme_id},
            ).fetchall()
        assert len(rows) == 1  # still exactly one row
        assert float(rows[0][0]) == 20.0  # updated, not duplicated
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM gold.holdings WHERE folio_number = :f"), {"f": folio_number})


def test_load_sip_updates_existing_on_conflict():
    folio_number = f"TEST-{uuid.uuid4().hex[:8]}"
    scheme_code = f"SCH{uuid.uuid4().hex[:6]}"
    registered_date = pd.Timestamp("2026-01-01").date()
    base = {
        "rta": "CAMS", "sip_reg_no": None, "folio_number": folio_number,
        "scheme_code": scheme_code, "scheme_name": "Test Scheme", "amc_code": None,
        "isin": None, "amount": 5000.0, "frequency": "MONTHLY", "start_date": None,
        "end_date": None, "next_due_date": None, "sip_day": None, "mandate_id": None,
        "status": "ACTIVE", "registered_date": registered_date, "ceased_date": None,
        "scheme_id": None, "amc_id": None, "client_id": None, "sip_type": None,
        "registered_installments": None, "completed_installments": 3,
        "bounced_installments": 0, "ceased_reason": None, "arn_id": None,
        "arn": "ARN-1", "sub_arn": None, "created_at": pd.Timestamp.now(),
        "enrichment_pending_since": None,
    }
    df = pd.DataFrame([base])
    try:
        r1 = load_sip(df.copy())
        assert r1["status"] == "ok"

        df2 = pd.DataFrame([{**base, "status": "CEASED", "completed_installments": 4}])
        r2 = load_sip(df2)
        assert r2["status"] == "ok"

        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT status, completed_installments FROM gold.sip "
                    "WHERE rta = 'CAMS' AND folio_number = :f AND scheme_code = :s "
                    "AND registered_date = :d AND amount = 5000.0"
                ),
                {"f": folio_number, "s": scheme_code, "d": registered_date},
            ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "CEASED"
        assert rows[0][1] == 4
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM gold.sip WHERE folio_number = :f"), {"f": folio_number})


def test_load_clients_updates_existing_on_pan_conflict():
    pan = f"TST{uuid.uuid4().hex[:6].upper()}P"
    df = pd.DataFrame([{
        "pan": pan, "full_name": "Test Client", "status": "ACTIVE",
        "phone": None, "email": None, "kyc_status": None, "source": "TEST",
    }])
    try:
        r1 = load_clients(df.copy())
        assert r1["status"] == "ok"

        df2 = pd.DataFrame([{
            "pan": pan, "full_name": "Test Client Renamed", "status": "ACTIVE",
            "phone": None, "email": None, "kyc_status": None, "source": "TEST",
        }])
        r2 = load_clients(df2)
        assert r2["status"] == "ok"

        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT full_name FROM gold.clients WHERE pan = :p"), {"p": pan}
            ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "Test Client Renamed"
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM gold.clients WHERE pan = :p"), {"p": pan})

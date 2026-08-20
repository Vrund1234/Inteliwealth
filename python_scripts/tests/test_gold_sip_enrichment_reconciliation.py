import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PKG_ROOT))

import pandas as pd  # noqa: E402
from sqlalchemy import text  # noqa: E402
from utils.db import engine  # noqa: E402
from etl_gold_sip import transform_sip  # noqa: E402


def _sip_silver_row(**overrides):
    row = {
        "source": "CAMS", "zone": None, "branch": None, "ter_location": None,
        "inv_name": None, "pan": "ABCDE1234F", "folio_no": "FOLIO-1", "folio_old": None,
        "inv_iin": None, "inv_dp_id": None, "inv_client_id": None, "dp_inv_name": None,
        "scheme_code": "SCH1", "product_code": "SCH1", "scheme_name": "Test Scheme",
        "plan": None, "sub_arn_code": None, "agent_name": None, "subbroker": None,
        "euin": None, "aut_trntyp": "SIP", "payment_mode": None, "periodicity": "MONTHLY",
        "auto_amount": 1000, "no_of_installments": 12, "period_day": 5,
        "reg_date": datetime(2026, 8, 1), "from_date": None, "to_date": None,
        "cease_date": None, "pause_from_date": None, "pause_to_date": None,
        "target_scheme": None, "target_scheme_code": None, "target_scheme_name": None,
        "target_plan": None, "bank": None, "ac_holder_name": None, "ecs_account_no": None,
        "ecsno": None, "instrm_no": None, "cheq_micr_no": None, "umrn_code": None,
        "ac_type": None, "amc_code": "AMC1", "user_code": None, "package_name": None,
        "special_product": None, "subtrxndesc": None, "remarks": None, "top_up_frq": None,
        "top_up_amt": None, "top_up_perc": None, "status": "ACTIVE", "modify_flag": None,
        "scheme_folio_number": None, "request_ref_no": None, "ft_sip_regno": "SIPREG-1",
        "scheme_id": None, "created_at": datetime.now(timezone.utc), "updated_at": None,
        "flag": 0,
    }
    row.update(overrides)
    return row


def test_transform_sip_marks_enrichment_pending_when_no_transaction_or_client_match():
    df = pd.DataFrame([_sip_silver_row(pan="NOMATCH1234")])  # PAN not in gold.clients, folio not in transactions
    gold_df = transform_sip(df)
    assert "enrichment_pending_since" in gold_df.columns
    assert pd.notna(gold_df.loc[0, "enrichment_pending_since"])
    assert pd.isna(gold_df.loc[0, "arn"])
    assert pd.isna(gold_df.loc[0, "client_id"])


def test_transform_sip_no_pending_marker_when_fully_resolved():
    # gold.clients.pan is varchar(10); keep the generated value within that bound.
    pan = uuid.uuid4().hex[:10].upper()
    folio = f"FOLIO-{uuid.uuid4().hex[:8]}"
    rta = "CAMS"
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO gold.clients (user_id, pan, full_name, status, created_at) "
                "VALUES (:id, :pan, 'Test Client', 'ACTIVE', now())"
            ), {"id": str(uuid.uuid4()), "pan": pan})
            conn.execute(text(
                "INSERT INTO silver.transaction_master_new (source, pan, folio_no, brokcode, created_at) "
                "VALUES (:rta, :pan, :folio, 'ARN-9999', now())"
            ), {"rta": rta, "pan": pan, "folio": folio})

        df = pd.DataFrame([_sip_silver_row(pan=pan, folio_no=folio, source=rta)])
        gold_df = transform_sip(df)

        assert pd.isna(gold_df.loc[0, "enrichment_pending_since"])
        assert gold_df.loc[0, "arn"] == "ARN-9999"
        assert gold_df.loc[0, "client_id"] is not None
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM gold.clients WHERE pan = :pan"), {"pan": pan})
            conn.execute(text("DELETE FROM silver.transaction_master_new WHERE pan = :pan"), {"pan": pan})

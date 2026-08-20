import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PKG_ROOT))

import pandas as pd  # noqa: E402
from sqlalchemy import text  # noqa: E402
from utils.db import engine  # noqa: E402
from utils.gold_result import load_result  # noqa: E402
from etl_gold_sip import transform_sip  # noqa: E402
import gold_loader  # noqa: E402


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


def test_extract_pending_sip_retry_candidates_finds_stale_row_and_excludes_fresh_and_expired():
    from etl_gold_sip import extract_pending_sip_retry_candidates

    # .upper() matters: gold.sip.folio_number is always stored uppercase (clean_folio()),
    # and the retry query's SQL uppercases silver.sip_master_new.folio_no before comparing —
    # inserting a mixed-case folio directly into gold.sip here (bypassing that cleaning)
    # would make the join silently miss it.
    rta, folio, scheme_code, amount = "CAMS", f"FOLIO-{uuid.uuid4().hex[:8].upper()}", "SCH-PENDING", 500
    reg_date = datetime(2026, 8, 1).date()
    fresh_row = dict(rta=rta, folio_number=folio, scheme_code=scheme_code,
                      registered_date=reg_date, amount=amount,
                      sip_reg_no="X", enrichment_pending_since=datetime.now(timezone.utc))
    expired_row = dict(rta=rta, folio_number=f"{folio}-EXPIRED", scheme_code=scheme_code,
                        registered_date=reg_date, amount=amount,
                        sip_reg_no="Y", enrichment_pending_since=datetime(2000, 1, 1, tzinfo=timezone.utc))

    try:
        with engine.begin() as conn:
            for row in (fresh_row, expired_row):
                conn.execute(text(
                    "INSERT INTO gold.sip (rta, folio_number, scheme_code, registered_date, amount, "
                    "sip_reg_no, enrichment_pending_since, created_at) "
                    "VALUES (:rta, :folio_number, :scheme_code, :registered_date, :amount, "
                    ":sip_reg_no, :enrichment_pending_since, now())"
                ), row)
            conn.execute(text(
                "INSERT INTO silver.sip_master_new (source, folio_no, product_code, scheme_code, "
                "reg_date, auto_amount, ft_sip_regno, created_at) "
                "VALUES (:rta, :folio, :scheme_code, :scheme_code, :reg_date, :amount, 'X', now())"
            ), {"rta": rta, "folio": folio, "scheme_code": scheme_code,
                "reg_date": reg_date, "amount": amount})

        candidates = extract_pending_sip_retry_candidates(limit=200, max_age_days=30)

        assert (candidates["folio_no"] == folio).any()
        assert not (candidates["folio_no"] == f"{folio}-EXPIRED").any()  # aged out, excluded
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM gold.sip WHERE folio_number IN (:f1, :f2)"),
                         {"f1": folio, "f2": f"{folio}-EXPIRED"})
            conn.execute(text("DELETE FROM silver.sip_master_new WHERE folio_no = :f"), {"f": folio})


def test_extract_pending_sip_retry_candidates_dedupes_colliding_silver_rows():
    # Reproduces the live CardinalityViolation shape found in silver.sip_master_new:
    # two silver rows genuinely collide on the retry query's natural key (rta,
    # folio_number, scheme_code, registered_date, amount) -- they differ only in
    # inv_iin (part of silver's OWN separate unique index, so both rows legally
    # coexist there) and created_at. Undeduped, both would join to the same
    # gold.sip pending row and be handed to load_sip()'s single-statement
    # ON CONFLICT upsert as a duplicate-key batch, raising CardinalityViolation.
    from etl_gold_sip import extract_pending_sip_retry_candidates

    rta = "CAMS"
    folio = f"FOLIO-{uuid.uuid4().hex[:8].upper()}"
    scheme_code = "SCH-DUPKEY"
    amount = 500
    reg_date = datetime(2026, 8, 1).date()
    older = datetime.now(timezone.utc) - timedelta(days=1)
    newer = datetime.now(timezone.utc)

    try:
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO gold.sip (rta, folio_number, scheme_code, registered_date, amount, "
                "sip_reg_no, enrichment_pending_since, created_at) "
                "VALUES (:rta, :folio, :scheme_code, :reg_date, :amount, 'DUP1', now(), now())"
            ), {"rta": rta, "folio": folio, "scheme_code": scheme_code,
                "reg_date": reg_date, "amount": amount})

            for inv_iin, created_at in (("IIN-OLD", older), ("IIN-NEW", newer)):
                conn.execute(text(
                    "INSERT INTO silver.sip_master_new (source, folio_no, product_code, scheme_code, "
                    "inv_iin, reg_date, auto_amount, ft_sip_regno, created_at) "
                    "VALUES (:rta, :folio, :scheme_code, :scheme_code, :inv_iin, :reg_date, :amount, "
                    "'DUP1', :created_at)"
                ), {"rta": rta, "folio": folio, "scheme_code": scheme_code,
                    "reg_date": reg_date, "amount": amount, "inv_iin": inv_iin,
                    "created_at": created_at})

        candidates = extract_pending_sip_retry_candidates(limit=200, max_age_days=30)
        matches = candidates[candidates["folio_no"] == folio]

        assert len(matches) == 1, (
            f"expected exactly one deduped candidate for the colliding natural key, "
            f"got {len(matches)} -- this is exactly the shape that raises "
            f"CardinalityViolation inside load_sip()'s ON CONFLICT upsert"
        )
        # freshest silver row (s.created_at DESC) wins the dedup.
        assert matches.iloc[0]["inv_iin"] == "IIN-NEW"
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM gold.sip WHERE folio_number = :f"), {"f": folio})
            conn.execute(text("DELETE FROM silver.sip_master_new WHERE folio_no = :f"), {"f": folio})


def test_reconcile_pending_sip_clears_marker_once_data_arrives():
    from etl_gold_sip import reconcile_pending_sip

    pan = f"RECON{uuid.uuid4().hex[:5].upper()}"  # varchar(10): "RECON" (5) + 5 hex chars
    # .upper() for the same reason as the retry-candidates test above.
    rta, folio, scheme_code, amount = "CAMS", f"FOLIO-{uuid.uuid4().hex[:8].upper()}", "SCH-RECON", 750
    reg_date = datetime(2026, 8, 1).date()

    try:
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO gold.sip (rta, folio_number, scheme_code, registered_date, amount, "
                "sip_reg_no, enrichment_pending_since, created_at) "
                "VALUES (:rta, :folio, :scheme_code, :reg_date, :amount, 'RECON1', now(), now())"
            ), {"rta": rta, "folio": folio, "scheme_code": scheme_code,
                "reg_date": reg_date, "amount": amount})
            conn.execute(text(
                "INSERT INTO silver.sip_master_new (source, folio_no, product_code, scheme_code, "
                "reg_date, auto_amount, ft_sip_regno, pan, created_at) "
                "VALUES (:rta, :folio, :scheme_code, :scheme_code, :reg_date, :amount, 'RECON1', :pan, now())"
            ), {"rta": rta, "folio": folio, "scheme_code": scheme_code,
                "reg_date": reg_date, "amount": amount, "pan": pan})

            # The dependency arrives NOW, after the SIP row was already gold-loaded pending:
            conn.execute(text(
                "INSERT INTO gold.clients (user_id, pan, full_name, status, created_at) "
                "VALUES (:id, :pan, 'Recon Client', 'ACTIVE', now())"
            ), {"id": str(uuid.uuid4()), "pan": pan})
            conn.execute(text(
                "INSERT INTO silver.transaction_master_new (source, pan, folio_no, brokcode, created_at) "
                "VALUES (:rta, :pan, :folio, 'ARN-RECON', now())"
            ), {"rta": rta, "pan": pan, "folio": folio})

        result = reconcile_pending_sip(limit=200, max_age_days=30)
        assert result["status"] == "ok"
        assert result["rows_loaded"] >= 1

        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT arn, client_id, enrichment_pending_since FROM gold.sip "
                "WHERE folio_number = :f"
            ), {"f": folio}).fetchone()
        assert row.arn == "ARN-RECON"
        assert row.client_id is not None
        assert row.enrichment_pending_since is None
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM gold.sip WHERE folio_number = :f"), {"f": folio})
            conn.execute(text("DELETE FROM silver.sip_master_new WHERE folio_no = :f"), {"f": folio})
            conn.execute(text("DELETE FROM silver.transaction_master_new WHERE folio_no = :f"), {"f": folio})
            conn.execute(text("DELETE FROM gold.clients WHERE pan = :p"), {"p": pan})


def test_load_gold_sip_status_stays_error_when_primary_load_fails_but_reconciliation_succeeds(monkeypatch):
    # A real primary load failure (results["sip"] = load_result("error", ...)) must
    # never be silently overwritten with "ok" just because reconcile_pending_sip()
    # subsequently resolves rows pending from an EARLIER run. Drives this through
    # the actual load_gold() code path -- every OTHER section is stubbed to a fast
    # empty/no-op so only the SIP merge logic under test does real work.

    empty_df = pd.DataFrame()

    monkeypatch.setattr(gold_loader, "extract_amc", lambda: empty_df)
    monkeypatch.setattr(gold_loader, "extract_scheme", lambda: (empty_df, empty_df))
    monkeypatch.setattr(gold_loader, "extract_scheme_nav", lambda: empty_df)
    monkeypatch.setattr(gold_loader, "extract_transactions", lambda: empty_df)
    monkeypatch.setattr(gold_loader, "extract_holdings", lambda: empty_df)

    if gold_loader.CLIENT_AVAILABLE:
        monkeypatch.setattr(gold_loader, "extract_clients", lambda: empty_df)
    if gold_loader.FOLIO_AVAILABLE:
        monkeypatch.setattr(gold_loader, "extract_folio_nominees", lambda: empty_df)

    assert gold_loader.SIP_AVAILABLE, "SIP module must be available for this test to be meaningful"

    non_empty_df = pd.DataFrame([{"placeholder": 1}])

    # Primary SIP load this run: fails.
    monkeypatch.setattr(gold_loader, "extract_sip", lambda: non_empty_df)
    monkeypatch.setattr(gold_loader, "transform_sip", lambda df: non_empty_df)
    monkeypatch.setattr(
        gold_loader, "load_sip",
        lambda gold_df: load_result("error", 0, "primary SIP load boom"),
    )

    # Reconciliation of an EARLIER run's pending rows: succeeds, resolves 5 rows.
    monkeypatch.setattr(
        gold_loader, "reconcile_pending_sip",
        lambda: load_result("ok", 5),
    )

    results = gold_loader.load_gold()

    assert results["sip"]["status"] == "error", (
        "a successful reconciliation must never mask this run's primary load failure"
    )
    assert results["sip"]["rows_loaded"] == 5
    assert results["sip"]["error"] == "primary SIP load boom"


def test_transform_sip_no_pending_marker_when_matched_but_arn_blank():
    # A real transaction match exists (folio found in silver.transaction_master_new), but
    # that transaction's brokcode is blank/NULL -- a legitimate direct-plan investment with
    # no distributor. This must NOT be treated as "pending" -- a match WAS found, the ARN is
    # just genuinely blank.
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
                "VALUES (:rta, :pan, :folio, NULL, now())"
            ), {"rta": rta, "pan": pan, "folio": folio})

        df = pd.DataFrame([_sip_silver_row(pan=pan, folio_no=folio, source=rta)])
        gold_df = transform_sip(df)

        assert pd.isna(gold_df.loc[0, "enrichment_pending_since"])
        assert pd.isna(gold_df.loc[0, "arn"])
        assert gold_df.loc[0, "client_id"] is not None
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM gold.clients WHERE pan = :pan"), {"pan": pan})
            conn.execute(text("DELETE FROM silver.transaction_master_new WHERE pan = :pan"), {"pan": pan})

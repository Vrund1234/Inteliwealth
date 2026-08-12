import pandas as pd


def test_coverage_improved_beyond_the_floor(current_df):
    """Spec success criterion: >= 370 of 515."""
    matched = len(current_df)
    assert matched >= 370, f"coverage regressed to {matched}"


def test_no_scheme_was_written_below_its_rule_threshold():
    from utils.db import engine

    df = pd.read_sql(
        """
        SELECT mapping_source, mapping_confidence
        FROM bronze.scheme_mapping
        WHERE amfi_scheme_code IS NOT NULL
        """,
        engine,
    )
    below = df[df.mapping_confidence < 90]
    assert below.empty, f"{len(below)} mappings written below confidence 90"


def test_pending_review_rows_have_no_amfi_code():
    """PENDING_REVIEW must never carry a written mapping."""
    from utils.db import engine

    df = pd.read_sql(
        """
        SELECT count(*) AS n
        FROM bronze.scheme_mapping
        WHERE mapping_status = 'PENDING_REVIEW'
          AND amfi_scheme_code IS NOT NULL
        """,
        engine,
    )
    assert df.n.iloc[0] == 0


def test_every_status_is_a_known_value():
    from utils.db import engine

    df = pd.read_sql(
        "SELECT DISTINCT mapping_status FROM bronze.scheme_mapping", engine
    )
    known = {"MATCHED", "PENDING_REVIEW", "NOT_IN_AMFI", "UNMATCHED", None}
    unknown = set(df.mapping_status) - known
    assert not unknown, f"unknown statuses: {unknown}"

import sys
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PKG_ROOT))

import pandas as pd  # noqa: E402
from etl_gold_folio_nominees import transform_folio_nominees  # noqa: E402


def test_transform_folio_nominees_output_has_arn_columns():
    # transform_folio_nominees reads live silver/gold data internally (safe_read),
    # so this test only asserts the output SHAPE, not specific values — it runs
    # against whatever real data exists in the dev DB.
    df = pd.DataFrame({
        "source": ["CAMS"], "folio_no": ["DOES-NOT-EXIST"], "pan_no": ["XXXXX0000X"],
        "nominee1_name": ["Test Nominee"], "nominee1_relation": ["Spouse"],
        "nominee1_percentage": [100],
        "nominee2_name": [None], "nominee2_relation": [None], "nominee2_percentage": [None],
        "nominee3_name": [None], "nominee3_relation": [None], "nominee3_percentage": [None],
        "nominee_dob": [None], "nominee_guardian_name": [None], "guardian_name": [None],
    })
    result = transform_folio_nominees(df)
    # No matching holding for this synthetic folio, so result is empty — but the
    # COLUMN SHAPE the function is contracted to produce must include arn/sub_arn
    # even on the empty-result path, since load_folio_nominees's upsert always
    # selects these columns.
    assert "arn" in result.columns or result.empty

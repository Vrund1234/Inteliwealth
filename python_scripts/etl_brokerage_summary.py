# =====================================================
# BRONZE LOADER : BROKERAGE SUMMARY BY SCHEME
#
# Feeds bronze.brokerage_summary from the RTA brokerage
# reports:
#
#   CAMS WBR36   - current period
#   CAMS WBR36H  - historic / adjustments
#   KFINTECH     - added later, no code change needed
#
# The loader itself now lives in etl_wbr_report.py, which
# runs every WBR report from the spec in
# mapping_wbr.WBR_REPORTS. This module stays as the
# brokerage entry point so existing callers and scripts
# keep working unchanged.
#
# House rules are unchanged:
#   - every value stored as TEXT (dates as DATE)
#   - nothing updated, nothing deleted
#   - repeat rows appended with flag = 1 and never
#     travel further, because Silver reads flag = 0
# =====================================================

from etl_wbr_report import (
    process_report,

    # Re-exported so anything that reached into this
    # module for the cleaning helpers still finds them.
    clean_columns,
    normalize,
    clean_identifier_columns,
    clean_amount_columns,
    format_dates,
    apply_mapping,
    DATE_FORMATS,
    STAMPED_COLUMNS
)


REPORT_KEY = "BROKERAGE_SUMMARY"

BRONZE_TABLE = "brokerage_summary"


def process_brokerage_summary(files):
    """
    Load the brokerage reports in `files` into
    bronze.brokerage_summary.

    files : list of dicts, one per uploaded report

        {
            "df"          : DataFrame,
            "source"      : "CAMS",
            "report_type" : "WBR36"
        }

    Returns the number of rows inserted.
    """

    return process_report(
        REPORT_KEY,
        files
    )

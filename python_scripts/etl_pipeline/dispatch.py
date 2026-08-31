"""Single source of truth for what a reserved file is, and what failing
downstream layer should fail it.

Nothing here touches the database or the network, so it is cheap to test and
safe to import from anywhere in the package.
"""

# (RTA, REPORT_CODE) -> (dtype, vendor)
#
# Authority: raw_ingestion.py:682-732, which is what actually decides which
# bronze loader a file reaches. CAMS is matched there by filename SUFFIX
# ("r2.csv", "r2.dbf", ...) and KFIN by SUBSTRING ("mfsd201" in name), and the
# API normalises `filename` to "<REPORT_CODE>.<ext>" -- so WBR2.dbf ends with
# "r2.dbf" and MFSD201.dbf contains "mfsd201". Both rules hold.
#
# The abandoned 20_08_2026_testing_auto_pipeline branch had MFSD201 and
# MFSD211 swapped, which loads every KFIN transaction file into
# bronze.investor_master. Do not "fix" this table to match that one.
DISPATCH = {
    ("CAMS", "WBR2"): ("transaction", "cams"),
    ("CAMS", "WBR9"): ("investor", "cams"),
    ("CAMS", "WBR49"): ("sip", "cams"),
    ("KFIN", "MFSD201"): ("transaction", "kfin"),
    ("KFIN", "MFSD211"): ("investor", "kfin"),
    ("KFIN", "MFSD243"): ("sip", "kfin"),
    # Second-generation KFIN codes, added 2026-08-31. raw_ingestion.py routes
    # each into the SAME bucket as its first-generation sibling
    # (raw_ingestion.py:1145-1172), so each pair shares a (dtype, vendor) and
    # a bronze table. Nothing else in the pipeline changes: no new silver
    # table, no new gold entity, no new dependency-map entry.
    ("KFIN", "MFSD307"): ("transaction", "kfin"),
    ("KFIN", "MFSD311"): ("investor", "kfin"),
    ("KFIN", "MFSD313"): ("sip", "kfin"),
}

# A code absent from DISPATCH is not dropped silently the way
# raw_ingestion.py's own "Unknown file type" branch drops it: resolve()
# returns None, and the runner logs it WARNING, writes a SKIPPED row, and
# reports it FAILED/UNSUPPORTED_FORMAT so it reaches ABANDONED after three
# attempts, where a human sees it.
#
# NOTE FOR WHOEVER ADDS THE NEXT CODE: an entry here is necessary but NOT
# sufficient. The backend decides what ever reaches the queue, via
# DE_ROUTABLE_CODES in app/modules/etl_handoff/constants.py -- a code missing
# there is marked SKIPPED at enqueue and this runner never sees it at all.
# Both sides have to list it.

SILVER_TABLE_BY_DTYPE = {
    "transaction": "transaction_master_new",
    "investor": "investor_master",
    "sip": "sip_master_new",
}

DTYPE_BY_SILVER_TABLE = {table: dtype for dtype, table in SILVER_TABLE_BY_DTYPE.items()}

# Which dtypes each gold entity reads, derived from what its extract_* pulls.
# A file of dtype D is only failed by a gold entity that actually consumes D,
# so one failing entity does not fail every file in the run.
GOLD_ENTITY_DEPENDENCIES = {
    "amc": frozenset({"transaction"}),
    "scheme_nav": frozenset({"transaction"}),
    "transactions": frozenset({"transaction"}),
    "scheme": frozenset({"transaction", "investor"}),
    "holdings": frozenset({"transaction", "investor"}),
    "sip": frozenset({"sip", "transaction"}),
    "folio_nominees": frozenset({"investor"}),
    # Reads all three silver tables, so a clients failure fails every file in
    # the run. That is the widest blast radius here, and it is correct.
    "clients": frozenset({"transaction", "investor", "sip"}),
}

# The six values POST/PATCH accepts (app/modules/etl_handoff/constants.py).
DOWNLOAD_FAILED = "DOWNLOAD_FAILED"
UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
CONVERSION_FAILED = "CONVERSION_FAILED"
EXTRACT_FAILED = "EXTRACT_FAILED"
TRANSFORM_FAILED = "TRANSFORM_FAILED"
UNKNOWN = "UNKNOWN"

UNSUPPORTED_REPORT_CODE_REASON = UNSUPPORTED_FORMAT


def resolve(rta, report_code):
    """(dtype, vendor) for a reserved item, or None if it is out of scope.

    Tolerant of case and surrounding whitespace: the backend uppercases
    report_code (email_automation/detector.py:113,123) but the runner must not
    depend on that staying true.
    """
    if rta is None or report_code is None:
        return None
    key = (str(rta).strip().upper(), str(report_code).strip().upper())
    return DISPATCH.get(key)


def failed_dtypes(silver_results, gold_results):
    """The dtypes whose downstream layers reported an error this run.

    `silver_results` is load_silver()'s return value and `gold_results` is
    load_gold()'s. A file is reported FAILED exactly when its dtype is in this
    set. An entity this module has not heard of is ignored rather than raising
    -- a new gold entity must not be able to crash the reporting step, which
    runs after every file's work is already done.
    """
    failed = set()

    for table, result in (silver_results or {}).items():
        if result.get("status") == "FAILED":
            dtype = DTYPE_BY_SILVER_TABLE.get(table)
            if dtype is not None:
                failed.add(dtype)

    for entity, result in (gold_results or {}).items():
        if result.get("status") == "FAILED":
            failed |= GOLD_ENTITY_DEPENDENCIES.get(entity, frozenset())

    return failed


def failure_reason_for(exception):
    """Map an exception raised while reading a file to a contract value.

    Download failures and bronze-loader failures are classified by WHERE they
    happened, not by exception type, so the runner passes DOWNLOAD_FAILED and
    EXTRACT_FAILED explicitly rather than through here. This function covers
    only the read_file() step, where the type is the only signal available.
    """
    if isinstance(exception, ValueError) and str(exception).startswith(
        "Unsupported file format:"
    ):
        # raw_ingestion.read_file() raises exactly this string at line 530.
        return UNSUPPORTED_FORMAT
    if isinstance(exception, ValueError):
        # e.g. "Unable to decode uploaded file." -- a parse/encoding problem.
        return CONVERSION_FAILED
    return UNKNOWN

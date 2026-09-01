"""Date-wise file logs under logs/.

Deliberately NOT logging.handlers.TimedRotatingFileHandler. That handler
rotates on a timer inside a long-lived process; this one lives for the
duration of a single cron run and exits, so its midnight rotation would never
fire and the "current" file would carry no date in its name at all. Resolving
the dated filename once at startup is both simpler and the only thing that
actually produces date-wise files here.
"""

import logging
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import config

LOGGER_NAME = "etl_pipeline"
_FILENAME_PREFIX = "etl_pipeline_"
_FILENAME_RE = re.compile(r"^etl_pipeline_(\d{4})-(\d{2})-(\d{2})\.log$")
# Everything in this pipeline records time in UTC: pipeline.etl_pipeline_log's
# started_at/ended_at/created_at, and the psql sessions used to read them. Logging
# in server-local time instead made log timestamps silently non-comparable to
# SQL output -- it produced two wrong verification queries on 2026-08-31, both
# of which looked like missing data. The literal " UTC " is in the format so a
# line is unambiguous when pasted somewhere without this context.
_FORMAT = "%(asctime)s UTC %(levelname)s [run=%(run_id)s] %(message)s"


def _utc_now():
    return datetime.now(timezone.utc)


def purge_old_logs(log_dir, retention_days, today=None):
    """Delete etl_pipeline_*.log files older than `retention_days`.

    Returns the names deleted. A file whose name is not a parseable date, and
    anything not matching the prefix (cron.log in particular), is left alone.
    """
    log_dir = Path(log_dir)
    if not log_dir.is_dir():
        return []

    # UTC, to agree with the filenames setup() writes.
    today = today or _utc_now().date()
    cutoff = today - timedelta(days=retention_days)
    deleted = []
    for path in sorted(log_dir.glob(f"{_FILENAME_PREFIX}*.log")):
        match = _FILENAME_RE.match(path.name)
        if not match:
            continue
        try:
            stamp = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            continue
        if stamp < cutoff:
            path.unlink(missing_ok=True)
            deleted.append(path.name)
    return deleted


def setup(run_id, log_dir=None, retention_days=None):
    """Configure the package logger and return an adapter carrying `run_id`.

    A missing logs/ directory is created rather than being a failure -- it is
    gitignored, so a fresh clone has none.
    """
    log_dir = Path(log_dir if log_dir is not None else config.ETL_LOG_DIR)
    retention_days = (
        config.ETL_LOG_RETENTION_DAYS if retention_days is None else retention_days
    )

    log_dir.mkdir(parents=True, exist_ok=True)
    purge_old_logs(log_dir, retention_days)

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    # Never let these lines also reach the root logger: cron already captures
    # stdout/stderr into logs/cron.log, and propagating would double every line.
    logger.propagate = False
    # setup() may run more than once in a single process (tests, or an import
    # cycle); duplicated handlers would write every line twice.
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)

    # The date is resolved HERE, once, at process start -- see the module
    # docstring for why nothing rotates it later. UTC, so the filename agrees
    # with the timestamps written inside it; a local-dated filename would put
    # 02:00 IST lines into the previous UTC day's file.
    path = log_dir / f"{_FILENAME_PREFIX}{_utc_now():%Y-%m-%d}.log"
    handler = logging.FileHandler(path, encoding="utf-8")
    formatter = logging.Formatter(_FORMAT)
    # time.gmtime, not the default time.localtime, is what actually makes
    # %(asctime)s render in UTC.
    formatter.converter = time.gmtime
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    return logging.LoggerAdapter(logger, {"run_id": run_id})

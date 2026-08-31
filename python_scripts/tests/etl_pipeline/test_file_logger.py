"""Date-wise log files under logs/, resolved once at process start."""

import logging
import re
from datetime import date, datetime, timedelta, timezone

import pytest

from etl_pipeline import file_logger
from etl_pipeline.file_logger import purge_old_logs, setup


@pytest.fixture
def log_dir(tmp_path):
    return tmp_path / "logs"


@pytest.fixture(autouse=True)
def clean_handlers():
    yield
    logger = logging.getLogger("etl_pipeline")
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)


def test_setup_creates_a_missing_log_directory(log_dir):
    # A missing logs/ must not be a failure -- it is gitignored, so a fresh
    # clone has none.
    setup("run-1", log_dir=log_dir)

    assert log_dir.is_dir()


def test_the_filename_carries_todays_date(log_dir):
    log = setup("run-1", log_dir=log_dir)
    log.info("hello")

    expected = log_dir / f"etl_pipeline_{datetime.now(timezone.utc):%Y-%m-%d}.log"
    assert expected.exists()


def test_the_run_id_appears_in_every_line(log_dir):
    log = setup("abc-123", log_dir=log_dir)
    log.info("first")
    log.warning("second")

    written = (log_dir / f"etl_pipeline_{datetime.now(timezone.utc):%Y-%m-%d}.log").read_text()
    assert written.count("[run=abc-123]") == 2
    assert "INFO" in written
    assert "WARNING" in written


def test_a_second_setup_does_not_duplicate_handlers(log_dir):
    # The runner may be imported twice in one pytest session; duplicated
    # handlers would write every line twice.
    setup("run-1", log_dir=log_dir)
    log = setup("run-2", log_dir=log_dir)
    log.info("once")

    written = (log_dir / f"etl_pipeline_{datetime.now(timezone.utc):%Y-%m-%d}.log").read_text()
    assert written.count("once") == 1


def test_appending_to_an_existing_file_keeps_earlier_lines(log_dir):
    log = setup("run-1", log_dir=log_dir)
    log.info("first")
    log = setup("run-2", log_dir=log_dir)
    log.info("second")

    written = (log_dir / f"etl_pipeline_{datetime.now(timezone.utc):%Y-%m-%d}.log").read_text()
    assert "first" in written
    assert "second" in written


def test_old_logs_are_deleted(log_dir):
    log_dir.mkdir(parents=True)
    today = date(2026, 8, 31)
    stale = log_dir / "etl_pipeline_2026-07-01.log"
    fresh = log_dir / "etl_pipeline_2026-08-30.log"
    stale.write_text("old")
    fresh.write_text("new")

    deleted = purge_old_logs(log_dir, retention_days=30, today=today)

    assert deleted == ["etl_pipeline_2026-07-01.log"]
    assert not stale.exists()
    assert fresh.exists()


def test_purge_keeps_a_file_exactly_on_the_boundary(log_dir):
    log_dir.mkdir(parents=True)
    today = date(2026, 8, 31)
    boundary = log_dir / f"etl_pipeline_{today - timedelta(days=30):%Y-%m-%d}.log"
    boundary.write_text("x")

    purge_old_logs(log_dir, retention_days=30, today=today)

    assert boundary.exists()


def test_purge_ignores_unrelated_and_malformed_names(log_dir):
    log_dir.mkdir(parents=True)
    other = log_dir / "cron.log"
    weird = log_dir / "etl_pipeline_notadate.log"
    other.write_text("x")
    weird.write_text("x")

    purge_old_logs(log_dir, retention_days=1, today=date(2026, 8, 31))

    assert other.exists()
    assert weird.exists()


def test_purge_on_a_missing_directory_is_a_noop(tmp_path):
    assert purge_old_logs(tmp_path / "nope", retention_days=30) == []


def test_setup_purges_on_startup(log_dir):
    log_dir.mkdir(parents=True)
    stale = log_dir / "etl_pipeline_2000-01-01.log"
    stale.write_text("ancient")

    setup("run-1", log_dir=log_dir, retention_days=30)

    assert not stale.exists()


def test_lines_are_timestamped_in_utc(log_dir):
    # The DB session and pipeline.etl_pipeline_log are both UTC. Logging in
    # server-local time made log timestamps non-comparable to SQL output and
    # produced two wrong verification queries on 2026-08-31.
    log = setup("run-1", log_dir=log_dir)
    before = datetime.now(timezone.utc)
    log.info("hello")
    after = datetime.now(timezone.utc)

    written = (log_dir / f"etl_pipeline_{datetime.now(timezone.utc):%Y-%m-%d}.log").read_text()
    stamp = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", written).group(1)
    logged = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)

    assert before.replace(microsecond=0) <= logged <= after + timedelta(seconds=1)


def test_every_line_is_marked_utc(log_dir):
    log = setup("run-1", log_dir=log_dir)
    log.info("hello")

    written = (log_dir / f"etl_pipeline_{datetime.now(timezone.utc):%Y-%m-%d}.log").read_text()
    assert " UTC " in written


def test_the_filename_uses_the_utc_date(log_dir):
    # Must agree with the timestamps inside the file, or a log written at
    # 02:00 IST lands in a file named for the previous UTC day's lines.
    setup("run-1", log_dir=log_dir).info("x")

    assert (log_dir / f"etl_pipeline_{datetime.now(timezone.utc):%Y-%m-%d}.log").exists()

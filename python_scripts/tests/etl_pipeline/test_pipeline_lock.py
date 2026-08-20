import sys
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PKG_ROOT))

from etl_pipeline import pipeline_lock  # noqa: E402


def test_acquire_then_second_acquire_fails_until_released():
    conn1 = pipeline_lock.try_acquire()
    assert conn1 is not None

    conn2 = pipeline_lock.try_acquire()
    assert conn2 is None  # already held

    pipeline_lock.release(conn1)

    conn3 = pipeline_lock.try_acquire()
    assert conn3 is not None
    pipeline_lock.release(conn3)


def test_release_of_none_is_a_no_op():
    pipeline_lock.release(None)  # must not raise

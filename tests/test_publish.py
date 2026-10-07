"""publish() is the write-audit-publish gate (D30). These tests pin that only a
passing run publishes, that a blocked or failed run leaves serving/ exactly as
it was, that versions are write-once and the old one stays readable, and that
the swap survives a reader holding the old version's files (open issue #7)."""

import sys
from contextlib import contextmanager

import duckdb
import pytest

from pharos import publish as pub_mod
from pharos.paths import data_root
from pharos.publish import PointerLockedError, current_version, publish
from pharos.stage import stage
from test_faults import RUN2, reland, tamper


@contextmanager
def exclusive_handle(path):
    """Open path the way a locking reader does: no FILE_SHARE_DELETE, so it can't be
    replaced while open. Python's own open() allows the replace, so it can't fake a lock."""
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    GENERIC_READ, FILE_SHARE_READ, OPEN_EXISTING = 0x80000000, 0x1, 3
    handle = kernel32.CreateFileW(str(path), GENERIC_READ, FILE_SHARE_READ, None, OPEN_EXISTING, 0, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield
    finally:
        kernel32.CloseHandle(handle)


def snapshot():
    """Every path under serving/ with its bytes: 'untouched' means equal snapshots."""
    root = data_root() / "serving"
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def rows(version, table="observations"):
    return duckdb.sql(f"SELECT count(*) FROM read_parquet('{(version / f'{table}.parquet').as_posix()}')").fetchone()[0]


def test_passing_run_publishes_and_points(staged_run):
    p = publish(staged_run)
    assert p.evaluation.verdict == "passed"
    assert p.version == data_root() / "serving" / staged_run
    assert current_version() == p.version
    assert rows(p.version) == rows(data_root() / "staging" / staged_run)
    assert not any(data_root().glob("serving/.building-*"))


def test_nothing_published_before_first_publish(staged_run):
    assert current_version() is None


@pytest.mark.parametrize("fault, verdict", [
    ("SELECT * REPLACE (0.0 AS value) FROM t", "blocked"),           # C09 error
    ("SELECT * EXCLUDE (value) FROM t", "failed"),                   # breaks checks that read value
])
def test_blocked_or_failed_run_leaves_serving_untouched(staged_run, fault, verdict):
    publish(staged_run)
    before, pointed = snapshot(), current_version()
    reland(staged_run, RUN2)
    stage(RUN2)
    tamper(RUN2, "observations", fault)
    p = publish(RUN2)
    assert (p.evaluation.verdict, p.version) == (verdict, None)
    assert snapshot() == before
    assert current_version() == pointed


def test_second_publish_moves_pointer_and_keeps_old_version(staged_run):
    first = publish(staged_run).version
    first_bytes = (first / "observations.parquet").read_bytes()
    reland(staged_run, RUN2)
    stage(RUN2)
    second = publish(RUN2).version
    assert current_version() == second != first
    assert (first / "observations.parquet").read_bytes() == first_bytes   # write-once
    # the reland is a full pull, so NVDA gains a new vintage (D20): 3 rows -> 5
    assert (rows(first), rows(second)) == (3, 5) == (3, rows(data_root() / "staging" / RUN2))


@pytest.mark.skipif(sys.platform != "win32", reason="Windows file-lock semantics")
def test_reader_holding_old_version_does_not_block_swap(staged_run):
    first = publish(staged_run).version
    reland(staged_run, RUN2)
    stage(RUN2)
    with open(first / "observations.parquet", "rb"):
        assert publish(RUN2).version == current_version()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows file-lock semantics")
def test_locked_pointer_fails_loudly_and_keeps_old_pointer(staged_run, monkeypatch):
    first = publish(staged_run).version
    reland(staged_run, RUN2)
    stage(RUN2)
    monkeypatch.setattr(pub_mod, "SWAP_WAIT_SECONDS", 0)
    with exclusive_handle(data_root() / "serving" / "CURRENT"):
        with pytest.raises(PointerLockedError):
            publish(RUN2)
    assert current_version() == first
    assert not (data_root() / "serving" / "CURRENT.pending").exists()

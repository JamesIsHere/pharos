"""health.evaluate() judges what is served, now (D34). These tests pin the
roll-up colors, that gate results are read from the record (staging/ not
needed), that monitors run against the given clock, that a blocked latest run
is red even while serving is fine, and that latest.md is rewritten."""

from datetime import datetime, timezone
import shutil

import pytest

from pharos import health
from pharos.paths import data_root
from pharos.publish import publish
from test_faults import CLEAN_BASELINE, RUN2, manifest_row, record_baseline, reland, tamper
from pharos.stage import stage


def at(month, day, hour, minute=0):
    return datetime(2026, month, day, hour, minute, tzinfo=timezone.utc)


@pytest.fixture
def served(staged_run):
    """The fixture run published at 14:30 UTC on 2026-10-07, with its manifest
    and a baseline matching it (two dates are too few for the real audit)."""
    publish(staged_run)
    manifest_row(staged_run, "published", "2026-10-07 14:30:00+00")
    record_baseline(CLEAN_BASELINE)
    return staged_run


def statuses(h):
    return {r["check_id"]: r["status"] for r in h.results}


def test_green_shortly_after_publish(served):
    h = health.evaluate(at(10, 7, 15))
    assert (h.status, h.reasons) == ("green", [])
    assert h.published_run == served and h.latest_run_status == "published"
    assert set(statuses(h).values()) == {"pass"}


def test_gate_results_come_from_the_record(served):
    shutil.rmtree(data_root() / "staging" / served)   # nothing to re-run gates against
    h = health.evaluate(at(10, 7, 15))
    assert h.status == "green"
    assert all(r["context"] == "run" for r in h.results if r["gate"])
    assert all(r["context"] == "view" for r in h.results if not r["gate"])


def test_yellow_on_warnings_only(served):
    h = health.evaluate(at(10, 8, 12))                  # NVDA 1 session late, C01 at 21.5h
    assert h.status == "yellow"
    assert statuses(h)["C02"] == "warn" and statuses(h)["C01"] == "pass"


def test_yellow_until_a_baseline_is_recorded(served):
    for p in (data_root() / "health" / "baseline").glob("*.parquet"):
        p.unlink()
    h = health.evaluate(at(10, 7, 15))
    assert h.status == "yellow" and statuses(h)["C13"] == "warn"


def test_red_when_pipeline_silently_stopped(served):
    h = health.evaluate(at(10, 9, 12))                  # 45.5h since publish, 2 sessions late
    assert h.status == "red"
    assert (statuses(h)["C01"], statuses(h)["C17"]) == ("error", "error")


def test_red_when_latest_run_blocked(served):
    reland(served, RUN2)
    stage(RUN2)
    tamper(RUN2, "observations", "SELECT * REPLACE (0.0 AS value) FROM t")
    assert publish(RUN2).version is None
    manifest_row(RUN2, "blocked", None)
    h = health.evaluate(at(10, 7, 15))
    assert h.status == "red" and h.published_run == served
    assert h.reasons[0].startswith(f"latest run {RUN2} blocked")


def test_red_when_nothing_published(staged_run):
    h = health.evaluate(at(10, 7, 15))
    assert (h.status, h.reasons, h.results) == ("red", ["nothing has been published"], [])


def test_unrecorded_gate_check_is_broken(served):
    for f in (data_root() / "health" / "check_results").glob("*.parquet"):
        f.unlink()
    h = health.evaluate(at(10, 7, 15))
    assert h.status == "red"
    assert {r["status"] for r in h.results if r["gate"]} == {"broken"}


def test_latest_md_is_rewritten(served):
    health.evaluate(at(10, 7, 15))
    first = (data_root() / "health" / "latest.md").read_text(encoding="utf-8")
    assert "Status:          GREEN" in first and "\033" not in first
    for cid in ("C01", "C03", "C17"):
        assert f"| {cid} " in first
    health.evaluate(at(10, 9, 12))
    assert "Status:          RED" in (data_root() / "health" / "latest.md").read_text(encoding="utf-8")


def test_reads_results_written_before_context_column(served):
    import duckdb
    for f in (data_root() / "health" / "check_results").glob("*.parquet"):
        duckdb.execute(f"COPY (SELECT * EXCLUDE (context) FROM read_parquet('{f.as_posix()}')) "
                       f"TO '{f.as_posix()}.old' (FORMAT parquet)")
        f.unlink()
        f.with_suffix(".parquet.old").rename(f)
    h = health.evaluate(at(10, 7, 15))
    assert h.status == "green"
    assert {r["context"] for r in h.results if r["gate"]} == {"run"}

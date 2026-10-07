"""refresh() owns the run and writes its manifest (D31). These tests pin that
every ending (published, blocked, crashed) leaves exactly one manifest with the
right status, that a crash is re-raised, and that the per-dataset numbers come
from the load records and the staged data."""

import duckdb
import pytest

from pharos import refresh as refresh_mod
from pharos.paths import data_root
from pharos.publish import current_version
from pharos.runs import write_raw
from test_faults import RUN2, raw_file


def copy_raw(run_from, source, dataset, run_to, select="SELECT * EXCLUDE (run_id, loaded_at) FROM r"):
    rel = duckdb.sql(f"WITH r AS (SELECT * FROM read_parquet('{raw_file(run_from, source, dataset).as_posix()}')) "
                     + select)
    frame = rel.df()
    for column, kind in zip(rel.columns, rel.types):
        if str(kind) == "DATE":
            frame[column] = frame[column].dt.date
    write_raw(frame, source, dataset, run_to)


@pytest.fixture
def loaders(staged_run, monkeypatch):
    """Point refresh at RUN2 with loaders that re-land the fixture run's raw data."""
    monkeypatch.setattr(refresh_mod, "new_run_id", lambda: RUN2)
    monkeypatch.setattr(refresh_mod.yahoo, "load_prices", lambda run: copy_raw(staged_run, "yf", "prices", run))

    def fred(run):
        copy_raw(staged_run, "fred", "observations", run)
        copy_raw(staged_run, "fred", "series", run)
    monkeypatch.setattr(refresh_mod.fred, "load_macro", fred)
    monkeypatch.setattr(refresh_mod.tiingo, "load_prices", lambda run: copy_raw(staged_run, "tiingo", "prices", run))
    return monkeypatch


def manifest():
    path = data_root() / "health" / "run_manifest" / f"{RUN2}.parquet"
    rel = duckdb.sql(f"SELECT * FROM read_parquet('{path.as_posix()}') ORDER BY source, dataset")
    return [dict(zip(rel.columns, row)) for row in rel.fetchall()]


def test_published_run(loaders):
    run_id, pub = refresh_mod.refresh()
    assert run_id == RUN2 and current_version() == pub.version
    rows = manifest()
    assert [(r["source"], r["dataset"]) for r in rows] == [("fred", "observations"), ("fred", "series"),
                                                          ("tiingo", "prices"), ("yf", "prices")]
    assert {r["status"] for r in rows} == {"published"}
    assert all(r["published_at"] is not None and r["error"] is None for r in rows)
    assert [(r["rows_downloaded"], r["rows_landed"]) for r in rows] == [(1, 1), (1, 1), (2, 2), (2, 2)]
    assert [str(r["latest_obs_date"]) for r in rows] == ["2026-04-01", "None", "2026-10-06", "2026-10-06"]
    assert all(r["dataset_error"] is None for r in rows)
    # C01 only: nothing was published before this run (D32)
    assert (rows[0]["checks_error"], rows[0]["checks_broken"]) == (1, 0)
    assert rows[0]["started_at"] <= rows[0]["finished_at"] == rows[0]["published_at"]


def test_blocked_run(loaders, staged_run):
    loaders.setattr(refresh_mod.yahoo, "load_prices", lambda run: copy_raw(
        staged_run, "yf", "prices", run, "SELECT * EXCLUDE (run_id, loaded_at) REPLACE (0.0 AS close) FROM r"))
    run_id, pub = refresh_mod.refresh()
    assert pub.version is None and current_version() is None
    rows = manifest()
    assert {r["status"] for r in rows} == {"blocked"}
    assert all(r["published_at"] is None for r in rows)
    assert rows[0]["checks_error"] >= 1


def test_crash_writes_failed_manifest_and_reraises(loaders):
    def boom(run):
        raise ConnectionError("FRED unreachable")
    loaders.setattr(refresh_mod.fred, "load_macro", boom)
    with pytest.raises(ConnectionError):
        refresh_mod.refresh()
    rows = manifest()
    assert {r["status"] for r in rows} == {"failed"}
    assert all("FRED unreachable" in r["error"] for r in rows)
    by_ds = {(r["source"], r["dataset"]): r for r in rows}
    assert by_ds[("yf", "prices")]["rows_landed"] == 2            # landed before the crash
    assert by_ds[("fred", "observations")]["rows_landed"] is None  # never loaded
    assert all(r["latest_obs_date"] is None for r in rows)         # never staged
    assert current_version() is None


def test_tiingo_failure_still_publishes_and_health_shows_it(loaders):
    def down(run):
        raise ConnectionError("Tiingo unreachable")
    loaders.setattr(refresh_mod.tiingo, "load_prices", down)
    run_id, pub = refresh_mod.refresh()                     # no exception: the run goes on (D37)
    assert pub.version is not None and current_version() == pub.version
    by_ds = {(r["source"], r["dataset"]): r for r in manifest()}
    assert {r["status"] for r in by_ds.values()} == {"published"}
    assert "Tiingo unreachable" in by_ds[("tiingo", "prices")]["dataset_error"]
    assert by_ds[("tiingo", "prices")]["rows_landed"] is None
    assert all(r["error"] is None for r in by_ds.values())
    assert by_ds[("yf", "prices")]["dataset_error"] is None
    from datetime import datetime, timezone
    from pharos import health
    h = health.evaluate(datetime(2026, 10, 8, 15, tzinfo=timezone.utc))
    assert h.status == "red"
    assert next(r for r in h.results if r["check_id"] == "C18")["status"] == "error"


def test_reference_load_record_does_not_change_completeness(staged_run):
    # the fixture run has a Tiingo load record on top of the three required ones (D36)
    from pharos.stage import complete_runs
    assert len(list(data_root().glob("health/loads/*tiingo*"))) == 1
    assert complete_runs() == [staged_run]

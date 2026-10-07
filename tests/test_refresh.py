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
    return monkeypatch


def manifest():
    path = data_root() / "health" / "run_manifest" / f"{RUN2}.parquet"
    rel = duckdb.sql(f"SELECT * FROM read_parquet('{path.as_posix()}') ORDER BY source, dataset")
    return [dict(zip(rel.columns, row)) for row in rel.fetchall()]


def test_published_run(loaders):
    run_id, pub = refresh_mod.refresh()
    assert run_id == RUN2 and current_version() == pub.version
    rows = manifest()
    assert [(r["source"], r["dataset"]) for r in rows] == [("fred", "observations"), ("fred", "series"), ("yf", "prices")]
    assert {r["status"] for r in rows} == {"published"}
    assert all(r["published_at"] is not None and r["error"] is None for r in rows)
    assert [(r["rows_downloaded"], r["rows_landed"]) for r in rows] == [(1, 1), (1, 1), (2, 2)]
    assert [str(r["latest_obs_date"]) for r in rows] == ["2026-04-01", "None", "2026-10-06"]
    assert rows[0]["checks_error"] == rows[0]["checks_broken"] == 0
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

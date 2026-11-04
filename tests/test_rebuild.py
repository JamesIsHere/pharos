"""Run provenance and staging retention (D103). These tests pin that every run
records the commit it ran and whether the tree was dirty, that a run which
can't read its commit fails before loading anything, that a publish deletes
older staging folders and keeps newer ones, and that an old run's staging
rebuilt from raw in a throwaway root equals the version it published."""

import shutil
import subprocess
from pathlib import Path

import duckdb
import pytest

from pharos import refresh as refresh_mod
from pharos import runs
from pharos.paths import PROJECT_ROOT, data_root
from pharos.publish import publish
from pharos.rebuild import RebuildError, compare, rebuild_staging
from pharos.runs import ProvenanceError
from pharos.stage import stage
from test_faults import RUN2
from test_refresh import loaders, manifest  # noqa: F401  (fixture and helper)


def test_refresh_records_the_commit(loaders):
    refresh_mod.refresh()
    rows = manifest()
    head = subprocess.run(["git", "-C", str(PROJECT_ROOT), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()
    assert {r["git_commit"] for r in rows} == {head}
    assert {type(r["git_dirty"]) for r in rows} == {bool}


def test_a_dirty_tree_is_recorded_dirty(loaders, monkeypatch):
    real = runs._git
    monkeypatch.setattr(runs, "_git", lambda *a: " M src/x.py" if a[0] == "status" else real(*a))
    refresh_mod.refresh()
    assert {r["git_dirty"] for r in manifest()} == {True}


def test_a_clean_tree_is_recorded_clean(loaders, monkeypatch):
    real = runs._git
    monkeypatch.setattr(runs, "_git", lambda *a: "" if a[0] == "status" else real(*a))
    refresh_mod.refresh()
    assert {r["git_dirty"] for r in manifest()} == {False}


def test_an_unreadable_commit_fails_before_loading(loaders, monkeypatch):
    def broken(*a):
        raise subprocess.CalledProcessError(128, ["git", *a], stderr="fatal: not a git repository")
    monkeypatch.setattr(runs, "_git", broken)
    with pytest.raises(ProvenanceError, match="not a git repository"):
        refresh_mod.refresh()
    assert not list((data_root() / "raw").glob(f"*/*/{RUN2}.parquet"))
    rows = manifest()
    assert {r["status"] for r in rows} == {"failed"} and {r["git_commit"] for r in rows} == {None}


def test_publish_prunes_older_staging_and_keeps_newer(staged_run, loaders):
    from test_refresh import copy_raw
    newer = "20261009T140000Z"
    for run in (RUN2, newer):
        copy_raw(staged_run, "yf", "prices", run)
        copy_raw(staged_run, "fred", "observations", run)
        copy_raw(staged_run, "fred", "series", run)
        stage(run)
    staging = data_root() / "staging"
    (staging / "notes").mkdir()                      # not a run folder: never touched
    assert publish(RUN2).version is not None
    assert sorted(p.name for p in staging.iterdir()) == sorted([RUN2, newer, "notes"])


def _code_copy(tmp_path: Path) -> Path:
    """The project's code with config/ written to match conftest's pinned config,
    since the rebuild runs stage() in its own process, which reads config files."""
    code = tmp_path / "code"
    code.mkdir()
    shutil.copytree(PROJECT_ROOT / "src", code / "src")
    for f in ("pyproject.toml", "uv.lock"):
        shutil.copy2(PROJECT_ROOT / f, code / f)
    cfg = code / "config"
    cfg.mkdir()
    (cfg / "sources.yaml").write_text(
        "backfill_start: 2026-01-01\n"
        "yahoo: {required_series: [close], expected_lag_days: 0}\n"
        "fred: {series: [GDP], expected_lag_days: {GDP: 35}}\n", encoding="utf-8")
    (cfg / "watchlist.csv").write_text("ticker,yahoo_symbol,cik,trap,active_from,active_to\n"
                                       "NVDA,NVDA,,,2026-10-05,\n", encoding="utf-8")
    (cfg / "corporate_actions.csv").write_text("ticker,event_date,note\n", encoding="utf-8")
    (cfg / "expected_states.csv").write_text("check_id,key,note\n", encoding="utf-8")
    return code


def test_rebuilt_staging_equals_the_published_version(loaders, tmp_path):
    run_id, publication = refresh_mod.refresh()
    assert publication.version is not None
    shutil.rmtree(data_root() / "staging" / run_id)   # as after a later publish prunes it
    r = rebuild_staging(run_id, code_dir=_code_copy(tmp_path))
    assert r.staging.is_dir() and not r.staging.is_relative_to(data_root())
    assert sorted(p.name for p in r.staging.parent.parent.iterdir()) == ["staging"]   # copied raw removed
    assert r.differences == {"observations": (0, 0), "series_catalog": (0, 0)}


def test_compare_counts_rows_on_each_side(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    duckdb.sql(f"COPY (SELECT * FROM range(5) t(x)) TO '{(a / 't.parquet').as_posix()}' (FORMAT parquet)")
    duckdb.sql(f"COPY (SELECT * FROM range(1, 7) t(x)) TO '{(b / 't.parquet').as_posix()}' (FORMAT parquet)")
    assert compare(a, b) == {"t": (1, 2)}


def test_rebuild_refuses_a_run_without_a_recorded_commit(loaders, monkeypatch):
    monkeypatch.setattr(refresh_mod, "provenance", lambda: (None, None))
    run_id, _ = refresh_mod.refresh()
    with pytest.raises(RebuildError, match="recorded no git commit"):
        rebuild_staging(run_id)

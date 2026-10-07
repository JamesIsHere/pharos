"""The FRED loader without the network: fake API responses, shaped like the real
ones verified on 2026-10-07, stand in for FRED."""

from datetime import date

import duckdb
import pytest

from pharos import config
from pharos.loaders import fred
from pharos.loaders.fred import SchemaDriftError, normalize_observations, normalize_series


def fake_obs(rows=None, count=None) -> dict:
    rows = rows if rows is not None else [
        {"realtime_start": "1992-12-22", "realtime_end": "1996-01-18", "date": "1947-01-01", "value": "1239.5"},
        {"realtime_start": "1996-01-19", "realtime_end": "9999-12-31", "date": "1947-01-01", "value": "."},
    ]
    return {"count": len(rows) if count is None else count, "observations": rows}


def fake_series(fred_id="GDPC1", units=("Billions of 1987 Dollars", "Billions of Chained 2017 Dollars")) -> dict:
    windows = [("1991-12-04", "1996-01-18"), ("1996-01-19", "9999-12-31")]
    return {"seriess": [
        {"id": fred_id, "realtime_start": s, "realtime_end": e, "title": "Real GDP",
         "observation_start": "1947-01-01", "observation_end": "2026-04-01",
         "frequency": "Quarterly", "frequency_short": "Q", "units": u, "units_short": u,
         "seasonal_adjustment": "Seasonally Adjusted Annual Rate",
         "seasonal_adjustment_short": "SAAR", "last_updated": "2026-09-30 07:53:55-05",
         "popularity": 80, "notes": ""}
        for (s, e), u in zip(windows, units)]}


@pytest.fixture
def root(monkeypatch, tmp_path):
    monkeypatch.setenv("PHAROS_TEST_MODE", "1")
    monkeypatch.setenv("PHAROS_DATA_ROOT", str(tmp_path))
    return tmp_path


# --- normalize_observations: the shape gate ---

def test_observations_pass_with_values_verbatim():
    out = normalize_observations(fake_obs(), "GDPC1")
    assert list(out.columns) == ["fred_id", "obs_date", "realtime_start", "realtime_end", "value"]
    assert list(out["realtime_start"]) == [date(1992, 12, 22), date(1996, 1, 19)]
    assert list(out["realtime_end"]) == [date(1996, 1, 18), date(9999, 12, 31)]
    assert list(out["value"]) == ["1239.5", "."]  # "." kept: staging decides what it means


def test_truncated_response_is_drift():
    with pytest.raises(SchemaDriftError, match="truncated"):
        normalize_observations(fake_obs(count=4425), "GDPC1")


def test_missing_count_is_drift():
    resp = fake_obs()
    del resp["count"]
    with pytest.raises(SchemaDriftError, match="count"):
        normalize_observations(resp, "GDPC1")


@pytest.mark.parametrize("change", ["drop", "add"])
def test_observation_key_drift(change):
    row = {"realtime_start": "1992-12-22", "realtime_end": "9999-12-31", "date": "1947-01-01", "value": "1"}
    if change == "drop":
        del row["value"]
    else:
        row["footnote"] = ""
    with pytest.raises(SchemaDriftError, match="observation keys"):
        normalize_observations(fake_obs([row]), "GDPC1")


def test_empty_observations_contribute_nothing():
    assert normalize_observations(fake_obs([]), "GDPC1").empty


# --- normalize_series: the metadata history ---

def test_series_history_keeps_every_units_regime():
    out = normalize_series(fake_series(), "GDPC1")
    assert list(out["units"]) == ["Billions of 1987 Dollars", "Billions of Chained 2017 Dollars"]
    assert list(out["realtime_start"]) == [date(1991, 12, 4), date(1996, 1, 19)]
    assert out.columns[0] == "fred_id" and "id" not in out.columns


def test_series_key_drift():
    resp = fake_series()
    del resp["seriess"][1]["units"]
    with pytest.raises(SchemaDriftError, match="series keys"):
        normalize_series(resp, "GDPC1")


def test_series_with_no_metadata_is_drift():
    with pytest.raises(SchemaDriftError, match="no metadata"):
        normalize_series({"seriess": []}, "GDPC1")


def test_series_for_wrong_id_is_drift():
    with pytest.raises(SchemaDriftError, match="metadata is for GDP"):
        normalize_series(fake_series(fred_id="GDP"), "GDPC1")


# --- load_macro: the run ---

def test_load_macro_writes_both_files(root, monkeypatch):
    monkeypatch.setattr(config, "sources", lambda: {"fred": {"series": ["GDP", "GDPC1"]}})
    monkeypatch.setattr(fred, "fetch_observations", lambda fid: fake_obs())
    monkeypatch.setattr(fred, "fetch_series", lambda fid: fake_series(fred_id=fid))

    obs_path, series_path = fred.load_macro("20260101T000000Z")

    assert obs_path == root / "raw" / "fred" / "observations" / "20260101T000000Z.parquet"
    assert series_path == root / "raw" / "fred" / "series" / "20260101T000000Z.parquet"
    counts = duckdb.sql(
        f"SELECT fred_id, count(*) FROM read_parquet('{obs_path.as_posix()}') GROUP BY 1 ORDER BY 1"
    ).fetchall()
    assert counts == [("GDP", 2), ("GDPC1", 2)]
    types = dict(duckdb.sql(
        f"SELECT column_name, column_type FROM (DESCRIBE SELECT * FROM read_parquet('{obs_path.as_posix()}'))"
    ).fetchall())
    assert types["obs_date"] == "DATE" and types["realtime_start"] == "DATE"
    assert types["value"] == "VARCHAR"


def test_drift_in_any_series_writes_nothing(root, monkeypatch):
    monkeypatch.setattr(config, "sources", lambda: {"fred": {"series": ["GDP", "GDPC1"]}})
    monkeypatch.setattr(fred, "fetch_observations",
                        lambda fid: fake_obs() if fid == "GDP" else fake_obs(count=999))
    monkeypatch.setattr(fred, "fetch_series", lambda fid: fake_series(fred_id=fid))
    with pytest.raises(SchemaDriftError):
        fred.load_macro("20260101T000000Z")
    assert not (root / "raw").exists()
    assert not (root / "health").exists()


def test_missing_api_key_fails_loudly(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="FRED_API_KEY is not set"):
        fred.fetch_observations("GDP")

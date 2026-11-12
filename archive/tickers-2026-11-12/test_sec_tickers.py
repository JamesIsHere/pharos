"""SEC ticker file loader (sec_tickers.py), with sec.fetch swapped for a fake."""

import json
from datetime import datetime, timezone

import duckdb
import pytest

from pharos.loaders import sec, sec_tickers
from pharos.loaders.sec_tickers import FIELDS, SchemaDriftError, load_tickers
from pharos.paths import data_root

STAMP = datetime(2026, 11, 4, 14, 21, 12, tzinfo=timezone.utc)
DATA = [[1045810, "NVIDIA CORP", "NVDA", "Nasdaq"], [320193, "Apple Inc.", "AAPL", "Nasdaq"],
        [1234567, "SOME CO", "SOMW", None]]


@pytest.fixture
def root(monkeypatch, tmp_path):
    monkeypatch.setenv("PHAROS_TEST_MODE", "1")
    monkeypatch.setenv("PHAROS_DATA_ROOT", str(tmp_path))
    return tmp_path


@pytest.fixture
def serve(monkeypatch, root):
    def setup(doc):
        body = json.dumps(doc).encode()
        monkeypatch.setattr(sec, "fetch", lambda url, to=None: sec.Fetched(url, body, STAMP))
    return setup


def test_lands_rows_as_sent(serve):
    serve({"fields": FIELDS, "data": DATA})
    assert load_tickers("20261105T073000Z") == 3
    p = data_root() / "raw" / "sec" / "tickers" / "20261105T073000Z.parquet"
    rel = duckdb.sql(f"SELECT cik, name, ticker, exchange FROM read_parquet('{p.as_posix()}') ORDER BY cik")
    assert dict(zip(rel.columns, rel.types))["cik"] == "BIGINT"
    assert rel.fetchall() == [(320193, "Apple Inc.", "AAPL", "Nasdaq"), (1045810, "NVIDIA CORP", "NVDA", "Nasdaq"),
                              (1234567, "SOME CO", "SOMW", None)]
    rec = data_root() / "health" / "loads" / "20261105T073000Z__sec__tickers.parquet"
    assert duckdb.sql(f"SELECT rows_written, source_count, last_modified FROM read_parquet('{rec.as_posix()}')"
                      ).fetchone() == (3, 3, STAMP)


def test_exchange_all_null_is_still_text(serve):
    serve({"fields": FIELDS, "data": [[1, "A", "A", None]]})
    load_tickers("20261105T073000Z")
    p = data_root() / "raw" / "sec" / "tickers" / "20261105T073000Z.parquet"
    kind = duckdb.sql(f"SELECT typeof(exchange) FROM read_parquet('{p.as_posix()}')").fetchone()[0]
    assert kind == "VARCHAR"


@pytest.mark.parametrize("doc, match", [
    ({"fields": FIELDS, "data": DATA, "extra": 1}, "top-level keys"),
    ({"fields": ["cik", "name", "ticker"], "data": DATA}, "fields"),
    ({"fields": FIELDS, "data": [[1, "A", "A"]]}, "data row 0"),
    ({"fields": FIELDS, "data": [["0000000001", "A", "A", "NYSE"]]}, "data row 0"),
    ({"fields": FIELDS, "data": [[1, "A", None, "NYSE"]]}, "data row 0"),
    ([1, 2], "top-level keys"),
])
def test_drift_stops_the_load(serve, doc, match):
    serve(doc)
    with pytest.raises(SchemaDriftError, match=match):
        load_tickers("20261105T073000Z")
    assert not (data_root() / "raw").exists() and not (data_root() / "health").exists()

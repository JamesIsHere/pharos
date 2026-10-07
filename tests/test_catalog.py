"""catalog.rebuild() regenerates catalog.duckdb. These tests pin that it holds
views only for layers that exist, that the views work from any working
directory, and that a catalog held open elsewhere fails loudly and survives."""

import duckdb
import pandas as pd
import pytest

from pharos import catalog
from pharos.catalog import CatalogLockedError
from pharos.runs import write_raw


@pytest.fixture
def root(monkeypatch, tmp_path):
    monkeypatch.setenv("PHAROS_TEST_MODE", "1")
    monkeypatch.setenv("PHAROS_DATA_ROOT", str(tmp_path))
    return tmp_path


def view_counts(path) -> dict:
    con = duckdb.connect(str(path), read_only=True)
    names = [r[0] for r in con.execute("SELECT view_name FROM duckdb_views() WHERE NOT internal").fetchall()]
    counts = {n: con.execute(f"SELECT count(*) FROM {n}").fetchone()[0] for n in names}
    tables = con.execute("SELECT count(*) FROM duckdb_tables()").fetchone()[0]
    con.close()
    return counts, tables


def test_views_only_for_existing_layers_and_no_tables(root):
    write_raw(pd.DataFrame({"ticker": ["NVDA", "AAPL"]}), "yf", "prices", "20260101T000000Z")
    counts, tables = view_counts(catalog.rebuild())
    assert counts == {"raw_yf_prices": 2, "health_loads": 1}
    assert tables == 0


def test_views_work_from_another_directory(root, tmp_path_factory, monkeypatch):
    write_raw(pd.DataFrame({"ticker": ["NVDA"]}), "yf", "prices", "20260101T000000Z")
    path = catalog.rebuild()
    monkeypatch.chdir(tmp_path_factory.mktemp("elsewhere"))
    assert view_counts(path)[0]["raw_yf_prices"] == 1


def test_open_catalog_fails_loudly_and_survives(root):
    write_raw(pd.DataFrame({"ticker": ["NVDA"]}), "yf", "prices", "20260101T000000Z")
    path = catalog.rebuild()
    write_raw(pd.DataFrame({"ticker": ["NVDA"]}), "fred", "series", "20260101T000000Z")
    holder = duckdb.connect(str(path), read_only=True)  # stands in for DBeaver
    try:
        with pytest.raises(CatalogLockedError, match="open in another program"):
            catalog.rebuild()
    finally:
        holder.close()
    assert "raw_fred_series" not in view_counts(path)[0]  # old catalog intact
    assert not path.with_suffix(".building").exists()

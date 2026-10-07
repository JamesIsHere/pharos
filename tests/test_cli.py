"""`pharos load` without the network: both loaders faked, the real write_raw()."""

import pandas as pd
import pytest

from pharos import cli
from pharos.loaders import fred, yahoo
from pharos.runs import write_raw


@pytest.fixture
def root(monkeypatch, tmp_path):
    monkeypatch.setenv("PHAROS_TEST_MODE", "1")
    monkeypatch.setenv("PHAROS_DATA_ROOT", str(tmp_path))
    return tmp_path


def test_load_gives_every_file_one_run_id_and_reports_from_disk(root, monkeypatch, capsys):
    seen = []

    def fake_prices(run_id):
        seen.append(run_id)
        return write_raw(pd.DataFrame({"x": [1, 2]}), "yf", "prices", run_id)

    def fake_macro(run_id):
        seen.append(run_id)
        return (write_raw(pd.DataFrame({"x": [1, 2, 3]}), "fred", "observations", run_id),
                write_raw(pd.DataFrame({"x": [1]}), "fred", "series", run_id))

    monkeypatch.setattr(yahoo, "load_prices", fake_prices)
    monkeypatch.setattr(fred, "load_macro", fake_macro)

    assert cli.main(["load"]) == 0
    assert len(set(seen)) == 1
    run_id = seen[0]
    assert sorted(p.name for p in (root / "raw").rglob("*.parquet")) == [f"{run_id}.parquet"] * 3

    out = capsys.readouterr().out
    assert f"run {run_id}" in out
    assert "fred/observations" in out and "fred/series" in out and "yf/prices" in out
    assert out.count("written") == 3

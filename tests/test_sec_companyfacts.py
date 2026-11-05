"""companyfacts.zip loader (sec_companyfacts.py), with sec.fetch swapped for a
local zip built per test."""

import json
import zipfile
from datetime import datetime, timezone

import duckdb
import pytest

from pharos.loaders import sec
from pharos.loaders.sec_companyfacts import SchemaDriftError, load_companyfacts
from pharos.paths import data_root

STAMP = datetime(2026, 11, 4, 9, 0, tzinfo=timezone.utc)
RUN = "20261105T090000Z"


def fact(**over):
    f = {"end": "2025-12-31", "val": 100, "accn": "0000320193-26-000001", "fy": 2025, "fp": "FY",
         "form": "10-K", "filed": "2026-01-30"}
    f.update(over)
    return {k: v for k, v in f.items() if v is not ...}


def company(cik=320193, name="Apple Inc.", facts=None, cik_value=...):
    doc = {"cik": cik if cik_value is ... else cik_value, "entityName": name,
           "facts": facts if facts is not None else {
               "us-gaap": {"Assets": {"label": "Assets", "description": "Total assets.",
                                      "units": {"USD": [fact(), fact(val=2**60, frame="CY2025Q4I")]}}},
               "dei": {"EntityCommonStockSharesOutstanding": {
                   "label": None, "description": None,
                   "units": {"shares": [fact(val=1.5, start="2025-01-01", fy=None, fp=None)]}}}}}
    if cik_value is None:
        del doc["cik"]
    return doc


@pytest.fixture
def root(monkeypatch, tmp_path):
    monkeypatch.setenv("PHAROS_TEST_MODE", "1")
    (tmp_path / "data").mkdir()
    monkeypatch.setenv("PHAROS_DATA_ROOT", str(tmp_path / "data"))
    return tmp_path


@pytest.fixture
def serve(monkeypatch, root):
    def setup(members: dict):
        z = root / "src.zip"
        with zipfile.ZipFile(z, "w") as out:
            for name, doc in members.items():
                out.writestr(name, doc if isinstance(doc, str) else json.dumps(doc))

        def fetch(url, to=None):
            to.write_bytes(z.read_bytes())
            return sec.Fetched(url, b"", STAMP, path=to)
        monkeypatch.setattr(sec, "fetch", fetch)
    return setup


def raw(dataset):
    p = data_root() / "raw" / "sec" / dataset / f"{RUN}.parquet"
    return duckdb.sql(f"SELECT * EXCLUDE (run_id, loaded_at) FROM read_parquet('{p.as_posix()}') ORDER BY ALL")


def test_lands_facts_and_concepts_as_sent(serve):
    serve({"CIK0000320193.json": company(), "CIK0000000001.json": {}})
    assert load_companyfacts(RUN) == {"members": 2, "facts": 3, "concepts": 2}
    facts = raw("companyfacts")
    assert dict(zip(facts.columns, facts.types))["val"] == "VARCHAR"
    rows = {r[facts.columns.index("val")]: dict(zip(facts.columns, r)) for r in facts.fetchall()}
    assert set(rows) == {"100", str(2**60), "1.5"}             # beyond 2^53, digit for digit
    assert rows["1.5"]["start"] == "2025-01-01" and rows["1.5"]["fy"] is None and rows["1.5"]["fp"] is None
    assert rows["100"]["start"] is None and rows["100"]["frame"] is None
    assert rows[str(2**60)]["frame"] == "CY2025Q4I"
    assert raw("companyfacts_concepts").fetchall() == [
        (320193, "Apple Inc.", "dei", "EntityCommonStockSharesOutstanding", None, None),
        (320193, "Apple Inc.", "us-gaap", "Assets", "Assets", "Total assets.")]
    rec = data_root() / "health" / "loads" / f"{RUN}__sec__companyfacts.parquet"
    assert duckdb.sql(f"SELECT rows_written, source_count, last_modified FROM read_parquet('{rec.as_posix()}')"
                      ).fetchone() == (3, 2, STAMP)
    assert not (data_root() / "tmp").exists()                  # the zip and batches are gone


def test_cik_from_the_file_name_when_sent_as_text_or_missing(serve):
    serve({"CIK0000000002.json": company(cik_value="0000000002"), "CIK0000000003.json": company(cik_value=None)})
    load_companyfacts(RUN)
    assert {r[0] for r in raw("companyfacts_concepts").fetchall()} == {2, 3}


@pytest.mark.parametrize("members, match", [
    ({"CIK0000320193.json": company(), "readme.txt": "x"}, "unexpected member"),
    ({"CIK0000320193.json": company(cik=999)}, "differs from the file name"),
    ({"CIK0000320193.json": company() | {"extra": 1}}, "top-level keys"),
    ({"CIK0000320193.json": company(facts={"us-gaap": {"A": {"label": "A", "units": {}}}})}, "concept keys"),
    ({"CIK0000320193.json": company(facts={"us-gaap": {"A": {"label": "A", "description": None,
                                                             "units": {"USD": [fact(accn=...)]}}}})}, "missing"),
    ({"CIK0000320193.json": company(facts={"us-gaap": {"A": {"label": "A", "description": None,
                                                             "units": {"USD": [fact(extra=1)]}}}})}, "unexpected"),
    ({"CIK0000320193.json": company(facts={"us-gaap": {"A": {"label": "A", "description": None,
                                                             "units": {"USD": [fact(val="100")]}}}})}, "val"),
    ({"CIK0000320193.json": company(facts={"us-gaap": {"A": {"label": "A", "description": None,
                                                             "units": {"USD": [fact(val=True)]}}}})}, "val"),
    ({"CIK0000320193.json": company(facts={"us-gaap": {"A": {"label": "A", "description": None,
                                                             "units": {"USD": [fact(fy="2025")]}}}})}, "fy"),
])
def test_drift_stops_the_load_before_anything_is_written(serve, members, match):
    serve(members)
    with pytest.raises(SchemaDriftError, match=match):
        load_companyfacts(RUN)
    assert not (data_root() / "raw").exists()
    assert not (data_root() / "tmp").exists()

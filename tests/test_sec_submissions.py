"""submissions.zip loader (sec_submissions.py) on synthetic zips built in the
2026-11-04 shape; sec.fetch is swapped for one that copies the zip into place."""

import json
import shutil
import zipfile
from datetime import datetime, timezone

import duckdb
import pytest

from pharos.loaders import sec, sec_submissions as subs
from pharos.loaders.sec_submissions import SchemaDriftError, load_submissions
from pharos.paths import data_root

STAMP = datetime(2026, 11, 4, 6, 21, tzinfo=timezone.utc)


def filings(*rows):
    """rows: (accession, filing_date, form) -> the recent/overflow arrays."""
    out = {k: [] for k in subs.FILING_KEYS}
    for acc, day, form in rows:
        for k in out:
            out[k].append({"accessionNumber": acc, "filingDate": day, "form": form,
                           "size": 100, "isXBRL": 1, "isInlineXBRL": 1, "isXBRLNumeric": 0}.get(k, ""))
    return out


def main_record(cik, recent, files=(), **over):
    d = {"cik": f"{cik:010d}", "entityType": "operating", "sic": "3571", "sicDescription": "Computers",
         "ownerOrg": None, "insiderTransactionForOwnerExists": 0, "insiderTransactionForIssuerExists": 1,
         "name": f"CO {cik}", "tickers": ["AAA"], "exchanges": ["Nasdaq"], "ein": None, "lei": None,
         "description": "", "website": "", "investorWebsite": "", "category": "", "fiscalYearEnd": "1231",
         "stateOfIncorporation": "DE", "stateOfIncorporationDescription": "DE",
         "addresses": {"business": {"city": "X"}}, "phone": None, "flags": "",
         "formerNames": [{"name": "OLD", "from": "2001-01-01T00:00:00.000Z", "to": "2002-01-01T00:00:00.000Z"}],
         "filings": {"recent": recent, "files": list(files)}}
    d.update(over)
    return d


def build_zip(path, members):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, body in members.items():
            z.writestr(name, body if isinstance(body, str) else json.dumps(body))


@pytest.fixture
def root(monkeypatch, tmp_path):
    monkeypatch.setenv("PHAROS_TEST_MODE", "1")
    monkeypatch.setenv("PHAROS_DATA_ROOT", str(tmp_path / "data"))
    (tmp_path / "data").mkdir()
    return tmp_path


@pytest.fixture
def serve(monkeypatch, root):
    """serve(members) makes sec.fetch hand back a zip of those members."""
    def setup(members):
        source = root / "source.zip"
        build_zip(source, members)

        def fetch(url, to=None):
            assert url == subs.URL and to is not None
            shutil.copy(source, to)
            return sec.Fetched(url, b"", STAMP, path=to)
        monkeypatch.setattr(sec, "fetch", fetch)
    return setup


def raw(dataset, run_id):
    p = data_root() / "raw" / "sec" / dataset / f"{run_id}.parquet"
    return duckdb.sql(f"SELECT * FROM read_parquet('{p.as_posix()}')")


def record(dataset, run_id):
    p = data_root() / "health" / "loads" / f"{run_id}__sec__{dataset}.parquet"
    rel = duckdb.sql(f"SELECT * FROM read_parquet('{p.as_posix()}')")
    return dict(zip(rel.columns, rel.fetchone()))


APPLE = main_record(320193, filings(("0000320193-26-000026", "2026-11-03", "10-K"),
                                    ("0000320193-26-000024", "2026-11-02", "8-K")),
                    files=[{"name": "CIK0000320193-submissions-001.json", "filingCount": 1,
                            "filingFrom": "1994-01-26", "filingTo": "1994-01-26"}])
APPLE_OLD = filings(("0000320193-94-000001", "1994-01-26", "10-K"))
OTHER = main_record(1000177, filings(("0000919574-26-006682", "2026-10-06", "4")))


def standard():
    return {"CIK0000320193.json": APPLE, "CIK0000320193-submissions-001.json": APPLE_OLD,
            "CIK0001000177.json": OTHER, "placeholder.txt": "Placeholder file"}


def test_first_run_lands_details_and_every_filing(serve):
    serve(standard())
    got = load_submissions("20261105T073000Z")
    assert got == {"details": 2, "filings": 4, "cutoff": None}
    d = raw("submission_details", "20261105T073000Z")
    row = dict(zip(d.columns, d.order("cik").fetchone()))
    assert (row["cik"], row["name"], row["tickers"], row["exchanges"], row["owner_org"]) == (
        320193, "CO 320193", ["AAA"], ["Nasdaq"], None)
    assert json.loads(row["filing_files"])[0]["filingCount"] == 1
    assert json.loads(row["former_names"])[0]["name"] == "OLD"
    f = raw("submission_filings", "20261105T073000Z")
    assert f.order("filing_date").project("cik, accession_number, filing_date, form, is_xbrl, source_file").fetchall() == [
        (320193, "0000320193-94-000001", "1994-01-26", "10-K", 1, "CIK0000320193-submissions-001.json"),
        (1000177, "0000919574-26-006682", "2026-10-06", "4", 1, "CIK0001000177.json"),
        (320193, "0000320193-26-000024", "2026-11-02", "8-K", 1, "CIK0000320193.json"),
        (320193, "0000320193-26-000026", "2026-11-03", "10-K", 1, "CIK0000320193.json")]


def test_load_records_carry_counts_and_last_modified(serve):
    serve(standard())
    load_submissions("20261105T073000Z")
    d, f = record("submission_details", "20261105T073000Z"), record("submission_filings", "20261105T073000Z")
    assert (d["rows_written"], d["source_count"], d["last_modified"]) == (2, 2, STAMP)
    assert (f["rows_written"], f["source_count"], f["note"]) == (4, None, None)


def test_later_run_lands_only_past_the_watermark_minus_overlap(serve):
    serve(standard())
    load_submissions("20261105T073000Z")            # latest filing 2026-11-03
    got = load_submissions("20261106T073000Z")      # overlap 10 days: from 2026-10-24
    assert got == {"details": 2, "filings": 2, "cutoff": "2026-10-24"}
    assert sorted(r[0] for r in raw("submission_filings", "20261106T073000Z").project("filing_date").fetchall()) == [
        "2026-11-02", "2026-11-03"]
    assert record("submission_filings", "20261106T073000Z")["note"] == \
        "filings dated 2026-10-24 or later (watermark minus 10 days)"
    assert raw("submission_details", "20261106T073000Z").aggregate("count(*)").fetchone() == (2,)   # details every run


def test_temp_folder_and_zip_are_removed(serve):
    serve(standard())
    load_submissions("20261105T073000Z")
    assert not (data_root() / "tmp").exists()


@pytest.mark.parametrize("change, match", [
    (lambda m: m.update({"readme.txt": "x"}), "unexpected member 'readme.txt'"),
    (lambda m: m["CIK0000320193.json"].pop("lei"), "top-level keys differ: missing \\['lei'\\]"),
    (lambda m: m["CIK0000320193.json"].update(newField=1), "unexpected \\['newField'\\]"),
    (lambda m: m["CIK0000320193.json"]["filings"]["recent"].pop("items"), "filing keys differ"),
    (lambda m: m["CIK0000320193-submissions-001.json"].update(extra=[]), "filing keys differ"),
    (lambda m: m["CIK0000320193.json"]["filings"]["recent"]["form"].append("10-Q"), "different lengths"),
    (lambda m: m["CIK0000320193.json"].update(cik="0000000001"), "differs from the file name"),
])
def test_drift_stops_the_load(serve, change, match):
    members = json.loads(json.dumps(standard()))   # deep copy
    change(members)
    serve(members)
    with pytest.raises(SchemaDriftError, match=match):
        load_submissions("20261105T073000Z")
    assert not (data_root() / "raw").exists() and not (data_root() / "health").exists()
    assert not (data_root() / "tmp").exists()


def test_a_value_of_the_wrong_type_stops_the_load(serve):
    members = json.loads(json.dumps(standard()))
    members["CIK0000320193.json"]["filings"]["recent"]["size"][0] = "big"
    serve(members)
    with pytest.raises(Exception, match="big|string|str"):
        load_submissions("20261105T073000Z")
    assert not (data_root() / "raw").exists()


def test_batches_are_written_and_read_back_whole(serve, monkeypatch):
    monkeypatch.setattr(subs, "BATCH", 1)   # one filing per temporary file
    serve(standard())
    assert load_submissions("20261105T073000Z")["filings"] == 4
    assert record("submission_filings", "20261105T073000Z")["rows_written"] == 4

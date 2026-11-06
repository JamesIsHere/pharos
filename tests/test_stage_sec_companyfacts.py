"""companyfacts staging (stage_sec.FACTS, CONCEPTS) and the first-landing fast
path, on raw landings in the loader's shape written through write_raw."""

from datetime import date, datetime, timezone

import duckdb
import pandas as pd
import pytest

from pharos.runs import write_raw
from pharos.stage import StageError
from pharos.parquet import scan
from pharos.stage_sec import (CONCEPTS, FACTS, Base, bind_sessions, connect,
                              stage_partitioned_table, stage_table)

R1, R2 = "20261105T090000Z", "20261106T090000Z"
STAMP = datetime(2026, 11, 4, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def root(monkeypatch, tmp_path):
    monkeypatch.setenv("PHAROS_TEST_MODE", "1")
    monkeypatch.setenv("PHAROS_DATA_ROOT", str(tmp_path))
    return tmp_path


def fact(concept="Assets", val="100", start=None, end="2025-12-31", accn="0000320193-26-000001",
         cik=320193,
         filed="2026-01-30", frame=None):
    return {"cik": cik, "taxonomy": "us-gaap", "concept": concept, "unit": "USD", "start": start,
            "end": end, "val": val, "accn": accn, "fy": 2025, "fp": "FY", "form": "10-K", "filed": filed,
            "frame": frame}


def concept(name="Assets", label="Assets"):
    return {"cik": 320193, "entity_name": "Apple Inc.", "taxonomy": "us-gaap", "concept": name,
            "label": label, "description": None}


def land(dataset, run_id, rows):
    frame = pd.DataFrame(rows)
    for c in frame.columns:
        if c not in ("cik", "fy"):
            frame[c] = frame[c].astype("string")   # every text column stays text when all-null
    write_raw(frame, "sec", dataset, run_id, last_modified=STAMP)


def land_changes(run_id, rows):
    frame = pd.DataFrame(rows, columns=["cik", "document_found", "mode"])
    write_raw(frame, "sec", "companyfacts_changes", run_id, last_modified=STAMP)


def stage(ds, runs, target=None):
    con = connect()
    if ds is FACTS:
        bind_sessions(con)
    merged = stage_table(con, ds, runs, None, target)
    return con, merged


def table(con, name):
    return con.execute(f"SELECT * FROM {name} ORDER BY ALL").fetchall()


FACTS_ROWS = [fact(), fact(concept="Liabilities", val=str(2**60), frame="CY2025Q4I"),
              fact(concept="Revenues", val="1.5", start="2025-01-01", end="2025-12-31")]


def test_fast_path_writes_the_same_table_as_the_merge(root, tmp_path):
    land("companyfacts", R1, FACTS_ROWS)
    out = tmp_path / "fast"
    out.mkdir()
    fast_con, fast = stage(FACTS, [R1], out)
    merge_con, _ = stage(FACTS, [R1])
    assert (out / "company_facts.parquet").exists() and fast.runs_read == (R1,)
    assert table(fast_con, "company_facts") == table(merge_con, "company_facts")


def test_fact_casts_and_dates(root, tmp_path):
    land("companyfacts", R1, FACTS_ROWS)
    con, _ = stage(FACTS, [R1], tmp_path)
    got = con.execute("SELECT concept, start_date, end_date, val, val_sent, filed, obs_date, available_date, "
                      "vintage FROM company_facts ORDER BY val").fetchall()
    # filed Friday 2026-01-30: available the next XNYS session, Monday 2026-02-02 (D99)
    common = (date(2025, 12, 31), date(2026, 1, 30), date(2025, 12, 31), date(2026, 2, 2), date(2026, 1, 30))
    assert got[0] == ("Revenues", date(2025, 1, 1), common[0], 1.5, "1.5") + common[1:]
    assert got[1] == ("Assets", None, common[0], 100.0, "100") + common[1:]
    assert got[2][0] == "Liabilities"
    assert got[2][4] == str(2**60)                       # the text keeps every digit


def test_fast_path_stops_on_a_repeated_key(root, tmp_path):
    land("companyfacts", R1, [fact(), fact(val="200")])
    with pytest.raises(StageError, match="not unique"):
        stage(FACTS, [R1], tmp_path)


def test_two_landings_take_the_merge(root, tmp_path):
    land("companyfacts", R1, [fact()])
    land("companyfacts", R2, [fact(val="101")])
    con, merged = stage(FACTS, [R1, R2], tmp_path)
    assert merged.runs_read == (R1, R2)
    assert con.execute("SELECT val, run_id FROM company_facts").fetchall() == [(101.0, R2)]
    on_disk = duckdb.sql(f"SELECT val FROM read_parquet('{(tmp_path / 'company_facts.parquet').as_posix()}')")
    assert on_disk.fetchall() == [(101.0,)]


def test_concepts_stage_on_the_fast_path(root, tmp_path):
    land("companyfacts_concepts", R1, [concept(), concept("Revenues", None)])
    con, merged = stage(CONCEPTS, [R1], tmp_path)
    assert merged.runs_read == (R1,)
    assert con.execute("SELECT concept, label, obs_date FROM company_concepts ORDER BY 1").fetchall() == [
        ("Assets", "Assets", date(2026, 11, 5)), ("Revenues", None, date(2026, 11, 5))]


def test_partitioned_stage_replaces_only_touched_cik_and_reuses_the_rest(root, tmp_path):
    land("companyfacts", R1, [fact(cik=1), fact(cik=1, concept="Liabilities"), fact(cik=2)])
    land_changes(R1, [{"cik": 1, "document_found": True, "mode": "full"},
                      {"cik": 2, "document_found": True, "mode": "full"}])
    first = tmp_path / "first"
    first.mkdir()
    stage_partitioned_table(FACTS, [R1], None, first)
    untouched_before = next((first / "company_facts.parquet" / "cik=2").glob("*.parquet"))

    # A per-CIK document is a complete replacement: Liabilities disappears,
    # Assets changes, and CIK 2 is physically reused.
    land("companyfacts", R2, [fact(cik=1, val="101")])
    land_changes(R2, [{"cik": 1, "document_found": True, "mode": "incremental"}])
    second = tmp_path / "second"
    second.mkdir()
    stage_partitioned_table(FACTS, [R1, R2], Base(R1, first), second)

    table = second / "company_facts.parquet"
    rows = duckdb.sql(f"SELECT cik, concept, val, run_id FROM {scan(table)} ORDER BY cik, concept").fetchall()
    assert rows == [(1, "Assets", 101.0, R2), (2, "Assets", 100.0, R1)]
    untouched_after = next((table / "cik=2").glob("*.parquet"))
    assert untouched_before.samefile(untouched_after)


def test_empty_document_deletes_the_old_partition(root, tmp_path):
    land("companyfacts", R1, [fact(cik=1), fact(cik=2)])
    land_changes(R1, [{"cik": 1, "document_found": True, "mode": "full"},
                      {"cik": 2, "document_found": True, "mode": "full"}])
    first = tmp_path / "first"
    first.mkdir()
    stage_partitioned_table(FACTS, [R1], None, first)

    land_changes(R2, [{"cik": 1, "document_found": False, "mode": "incremental"}])
    second = tmp_path / "second"
    second.mkdir()
    stage_partitioned_table(FACTS, [R1, R2], Base(R1, first), second)
    table = second / "company_facts.parquet"
    assert not (table / "cik=1").exists()
    assert duckdb.sql(f"SELECT DISTINCT cik FROM {scan(table)}").fetchall() == [(2,)]


def test_legacy_single_file_base_is_partitioned_once(root, tmp_path):
    land("companyfacts", R1, [fact(cik=1), fact(cik=2)])
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    con = connect()
    bind_sessions(con)
    stage_table(con, FACTS, [R1], None, legacy)
    con.close()
    assert (legacy / "company_facts.parquet").is_file()

    land_changes(R2, [])
    target = tmp_path / "partitioned"
    target.mkdir()
    stage_partitioned_table(FACTS, [R1, R2], Base(R1, legacy), target)
    table = target / "company_facts.parquet"
    assert table.is_dir()
    assert duckdb.sql(f"SELECT count(*) FROM {scan(table)}").fetchone() == (2,)

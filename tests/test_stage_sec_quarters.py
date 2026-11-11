"""filings staged one folder per quarter (D155): the same rows as the full
rebuild in stage_table, with untouched quarters hard-linked from the base."""

import os
from datetime import date

import duckdb
import pytest

from pharos.parquet import scan
from pharos.stage import StageError
from pharos.stage_sec import INDEX, Base, stage_quarter_table
from test_stage_sec import (A1, A2, R1, R2, R3, RUNS, SCENARIOS, index_row, land,  # noqa: F401
                            root, save_base, staged, table)

Q3 = dict(filed=date(2026, 8, 3), quarter="2026Q3")
A3 = "0000000003-26-000003"


def quartered(root, runs, base=None, run=None):
    """stage_quarter_table into staging/<run>/; returns (result, rows sorted like table())."""
    target = root / "staging" / (run or runs[-1])
    target.mkdir(parents=True, exist_ok=True)
    result = stage_quarter_table(INDEX, runs, base, target)
    cols, rows = by_name(duckdb.connect(), scan(target / "filings.parquet"))
    return result, target / "filings.parquet", rows, cols


def by_name(con, rel):
    """(column names A-Z, rows with columns in that order, sorted): compares tables
    whatever order a single file or a quarter folder returns columns in."""
    cols = sorted(c[0] for c in con.execute(f"DESCRIBE SELECT * FROM {rel}").fetchall())
    rows = con.execute(f"SELECT {', '.join(cols)} FROM {rel}").fetchall()
    return cols, sorted(rows, key=repr)


def full_rows(runs, base=None):
    return by_name(staged(runs, base), "filings")


def save_quartered_base(root, run_id):
    """A serving version whose filings is already split by quarter."""
    result, out, _, _ = quartered(root, [r for r in RUNS if r <= run_id], run=f"base-{run_id}")
    folder = root / "serving" / run_id
    folder.mkdir(parents=True)
    os.rename(out, folder / "filings.parquet")
    return Base(run_id, folder)


@pytest.mark.parametrize("scenario", SCENARIOS)
@pytest.mark.parametrize("base_run", [R1, R2, R3])
@pytest.mark.parametrize("base_kind", ["one file", "quarters"])
def test_quarters_equal_the_full_rebuild(root, scenario, base_run, base_kind):
    for run, rows in zip(RUNS, SCENARIOS[scenario]):
        land(run, [*rows, index_row(3, A3, **Q3)])          # a second quarter beside each scenario
    base = save_base(root, base_run) if base_kind == "one file" else save_quartered_base(root, base_run)
    result, _, rows, cols = quartered(root, RUNS, base)
    assert result.base_run == base_run
    assert result.runs_read == tuple(r for r in RUNS if r > base_run)
    assert (cols, rows) == full_rows(RUNS)


def test_one_folder_per_quarter(root):
    land(R1, [index_row(1, A1), index_row(3, A3, **Q3)])
    _, out, _, _ = quartered(root, [R1])
    assert sorted(p.name for p in out.iterdir()) == ["quarter=2026Q3", "quarter=2026Q4"]


def test_untouched_quarter_is_hard_linked_from_the_base(root):
    land(R1, [index_row(1, A1), index_row(3, A3, **Q3)])
    base = save_quartered_base(root, R1)
    land(R2, [index_row(1, A1), index_row(2, A2)])           # only 2026Q4 re-lands
    _, out, rows, _ = quartered(root, [R1, R2], base)
    [old] = (base.folder / "filings.parquet" / "quarter=2026Q3").glob("*.parquet")
    [new] = (out / "quarter=2026Q3").glob("*.parquet")
    assert os.path.samefile(old, new)                         # the same file, not a copy
    assert len(rows) == 3


def test_touched_quarter_is_rewritten(root):
    land(R1, [index_row(1, A1), index_row(3, A3, **Q3)])
    base = save_quartered_base(root, R1)
    land(R2, [index_row(1, A1), index_row(2, A2)])
    _, out, _, _ = quartered(root, [R1, R2], base)
    [old] = (base.folder / "filings.parquet" / "quarter=2026Q4").glob("*.parquet")
    new = list((out / "quarter=2026Q4").glob("*.parquet"))
    assert new and not any(os.path.samefile(old, n) for n in new)


def test_row_missing_from_later_landing_is_kept_in_its_quarter(root):
    # the 3 rows of 2026-11-11: in the served table, gone from SEC's later index (D84)
    land(R1, [index_row(1, A1), index_row(2, A2)])
    base = save_quartered_base(root, R1)
    land(R2, [index_row(1, A1)])
    _, _, rows, cols = quartered(root, [R1, R2], base)
    assert sorted(r[cols.index("accession")] for r in rows) == [A1, A2]


def test_key_in_an_untouched_quarter_of_the_base_stops(root):
    land(R1, [index_row(1, A1, **Q3)])
    base = save_quartered_base(root, R1)
    land(R2, [index_row(1, A1)])                              # same key, now dated 2026Q4
    with pytest.raises(StageError, match="the base holds in another quarter"):
        quartered(root, [R1, R2], base)


def test_key_moving_between_two_touched_quarters_matches_the_full_rebuild(root):
    land(R1, [index_row(1, A1, **Q3), index_row(2, A2)])
    base = save_base(root, R1)
    land(R2, [index_row(1, A1), index_row(2, A2), index_row(3, A3, **Q3)])
    _, _, rows, cols = quartered(root, [R1, R2], base)
    assert (cols, rows) == full_rows([R1, R2])


def test_later_run_without_a_file_keeps_the_base(root):
    import pandas as pd
    from pharos.runs import write_raw
    land(R1, [index_row(1, A1), index_row(3, A3, **Q3)])
    base = save_quartered_base(root, R1)
    write_raw(pd.DataFrame(), "sec", "full_index", R2)       # recorded, no file
    result, _, rows, _ = quartered(root, [R1, R2], base)
    assert result.runs_read == () and len(rows) == 2


def test_no_raw_stops(root):
    with pytest.raises(StageError, match="no raw files for sec/full_index"):
        quartered(root, [R1])

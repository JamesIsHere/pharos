"""Source groups (D81, D82, D83): each group is loaded, staged, gated and
published on its own. M1's data is the only real group, so these tests declare
a second, toy group with its own loader, stager, check binder and gate check,
and pin that:
- a group that fails to load, stage or pass its gates is carried forward from
  the current version, row for row, while the other group publishes;
- a group that has never published is left out of the version, recorded
  never_published;
- the next good run publishes both fresh;
- when no group passes nothing publishes and serving/ is untouched;
- a group's checks can't read another group's tables;
- the manifest and serving/<run>/groups.parquet record each group's outcome,
  reason and source run."""

import shutil
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from pharos import checks as checks_mod
from pharos import refresh as refresh_mod
from pharos import stage as stage_mod
from pharos.checks import CHECKS_DIR, bind_run
from pharos.groups import GROUPS, Group
from pharos.paths import data_root
from pharos.publish import composition, current_version
from pharos.runs import raw_select, write_raw
from test_refresh import copy_raw

TOY = Group(name="toy", required=(("toy", "rows"),), reference=(), tables=("toy_table",), inputs=())
RUNS = ["20261008T140000Z", "20261009T140000Z", "20261010T140000Z"]


def _stage_toy(included, target):
    con = duckdb.connect()
    con.execute(f"COPY (SELECT x FROM ({raw_select('toy', 'rows', included)}) WHERE run_id = '{included[-1]}') "
                f"TO '{(target / 'toy_table.parquet').as_posix()}' (FORMAT parquet)")
    con.close()


def _bind_toy(con, run_id, now, tables, group):
    bind_run(con, run_id, now)
    folder = tables or data_root() / "staging" / run_id
    con.execute(f"CREATE VIEW toy_table AS SELECT * FROM read_parquet('{(folder / 'toy_table.parquet').as_posix()}')")


@pytest.fixture
def toy(staged_run, monkeypatch, tmp_path):
    """The toy group wired into every registry, with one gate check (C96: a
    negative x blocks) and one check that reaches for the other group's table."""
    monkeypatch.setitem(GROUPS, "toy", TOY)
    monkeypatch.setitem(stage_mod.STAGERS, "toy", _stage_toy)
    monkeypatch.setitem(checks_mod.BINDERS, "toy", _bind_toy)
    folder = tmp_path / "checks"
    shutil.copytree(CHECKS_DIR, folder)
    (folder / "C96_toy_negative.sql").write_text(
        "-- id: C96\n-- severity: error\n-- gate: yes\n-- group: toy\n-- description: test: negative x\n"
        "SELECT x FROM toy_table WHERE x < 0\n", encoding="utf-8")
    (folder / "C95_toy_reach.sql").write_text(
        "-- id: C95\n-- severity: warn\n-- gate: no\n-- group: toy\n-- description: test: reads prices\n"
        "SELECT * FROM observations WHERE false\n", encoding="utf-8")
    defaults = list(checks_mod.run_checks.__defaults__)
    defaults[0] = folder
    monkeypatch.setattr(checks_mod.run_checks, "__defaults__", tuple(defaults))

    state = {"toy": [1, 2, 3], "toy_fails": False, "prices_fail": False, "checks": folder}

    def prices(run_id):
        if state["prices_fail"]:
            raise RuntimeError("yahoo down")
        copy_raw(staged_run, "yf", "prices", run_id)
        copy_raw(staged_run, "fred", "observations", run_id)
        copy_raw(staged_run, "fred", "series", run_id)

    def toy_load(run_id):
        if state["toy_fails"]:
            raise RuntimeError("toy source down")
        write_raw(pd.DataFrame({"x": state["toy"]}), "toy", "rows", run_id)

    monkeypatch.setitem(refresh_mod.LOADERS, "prices_macro", prices)
    monkeypatch.setitem(refresh_mod.LOADERS, "toy", toy_load)
    monkeypatch.setitem(refresh_mod.REFERENCE_LOADERS, ("tiingo", "prices"),
                        lambda run_id: copy_raw(staged_run, "tiingo", "prices", run_id))

    def run(run_id):
        monkeypatch.setattr(refresh_mod, "new_run_id", lambda: run_id)
        return refresh_mod.refresh()[1]
    state["run"] = run
    return state


def table(version: Path, name: str) -> list:
    return duckdb.sql(f"SELECT * FROM read_parquet('{(version / f'{name}.parquet').as_posix()}') "
                      "ORDER BY ALL").fetchall()


def outcomes(run_id: str) -> dict:
    path = data_root() / "health" / "run_manifest" / f"{run_id}.parquet"
    rows = duckdb.sql(f"SELECT DISTINCT \"group\", group_outcome, group_source_run, group_reason "
                      f"FROM read_parquet('{path.as_posix()}')").fetchall()
    return {g: (o, src, why) for g, o, src, why in rows}


def test_both_groups_publish_fresh(toy):
    pub = toy["run"](RUNS[0])
    assert pub.version is not None
    assert table(pub.version, "toy_table") == [(1,), (2,), (3,)]
    assert {g: c["outcome"] for g, c in composition(pub.version).items()} == \
        {"prices_macro": "published", "toy": "published"}
    o = outcomes(RUNS[0])
    assert o["toy"][:2] == ("published", RUNS[0]) and o["prices_macro"][:2] == ("published", RUNS[0])


def test_a_blocked_group_is_carried_forward_and_the_other_publishes(toy):
    first = toy["run"](RUNS[0])
    toy["toy"] = [5, -1]                                  # C96 blocks toy
    pub = toy["run"](RUNS[1])
    assert pub.version is not None and pub.version.name == RUNS[1]
    assert table(pub.version, "toy_table") == table(first.version, "toy_table")   # row for row
    c = composition(pub.version)
    assert c["toy"]["outcome"] == "carried_forward" and c["toy"]["source_run"] == RUNS[0]
    assert "C96" in c["toy"]["reason"]
    assert c["prices_macro"] == {"outcome": "published", "source_run": RUNS[1], "reason": None}
    assert outcomes(RUNS[1])["toy"][:2] == ("carried_forward", RUNS[0])
    toy["toy"] = [7]                                       # the next good run publishes both
    pub = toy["run"](RUNS[2])
    assert table(pub.version, "toy_table") == [(7,)]
    assert {g: c["outcome"] for g, c in composition(pub.version).items()} == \
        {"prices_macro": "published", "toy": "published"}


def test_a_group_that_fails_to_load_is_carried_forward(toy):
    toy["run"](RUNS[0])
    toy["toy_fails"] = True
    pub = toy["run"](RUNS[1])
    c = composition(pub.version)
    assert c["toy"]["outcome"] == "carried_forward" and "toy source down" in c["toy"]["reason"]
    assert table(pub.version, "toy_table") == [(1,), (2,), (3,)]


def test_a_group_that_fails_to_stage_is_carried_forward(toy, monkeypatch):
    toy["run"](RUNS[0])
    def broken(included, target):
        raise RuntimeError("toy stager bug")
    monkeypatch.setitem(stage_mod.STAGERS, "toy", broken)
    pub = toy["run"](RUNS[1])
    c = composition(pub.version)
    assert c["toy"]["outcome"] == "carried_forward" and "toy stager bug" in c["toy"]["reason"]
    assert not (data_root() / "staging" / RUNS[1] / "toy_table.parquet").exists()


def test_prices_blocked_while_toy_publishes(toy):
    toy["run"](RUNS[0])
    toy["prices_fail"] = True
    toy["toy"] = [9]
    pub = toy["run"](RUNS[1])
    c = composition(pub.version)
    assert c["prices_macro"]["outcome"] == "carried_forward" and "yahoo down" in c["prices_macro"]["reason"]
    assert c["toy"]["outcome"] == "published" and table(pub.version, "toy_table") == [(9,)]
    inputs = sorted(p.name for p in (pub.version / "inputs").glob("*.parquet"))
    assert inputs == sorted(f"{n}.parquet" for n in GROUPS["prices_macro"].inputs)   # carried with it


def test_a_group_that_never_published_is_left_out(toy):
    toy["toy_fails"] = True
    pub = toy["run"](RUNS[0])
    assert pub.version is not None and not (pub.version / "toy_table.parquet").exists()
    c = composition(pub.version)
    assert c["toy"]["outcome"] == "never_published" and c["toy"]["source_run"] is None
    assert outcomes(RUNS[0])["toy"][0] == "never_published"


def test_no_group_passes_nothing_publishes(toy):
    first = toy["run"](RUNS[0])
    before = {p: p.read_bytes() for p in (data_root() / "serving").rglob("*") if p.is_file()}
    toy["prices_fail"] = True
    toy["toy"] = [-5]
    pub = toy["run"](RUNS[1])
    assert pub.version is None and current_version() == first.version
    assert {p: p.read_bytes() for p in (data_root() / "serving").rglob("*") if p.is_file()} == before
    o = outcomes(RUNS[1])
    assert o["toy"][0] == "blocked" and o["prices_macro"][0] == "failed"


def test_a_groups_checks_cannot_read_another_groups_tables(toy):
    pub = toy["run"](RUNS[0])
    c95 = next(r for r in pub.evaluation.results if r["check_id"] == "C95")
    assert c95["status"] == "broken" and "observations" in c95["error"]
    assert pub.evaluation.groups == {"prices_macro": "passed", "toy": "passed"}   # a broken monitor never blocks

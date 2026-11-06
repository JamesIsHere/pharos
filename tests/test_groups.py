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
  reason and source run;
- an on-demand group (D135) is left alone by refresh and runs alone by its
  own command; the group a run didn't attempt is not_run, served unchanged."""

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


def copy_raw(run_from, source, dataset, run_to):
    """Re-land a fixture run's raw file under another run id (as test_refresh's
    helper does; kept here so this module imports no other test module)."""
    path = data_root() / "raw" / source / dataset / f"{run_from}.parquet"
    rel = duckdb.sql(f"SELECT * EXCLUDE (run_id, loaded_at) FROM read_parquet('{path.as_posix()}')")
    frame = rel.df()
    for column, kind in zip(rel.columns, rel.types):
        if str(kind) == "DATE":
            frame[column] = frame[column].dt.date
    write_raw(frame, source, dataset, run_to)

TOY = Group(name="toy", required=(("toy", "rows"),), reference=(), tables=("toy_table",), inputs=())
RUNS = ["20261008T140000Z", "20261009T140000Z", "20261010T140000Z"]
TOY_ON_DEMAND = Group(name="toy", required=(("toy", "rows"),), reference=(), tables=("toy_table",),
                      inputs=(), on_demand=True)


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
    monkeypatch.setattr(checks_mod.discover, "__defaults__", (folder,))   # health finds them too

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

    def on_demand(run_id):
        """Make toy an on-demand group (D135), then run it alone."""
        monkeypatch.setitem(GROUPS, "toy", TOY_ON_DEMAND)
        monkeypatch.setattr(refresh_mod, "new_run_id", lambda: run_id)
        return refresh_mod.run_on_demand("toy")[1]
    state["on_demand"] = on_demand
    state["make_on_demand"] = lambda: monkeypatch.setitem(GROUPS, "toy", TOY_ON_DEMAND)
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


# --- health by group (D98, D102, D115) ---------------------------------------

def _health():
    from datetime import datetime, timezone
    from pharos import health
    return health.evaluate(now=datetime(2026, 10, 10, 15, tzinfo=timezone.utc), record=False)


def test_a_never_published_groups_checks_are_not_served_never_broken(toy):
    toy["toy_fails"] = True
    toy["run"](RUNS[0])
    by_id = {r["check_id"]: r for r in _health().results}
    assert by_id["C96"]["status"] == "not_served" and "never_published" in by_id["C96"]["error"]
    assert by_id["C95"]["status"] == "not_served"               # its monitor too
    assert by_id["C36"]["status"] == "error"                    # the red comes from C36 (D83)
    assert not [r for r in _health().reasons if r.startswith("C96") or r.startswith("C95")]


def test_a_carried_groups_gate_results_are_its_source_runs(toy):
    toy["run"](RUNS[0])
    toy["toy"] = [-3]                                           # C96 errors in RUNS[1]: toy carried
    toy["run"](RUNS[1])
    c96 = next(r for r in _health().results if r["check_id"] == "C96")
    assert (c96["status"], c96["run_id"]) == ("pass", RUNS[0])   # the served tables passed then


def test_rows_of_another_groups_check_file_under_that_group():
    import json
    from pharos.board import failing_rows_by_source
    rows = [{"cik": 320193, "ticker": "AAPL"}]
    assert failing_rows_by_source({"group": "sec", "sample": json.dumps(rows)}) == {"sec": rows}
    # prices_macro keeps D42's column rule: a ticker-only row is Tiingo's
    assert failing_rows_by_source({"group": "prices_macro", "sample": json.dumps(rows)}) == {"tiingo": rows}


def test_latest_md_and_the_email_list_each_group(toy):
    from pharos import health, notify
    toy["run"](RUNS[0])
    toy["toy_fails"] = True
    toy["run"](RUNS[1])
    text = health.render(_health(), color=False)
    assert "- prices_macro: published from " + RUNS[1] in text
    assert "- toy: carried_forward from " + RUNS[0] + ": RuntimeError: toy source down" in text
    body = notify.compose(RUNS[1], _health()).get_content()
    assert "  toy: carried_forward from " + RUNS[0] + ": RuntimeError: toy source down" in body
    assert "  prices_macro: published" in body


def test_sec_declaration_matches_its_stager():
    # one group, two declarations: the raw datasets it requires and the tables
    # it serves are exactly what stage_sec merges
    from pharos.groups import SEC
    from pharos.stage_sec import DATASETS
    assert {("sec", ds.raw) for ds in DATASETS} | {("sec", "companyfacts_changes")} == set(SEC.required)
    assert {ds.table for ds in DATASETS} == set(SEC.tables)


def test_sec_loader_refreshes_companyfacts_after_submissions(monkeypatch):
    calls = []
    monkeypatch.setattr(refresh_mod.sec_index, "load_index", lambda run: calls.append("index"))
    monkeypatch.setattr(refresh_mod.sec_submissions, "load_submissions", lambda run: calls.append("submissions"))
    monkeypatch.setattr(refresh_mod.sec_tickers, "load_tickers", lambda run: calls.append("tickers"))
    monkeypatch.setattr(refresh_mod.sec_companyfacts, "load_incremental_companyfacts",
                        lambda run: calls.append("companyfacts_incremental"))
    refresh_mod._load_sec("20261106T073000Z")
    assert calls == ["index", "submissions", "tickers", "companyfacts_incremental"]


def test_forced_companyfacts_loader_refreshes_the_whole_sec_unit(monkeypatch):
    calls = []
    monkeypatch.setattr(refresh_mod.sec_index, "load_index", lambda run: calls.append("index"))
    monkeypatch.setattr(refresh_mod.sec_submissions, "load_submissions", lambda run: calls.append("submissions"))
    monkeypatch.setattr(refresh_mod.sec_tickers, "load_tickers", lambda run: calls.append("tickers"))
    monkeypatch.setattr(refresh_mod.sec_companyfacts, "load_companyfacts",
                        lambda run: calls.append("companyfacts_full"))
    refresh_mod._load_sec_full_companyfacts("20261106T073000Z")
    assert calls == ["index", "submissions", "tickers", "companyfacts_full"]


def test_sec_is_not_registered_before_it_is_wired():
    # registered without a stager, every nightly records it never_published: C36 red (D83)
    assert "sec" not in GROUPS or "sec" in stage_mod.STAGERS


def test_refresh_loads_only_registered_groups(monkeypatch):
    # a loader for a group not in GROUPS is switched off: SEC's waits for its registration
    calls = []
    monkeypatch.setitem(refresh_mod.LOADERS, "unregistered", lambda run_id: calls.append(run_id))
    monkeypatch.setattr(refresh_mod, "provenance", lambda: ("0" * 40, False))
    monkeypatch.setattr(refresh_mod, "stage", lambda run_id: (_ for _ in ()).throw(RuntimeError("stop")))
    monkeypatch.setitem(refresh_mod.LOADERS, "prices_macro", lambda run_id: None)
    monkeypatch.setattr(refresh_mod, "REFERENCE_LOADERS", {})      # no Tiingo call
    monkeypatch.setattr(refresh_mod, "write_manifest", lambda *a, **k: None)
    monkeypatch.setattr(refresh_mod.health, "evaluate", lambda: None)
    monkeypatch.setattr(refresh_mod.health, "acknowledgments", lambda: None)
    monkeypatch.setattr(refresh_mod.notify, "send_status", lambda *a: None)
    with pytest.raises(RuntimeError, match="stop"):
        refresh_mod.refresh()
    assert calls == []


# --- on-demand groups (D135) ---------------------------------------------------

def test_refresh_leaves_an_on_demand_group_alone(toy):
    toy["make_on_demand"]()
    pub = toy["run"](RUNS[0])
    assert not (data_root() / "raw" / "toy").exists()            # never loaded
    assert not (pub.version / "toy_table.parquet").exists()      # nothing to serve yet
    assert composition(pub.version)["toy"] == {"outcome": "not_run", "source_run": None, "reason": None}
    assert outcomes(RUNS[0])["toy"] == ("not_run", None, None)
    assert outcomes(RUNS[0])["prices_macro"][:2] == ("published", RUNS[0])


def test_an_on_demand_run_publishes_its_group_alone_and_serves_the_rest_unchanged(toy):
    first = toy["run"](RUNS[0])
    toy["toy"] = [8, 9]
    pub = toy["on_demand"](RUNS[1])
    assert not (data_root() / "raw" / "yf" / "prices" / f"{RUNS[1]}.parquet").exists()   # nightly not loaded
    assert table(pub.version, "toy_table") == [(8,), (9,)]
    assert table(pub.version, "observations") == table(first.version, "observations")   # row for row
    c = composition(pub.version)
    assert c["toy"] == {"outcome": "published", "source_run": RUNS[1], "reason": None}
    assert c["prices_macro"] == {"outcome": "not_run", "source_run": RUNS[0], "reason": None}
    assert (pub.version / "inputs" / "settings.parquet").exists()          # its inputs come with it
    nxt = toy["run"](RUNS[2])                                             # the nightly after it
    assert table(nxt.version, "toy_table") == [(8,), (9,)]
    assert composition(nxt.version)["toy"] == {"outcome": "not_run", "source_run": RUNS[1], "reason": None}
    assert outcomes(RUNS[2])["prices_macro"][:2] == ("published", RUNS[2])


def test_a_blocked_on_demand_run_publishes_nothing(toy):
    first = toy["run"](RUNS[0])
    toy["toy"] = [-1]
    pub = toy["on_demand"](RUNS[1])
    assert pub.version is None and current_version() == first.version
    o = outcomes(RUNS[1])
    assert o["toy"][0] == "blocked" and o["prices_macro"] == ("not_run", RUNS[0], None)


def test_a_crashed_on_demand_run_records_the_others_not_run(toy):
    toy["run"](RUNS[0])
    toy["toy_fails"] = True
    with pytest.raises(RuntimeError, match="toy source down"):
        toy["on_demand"](RUNS[1])
    o = outcomes(RUNS[1])
    assert o["toy"][0] == "failed" and o["prices_macro"] == ("not_run", None, None)


def test_run_on_demand_refuses_a_nightly_group(toy):
    with pytest.raises(ValueError, match="not a registered on-demand group"):
        refresh_mod.run_on_demand("prices_macro")

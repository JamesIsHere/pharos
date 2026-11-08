"""Raw snapshots -> staging/<run_id>/: the candidate tables a run would publish.

The transformations are the SQL files in transform/; this module only binds
their inputs, checks their row counts, deduplicates, and writes the result.

Rules applied here:
- Staging is per source group (D81). A group stages in a run only when that
  run is complete for it: a load record exists for every dataset the group
  requires (finding c, the loaders write separately). Staging a group reads
  every run complete for it up to and including this one, because raw is a
  snapshot log (D18) and the full history of what each source said is the input.
- A run stages only the groups it attempted (D135), read from its load
  records: the nightly groups when it has a record of any nightly dataset (a
  refresh), an on-demand group when it has a record of one of its datasets
  (that group's own command). A group the run didn't attempt is neither staged
  nor failed: publish serves it unchanged.
- A group that fails to stage is recorded with its reason and the other groups
  go on; its tables are left out of the folder, never half-written. Only when
  no group stages does stage() raise, with the first group's error.
- Each SQL file must return exactly one row per raw row, with no NULL units
  (FRED, D19) and no NULL vintage (Yahoo, D20); every catalog row needs a
  frequency and start date (#64), and a source and frequency the health
  system has cases for (#65, D74). Anything else stops the group.
- Dedup collapses rows that agree on everything but lineage, keeping the
  earliest run's run_id and loaded_at, so an identical rerun changes nothing.
  Rows that share (series_id, obs_date, vintage) but disagree on value both
  survive: that is a silent source correction, for C07 / C14 to block.
- staging/ is regenerable, so restaging a run replaces its folder. raw/ is
  the record and is only ever read here.
"""

import shutil
import traceback
from dataclasses import dataclass, field
from pathlib import Path

import duckdb

from pharos import config
from pharos.db import query
from pharos.groups import GROUPS, PRICES_MACRO, Group, nightly
from pharos.paths import data_root
from pharos.runs import raw_select

TRANSFORM_DIR = Path(__file__).parent / "transform"
DATASETS = list(PRICES_MACRO.required)   # the group's declaration is the one list (D81)
# The sources and frequencies every check and expected-date rule has a case for.
# Widening either set is the same change that adds those cases (D74)
CHECKED_SOURCES = {"yf", "fred"}
CHECKED_FREQUENCIES = {"D", "M", "Q"}


class StageError(RuntimeError):
    pass


@dataclass(frozen=True)
class Staging:
    run_id: str
    path: Path                                            # staging/<run_id>/
    staged: tuple[str, ...]                               # groups whose tables are in the folder
    failed: dict[str, str] = field(default_factory=dict)  # group -> why it isn't (incomplete or an error)


def complete_runs(group: Group = PRICES_MACRO) -> list[str]:
    """Run IDs with a load record for every dataset the group requires, oldest
    first. Empty on a new data root, before any load has been recorded."""
    folder = data_root() / "health" / "loads"
    if not any(folder.glob("*.parquet")):
        return []
    records = folder.as_posix() + "/*.parquet"
    # only the required datasets count: a reference load (Tiingo) neither makes
    # nor breaks a run's completeness (D36)
    required = ", ".join(f"'{s}/{d}'" for s, d in group.required)
    # on the app's threads too (health binds it at view time): own connection (#68)
    _, rows = query(
        f"""SELECT run_id FROM read_parquet('{records}', union_by_name = true)
            WHERE source || '/' || dataset IN ({required})
            GROUP BY run_id
            HAVING count(DISTINCT source || '/' || dataset) = {len(group.required)}
            ORDER BY run_id""")
    return [r[0] for r in rows]


def any_complete_runs() -> list[str]:
    """Runs complete for at least one group, oldest first."""
    return sorted({r for g in GROUPS.values() for r in complete_runs(g)})


def stage(run_id: str | None = None) -> Staging:
    """Build staging/<run_id>/ (default: the latest run complete for any group),
    one group at a time. Raises only when no group stages."""
    runs = any_complete_runs()
    if not runs:
        raise StageError("no complete run in health/loads")
    run_id = run_id or runs[-1]
    if run_id not in runs:
        raise StageError(f"run {run_id} is not complete for any group (a load record is missing)")

    target = data_root() / "staging" / run_id
    fresh_parent = not target.parent.exists()
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)

    staged, failed, first_error = [], {}, None
    for group in attempted(run_id):
        runs_g = complete_runs(group)
        if run_id not in runs_g:
            have = _loaded(run_id)
            failed[group.name] = (f"incomplete: no load record for "
                                  f"{[f'{s}/{d}' for s, d in group.required if (s, d) not in have]}")
            continue
        try:
            STAGERS[group.name]([r for r in runs_g if r <= run_id], target)
            staged.append(group.name)
        except Exception as e:
            for table in group.tables:           # never leave a group half-written
                table_path = target / f"{table}.parquet"
                if table_path.is_dir():
                    shutil.rmtree(table_path)
                else:
                    table_path.unlink(missing_ok=True)
            failed[group.name] = traceback.format_exc(limit=5)
            first_error = first_error or e
    if not staged:
        shutil.rmtree(target)                    # nothing staged: no folder, as before groups
        if fresh_parent:
            target.parent.rmdir()
        if first_error is not None:
            raise first_error
        raise StageError(f"no group staged for run {run_id}: {failed}")
    return Staging(run_id, target, tuple(staged), failed)


def attempted(run_id: str) -> list[Group]:
    """The registered groups this run attempted (D135), from its load records:
    every nightly group when the run loaded any nightly dataset, and each
    on-demand group one of whose datasets it loaded."""
    have = _loaded(run_id)
    night = [g for g in nightly() if have & set(g.datasets)]
    out = nightly() if night else []
    return out + [g for g in GROUPS.values() if g.on_demand and have & set(g.datasets)]


def _loaded(run_id: str) -> set[tuple[str, str]]:
    folder = data_root() / "health" / "loads"
    return {tuple(p.stem.split("__")[1:3]) for p in folder.glob(f"{run_id}__*.parquet")}


def _stage_prices_macro(included: list[str], target: Path) -> None:
    con = duckdb.connect()
    bind_raw(con, included)
    bind_config(con)

    fred = _transform(con, "fred_observations", "raw_fred_observations")
    _require_none(con, fred, "units IS NULL", "FRED rows with no units window (D19)")
    yf = _transform(con, "yf_observations", "raw_yf_prices")
    _require_none(con, yf, "vintage IS NULL", "price rows with no full-history pull at or before their run (D20)")
    con.execute(f"CREATE TABLE series_catalog AS {_sql('series_catalog')}")
    # a configured FRED series the loader left out this run (D144) that has no
    # metadata from any run has never loaded: it has no catalog row yet, and C41
    # names it red. Every other series with no metadata still stops the group
    left_out = fred_left_out(included[-1])
    if left_out:
        con.execute("DELETE FROM series_catalog WHERE source = 'fred' AND frequency IS NULL "
                    "AND active_from IS NULL AND source_key IN (SELECT unnest(?))", [left_out])
    # a NULL frequency or start date makes C04 / C16 skip the series silently (#64)
    missing = [r[0] for r in con.execute(
        "SELECT series_id FROM series_catalog WHERE frequency IS NULL OR active_from IS NULL "
        "ORDER BY 1").fetchall()]
    if missing:
        raise StageError(f"series_catalog: no current metadata (frequency, start date) for {missing}")
    # a source or frequency outside the checked sets is skipped silently by
    # C04's expected dates, C09/C10's yf prefix, and the yf/FRED split (#65)
    unchecked = con.execute(
        "SELECT series_id, source, frequency FROM series_catalog "
        "WHERE source NOT IN (SELECT unnest(?)) OR frequency NOT IN (SELECT unnest(?)) ORDER BY 1",
        [sorted(CHECKED_SOURCES), sorted(CHECKED_FREQUENCIES)]).fetchall()
    if unchecked:
        raise StageError(f"series_catalog: source or frequency the checks have no case for "
                         f"(series_id, source, frequency): {unchecked} (D74)")

    con.execute(f"""
        CREATE TABLE observations AS
        SELECT * FROM (SELECT * FROM {fred} UNION ALL BY NAME SELECT * FROM {yf})
        QUALIFY row_number() OVER (
            PARTITION BY series_id, obs_date, vintage, value, units, available_date
            ORDER BY run_id) = 1""")

    for table in PRICES_MACRO.tables:
        con.execute(f"COPY {table} TO '{(target / f'{table}.parquet').as_posix()}' (FORMAT parquet)")
    con.close()


def fred_left_out(run_id: str) -> list[str]:
    """The FRED series the run's loader left out, from its observations load
    record's note (D144). Empty when the record has no note."""
    record = data_root() / "health" / "loads" / f"{run_id}__fred__observations.parquet"
    if not record.exists():
        return []
    con = duckdb.connect()
    have = {c[0] for c in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{record.as_posix()}')").fetchall()}
    if "note" not in have:   # records from before D91 carry no note
        return []
    rows = con.execute(f"""SELECT e.fred_id FROM (
            SELECT unnest(from_json(note, '[{{"fred_id": "VARCHAR", "error": "VARCHAR"}}]')) AS e
            FROM read_parquet('{record.as_posix()}') WHERE note IS NOT NULL) ORDER BY 1""").fetchall()
    con.close()
    return [r[0] for r in rows]


def _stage_sec(included: list[str], target: Path) -> None:
    from pharos.stage_sec import stage_group   # stage_sec imports this module
    stage_group(included, target)


# group name -> the function that stages it into staging/<run_id>/
STAGERS = {"prices_macro": _stage_prices_macro, "sec": _stage_sec}


def bind_raw(con, included: list[str], group: Group = PRICES_MACRO) -> None:
    """One view per required raw dataset of the group, over the files of the
    included runs. A run whose source returned nothing has a load record but no file."""
    for source, dataset in group.required:
        select = raw_select(source, dataset, included)
        if select is None:
            raise StageError(f"no raw files for {source}/{dataset}")
        con.execute(f"CREATE VIEW raw_{source}_{dataset} AS {select}")


def bind_config(con) -> None:
    """The expected set, from config: watchlist windows and configured FRED series,
    each with the lag after which its next period is due (D33)."""
    cfg = config.sources()
    # a lag that isn't an integer makes C02/C17's due-date arithmetic NULL, which
    # counts 0 periods behind: freshness off, silently (#63). bool is an int in
    # Python, so a YAML `true` is refused explicitly
    is_int = lambda v: isinstance(v, int) and not isinstance(v, bool)
    if not is_int(cfg["yahoo"].get("expected_lag_days")):
        raise StageError(f"yahoo.expected_lag_days must be an integer, got "
                         f"{cfg['yahoo'].get('expected_lag_days')!r} (sources.yaml, D33)")
    con.execute("CREATE TABLE watchlist_windows (ticker VARCHAR, yahoo_symbol VARCHAR, "
                "first_expected DATE, active_to DATE, expected_lag_days INTEGER)")
    con.executemany("INSERT INTO watchlist_windows VALUES (?, ?, ?, ?, ?)",
                    [(w["ticker"], w["yahoo_symbol"], w["active_from"] or cfg["backfill_start"],
                      w["active_to"], cfg["yahoo"]["expected_lag_days"]) for w in config.watchlist()])
    lags = cfg["fred"]["expected_lag_days"]
    missing = [s for s in cfg["fred"]["series"] if not is_int(lags.get(s))]
    if missing:
        raise StageError(f"fred.expected_lag_days has no integer lag for {missing} (sources.yaml, D33)")
    con.execute("CREATE TABLE fred_expected (fred_id VARCHAR, expected_lag_days INTEGER)")
    con.executemany("INSERT INTO fred_expected VALUES (?, ?)", [(s, lags[s]) for s in cfg["fred"]["series"]])


def _transform(con, name: str, raw_view: str) -> str:
    """Run one SQL file into a table; its row count must equal its raw input's."""
    con.execute(f"CREATE TABLE {name} AS {_sql(name)}")
    n_in = con.execute(f"SELECT count(*) FROM {raw_view}").fetchone()[0]
    n_out = con.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
    if n_in != n_out:
        raise StageError(f"{name}: {n_in:,} raw rows in, {n_out:,} out (a join fanned out or dropped rows)")
    return name


def _require_none(con, table: str, condition: str, what: str) -> None:
    n = con.execute(f"SELECT count(*) FROM {table} WHERE {condition}").fetchone()[0]
    if n:
        raise StageError(f"{table}: {n:,} {what}")


def _sql(name: str) -> str:
    return (TRANSFORM_DIR / f"{name}.sql").read_text(encoding="utf-8")

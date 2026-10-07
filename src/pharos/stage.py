"""Raw snapshots -> staging/<run_id>/: the candidate tables a run would publish.

The transformations are the SQL files in transform/; this module only binds
their inputs, checks their row counts, deduplicates, and writes the result.

Rules applied here:
- A run counts only when it is complete: a load record exists for every raw
  dataset (finding c, the loaders write separately). Staging a run reads every
  complete run up to and including it, because raw is a snapshot log (D18) and
  the full history of what each source said is the input.
- Each SQL file must return exactly one row per raw row, with no NULL units
  (FRED, D19) and no NULL vintage (Yahoo, D20). Anything else stops the run.
- Dedup collapses rows that agree on everything but lineage, keeping the
  earliest run's run_id and loaded_at, so an identical rerun changes nothing.
  Rows that share (series_id, obs_date, vintage) but disagree on value both
  survive: that is a silent source correction, for C07 / C14 to block.
- staging/ is regenerable, so restaging a run replaces its folder. raw/ is
  the record and is only ever read here.
"""

import shutil
from pathlib import Path

import duckdb

from pharos import config
from pharos.paths import data_root
from pharos.runs import raw_select

TRANSFORM_DIR = Path(__file__).parent / "transform"
DATASETS = [("yf", "prices"), ("fred", "observations"), ("fred", "series")]


class StageError(RuntimeError):
    pass


def complete_runs() -> list[str]:
    """Run IDs with a load record for every dataset, oldest first."""
    records = (data_root() / "health" / "loads").as_posix() + "/*.parquet"
    rows = duckdb.sql(
        f"""SELECT run_id FROM read_parquet('{records}')
            GROUP BY run_id
            HAVING count(DISTINCT source || '/' || dataset) = {len(DATASETS)}
            ORDER BY run_id""").fetchall()
    return [r[0] for r in rows]


def stage(run_id: str | None = None) -> Path:
    """Build staging/<run_id>/ from every complete run up to run_id (default: latest)."""
    runs = complete_runs()
    if not runs:
        raise StageError("no complete run in health/loads")
    run_id = run_id or runs[-1]
    if run_id not in runs:
        raise StageError(f"run {run_id} is not complete (a load record is missing)")
    included = [r for r in runs if r <= run_id]

    con = duckdb.connect()
    _bind_raw(con, included)
    _bind_config(con)

    fred = _transform(con, "fred_observations", "raw_fred_observations")
    _require_none(con, fred, "units IS NULL", "FRED rows with no units window (D19)")
    yf = _transform(con, "yf_observations", "raw_yf_prices")
    _require_none(con, yf, "vintage IS NULL", "price rows with no full-history pull at or before their run (D20)")
    con.execute(f"CREATE TABLE series_catalog AS {_sql('series_catalog')}")

    con.execute(f"""
        CREATE TABLE observations AS
        SELECT * FROM (SELECT * FROM {fred} UNION ALL BY NAME SELECT * FROM {yf})
        QUALIFY row_number() OVER (
            PARTITION BY series_id, obs_date, vintage, value, units, available_date
            ORDER BY run_id) = 1""")

    target = data_root() / "staging" / run_id
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    for table in ("observations", "series_catalog"):
        con.execute(f"COPY {table} TO '{(target / f'{table}.parquet').as_posix()}' (FORMAT parquet)")
    con.close()
    return target


def _bind_raw(con, included: list[str]) -> None:
    """One view per raw dataset over the files of the included runs.
    A run whose source returned nothing has a load record but no file."""
    for source, dataset in DATASETS:
        select = raw_select(source, dataset, included)
        if select is None:
            raise StageError(f"no raw files for {source}/{dataset}")
        con.execute(f"CREATE VIEW raw_{source}_{dataset} AS {select}")


def _bind_config(con) -> None:
    """The expected set, from config: watchlist windows and configured FRED series."""
    cfg = config.sources()
    con.execute("CREATE TABLE watchlist_windows (ticker VARCHAR, yahoo_symbol VARCHAR, "
                "first_expected DATE, active_to DATE)")
    con.executemany("INSERT INTO watchlist_windows VALUES (?, ?, ?, ?)",
                    [(w["ticker"], w["yahoo_symbol"], w["active_from"] or cfg["backfill_start"],
                      w["active_to"]) for w in config.watchlist()])
    con.execute("CREATE TABLE fred_expected (fred_id VARCHAR)")
    con.executemany("INSERT INTO fred_expected VALUES (?)", [(s,) for s in cfg["fred"]["series"]])


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

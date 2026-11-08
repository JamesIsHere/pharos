"""Runs checks/*.sql against one staged run and records the results.

A check is a SQL file that returns failing rows; zero rows is a pass. Its header
declares id, severity (error|warn), gate (yes|no), group (a source group, or
`run` for a monitor about the run itself, D81) and description, one
`-- key: value` line each, before any SQL. The runner owns everything around
the SQL:

- Discovery fails loudly. A missing or invalid header field, a filename that
  doesn't start with its id, a repeated id, or an empty checks folder stops the
  run: a misconfigured control is never skipped.
- Checks query names, never paths. One DuckDB connection binds this run's
  staged tables, the raw snapshots of every complete run up to it, the load
  records, the expected set from config (watchlist windows, FRED series,
  required series per ticker), every raw file on disk with its row count,
  the XNYS trading calendar (D24), the run manifests (D31), a one-row this_run
  table and a one-row clock (run time, or the wall clock at view time).
- Checks run per source group (D81): each group present in the tables (all its
  tables there) gets its own connection binding its own tables, raw snapshots,
  raw files and load records, so a group's gates can't read, or block on,
  another group's data. `group: run` checks get only the run-level names
  (this_run, clock, run_manifest).
- A check whose SQL fails is `broken`. A broken gate fails its group: a control
  that didn't execute proves nothing, so publish treats `failed` like
  `blocked` for that group.
- A group's verdict is `blocked` when any of its gate checks returns rows at
  error severity. Warnings and monitor checks are recorded but never block
  (D17). The evaluation's verdict is `passed` when any group passed, so the
  run can publish (carrying the others forward); else `failed` if any group
  failed, else `blocked`.
- Results append: each evaluation writes its own file under
  health/check_results/, so re-checking a run never rewrites an earlier answer.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import exchange_calendars as xc

from pharos import config
from pharos.groups import DECLARED, GROUPS, PRICES_MACRO, RUN, Group
from pharos.paths import PROJECT_ROOT, data_root
from pharos.parquet import table_scan
from pharos.runs import sha256_of
from pharos.stage import any_complete_runs, bind_config, bind_raw, complete_runs

CHECKS_DIR = PROJECT_ROOT / "checks"
HEADER_FIELDS = ("id", "severity", "gate", "group", "description")
# what checks and the app read besides the version's tables; publish copies
# each into serving/<version>/inputs/ (D76). Each group declares its own.
INPUTS_DIR = "inputs"
VIEW_INPUTS = PRICES_MACRO.inputs
# the per-group manifest columns refresh writes (D115)
GROUP_COLUMNS = ("group", "group_outcome", "group_reason", "group_source_run")
# load-record columns added after the first records were written (D91, D108, D119)
LOAD_COLUMNS_ADDED = {"sha256": "VARCHAR", "last_modified": "TIMESTAMPTZ", "source_count": "BIGINT",
                      "note": "VARCHAR"}


class CheckError(RuntimeError):
    pass


@dataclass(frozen=True)
class Check:
    id: str
    severity: str
    gate: bool
    description: str
    sql: str
    path: Path
    ack_key: tuple[str, ...] = ()   # the columns naming one problem; empty: can't be acknowledged (D75)
    group: str = "prices_macro"     # the source group it tests, or `run` (D81)


@dataclass(frozen=True)
class Evaluation:
    run_id: str
    verdict: str          # passed (some group passed) | blocked | failed
    results: list[dict]
    path: Path | None     # None when record=False (D43)
    groups: dict[str, str] = field(default_factory=dict)   # present group -> passed | blocked | failed


def discover(directory: Path = CHECKS_DIR) -> list[Check]:
    """Every check in the folder, sorted by id. Any defect stops discovery."""
    checks = [parse(p) for p in sorted(directory.glob("*.sql"))]
    if not checks:
        raise CheckError(f"no checks in {directory}")
    ids = [c.id for c in checks]
    repeated = sorted({i for i in ids if ids.count(i) > 1})
    if repeated:
        raise CheckError(f"check ids used by more than one file: {repeated}")
    return sorted(checks, key=lambda c: c.id)


def parse(path: Path) -> Check:
    """Read the header (leading `-- key: value` lines) and the SQL after it."""
    header = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"--\s*(\w+)\s*:\s*(.*\S)\s*$", line)
        if not m:
            break
        header[m.group(1)] = m.group(2)

    missing = [f for f in HEADER_FIELDS if f not in header]
    if missing:
        raise CheckError(f"{path.name}: header is missing {missing}")
    if header["severity"] not in ("error", "warn"):
        raise CheckError(f"{path.name}: severity must be error or warn, not {header['severity']!r}")
    if header["gate"] not in ("yes", "no"):
        raise CheckError(f"{path.name}: gate must be yes or no, not {header['gate']!r}")
    declared = sorted({g.name for g in DECLARED} | set(GROUPS))
    if header["group"] != RUN and header["group"] not in declared:
        raise CheckError(f"{path.name}: group must be one of {declared + [RUN]}, not {header['group']!r}")
    if header["group"] == RUN and header["gate"] == "yes":
        raise CheckError(f"{path.name}: a `group: run` check can't gate: a gate blocks one group's publish (D81)")
    if not re.fullmatch(re.escape(header["id"]) + r"(_\w+)?", path.stem):
        raise CheckError(f"{path.name}: filename must be {header['id']}.sql or {header['id']}_<name>.sql")

    ack_key = tuple(c.strip() for c in header.get("ack_key", "").split(",") if c.strip())
    if ack_key and header["severity"] != "warn":
        raise CheckError(f"{path.name}: ack_key on a {header['severity']}-severity check; "
                         "only a warn check's rows can be acknowledged (D46)")

    sql = path.read_text(encoding="utf-8").strip().rstrip(";")
    return Check(header["id"], header["severity"], header["gate"] == "yes",
                 header["description"], sql, path, ack_key, header["group"])


def run_checks(run_id: str, directory: Path = CHECKS_DIR, now: datetime | None = None,
               tables: Path | None = None, monitors_only: bool = False, record: bool = True) -> Evaluation:
    """Evaluate every check against staging/<run_id>/ and append the results.
    `now` is the clock monitor checks measure against: the run's own time when
    omitted (run time), the wall clock when the health CLI evaluates at view time.
    At view time the health CLI passes `tables` (the published version) and
    `monitors_only`: gate checks tested staged data that hasn't changed, so their
    run-time results stand (D34). Results record which context produced them.
    The app passes record=False: it reads health/, never writes it (D43).
    A group's checks run only when all its tables are in the folder; a group
    that isn't there has no results here (D81, D98)."""
    checks = discover(directory)
    if monitors_only:
        checks = [c for c in checks if not c.gate]
    context = "view" if monitors_only else "run"
    if run_id not in any_complete_runs():
        raise CheckError(f"run {run_id} is not complete for any group (a load record is missing)")
    folder = tables or data_root() / "staging" / run_id
    if not folder.is_dir():
        raise CheckError(f"tables for run {run_id} not found: {folder} does not exist")
    present = groups_in(folder)
    if not present:
        raise CheckError(f"no group's tables are complete in {folder}")
    evaluated_at = datetime.now(timezone.utc)

    results, verdicts = [], {}
    for group in present:
        con = duckdb.connect()
        BINDERS[group.name](con, run_id, now, tables, group)
        mine = [_evaluate(con, c, run_id, evaluated_at, context) for c in checks if c.group == group.name]
        con.close()
        results += mine
        verdicts[group.name] = _verdict(mine)
    run_level = [c for c in checks if c.group == RUN]
    if run_level:
        con = duckdb.connect()
        bind_run(con, run_id, now)
        results += [_evaluate(con, c, run_id, evaluated_at, context) for c in run_level]
        con.close()
    results.sort(key=lambda r: r["check_id"])

    if "passed" in verdicts.values():
        verdict = "passed"
    elif "failed" in verdicts.values():
        verdict = "failed"
    else:
        verdict = "blocked"
    return Evaluation(run_id, verdict, results, _write(results, run_id, evaluated_at) if record else None,
                      verdicts)


def _verdict(results: list[dict]) -> str:
    """One group's verdict from its results. A broken gate leaves the staged
    data unproven; a broken monitor tests the world, not the data, so it shows
    red on health but never blocks (D17, D77)."""
    if any(r["gate"] and r["status"] == "broken" for r in results):
        return "failed"
    if any(r["gate"] and r["status"] == "error" for r in results):
        return "blocked"
    return "passed"


def groups_in(folder: Path) -> list[Group]:
    """The groups whose every table is in folder (a staging run or a version)."""
    return [g for g in GROUPS.values() if all((folder / f"{t}.parquet").exists() for t in g.tables)]


def bind_run(con: duckdb.DuckDBPyConnection, run_id: str, now: datetime | None = None) -> None:
    """The run-level names every check may query: this_run, clock, every
    run's manifest (D31) and every run's first load (run_loads, C42). `group:
    run` checks get only these."""
    run_at = datetime.strptime(run_id, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    con.execute("CREATE TABLE this_run AS SELECT ? AS run_id, ?::TIMESTAMPTZ AS run_at",
                [run_id, run_at])
    con.execute("CREATE TABLE clock AS SELECT ?::TIMESTAMPTZ AS now", [now or run_at])
    # none before the first refresh. The run being checked has no manifest yet:
    # refresh writes it after publish.
    manifests = data_root() / "health" / "run_manifest"
    if any(manifests.glob("*.parquet")):
        source = f"read_parquet('{manifests.as_posix()}/*.parquet', union_by_name = true)"
        # manifests from before source groups have no group columns; when none
        # has them, add them as NULL so a check always sees one shape (D116)
        have = {c[0] for c in con.execute(f"DESCRIBE SELECT * FROM {source}").fetchall()}
        extra = "".join(f', NULL::VARCHAR AS "{c}"' for c in GROUP_COLUMNS if c not in have)
        con.execute(f"CREATE VIEW run_manifest AS SELECT *{extra} FROM {source}")
    else:
        con.execute("CREATE TABLE run_manifest (run_id VARCHAR, status VARCHAR, published_at TIMESTAMPTZ, "
                    + ", ".join(f'"{c}" VARCHAR' for c in GROUP_COLUMNS) + ")")
    # every run that loaded anything, from its load records: a run that crashed
    # past its first load has these but no manifest (C42)
    loads = data_root() / "health" / "loads"
    if any(loads.glob("*.parquet")):
        con.execute(f"CREATE VIEW run_loads AS SELECT run_id, min(loaded_at) AS first_loaded_at FROM "
                    f"read_parquet('{loads.as_posix()}/*.parquet', union_by_name = true) GROUP BY run_id")
    else:
        con.execute("CREATE TABLE run_loads (run_id VARCHAR, first_loaded_at TIMESTAMPTZ)")


def bind(con: duckdb.DuckDBPyConnection, run_id: str, now: datetime | None = None,
         tables: Path | None = None, group: Group = PRICES_MACRO) -> None:
    """The names a group's checks may query: the run-level names plus the
    group's own tables, raw snapshots, raw files, load records and inputs.
    Anything a check needs that isn't here is a runner change, not a path
    inside a SQL file."""
    if group is not PRICES_MACRO:
        raise CheckError(f"no bindings declared for group {group.name!r}")
    runs = complete_runs(group)
    # at run time the group's raw must be complete; at view time its tables may
    # have been carried forward from an earlier run (D81) and raw isn't read
    if tables is None and run_id not in runs:
        raise CheckError(f"run {run_id} is not complete for {group.name} (a load record is missing)")
    staged = tables or data_root() / "staging" / run_id
    if not staged.is_dir():
        raise CheckError(f"tables for run {run_id} not found: {staged} does not exist")

    bind_run(con, run_id, now)
    for table in group.tables:
        con.execute(f"CREATE VIEW {table} AS SELECT * FROM {table_scan(staged, table)}")
    mine = {f"{s}/{d}" for s, d in group.datasets}
    if tables is None:
        # run time: the gate checks read raw/, and config/ is read as it stands
        bind_raw(con, [r for r in runs if r <= run_id], group)
        # the group's raw files on disk with the row count read from each footer
        # and the hash of the bytes (C05, D108)
        con.execute("CREATE TABLE raw_files (source VARCHAR, dataset VARCHAR, run_id VARCHAR, "
                    "path VARCHAR, rows_on_disk BIGINT, sha256_on_disk VARCHAR)")
        for f in sorted((data_root() / "raw").glob("*/*/*.parquet")):
            if f"{f.parent.parent.name}/{f.parent.name}" not in mine:
                continue
            n = con.execute(f"SELECT sum(num_rows) FROM parquet_file_metadata('{f.as_posix()}')").fetchone()[0]
            con.execute("INSERT INTO raw_files VALUES (?, ?, ?, ?, ?, ?)",
                        [f.parent.parent.name, f.parent.name, f.stem, f.as_posix(), n, sha256_of(f)])
        bind_view_inputs(con, run_id)
    else:
        # view time: the version's own copy of what its checks read (D76)
        load_view_inputs(con, tables, group)
    loads = (data_root() / "health" / "loads").as_posix() + "/*.parquet"
    names = ", ".join(f"'{m}'" for m in sorted(mine))   # declared dataset names, not user input
    source = f"read_parquet('{loads}', union_by_name = true)"
    # load records from before D91/D108 lack the newer columns; when none has
    # them, add them as NULL so a check always sees one shape
    have = {c[0] for c in con.execute(f"DESCRIBE SELECT * FROM {source}").fetchall()}
    extra = "".join(f", NULL::{kind} AS {c}" for c, kind in LOAD_COLUMNS_ADDED.items() if c not in have)
    con.execute(f"CREATE VIEW loads AS SELECT *{extra} FROM {source} "
                f"WHERE source || '/' || dataset IN ({names})")
    # the FRED series a run left out, from its observations load record's note
    # (D144); read from the load records, so run time and view time see the same
    con.execute("""CREATE VIEW fred_load_errors AS
                   SELECT run_id, e.fred_id, e.error
                   FROM (SELECT run_id, unnest(from_json(note, '[{"fred_id": "VARCHAR", "error": "VARCHAR"}]')) AS e
                         FROM loads WHERE source = 'fred' AND dataset = 'observations' AND note IS NOT NULL)""")

    # Views shared by a check and another reader, one SQL file each:
    # Yahoo vs Tiingo on every common date (the audit, D41) and C12's draw from
    # it (D38), and per-series baseline metrics (C13, D39).
    _bind_shared(con, "reconcile_all", "ticker VARCHAR, obs_date DATE, yahoo_close DOUBLE, "
                 "tiingo_close DOUBLE, tiingo_raw_close DOUBLE, rel_diff DOUBLE")
    _bind_shared(con, "reconcile_sample", "ticker VARCHAR, obs_date DATE, yahoo_close DOUBLE, "
                 "tiingo_close DOUBLE, tiingo_raw_close DOUBLE, rel_diff DOUBLE, pick VARCHAR")
    _bind_shared(con, "baseline_metrics", "series_id VARCHAR, first_date DATE, last_date DATE, row_count BIGINT")
    # and the expected set with presence, shared by C04 and the coverage heatmap (D44)
    _bind_shared(con, "expected_dates", "series_id VARCHAR, obs_date DATE, present BOOLEAN")
    # the recorded opening balance (D39); empty before the audit records it, so
    # C13 reports the absence instead of breaking. The audit writes it once:
    # a second file would double every comparison, so it stops the run.
    baselines = sorted((data_root() / "health" / "baseline").glob("*.parquet"))
    if len(baselines) > 1:
        raise CheckError(f"more than one baseline recorded: {[p.name for p in baselines]}")
    if baselines:
        con.execute(f"CREATE VIEW baseline AS SELECT * FROM read_parquet('{baselines[0].as_posix()}')")
    else:
        con.execute("CREATE TABLE baseline (series_id VARCHAR, first_date DATE, last_date DATE, "
                    "row_count BIGINT, audit_run_id VARCHAR, recorded_at TIMESTAMPTZ)")


def bind_sec(con: duckdb.DuckDBPyConnection, run_id: str, now: datetime | None = None,
             tables: Path | None = None, group: Group | None = None) -> None:
    """An SEC group's names, at the landed level (D127): the run-level names,
    its tables and its load records (the Last-Modified the freshness checks
    read, step 4). At run time from staging, at view time from the version
    (D76). No raw views: a landed source has no reconciliation. Binds the
    unified SEC group."""
    from pharos.groups import SEC
    group = group or SEC
    staged = tables or data_root() / "staging" / run_id
    if not staged.is_dir():
        raise CheckError(f"tables for run {run_id} not found: {staged} does not exist")
    bind_run(con, run_id, now)
    for table in group.tables:
        con.execute(f"CREATE VIEW {table} AS SELECT * FROM {table_scan(staged, table)}")
    names = ", ".join(f"'{s}/{d}'" for s, d in sorted(group.datasets))   # declared names, not user input
    loads = (data_root() / "health" / "loads").as_posix() + "/*.parquet"
    con.execute(f"CREATE VIEW loads AS SELECT * FROM read_parquet('{loads}', union_by_name = true) "
                f"WHERE source || '/' || dataset IN ({names})")


# group name -> the function that binds the names its checks may query
BINDERS = {"prices_macro": bind, "sec": bind_sec}


def bind_view_inputs(con: duckdb.DuckDBPyConnection, run_id: str) -> None:
    """The VIEW_INPUTS tables, from config/ and this run's Tiingo file. Publish
    copies them into the version, so view-time health and the app read the
    inputs the run was checked against, never raw/ or config/ (D76)."""
    # the reference source, this run's file only (D37); a run without one binds
    # an empty table of the same shape, so C18 reports the gap instead of breaking
    tiingo = data_root() / "raw" / "tiingo" / "prices" / f"{run_id}.parquet"
    if tiingo.exists():
        con.execute(f"CREATE VIEW raw_tiingo_prices AS SELECT * FROM read_parquet('{tiingo.as_posix()}')")
    else:
        con.execute("CREATE TABLE raw_tiingo_prices (ticker VARCHAR, tiingo_symbol VARCHAR, obs_date DATE, "
                    "close DOUBLE, split_factor DOUBLE, div_cash DOUBLE)")
    bind_config(con)
    cfg = config.sources()
    con.execute("CREATE TABLE required_series (measure VARCHAR)")
    con.executemany("INSERT INTO required_series VALUES (?)", [(m,) for m in cfg["yahoo"]["required_series"]])
    calendar = xc.get_calendar("XNYS", start=cfg["backfill_start"])
    con.execute("CREATE TABLE trading_days (session DATE)")
    con.executemany("INSERT INTO trading_days VALUES (?)", [(d.date(),) for d in calendar.sessions])
    # reviewed Tiingo distributions the reconciliation treats as splits (D40)
    con.execute("CREATE TABLE reclassified (ticker VARCHAR, event_date DATE)")
    reclassified = [(r["ticker"], r["event_date"]) for r in config.corporate_actions()]
    if reclassified:                                  # executemany refuses an empty list
        con.executemany("INSERT INTO reclassified VALUES (?, ?)", reclassified)
    con.execute("CREATE TABLE settings AS SELECT ?::DATE AS backfill_start", [cfg["backfill_start"]])


def load_view_inputs(con: duckdb.DuckDBPyConnection, version: Path, group: Group = PRICES_MACRO) -> None:
    """Bind a published version's inputs/ folder under the group's input names.
    A version published before D76 has none: fail loudly, never fall back to
    config/ or raw/."""
    folder = version / INPUTS_DIR
    missing = [n for n in group.inputs if not (folder / f"{n}.parquet").exists()]
    if missing:
        raise CheckError(f"version {version.name} carries no view-time inputs {missing} in {folder} "
                         "(published before D76: the next publish brings them)")
    for name in group.inputs:
        con.execute(f"CREATE VIEW {name} AS SELECT * FROM read_parquet('{(folder / f'{name}.parquet').as_posix()}')")


def _bind_shared(con, name: str, columns: str) -> None:
    """Bind transform/<name>.sql as a view. DuckDB binds a view when it is
    created, so broken inputs (a dropped column) fail here. That must reach the
    checks that use the view as `broken`, with the real message, not crash the
    whole evaluation: bind a view of the same columns that raises it."""
    sql = (PROJECT_ROOT / "src" / "pharos" / "transform" / f"{name}.sql").read_text(encoding="utf-8")
    try:
        con.execute(f"CREATE VIEW {name} AS {sql}")
    except duckdb.Error as e:
        message = f"{name} could not be built: {type(e).__name__}: {e}".replace("'", "''")
        cols = [c.strip().split(" ", 1) for c in columns.split(",")]
        first, rest = cols[0], cols[1:]
        select = [f"CAST(error('{message}') AS {first[1]}) AS {first[0]}"] + [f"NULL::{t} AS {n}" for n, t in rest]
        con.execute(f"CREATE VIEW {name} AS SELECT {', '.join(select)}")


def _evaluate(con, check: Check, run_id: str, evaluated_at: datetime, context: str) -> dict:
    result = {"run_id": run_id, "context": context, "check_id": check.id, "group": check.group,
              "severity": check.severity, "gate": check.gate, "description": check.description,
              "status": "broken", "failing_row_count": None, "sample": None, "error": None,
              "evaluated_at": evaluated_at}
    try:
        con.execute(f"CREATE OR REPLACE TEMP TABLE failing AS {check.sql}")
    except duckdb.Error as e:
        result["error"] = f"{type(e).__name__}: {e}"
        return result

    n = con.execute("SELECT count(*) FROM failing").fetchone()[0]
    cols = [d[0] for d in con.execute("SELECT * FROM failing LIMIT 0").description]
    # every failing row, not a sample: the detail beneath the status (D42)
    sample = con.execute("SELECT * FROM failing").fetchall()
    result.update(status="pass" if n == 0 else check.severity, failing_row_count=n,
                  sample=json.dumps([dict(zip(cols, row)) for row in sample], default=str) if n else None)
    return result


def _write(results: list[dict], run_id: str, evaluated_at: datetime) -> Path:
    """One write-once file per evaluation, named for the run and the moment."""
    stamp = evaluated_at.strftime("%Y%m%dT%H%M%S%fZ")
    context = results[0]["context"] if results else "run"
    target = data_root() / "health" / "check_results" / f"{run_id}__{stamp}__{context}.parquet"
    target.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("""CREATE TABLE r (run_id VARCHAR, context VARCHAR, check_id VARCHAR, "group" VARCHAR,
                   severity VARCHAR, gate BOOLEAN, description VARCHAR, status VARCHAR,
                   failing_row_count BIGINT, sample VARCHAR, error VARCHAR, evaluated_at TIMESTAMPTZ)""")
    con.executemany("INSERT INTO r VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [[r[k] for k in ("run_id", "context", "check_id", "group", "severity", "gate", "description",
                                     "status", "failing_row_count", "sample", "error", "evaluated_at")]
                     for r in results])
    con.execute(f"COPY r TO '{target.as_posix()}' (FORMAT parquet)")
    con.close()
    return target

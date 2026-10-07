"""Runs checks/*.sql against one staged run and records the results.

A check is a SQL file that returns failing rows; zero rows is a pass. Its header
declares id, severity (error|warn), gate (yes|no) and description, one
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
- A check whose SQL fails is `broken`, and a broken check fails the whole run.
  A control that didn't execute proves nothing, so publish treats `failed`
  like `blocked`: serving/ stays untouched.
- The verdict is `blocked` when any gate check returns rows at error severity.
  Warnings and monitor checks are recorded but never block (D17).
- Results append: each evaluation writes its own file under
  health/check_results/, so re-checking a run never rewrites an earlier answer.
"""

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import exchange_calendars as xc

from pharos import config
from pharos.paths import PROJECT_ROOT, data_root
from pharos.stage import bind_config, bind_raw, complete_runs

CHECKS_DIR = PROJECT_ROOT / "checks"
HEADER_FIELDS = ("id", "severity", "gate", "description")
SAMPLE_ROWS = 5


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


@dataclass(frozen=True)
class Evaluation:
    run_id: str
    verdict: str          # passed | blocked | failed
    results: list[dict]
    path: Path


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
    if not re.fullmatch(re.escape(header["id"]) + r"(_\w+)?", path.stem):
        raise CheckError(f"{path.name}: filename must be {header['id']}.sql or {header['id']}_<name>.sql")

    sql = path.read_text(encoding="utf-8").strip().rstrip(";")
    return Check(header["id"], header["severity"], header["gate"] == "yes",
                 header["description"], sql, path)


def run_checks(run_id: str, directory: Path = CHECKS_DIR, now: datetime | None = None,
               tables: Path | None = None, monitors_only: bool = False) -> Evaluation:
    """Evaluate every check against staging/<run_id>/ and append the results.
    `now` is the clock monitor checks measure against: the run's own time when
    omitted (run time), the wall clock when the health CLI evaluates at view time.
    At view time the health CLI passes `tables` (the published version) and
    `monitors_only`: gate checks tested staged data that hasn't changed, so their
    run-time results stand (D34). Results record which context produced them."""
    checks = discover(directory)
    if monitors_only:
        checks = [c for c in checks if not c.gate]
    context = "view" if monitors_only else "run"
    con = duckdb.connect()
    bind(con, run_id, now, tables)
    evaluated_at = datetime.now(timezone.utc)

    results = [_evaluate(con, c, run_id, evaluated_at, context) for c in checks]
    con.close()

    if any(r["status"] == "broken" for r in results):
        verdict = "failed"
    elif any(r["gate"] and r["status"] == "error" for r in results):
        verdict = "blocked"
    else:
        verdict = "passed"
    return Evaluation(run_id, verdict, results, _write(results, run_id, evaluated_at))


def bind(con: duckdb.DuckDBPyConnection, run_id: str, now: datetime | None = None,
         tables: Path | None = None) -> None:
    """The names a check may query. Anything a check needs that isn't here is a
    runner change, not a path inside a SQL file."""
    runs = complete_runs()
    if run_id not in runs:
        raise CheckError(f"run {run_id} is not complete (a load record is missing)")
    staged = tables or data_root() / "staging" / run_id
    if not staged.is_dir():
        raise CheckError(f"tables for run {run_id} not found: {staged} does not exist")

    for table in ("observations", "series_catalog"):
        con.execute(f"CREATE VIEW {table} AS SELECT * FROM "
                    f"read_parquet('{(staged / f'{table}.parquet').as_posix()}')")
    bind_raw(con, [r for r in runs if r <= run_id])
    # the reference source, this run's file only (D37); a run without one binds
    # an empty table of the same shape, so C18 reports the gap instead of breaking
    tiingo = data_root() / "raw" / "tiingo" / "prices" / f"{run_id}.parquet"
    if tiingo.exists():
        con.execute(f"CREATE VIEW raw_tiingo_prices AS SELECT * FROM read_parquet('{tiingo.as_posix()}')")
    else:
        con.execute("CREATE TABLE raw_tiingo_prices (ticker VARCHAR, tiingo_symbol VARCHAR, obs_date DATE, "
                    "close DOUBLE, split_factor DOUBLE, div_cash DOUBLE)")
    bind_config(con)
    con.execute("CREATE TABLE required_series (measure VARCHAR)")
    con.executemany("INSERT INTO required_series VALUES (?)",
                    [(m,) for m in config.sources()["yahoo"]["required_series"]])
    loads = (data_root() / "health" / "loads").as_posix() + "/*.parquet"
    con.execute(f"CREATE VIEW loads AS SELECT * FROM read_parquet('{loads}')")

    # every raw file on disk with the row count read from its own footer (C05)
    con.execute("CREATE TABLE raw_files (source VARCHAR, dataset VARCHAR, run_id VARCHAR, "
                "path VARCHAR, rows_on_disk BIGINT)")
    for f in sorted((data_root() / "raw").glob("*/*/*.parquet")):
        n = con.execute(f"SELECT sum(num_rows) FROM parquet_file_metadata('{f.as_posix()}')").fetchone()[0]
        con.execute("INSERT INTO raw_files VALUES (?, ?, ?, ?, ?)",
                    [f.parent.parent.name, f.parent.name, f.stem, f.as_posix(), n])

    calendar = xc.get_calendar("XNYS", start=config.sources()["backfill_start"])
    con.execute("CREATE TABLE trading_days (session DATE)")
    con.executemany("INSERT INTO trading_days VALUES (?)",
                    [(d.date(),) for d in calendar.sessions])

    run_at = datetime.strptime(run_id, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    con.execute("CREATE TABLE this_run AS SELECT ? AS run_id, ?::TIMESTAMPTZ AS run_at",
                [run_id, run_at])
    # Views shared by a check and the opening-balance audit, one SQL file each:
    # Yahoo vs Tiingo on sampled dates (C12, D38) and per-series baseline
    # metrics (C13, D39).
    _bind_shared(con, "reconcile_sample", "ticker VARCHAR, obs_date DATE, yahoo_close DOUBLE, "
                 "tiingo_close DOUBLE, tiingo_raw_close DOUBLE, rel_diff DOUBLE, pick VARCHAR")
    _bind_shared(con, "baseline_metrics", "series_id VARCHAR, first_date DATE, last_date DATE, row_count BIGINT")
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
    con.execute("CREATE TABLE clock AS SELECT ?::TIMESTAMPTZ AS now", [now or run_at])

    # every run's manifest (D31); none before the first refresh. The run being
    # checked has no manifest yet: refresh writes it after publish.
    manifests = data_root() / "health" / "run_manifest"
    if any(manifests.glob("*.parquet")):
        con.execute(f"CREATE VIEW run_manifest AS SELECT * FROM "
                    f"read_parquet('{manifests.as_posix()}/*.parquet', union_by_name = true)")
    else:
        con.execute("CREATE TABLE run_manifest (run_id VARCHAR, status VARCHAR, published_at TIMESTAMPTZ)")


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
    result = {"run_id": run_id, "context": context, "check_id": check.id, "severity": check.severity,
              "gate": check.gate, "description": check.description,
              "status": "broken", "failing_row_count": None, "sample": None, "error": None,
              "evaluated_at": evaluated_at}
    try:
        con.execute(f"CREATE OR REPLACE TEMP TABLE failing AS {check.sql}")
    except duckdb.Error as e:
        result["error"] = f"{type(e).__name__}: {e}"
        return result

    n = con.execute("SELECT count(*) FROM failing").fetchone()[0]
    cols = [d[0] for d in con.execute("SELECT * FROM failing LIMIT 0").description]
    sample = con.execute(f"SELECT * FROM failing LIMIT {SAMPLE_ROWS}").fetchall()
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
    con.execute("""CREATE TABLE r (run_id VARCHAR, context VARCHAR, check_id VARCHAR, severity VARCHAR, gate BOOLEAN,
                   description VARCHAR, status VARCHAR, failing_row_count BIGINT, sample VARCHAR,
                   error VARCHAR, evaluated_at TIMESTAMPTZ)""")
    con.executemany("INSERT INTO r VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [[r[k] for k in ("run_id", "context", "check_id", "severity", "gate", "description", "status",
                                     "failing_row_count", "sample", "error", "evaluated_at")]
                     for r in results])
    con.execute(f"COPY r TO '{target.as_posix()}' (FORMAT parquet)")
    con.close()
    return target

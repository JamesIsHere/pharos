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
  required series per ticker), the XNYS trading calendar (D24) and a
  one-row this_run table.
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


def run_checks(run_id: str, directory: Path = CHECKS_DIR) -> Evaluation:
    """Evaluate every check against staging/<run_id>/ and append the results."""
    checks = discover(directory)
    con = duckdb.connect()
    bind(con, run_id)
    evaluated_at = datetime.now(timezone.utc)

    results = [_evaluate(con, c, run_id, evaluated_at) for c in checks]
    con.close()

    if any(r["status"] == "broken" for r in results):
        verdict = "failed"
    elif any(r["gate"] and r["status"] == "error" for r in results):
        verdict = "blocked"
    else:
        verdict = "passed"
    return Evaluation(run_id, verdict, results, _write(results, run_id, evaluated_at))


def bind(con: duckdb.DuckDBPyConnection, run_id: str) -> None:
    """The names a check may query. Anything a check needs that isn't here is a
    runner change, not a path inside a SQL file."""
    runs = complete_runs()
    if run_id not in runs:
        raise CheckError(f"run {run_id} is not complete (a load record is missing)")
    staged = data_root() / "staging" / run_id
    if not staged.is_dir():
        raise CheckError(f"run {run_id} has not been staged: {staged} does not exist")

    for table in ("observations", "series_catalog"):
        con.execute(f"CREATE VIEW {table} AS SELECT * FROM "
                    f"read_parquet('{(staged / f'{table}.parquet').as_posix()}')")
    bind_raw(con, [r for r in runs if r <= run_id])
    bind_config(con)
    con.execute("CREATE TABLE required_series (measure VARCHAR)")
    con.executemany("INSERT INTO required_series VALUES (?)",
                    [(m,) for m in config.sources()["yahoo"]["required_series"]])
    loads = (data_root() / "health" / "loads").as_posix() + "/*.parquet"
    con.execute(f"CREATE VIEW loads AS SELECT * FROM read_parquet('{loads}')")

    calendar = xc.get_calendar("XNYS", start=config.sources()["backfill_start"])
    con.execute("CREATE TABLE trading_days (session DATE)")
    con.executemany("INSERT INTO trading_days VALUES (?)",
                    [(d.date(),) for d in calendar.sessions])

    run_at = datetime.strptime(run_id, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    con.execute("CREATE TABLE this_run AS SELECT ? AS run_id, ?::TIMESTAMPTZ AS run_at",
                [run_id, run_at])


def _evaluate(con, check: Check, run_id: str, evaluated_at: datetime) -> dict:
    result = {"run_id": run_id, "check_id": check.id, "severity": check.severity,
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
    target = data_root() / "health" / "check_results" / f"{run_id}__{stamp}.parquet"
    target.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("""CREATE TABLE r (run_id VARCHAR, check_id VARCHAR, severity VARCHAR, gate BOOLEAN,
                   description VARCHAR, status VARCHAR, failing_row_count BIGINT, sample VARCHAR,
                   error VARCHAR, evaluated_at TIMESTAMPTZ)""")
    con.executemany("INSERT INTO r VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [[r[k] for k in ("run_id", "check_id", "severity", "gate", "description", "status",
                                     "failing_row_count", "sample", "error", "evaluated_at")]
                     for r in results])
    con.execute(f"COPY r TO '{target.as_posix()}' (FORMAT parquet)")
    con.close()
    return target

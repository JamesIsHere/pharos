"""Opening-balance audit (M1 step 7): reconcile what serving/CURRENT points at
against the second source, then record the baseline C13 watches (D39).

- The comparisons are reconcile_sample, the same SQL C12 runs nightly (D38),
  seeded by the published run's id: 5 random dates per ticker plus the latest
  common date, Yahoo's split-adjusted close against Tiingo's.
- Every comparison is written, pass or fail, to
  health/audit/<run_id>__<stamp>.parquet, one write-once file per attempt.
- The baseline is recorded only when the whole audit passes: every active
  watchlist ticker has at least 5 random dates compared, and no compared date
  (any ticker, the latest included) differs by more than 0.5%. Otherwise
  nothing is recorded and the CLI exits 1. A baseline that carries a known
  disagreement would be the anchor C13 measures drift against.
- The baseline is written once, to health/baseline/<run_id>.parquet. An audit
  when one already exists stops before doing anything: the opening balance is
  not silently replaced.
"""

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from pharos.checks import bind
from pharos.paths import data_root
from pharos.publish import current_version

TOLERANCE = 0.005
MIN_RANDOM_DATES = 5


class AuditError(RuntimeError):
    pass


@dataclass(frozen=True)
class Audit:
    run_id: str
    failures: list[dict]       # rows of the comparisons file with a problem
    comparisons: Path
    baseline: Path | None      # None when the audit failed

    @property
    def passed(self) -> bool:
        return self.baseline is not None


def baseline_dir() -> Path:
    return data_root() / "health" / "baseline"


def audit() -> Audit:
    existing = sorted(baseline_dir().glob("*.parquet"))
    if existing:
        raise AuditError(f"a baseline is already recorded: {existing[0]}")
    version = current_version()
    if version is None:
        raise AuditError("nothing is published: serving/CURRENT does not exist")
    run_id = version.name

    con = duckdb.connect()
    bind(con, run_id, tables=version)
    con.execute(f"""
        CREATE TABLE comparisons AS
        SELECT s.ticker, s.obs_date, s.pick, s.yahoo_close, s.tiingo_close, s.tiingo_raw_close, s.rel_diff,
               CASE WHEN abs(s.rel_diff) > {TOLERANCE} THEN 'over tolerance' END AS problem
        FROM reconcile_sample AS s

        UNION ALL

        -- an active ticker without enough random dates compared, Tiingo
        -- missing it entirely included: not audited is not passed
        SELECT w.ticker, NULL, NULL, NULL, NULL, NULL, NULL,
               'only ' || count(s.ticker) || ' random dates compared' AS problem
        FROM watchlist_windows AS w
        CROSS JOIN clock AS c
        LEFT JOIN reconcile_sample AS s ON s.ticker = w.ticker AND s.pick = 'random'
        WHERE w.active_to IS NULL OR w.active_to >= CAST(timezone('UTC', c.now) AS DATE)
        GROUP BY w.ticker
        HAVING count(s.ticker) < {MIN_RANDOM_DATES}
    """)

    stamp = datetime.now(timezone.utc)
    comparisons = data_root() / "health" / "audit" / f"{run_id}__{stamp.strftime('%Y%m%dT%H%M%S%fZ')}.parquet"
    comparisons.parent.mkdir(parents=True, exist_ok=True)
    con.execute(f"COPY (SELECT * FROM comparisons ORDER BY ticker, obs_date) TO '{comparisons.as_posix()}' "
                "(FORMAT parquet)")

    cols = [d[0] for d in con.execute("SELECT * FROM comparisons LIMIT 0").description]
    failures = [dict(zip(cols, row)) for row in con.execute(
        "SELECT * FROM comparisons WHERE problem IS NOT NULL ORDER BY ticker, obs_date").fetchall()]

    baseline = None
    if not failures:
        baseline = baseline_dir() / f"{run_id}.parquet"
        baseline.parent.mkdir(parents=True, exist_ok=True)
        # built under a temporary name, so a crash never leaves a partial
        # baseline that would block the next attempt
        pending = baseline.with_suffix(".parquet.pending")
        con.execute("""CREATE TABLE recorded AS
                       SELECT series_id, first_date, last_date, row_count,
                              ? AS audit_run_id, ?::TIMESTAMPTZ AS recorded_at
                       FROM baseline_metrics ORDER BY series_id""", [run_id, stamp])
        con.execute(f"COPY recorded TO '{pending.as_posix()}' (FORMAT parquet)")
        os.replace(pending, baseline)
    con.close()
    return Audit(run_id, failures, comparisons, baseline)

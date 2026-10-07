"""Opening-balance audit (M1 step 7): reconcile what serving/CURRENT points at
against the second source, then record the baseline C13 watches (D39, D41).

- The comparison is reconcile_all, every date both sources have, Yahoo's
  split-adjusted close against Tiingo's (the same SQL C12 samples from).
- Every comparison is written, pass or fail, to
  health/audit/<run_id>__<stamp>.parquet, one write-once file per attempt.
- A date over 0.5% is classified by shape (D41). Isolated (the common dates
  either side both within tolerance) is a print disagreement: two vendors'
  closing print for one day, reported but not blocking. Anything else blocks:
  two or more consecutive dates over, or one at either end of a ticker's
  common range, where isolation can't be shown. Adjustment, mapping and
  identity errors move every date on one side of an event, so they can't
  look isolated.
- The baseline is recorded only when nothing blocks and every active watchlist
  ticker has at least 5 common dates compared. Otherwise nothing is recorded
  and the CLI exits 1.
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
MIN_DATES = 5
PRINT_DISAGREEMENT = "print disagreement"


class AuditError(RuntimeError):
    pass


@dataclass(frozen=True)
class Audit:
    run_id: str
    failures: list[dict]       # blocking rows of the comparisons file
    disagreements: list[dict]  # isolated print disagreements: reported, not blocking
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
        WITH flagged AS (
            SELECT *, abs(rel_diff) > {TOLERANCE} AS over,
                   lag(abs(rel_diff) > {TOLERANCE}) OVER w AS prev_over,
                   lead(abs(rel_diff) > {TOLERANCE}) OVER w AS next_over
            FROM reconcile_all
            WINDOW w AS (PARTITION BY ticker ORDER BY obs_date)
        )
        SELECT ticker, obs_date, yahoo_close, tiingo_close, tiingo_raw_close, rel_diff,
               CASE WHEN NOT over THEN NULL
                    WHEN prev_over = false AND next_over = false THEN '{PRINT_DISAGREEMENT}'
                    ELSE 'disagreement not isolated' END AS problem
        FROM flagged

        UNION ALL

        -- an active ticker with too few dates compared, Tiingo missing it
        -- entirely included: not audited is not passed
        SELECT w.ticker, NULL, NULL, NULL, NULL, NULL,
               'only ' || count(a.ticker) || ' common dates compared' AS problem
        FROM watchlist_windows AS w
        CROSS JOIN clock AS c
        LEFT JOIN reconcile_all AS a ON a.ticker = w.ticker
        WHERE w.active_to IS NULL OR w.active_to >= CAST(timezone('UTC', c.now) AS DATE)
        GROUP BY w.ticker
        HAVING count(a.ticker) < {MIN_DATES}
    """)

    stamp = datetime.now(timezone.utc)
    comparisons = data_root() / "health" / "audit" / f"{run_id}__{stamp.strftime('%Y%m%dT%H%M%S%fZ')}.parquet"
    comparisons.parent.mkdir(parents=True, exist_ok=True)
    con.execute(f"COPY (SELECT * FROM comparisons ORDER BY ticker, obs_date) TO '{comparisons.as_posix()}' "
                "(FORMAT parquet)")

    cols = [d[0] for d in con.execute("SELECT * FROM comparisons LIMIT 0").description]
    flagged = [dict(zip(cols, row)) for row in con.execute(
        "SELECT * FROM comparisons WHERE problem IS NOT NULL ORDER BY ticker, obs_date").fetchall()]
    failures = [f for f in flagged if f["problem"] != PRINT_DISAGREEMENT]
    disagreements = [f for f in flagged if f["problem"] == PRINT_DISAGREEMENT]

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
    return Audit(run_id, failures, disagreements, comparisons, baseline)

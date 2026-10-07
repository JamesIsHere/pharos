"""`pharos health`: the health of what is being served, now (D34).

What serving/CURRENT points at is judged two ways:
- Gate checks tested the staged data at run time. That data hasn't changed
  since, so their recorded results stand: the latest run-context row per check
  for the published run. Nothing is re-run and staging/ isn't needed.
- Monitor checks test the world, so they are re-run now against the published
  tables with the wall clock: a pipeline that silently stopped shows red here
  even though its last run was green (C01, C02, C17).

Roll-up (design.md section 7): red on any error or broken check, on nothing
published, or when the latest run did not publish (blocked or failed: serving
is behind what was attempted); yellow on warnings only; green otherwise.

health/latest.md is rewritten on every evaluation, so it always says what the
last look found. It is plain text with aligned columns. The app evaluates
with record=False: it writes neither latest.md nor check_results (D43).
"""

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from pharos.checks import discover, run_checks
from pharos.paths import data_root
from pharos.publish import current_version


@dataclass(frozen=True)
class Health:
    status: str                    # green | yellow | red
    reasons: list[str]             # why it isn't green, run-level first
    evaluated_at: datetime
    published_run: str | None
    published_at: datetime | None
    latest_run: str | None
    latest_run_status: str | None
    results: list[dict]            # one per check, gate (recorded) then monitor (now)


def evaluate(now: datetime | None = None, record: bool = True) -> Health:
    now = now or datetime.now(timezone.utc)
    manifest = _manifest()
    latest_run, latest_status = (manifest[-1]["run_id"], manifest[-1]["status"]) if manifest else (None, None)
    published = [m for m in manifest if m["status"] == "published"]
    published_at = published[-1]["published_at"] if published else None

    version = current_version()
    reasons, results = [], []
    if version is None:
        reasons.append("nothing has been published")
    else:
        results = _recorded_gate_results(version.name) + \
            run_checks(version.name, now=now, tables=version, monitors_only=True, record=record).results
    if latest_status in ("blocked", "failed"):
        reasons.append(f"latest run {latest_run} {latest_status}: serving still shows {version.name if version else 'nothing'}")

    for r in results:
        if r["status"] in ("error", "broken"):
            reasons.append(f"{r['check_id']} {r['status']}: {r['error'] or r['description']}")
    warns = [r for r in results if r["status"] == "warn"]
    status = "red" if reasons else "yellow" if warns else "green"
    if status == "yellow":
        reasons = [f"{r['check_id']} warn: {r['description']}" for r in warns]

    health = Health(status, reasons, now, version.name if version else None, published_at,
                    latest_run, latest_status, results)
    if record:
        write_latest(health)
    return health


def _manifest() -> list[dict]:
    """One row per run (run-level fields), oldest first."""
    folder = data_root() / "health" / "run_manifest"
    if not any(folder.glob("*.parquet")):
        return []
    rel = duckdb.sql(f"""SELECT DISTINCT run_id, status, published_at
                         FROM read_parquet('{folder.as_posix()}/*.parquet', union_by_name = true)
                         ORDER BY run_id""")
    return [dict(zip(rel.columns, row)) for row in rel.fetchall()]


def _recorded_gate_results(run_id: str) -> list[dict]:
    """The latest run-time result of every gate check for run_id. A gate check
    with no recorded result is reported broken: unproven is not passed."""
    folder = data_root() / "health" / "check_results"
    rows = {}
    if any(folder.glob(f"{run_id}__*.parquet")):
        source = f"read_parquet('{folder.as_posix()}/{run_id}__*.parquet', union_by_name = true)"
        # results written before D34 have no context column: all of them are run-time
        has_context = "context" in duckdb.sql(f"SELECT * FROM {source} LIMIT 0").columns
        context = "coalesce(context, 'run')" if has_context else "'run'"
        rel = duckdb.sql(f"""SELECT * REPLACE ({context} AS context) FROM {source}
                             WHERE gate AND {context} = 'run'
                             QUALIFY row_number() OVER (PARTITION BY check_id ORDER BY evaluated_at DESC) = 1""")             if has_context else duckdb.sql(f"""SELECT *, 'run' AS context FROM {source} WHERE gate
                             QUALIFY row_number() OVER (PARTITION BY check_id ORDER BY evaluated_at DESC) = 1""")
        rows = {r["check_id"]: r for r in (dict(zip(rel.columns, row)) for row in rel.fetchall())}
    out = []
    for check in (c for c in discover() if c.gate):
        out.append(rows.get(check.id) or {
            "run_id": run_id, "context": "run", "check_id": check.id, "severity": check.severity,
            "gate": True, "description": check.description, "status": "broken", "failing_row_count": None,
            "sample": None, "error": "no recorded result for this run", "evaluated_at": None})
    return out


def write_latest(health: Health) -> Path:
    target = data_root() / "health" / "latest.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render(health, color=False), encoding="utf-8")
    return target


def render(health: Health, color: bool) -> str:
    paint = {"green": "\033[32m", "yellow": "\033[33m", "red": "\033[31m",
             "pass": "\033[32m", "warn": "\033[33m", "error": "\033[31m", "broken": "\033[31m"}
    def c(text, key):
        return f"{paint[key]}{text}\033[0m" if color and key in paint else text

    lines = [
        "# Pharos health",
        "",
        f"Status:          {c(health.status.upper(), health.status)}",
        f"Evaluated at:    {health.evaluated_at:%Y-%m-%d %H:%M:%S %Z}",
        f"Serving:         {health.published_run or 'nothing published'}"
        + (f" (published {health.published_at:%Y-%m-%d %H:%M %Z})" if health.published_at else ""),
        f"Latest run:      {health.latest_run or 'none'} ({health.latest_run_status or 'no manifest'})",
        "",
    ]
    if health.reasons:
        lines += ["Why not green:"] + [f"- {r}" for r in health.reasons] + [""]

    header = ("Check", "Kind", "Severity", "Status", "Rows", "Evaluated", "Description")
    table = [(r["check_id"], "gate" if r["gate"] else "monitor", r["severity"], r["status"],
              "" if r["failing_row_count"] is None else str(r["failing_row_count"]),
              "at run" if r["gate"] else "now", r["description"]) for r in health.results]
    widths = [max(len(row[i]) for row in [header] + table) for i in range(len(header) - 1)]
    def fmt(row, status_key=None):
        cells = [row[i].ljust(widths[i]) for i in range(len(widths))]
        if status_key:
            cells[3] = c(cells[3], status_key)
        return "| " + " | ".join(cells + [row[-1]]) + " |"
    lines += ["```", fmt(header), "|-" + "-|-".join("-" * w for w in widths) + "-|-" + "-" * 11 + "-|"]
    lines += [fmt(row, row[3]) for row in table]
    lines += ["```"]

    failing = [r for r in health.results if r["status"] != "pass"]
    if failing:
        lines += ["", "First failing rows:"]
        for r in failing:
            first = json.loads(r["sample"])[0] if r["sample"] else r["error"]
            lines.append(f"- {r['check_id']}: {first}")
    return "\n".join(lines) + "\n"

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

Source groups (D81, D98, D115): the served version records each group's
outcome and the run its tables came from (serving/<run>/groups.parquet). A
group's recorded gate results are those of that source run, so a carried-forward
group shows the results its served tables passed. A group not in the version
(never published) has nothing to judge: each of its checks is reported
`not_served`, never `broken`, and C36 carries the red (D83, D116).

Acknowledged expected states (D46): config/expected_states.csv names reviewed
rows of warn checks (ATVI's missing history, a value FRED withdrew), each by
the values of the columns its check declares as ack_key (D75). A warn
check whose every failing row is acknowledged reports `acknowledged` and
doesn't turn health yellow; its rows stay listed, each carrying its note. A new
row still warns, and an acknowledgment that matches no failing row warns as
check `expected_states`, so the file can't go stale quietly. Error checks
can't be acknowledged. The recorded check_results keep the check's own status.

health/latest.md is rewritten on every evaluation, so it always says what the
last look found. It is plain text with aligned columns. An evaluation
with record=False writes neither latest.md nor check_results (D43).
"""

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from pharos import config
from pharos.checks import discover, run_checks
from pharos.db import query, records
from pharos.groups import GROUPS, HELD
from pharos.paths import data_root
from pharos.publish import composition, current_version

NOT_SERVED = "not_served"   # a check of a group the served version doesn't hold


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
    groups: dict[str, dict] = None  # served version: group -> outcome, source_run, reason


def evaluate(now: datetime | None = None, record: bool = True) -> Health:
    now = now or datetime.now(timezone.utc)
    manifest = _manifest()
    latest_run, latest_status = (manifest[-1]["run_id"], manifest[-1]["status"]) if manifest else (None, None)
    published = [m for m in manifest if m["status"] == "published"]
    published_at = published[-1]["published_at"] if published else None

    version = current_version()
    reasons, results, groups = [], [], {}
    if version is None:
        reasons.append("nothing has been published")
    else:
        groups = composition(version)
        results = _recorded_gate_results(version, groups) + \
            run_checks(version.name, now=now, tables=version, monitors_only=True, record=record).results + \
            _not_served(groups, gate=False)
        results = acknowledge(results)
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
                    latest_run, latest_status, results, groups)
    if record:
        write_latest(health)
    return health


STATES_CHECK = "expected_states"


def acknowledgments() -> list[dict]:
    """config/expected_states.csv, each row checked against checks/: the check
    must exist, warn, and declare an ack_key as long as the row's key.
    Acknowledging an error would hide a defect in the data."""
    checks = {c.id: c for c in discover()}
    states = config.expected_states()
    for s in states:
        check = checks.get(s["check_id"])
        if check is None:
            raise ValueError(f"expected_states.csv: no check {s['check_id']!r} in checks/")
        if check.severity != "warn":
            raise ValueError(f"expected_states.csv: {s['check_id']} is {check.severity}-severity; "
                             "only a warn check's rows can be acknowledged")
        if not check.ack_key:
            raise ValueError(f"expected_states.csv: {s['check_id']} declares no ack_key, "
                             "so its rows can't be acknowledged (D75)")
        if len(s["key"]) != len(check.ack_key):
            raise ValueError(f"expected_states.csv: {s['check_id']} key {' | '.join(s['key'])!r} needs "
                             f"{len(check.ack_key)} parts: {' | '.join(check.ack_key)}")
    return states


def acknowledge(results: list[dict]) -> list[dict]:
    """The results with acknowledged rows marked, plus the expected_states
    check: warn when an acknowledgment matched no failing row (D46). A row
    matches on its check's ack_key columns, so a different problem for the same
    series never hides under an old note (D75); a broken check's rows are
    unknown, so its acknowledgments are neither applied nor called stale."""
    if not results:
        return results
    states = acknowledgments()
    ack_keys = {c.id: c.ack_key for c in discover()}
    out, matched = [], set()
    for r in results:
        notes = {s["key"]: s["note"] for s in states if s["check_id"] == r["check_id"]}
        if not notes or r["status"] == "broken":
            matched |= {(r["check_id"], *k) for k in notes} if r["status"] == "broken" else set()
            out.append(r)
            continue
        rows = json.loads(r["sample"]) if r["sample"] else []
        for row in rows:
            absent = [c for c in ack_keys[r["check_id"]] if c not in row]
            if absent:
                raise ValueError(f"{r['check_id']}: ack_key columns {absent} are not in its rows")
            key = tuple("" if row[c] is None else str(row[c]) for c in ack_keys[r["check_id"]])
            row["acknowledged"] = notes.get(key)
            if key in notes:
                matched.add((r["check_id"], *key))
        n = sum(row["acknowledged"] is not None for row in rows)
        status = "acknowledged" if r["status"] == "warn" and n == len(rows) else r["status"]
        out.append({**r, "status": status, "sample": json.dumps(rows) if rows else r["sample"]})
    stale = [{"check_id": s["check_id"], "key": " | ".join(s["key"]), "note": s["note"]}
             for s in states if (s["check_id"], *s["key"]) not in matched]
    last = results[-1]                    # a monitor: evaluate() lists gate results first
    out.append({"run_id": last["run_id"], "context": last.get("context"), "check_id": STATES_CHECK,
                "group": "run", "severity": "warn", "gate": False,
                "description": "acknowledged expected state that no longer fails: remove it from config/expected_states.csv",
                "status": "warn" if stale else "pass", "failing_row_count": len(stale),
                "sample": json.dumps(stale) if stale else None, "error": None,
                "evaluated_at": max((r["evaluated_at"] for r in results if r["evaluated_at"]), default=None)})
    return out


def _manifest() -> list[dict]:
    """One row per run (run-level fields), oldest first."""
    folder = data_root() / "health" / "run_manifest"
    if not any(folder.glob("*.parquet")):
        return []
    return records(f"""SELECT DISTINCT run_id, status, published_at
                       FROM read_parquet('{folder.as_posix()}/*.parquet', union_by_name = true)
                       ORDER BY run_id""")


def _served_by(groups: dict[str, dict]) -> dict[str, str]:
    """group -> the run whose tables the version serves, for groups it holds."""
    return {g: c["source_run"] for g, c in groups.items() if c["outcome"] in HELD and c["source_run"]}


def _not_served(groups: dict[str, dict], gate: bool) -> list[dict]:
    """One result per check (gate or monitor) of each group the version doesn't hold (D98)."""
    held = _served_by(groups)
    out = []
    for check in discover():
        if check.gate == gate and check.group in GROUPS and check.group not in held:
            outcome = (groups.get(check.group) or {}).get("outcome") or "never published"
            out.append({"run_id": None, "context": "run" if gate else "view", "check_id": check.id,
                        "group": check.group, "severity": check.severity, "gate": check.gate,
                        "description": check.description, "status": NOT_SERVED, "failing_row_count": None,
                        "sample": None, "evaluated_at": None,
                        "error": f"group {check.group} is not in the served version ({outcome}; C36)"})
    return out


def _recorded_gate_results(version: Path, groups: dict[str, dict]) -> list[dict]:
    """The latest run-time result of every gate check of each group the version
    holds, from the run its tables came from (D115). A gate check with no
    recorded result is reported broken: unproven is not passed."""
    out = []
    for group, source_run in _served_by(groups).items():
        out += [r for r in _recorded_for_run(source_run) if r["group"] == group]
    return out + _not_served(groups, gate=True)


def _recorded_for_run(run_id: str) -> list[dict]:
    """The latest run-time result of every gate check for run_id."""
    folder = data_root() / "health" / "check_results"
    rows = {}
    if any(folder.glob(f"{run_id}__*.parquet")):
        source = f"read_parquet('{folder.as_posix()}/{run_id}__*.parquet', union_by_name = true)"
        # results written before D34 have no context column: all of them are run-time
        has_context = "context" in query(f"SELECT * FROM {source} LIMIT 0")[0]
        latest = "QUALIFY row_number() OVER (PARTITION BY check_id ORDER BY evaluated_at DESC) = 1"
        sql = (f"SELECT * REPLACE (coalesce(context, 'run') AS context) FROM {source} "
               f"WHERE gate AND coalesce(context, 'run') = 'run' {latest}") if has_context else \
              f"SELECT *, 'run' AS context FROM {source} WHERE gate {latest}"
        rows = {r["check_id"]: r for r in records(sql)}
    out = []
    for check in (c for c in discover() if c.gate):
        if check.id in rows:   # the header is the authority on a check's group (results before D115 lack it)
            rows[check.id]["group"] = check.group
        out.append(rows.get(check.id) or {
            "run_id": run_id, "context": "run", "check_id": check.id, "group": check.group,
            "severity": check.severity,
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
    if health.groups:
        lines.append("Groups served:")
        for name, g in sorted(health.groups.items()):
            src = f" from {g['source_run']}" if g["source_run"] else ""
            why = f": {g['reason'].strip().splitlines()[-1]}" if g["reason"] else ""
            lines.append(f"- {name}: {g['outcome']}{src}{why}")
        lines.append("")
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

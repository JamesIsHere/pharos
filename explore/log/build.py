"""Log page: one row per refresh run, newest first, built from what every run already records under
<data root>/health/ (run_manifest, loads, check_results) and the nightly task's console logs in <data root>/logs/.

A run is any run_id with a manifest or a load record. A run with load records but no manifest started and never
finished: outcome "died". A past calendar day (machine-local) with no nightly log is a row of its own: "missed".
Reads only; writes log.html next to this file."""
import html as h
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb

from pharos.paths import data_root

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "lib"))
import nav  # noqa: E402

DATA = data_root()
HEALTH = DATA / "health"
LOGS = DATA / "logs"
TASK = "Pharos Nightly Refresh"
LOCAL = datetime.now().astimezone().tzinfo
GROUPS = ("prices_macro", "sec", "companyfacts")
TAKE_LOG_LINES = 15


def utc_of(run_id: str) -> datetime:
    return datetime.strptime(run_id, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)


def local(ts: datetime) -> str:
    return ts.astimezone(LOCAL).strftime("%Y-%m-%d %H:%M")


def duration(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 60}m{s % 60:02d}s" if s >= 60 else f"{s}s"


def first_line(text: str | None) -> str:
    """A traceback's last line is its message; anything else, its first line."""
    if not text:
        return ""
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    if lines and "Traceback" in lines[0]:
        head = lines[0].split("Traceback")[0].rstrip(": ")
        return (head + ": " if head else "") + lines[-1].strip()
    return lines[0] if lines else ""


con = duckdb.connect()
manifest = con.sql(f"""select * from read_parquet('{(HEALTH / 'run_manifest').as_posix()}/*.parquet',
                       union_by_name=true)""").pl()
loads = con.sql(f"""select * from read_parquet('{(HEALTH / 'loads').as_posix()}/*.parquet', union_by_name=true)
                    order by loaded_at""").pl()
checks = con.sql(f"""select * from read_parquet('{(HEALTH / 'check_results').as_posix()}/*.parquet',
                     union_by_name=true) where context = 'run' and status <> 'pass'
                     order by severity, check_id""").pl()

# Nightly logs: start time from the file name (machine-local), full text kept for matching and the tail.
nightly = []
for p in sorted(LOGS.glob("nightly-*.log")):
    started = datetime.strptime(p.stem, "nightly-%Y%m%d-%H%M%S").replace(tzinfo=LOCAL)
    nightly.append({"path": p, "started": started, "text": p.read_text(encoding="utf-8", errors="replace")})

run_ids = sorted(set(manifest["run_id"].to_list()) | set(loads["run_id"].to_list()), reverse=True)


def nightly_log_for(run_id: str) -> dict | None:
    """The nightly log that names the run_id; failing that (a run that died before printing it), the latest log
    that names no run and started at most 190 minutes (the script's 180-minute mirror wait, plus slack) before it."""
    for n in nightly:
        if f"run {run_id}" in n["text"]:
            return n
    t = utc_of(run_id)
    near = [n for n in nightly if not re.search(r"run \d{8}T\d{6}Z", n["text"])
            and timedelta(0) <= t - n["started"] <= timedelta(minutes=190)]
    return max(near, key=lambda n: n["started"]) if near else None


rows = []
for rid in run_ids:
    m = manifest.filter(manifest["run_id"] == rid)
    ld = loads.filter(loads["run_id"] == rid)
    log = nightly_log_for(rid)
    r = {"run_id": rid, "when": utc_of(rid), "trigger": "nightly" if log else "by hand", "log": log,
         "groups": {}, "loads": ld.to_dicts(), "checks": checks.filter(checks["run_id"] == rid).to_dicts(),
         "errors": []}
    if m.height == 0:
        r["outcome"] = "died"
        r["took"] = ""
        r["checks_txt"] = ""
        last = log["text"].strip().splitlines()[-1].split("  ", 1)[-1] if log else ""
        r["note"] = (f"{ld.height} file{'s' if ld.height != 1 else ''} landed, no finish record"
                     + (f"; log stops after '{last}'" if last else ""))
    else:
        rec = m.row(0, named=True)
        r["took"] = duration((rec["finished_at"] - rec["started_at"]).total_seconds())
        for g in m.filter(m["group"].is_not_null()).unique(subset=["group"]).iter_rows(named=True):
            r["groups"][g["group"]] = {"outcome": g["group_outcome"], "reason": first_line(g["group_reason"]),
                                       "source_run": g["group_source_run"]}
        status = rec["status"]
        partial = any(g["outcome"] in ("carried_forward", "never_published") for g in r["groups"].values())
        r["outcome"] = "partial" if status == "published" and partial else status
        err, warn = int(m["checks_error"].max() or 0), int(m["checks_warn"].max() or 0)
        broken, ack = int(m["checks_broken"].max() or 0), int(m["checks_acknowledged"].max() or 0)
        r["checks_txt"] = ", ".join([f"{err} error", f"{warn} warn"] + ([f"{broken} broken"] if broken else [])
                                    + ([f"{ack} ack"] if ack else []))
        for col in ("error", "dataset_error"):
            r["errors"] += [e for e in m[col].drop_nulls().unique().to_list() if e]
        notes = [f"{g}: {v['reason']}" for g, v in r["groups"].items() if v["reason"]]
        if not r["groups"]:   # before source groups (D113): one publish, and prices + macro were everything
            r["groups"]["prices_macro"] = {"outcome": status, "reason": "", "source_run": rid}
        if r["errors"]:
            notes.append(first_line(r["errors"][0]))
        r["note"] = "; ".join(notes)
    rows.append(r)

# Missed: past machine-local days since the first nightly log with no nightly log at all.
if nightly:
    have = {n["started"].date() for n in nightly}
    day, today = nightly[0]["started"].date(), datetime.now(LOCAL).date()
    while day < today:
        if day not in have:
            rows.append({"run_id": "", "when": datetime(day.year, day.month, day.day, 23, 59, tzinfo=LOCAL),
                         "trigger": "nightly", "outcome": "missed", "took": "", "groups": {}, "checks_txt": "",
                         "note": "no nightly log for this day: the scheduled task did not start", "loads": [],
                         "checks": [], "errors": [], "log": None, "day_only": True})
        day += timedelta(days=1)
rows.sort(key=lambda r: r["when"], reverse=True)

# Next scheduled start, from Task Scheduler itself.
q = subprocess.run(["schtasks", "/query", "/tn", TASK, "/fo", "csv", "/nh"], capture_output=True, text=True)
next_run = q.stdout.strip().split('","')[1] if q.returncode == 0 else f"task '{TASK}' not registered"

GROUP_WORD = {"published": "published", "carried_forward": "carried fwd", "not_run": "not run",
              "never_published": "never published", "blocked": "blocked", "failed": "failed"}


def cell_group(r: dict, g: str) -> str:
    v = r["groups"].get(g)
    return GROUP_WORD.get(v["outcome"], v["outcome"]) if v else ""


def detail(r: dict) -> str:
    parts = []
    if r.get("day_only"):
        return "<p>No run was attempted. Nothing landed, nothing changed in serving.</p>"
    parts.append(f"<p class=muted>run_id {r['run_id']}"
                 + (f" &middot; log {h.escape(r['log']['path'].name)}" if r["log"] else " &middot; no nightly log (run by hand)")
                 + "</p>")
    if r["groups"]:
        gl = "".join(f"<tr><td>{h.escape(g)}</td><td>{GROUP_WORD.get(v['outcome'], v['outcome'])}</td>"
                     f"<td>{h.escape(v['source_run'] or '')}</td><td>{h.escape(v['reason'])}</td></tr>"
                     for g, v in sorted(r["groups"].items()))
        parts.append("<h3>Groups</h3><table><tr><th>Group</th><th>Outcome</th><th>Served from run</th>"
                     f"<th>Reason</th></tr>{gl}</table>")
    if r["loads"]:
        ll = "".join(f"<tr><td>{h.escape(x['source'])}/{h.escape(x['dataset'])}</td>"
                     f"<td class=num>{x['rows_written']:,}</td><td>{local(x['loaded_at'])}</td>"
                     f"<td>{h.escape(x['note'] or '')}</td></tr>" for x in r["loads"])
        parts.append("<h3>Files landed</h3><table><tr><th>Dataset</th><th class=num>Rows</th><th>Landed at</th>"
                     f"<th>Note</th></tr>{ll}</table>")
    else:
        parts.append("<h3>Files landed</h3><p>None.</p>")
    if r["checks"]:
        cl = "".join(f"<tr><td>{h.escape(c['check_id'])}</td><td>{h.escape(c['status'])}</td>"
                     f"<td class=num>{c['failing_row_count']:,}</td><td>{h.escape(c['description'] or '')}"
                     f"<div class=sample>{h.escape((c['sample'] or '')[:400])}</div></td></tr>" for c in r["checks"])
        parts.append("<h3>Checks that did not pass</h3><table><tr><th>Check</th><th>Status</th>"
                     f"<th class=num>Rows</th><th>What it tests, first failing rows</th></tr>{cl}</table>")
    for e in r["errors"]:
        parts.append(f"<h3>Error</h3><pre>{h.escape(e)}</pre>")
    if r["log"]:
        tail = "\n".join(r["log"]["text"].rstrip().splitlines()[-TAKE_LOG_LINES:])
        parts.append(f"<h3>Nightly log, last lines</h3><pre>{h.escape(tail)}</pre>")
    return "".join(parts)


body = []
for i, r in enumerate(rows):
    when = r["when"].astimezone(LOCAL).strftime("%Y-%m-%d") if r.get("day_only") else local(r["when"])
    body.append(
        f"<tbody><tr class=run onclick=\"document.getElementById('d{i}').classList.toggle('open')\">"
        f"<td>{when}</td><td>{r['trigger']}</td><td><span class='o {r['outcome']}'>{r['outcome']}</span></td>"
        f"<td class=num>{r['took']}</td>"
        + "".join(f"<td>{cell_group(r, g)}</td>" for g in GROUPS)
        + f"<td>{r['checks_txt']}</td><td class=note>{h.escape(r['note'])}</td></tr>"
        f"<tr id=d{i} class=detail><td colspan={6 + len(GROUPS)}>{detail(r)}</td></tr></tbody>")

counts = Counter(r["outcome"] for r in rows)
last = next(r for r in rows if not r.get("day_only"))
tz = datetime.now(LOCAL).strftime("%Z")
built = local(datetime.now(timezone.utc))
summary = ", ".join(f"{n} {o}" for o, n in counts.most_common())

page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pharos run log</title>
<style>
:root {{ --bg:#ffffff; --fg:#1d1d1b; --muted:#5f5e58; --line:#e3e1da; --box:#f6f5f1; }}
body {{ background:var(--bg); color:var(--fg); font:15px/1.5 system-ui, "Segoe UI", sans-serif; margin:0; padding:24px 16px 60px; }}
main {{ max-width:1250px; margin:0 auto; }}
h1 {{ font-size:24px; margin:0 0 6px; }}
h3 {{ font-size:14px; margin:14px 0 4px; }}
.muted {{ color:var(--muted); }}
.lead {{ background:var(--box); padding:12px 16px; margin:12px 0 16px; }}
.wrap {{ overflow-x:auto; }}
table {{ border-collapse:collapse; width:100%; font-size:14px; }}
th, td {{ border-bottom:1px solid var(--line); padding:6px 10px; text-align:left; vertical-align:top; }}
th {{ white-space:nowrap; }}
td.num, th.num {{ text-align:right; white-space:nowrap; }}
tr.run {{ cursor:pointer; }}
tr.run:hover {{ background:var(--box); }}
tr.run td {{ white-space:nowrap; }}
tr.run td.note {{ white-space:normal; }}
td.note {{ color:var(--muted); }}
tr.detail {{ display:none; }}
tr.detail.open {{ display:table-row; }}
tr.detail > td {{ background:var(--box); padding:4px 16px 14px; }}
tr.detail table {{ background:var(--bg); }}
pre {{ background:var(--bg); border:1px solid var(--line); padding:8px 10px; overflow-x:auto; font-size:12px; margin:0; }}
.sample {{ font:12px ui-monospace, Consolas, monospace; color:var(--muted); word-break:break-all; margin-top:2px; }}
.o {{ padding:1px 8px; border-radius:10px; font-size:13px; white-space:nowrap; }}
.o.published {{ background:#dcfce7; color:#166534; }}
.o.partial {{ background:#fef3c7; color:#92400e; }}
.o.blocked, .o.failed, .o.died, .o.missed {{ background:#fee2e2; color:#991b1b; }}
{nav.CSS}
</style></head>
<body>{nav.html("Log")}<main>
<h1>Run log</h1>
<div class="lead">Last run: <b>{local(last['when'])}</b>, {last['trigger']}, <b>{last['outcome']}</b>.
Next scheduled nightly: {h.escape(next_run)}. Page built {built}. Times are this machine's clock ({tz}).</div>
<p class="muted">{len(rows)} rows: {summary}. Click a row for its files, checks, errors and log.
Outcomes: published (every group it tried published), partial (a group was carried forward or never published),
blocked or failed (nothing published), died (started, never wrote a finish record), missed (no nightly started that
day). Checks count the run's own checks; ack = a warning acknowledged as expected (config/expected_states.csv).</p>
<div class="wrap"><table>
<thead><tr><th>When</th><th>Trigger</th><th>Outcome</th><th class=num>Took</th>
{''.join(f'<th>{g}</th>' for g in GROUPS)}<th>Checks</th><th>Note</th></tr></thead>
{''.join(body)}
</table></div>
</main></body></html>"""

out = HERE / "log.html"
out.write_text(page, encoding="utf-8")
print("wrote", out, "|", summary)

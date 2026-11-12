"""Scaffold: James's pages for keeping up with Pharos (2026-11-11).

This build writes catalog.html: every data source Pharos has in any state, one row each. The definitions (what, state,
cadence, which raw folders and served tables) are typed in catalog.csv, which opens in Excel; every number is measured
here from the data root at build time: raw files, served rows, date ranges, the last load, and the freshness checks'
status from health/latest.md. A name in catalog.csv that is not on disk stops the build (fail loud).
Run: uv run python scaffold/build.py"""
import csv
import re
from datetime import datetime, timezone
from pathlib import Path

import duckdb
from common import ET, esc, now_et, num
from common import page as render

from pharos.groups import GROUPS
from pharos.paths import data_root

HERE = Path(__file__).parent
DATA = data_root()
STATES = ("trusted", "landed", "quick lane", "trial", "retired")

rows = list(csv.DictReader((HERE / "catalog.csv").open(encoding="utf-8")))
bad = [r["source"] for r in rows if r["state"] not in STATES]
if bad:
    raise SystemExit(f"catalog.csv: unknown state for {bad}; allowed {STATES}")

version = (DATA / "serving" / "CURRENT").read_text(encoding="utf-8").strip()
serving = DATA / "serving" / version
con = duckdb.connect()


def run_et(run_id):
    t = datetime.strptime(run_id, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).astimezone(ET)
    return t.strftime("%Y-%m-%d %H:%M ET")


def raw_stats(spec):
    files, newest = 0, ""
    for ds in spec.split():
        folder = DATA / "raw" / ds
        if not folder.is_dir():
            raise SystemExit(f"catalog.csv: raw folder {folder} not on disk")
        names = sorted(p.stem for p in folder.glob("*.parquet"))
        files += len(names)
        newest = max(newest, names[-1] if names else "")
    return files, newest


def served_stats(spec):
    """(rows, first obs_date, last obs_date, series) over the served tables named; 'table:prefix' filters series_id."""
    n, lo, hi, series = 0, None, None, None
    for item in spec.split():
        table, _, prefix = item.partition(":")
        p = serving / f"{table}.parquet"
        if not p.exists():
            raise SystemExit(f"catalog.csv: served table {p} not on disk")
        src = f"read_parquet('{p.as_posix()}/**/*.parquet', hive_partitioning=false)" if p.is_dir() \
            else f"read_parquet('{p.as_posix()}')"
        where = f"WHERE series_id LIKE '{prefix}:%'" if prefix else ""
        c, a, b, s = con.execute(
            f"SELECT count(*), min(obs_date), max(obs_date), "
            f"{'count(DISTINCT series_id)' if prefix else 'NULL'} FROM {src} {where}").fetchone()
        n += c
        lo = a if lo is None or (a and a < lo) else lo
        hi = b if hi is None or (b and b > hi) else hi
        series = s if s is not None else series
    return n, lo, hi, series


# freshness status from the latest health report: | Cxx | kind | severity | status | ...
health_md = (DATA / "health" / "latest.md").read_text(encoding="utf-8")
status = {m[0]: m[1].strip() for m in re.findall(r"^\| (C\d+)\s*\|[^|]*\|[^|]*\| ([^|]+)\|", health_md, re.M)}
overall = re.search(r"^Status:\s+(\S+)", health_md, re.M).group(1)
evaluated = datetime.strptime(re.search(r"^Evaluated at:\s+(.+) UTC$", health_md, re.M).group(1).strip(), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).astimezone(ET).strftime("%Y-%m-%d %H:%M ET")

out = []
for r in rows:
    files, newest = raw_stats(r["raw"]) if r["raw"] else (None, None)
    n, lo, hi, series = served_stats(r["served"]) if r["served"] else (None, None, None, None)
    checks = []
    for c in r["freshness"].split():
        if c not in status:
            raise SystemExit(f"catalog.csv: check {c} not in health/latest.md")
        checks.append((c, status[c]))
    out.append(dict(r, files=files, newest=newest, n=n, lo=lo, hi=hi, series=series, checks=checks))


DATASET_GROUP = {f"{s}/{d}": g.name for g in GROUPS.values() for s, d in g.datasets}


def group_of(r):
    """The source group whose run loads this row's raw folders, linked to its diagram on the Pipeline page.
    A stored row whose folders belong to no group, or to two, stops the build."""
    if not r["raw"]:
        return ""
    gs = {DATASET_GROUP.get(ds) for ds in r["raw"].split()}
    if None in gs or len(gs) != 1:
        raise SystemExit(f"catalog.csv: {r['source']} raw {r['raw']!r} maps to groups {gs}")
    g = gs.pop()
    return f'<a href="pipeline.html#{g}">{g}</a>'


def link(r):
    return f'<a href="{r["sheet"]}">{esc(r["source"])}</a>' if r['sheet'] else esc(r['source'])


def chk(checks):
    return " ".join(f'<span class="{"ok" if s in ("pass", "acknowledged") else "warn" if s == "warn" else "bad"}">{c} {esc(s)}</span>'
                    for c, s in checks)


ROOT = HERE.parent


def as_of(r):
    """Stored: the newest raw file's run. Otherwise the files named in the asof column: for pages that fetch at
    build time, the oldest page (the stalest fetch); for downloads, the newest file. A named file missing stops."""
    if r["newest"]:
        return run_et(r["newest"])
    if not r["asof"]:
        return ""
    files = []
    for spec in r["asof"].split():
        base, pat = (DATA, spec[5:]) if spec.startswith("data:") else (ROOT, spec)
        hits = list(base.glob(pat))
        if not hits:
            raise SystemExit(f"catalog.csv: asof file {base / pat} not on disk")
        files += hits
    times = [datetime.fromtimestamp(f.stat().st_mtime, ET) for f in files]
    t = min(times) if r["state"] == "quick lane" else max(times)
    return f"{t:%Y-%m-%d %H:%M} ET"


list_rows = "".join(f"<tr><td>{link(r)}</td><td><span class='st {r['state'].replace(' ', '-')}'>{esc(r['state'])}</span></td>"
                    f"<td>{esc(r['what'])}</td><td class=sub>{esc(as_of(r))}</td><td>{group_of(r)}</td></tr>"
                    for r in out)
published = re.search(r"^Serving:.*\(published (.+?) E[SD]T\)", health_md, re.M).group(1) + " ET"
cat_rows = "".join(
    f"<tr><td><b>{link(r)}</b><div class=sub>{esc(r['what'])}</div></td>"
    f"<td><span class='st {r['state'].replace(' ', '-')}'>{esc(r['state'])}</span></td>"
    f"<td>{esc(r['cadence'])}</td>"
    f"<td class=num>{num(r['files'])}</td><td>{esc(run_et(r['newest']) if r['newest'] else '')}</td>"
    f"<td class=mono>{esc(r['served'].replace(':', ' '))}</td><td class=num>{num(r['n'])}</td>"
    f"<td class=num>{num(r['series'])}</td>"
    f"<td>{esc(r['lo'] or '')}{' to ' + esc(r['hi']) if r['hi'] else ''}</td>"
    f"<td>{chk(r['checks'])}</td><td class=sub>{esc(r['where'])}</td></tr>" for r in out)

body = f"""<div class="head"><h1>Data Sources</h1>
<div class="stamps"><span>Page built {now_et()}</span></div></div>
<table class="list"><tr><th>Source</th><th>State</th><th>What</th><th>As of</th><th>Pipeline</th></tr>{list_rows}</table>

<h2>Details</h2>
<div class="wrap"><table>
<tr><th>Source</th><th>State</th><th>Cadence</th><th>Raw files</th><th>Last loaded</th><th>Served as</th>
<th>Rows served</th><th>Series</th><th>Dates (obs_date)</th><th>Freshness</th><th>Where</th></tr>
{cat_rows}
</table></div>"""
page = render("Data Sources", "Catalog", body)
(HERE / "catalog.html").write_text(page, encoding="utf-8")
print(f"wrote {HERE / 'catalog.html'}: {len(out)} sources")

"""Series page: every series Pharos has, one row each, regrouped by a click (James, 2026-11-14).

The grouping (subject, group, grain) is typed in series.csv, which opens in Excel; a stored series is named there by
its series_id (a trailing * matches a prefix), and everything else about it (name, source, frequency, units,
adjustment, first and last date, periods) is read from the served version at build time. Rows of other kinds are
typed in full: 'fetched' (a page fetches it when it builds; not stored), 'planned' (not in Pharos yet), and 'facts'
(SEC Company Facts, a company-by-concept grid too large to list; its counts are measured here).
A served series with no row in series.csv, or a row that matches nothing served, stops the build (fail loud).
Read-only; seconds. Writes series.html.
Run: uv run python scaffold/series.py"""
import csv
import json
from pathlib import Path

import duckdb
from common import esc, now_et, page

from pharos.paths import data_root

HERE = Path(__file__).parent
DATA = data_root()
version = (DATA / "serving" / "CURRENT").read_text(encoding="utf-8").strip()
S = DATA / "serving" / version

KINDS = ("series", "fetched", "planned", "facts")
STATE = {"series": "stored", "facts": "stored", "fetched": "fetched at page build", "planned": "planned"}
SOURCE = {"fred": "FRED", "yf": "Yahoo"}                      # series_catalog.source -> the name shown
SHEET = {"FRED": "fred.html", "Yahoo": "prices.html", "SEC Company Facts": "company-facts.html", "IMF": "weo.html", "UN": "population.html"}
FREQ = {"D": "Daily", "M": "Monthly", "Q": "Quarterly", "A": "Annual"}

con = duckdb.connect()
cat = {r[0]: r for r in con.execute(
    f"SELECT series_id, name, source, source_key, frequency, units, adjustment "
    f"FROM '{(S / 'series_catalog.parquet').as_posix()}' ORDER BY series_id").fetchall()}
# Each period at its latest vintage; periods the latest vintage withdrew (null) are not counted.
span = {r[0]: r[1:] for r in con.execute(f"""
    SELECT series_id, min(obs_date)::VARCHAR, max(obs_date)::VARCHAR, count(*) FROM (
        SELECT series_id, obs_date, value FROM '{(S / 'observations.parquet').as_posix()}'
        QUALIFY row_number() OVER (PARTITION BY series_id, obs_date ORDER BY vintage DESC) = 1)
    WHERE value IS NOT NULL GROUP BY series_id""").fetchall()}

typed = list(csv.DictReader((HERE / "series.csv").open(encoding="utf-8")))
bad = [r for r in typed if r["kind"] not in KINDS or not r["subject"] or not r["grain"]]
if bad:
    raise SystemExit(f"series.csv: rows need a kind in {KINDS}, a subject and a grain: {bad}")


def freq(code, where):
    if any(c not in FREQ for c in code.split()):
        raise SystemExit(f"series.csv: unknown frequency {code!r} for {where}; known {sorted(FREQ)}")
    return ", ".join(FREQ[c] for c in code.split())


rows, claimed = [], {}
for t in typed:
    base = {"subject": t["subject"], "group": t["group"], "grain": t["grain"], "state": STATE[t["kind"]],
            "note": t["note"]}
    if t["kind"] == "series":
        pat = t["id"]
        ids = [s for s in cat if s.startswith(pat[:-1])] if pat.endswith("*") else [s for s in cat if s == pat]
        if not ids:
            raise SystemExit(f"series.csv: {pat!r} matches no served series in {S}")
        for sid in ids:
            if sid in claimed:
                raise SystemExit(f"series.csv: {sid} is matched by both {claimed[sid]!r} and {pat!r}")
            claimed[sid] = pat
            _, name, source, key, f, units, adj = cat[sid]
            if source not in SOURCE:
                raise SystemExit(f"series.py: no display name for source {source!r} ({sid})")
            # A series in the expected set with nothing served (ATVI: Yahoo returns no history, check C15) keeps
            # its row, with 0 periods and its own state, so the gap shows.
            lo, hi, n = span.get(sid, ("", "", 0))
            rows.append(dict(base, name=name, code=key, source=SOURCE[source], freq=freq(f, sid), units=units,
                             adj=adj.replace("_", " "), first=lo, last=hi, n=n,
                             state=base["state"] if n else "expected, none served"))
    else:
        if not t["name"]:
            raise SystemExit(f"series.csv: a {t['kind']} row needs a name: {t}")
        r = dict(base, name=t["name"], code=t["code"], source=t["source"], units=t["units"], adj="",
                 freq=freq(t["frequency"], t["name"]), first="", last="", n=None)
        if t["kind"] == "facts":
            if t["id"] != "company_facts":
                raise SystemExit(f"series.csv: the only facts table this page can measure is company_facts, not {t['id']!r}")
            # Counted from company_facts, as the two pages this row links to count them (companies.py, concepts.py).
            ciks, concepts = con.execute(
                f"SELECT count(DISTINCT cik), count(DISTINCT concept) FILTER (taxonomy = 'us-gaap') FROM "
                f"read_parquet('{(S / 'company_facts.parquet').as_posix()}/**/*.parquet', hive_partitioning=false)").fetchone()
            r["note_html"] = (f'<a href="companies.html">{ciks:,} companies</a>, '
                              f'<a href="concepts.html">{concepts:,} us-gaap concepts</a>')
        rows.append(r)

missing = sorted(set(cat) - set(claimed))
if missing:
    raise SystemExit(f"series.csv: served series with no row: {missing}")
for r in rows:
    if r["source"] and r["source"] not in SHEET and r["state"] != "planned":
        raise SystemExit(f"series.py: no source sheet for source {r['source']!r} ({r['name']})")
    r["sheet"] = SHEET.get(r["source"], "")

n_stored = sum(1 for r in rows if r["state"] == "stored" and r["n"] is not None)

SCRIPT = """
const ROWS = __ROWS__;
const MODES = {
  subject: r => [r.subject, r.group].filter(Boolean),
  source: r => [r.source || 'Source not chosen', r.subject],
};
const E = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const N = 12;  // columns
function line(r, pad) {
  const src = r.sheet ? `<a href="${r.sheet}">${E(r.source)}</a>` : E(r.source);
  const note = r.note_html ? ` <span class="sub">${r.note_html}</span>` : r.note ? ` <span class="sub">${E(r.note)}</span>` : '';
  return `<tr class="${r.state === 'planned' ? 'planned' : ''}"><td class="nw" style="padding-left:${pad}px">${E(r.name)}${note}</td>`
    + `<td class="nw">${E(r.code)}</td><td class="nw c-subject">${E(r.subject)}</td><td class="nw c-group">${E(r.group)}</td><td class="nw c-source">${src}</td>`
    + `<td class="nw">${E(r.grain)}</td><td class="nw">${E(r.freq)}</td><td class="nw">${E(r.units)}</td><td class="nw">${E(r.adj)}</td>`
    + `<td class="nw">${E(r.first)}</td><td class="nw">${E(r.last)}</td>`
    + `<td class="num">${r.n == null ? '' : r.n.toLocaleString('en-US')}</td><td class="nw">${E(r.state)}</td></tr>`;
}
function draw(mode) {
  const top = new Map();   // group -> {rows, kids: subgroup -> rows}, in first-appearance order
  for (const r of ROWS) {
    const [a, b] = MODES[mode](r);
    if (!top.has(a)) top.set(a, {rows: [], kids: new Map()});
    const g = top.get(a);
    if (b) { if (!g.kids.has(b)) g.kids.set(b, []); g.kids.get(b).push(r); } else g.rows.push(r);
  }
  let h = '';
  for (const [a, g] of top) {
    const total = g.rows.length + [...g.kids.values()].reduce((s, k) => s + k.length, 0);
    h += `<tr class="g1"><td colspan="${N + 1}">${E(a)} <span class="cnt">${total}</span></td></tr>`;
    h += g.rows.map(r => line(r, 10)).join('');
    for (const [b, k] of g.kids) {
      h += `<tr class="g2"><td colspan="${N + 1}">${E(b)} <span class="cnt">${k.length}</span></td></tr>`;
      h += k.map(r => line(r, 34)).join('');
    }
  }
  document.getElementById('rows').innerHTML = h;
  document.getElementById('all').className = 'm-' + mode;
  for (const b of document.querySelectorAll('#modes button')) b.classList.toggle('on', b.dataset.mode === mode);
  history.replaceState(null, '', '#' + mode);
}
for (const b of document.querySelectorAll('#modes button')) b.onclick = () => draw(b.dataset.mode);
draw(MODES[location.hash.slice(1)] ? location.hash.slice(1) : 'subject');
"""

STYLE = """<style>
#modes { margin:10px 0 6px; display:flex; gap:6px; align-items:center; }
#modes button { font:inherit; padding:4px 14px; border:1px solid var(--line); background:var(--bg); border-radius:4px; cursor:pointer; }
#modes button.on { background:var(--fg); color:var(--bg); border-color:var(--fg); }
tr.g1 td { background:var(--box); font-weight:700; border-bottom:2px solid var(--fg); padding-top:10px; }
tr.g2 td { font-weight:600; padding-left:22px; }
.cnt { color:var(--muted); font-weight:400; margin-left:6px; }
tr.planned td { color:#9ca3af; }
#rows td { font-size:15px; padding:3px 10px; }
main, nav { max-width:none; }
#rows td:first-child { white-space:normal; max-width:540px; }
#rows .sub { font-size:inherit; }
.m-subject .c-subject, .m-subject .c-group, .m-source .c-source, .m-source .c-subject { display:none; }
</style>"""

body = f"""<div class="head"><h1>Series</h1>
<div class="stamps"><span>Page built {now_et()}</span></div></div>
<p class=sub>Every series in Pharos, one row each. A stored series shows its first date, last date and number of
periods as read from served version {esc(version)}, each period at its latest vintage.</p>
<div id="modes"><span class=sub>Group by</span><button data-mode="subject">Subject</button><button data-mode="source">Source</button></div>
<div class="wrap"><table id="all">
<thead><tr><th>Series</th><th>Code</th><th class="c-subject">Subject</th><th class="c-group">Group</th><th class="c-source">Source</th><th>Grain</th><th>Frequency</th>
<th>Units</th><th>Adjustment</th><th>First</th><th>Last</th><th>Periods</th><th>State</th></tr></thead>
<tbody id="rows"></tbody></table></div>
<script>{SCRIPT.replace("__ROWS__", json.dumps(rows).replace("</", "<\\/"))}</script>"""

(HERE / "series.html").write_text(page("Series", "Series", body, head=STYLE), encoding="utf-8")
print(f"wrote {HERE / 'series.html'}: {len(rows)} rows, {n_stored} stored series")

"""Temporary exploration page: US bank mergers since 1970 and the family tree of each surviving bank (James, 2026-11-16).

Reads three files pulled from the FDIC's public BankFind API to <data root>/tmp/scaffold/banks/fdic/ (quick lane, nothing stored):
  institutions.csv   one row per institution ever insured (27,835)
  events_200.csv     one row per institution disappearing: merger, failure, closing (change codes 200 to 299)
  events_100.csv     one row per institution opening (change codes 100 to 199)
Banks are keyed by the FDIC's permanent number (UNINUM), never by name. A family tree walks each disappeared bank forward
through its acquirers to the last one. Charter level only: holding-company ownership is not in these files.
Every figure is computed here. Writes bank-mergers.html.  Run: uv run python scaffold/bank_mergers.py"""
import json
from pathlib import Path

import duckdb
from common import esc, now_et, page

from pharos.paths import data_root

HERE = Path(__file__).parent
FDIC = data_root() / "tmp" / "scaffold" / "banks" / "fdic"
PLOTLY = "https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.35.2/plotly.min.js"
MIN_TREE = 5
GROUPS = {"Merger, no assistance": (223, 221, 222, 224, 225), "Failure or assisted": (211, 213, 215, 216, 217), "Closed, no acquirer named": (230, 235, 240, 260)}
GCOL = {"Merger, no assistance": "#2563eb", "Failure or assisted": "#dc2626", "Closed, no acquirer named": "#9ca3af"}
con = duckdb.connect()


def q(sql):
    return con.execute(sql).fetchall()


for t in ("institutions", "events_200", "events_100"):
    con.execute(f"CREATE TABLE {t} AS SELECT * FROM read_csv('{(FDIC / (t + '.csv')).as_posix()}', header=true, all_varchar=true)")
gcase = "CASE " + " ".join(f"WHEN CHANGECODE::INT IN {codes} THEN '{g}'" for g, codes in GROUPS.items()) + " END"
con.execute(f"""CREATE TABLE ev AS SELECT ID, UNINUM AS o, nullif(ACQ_UNINUM, '0') AS a, EFFDATE[:10]::DATE AS dt, CHANGECODE::INT AS code,
                CHANGECODE_DESC AS descr, {gcase} AS grp, coalesce(nullif(OUT_INSTNAME, ''), INSTNAME) AS name,
                coalesce(nullif(OUT_PCITY, ''), PCITY) AS city, coalesce(nullif(OUT_PSTALP, ''), PSTALP) AS st,
                ACQ_INSTNAME AS acq_name, nullif(OUT_UNINUM, '0') AS out_u FROM events_200""")
con.execute("""CREATE TABLE inst AS SELECT CERT, UNINUM AS u, NAME, CITY, STALP, ACTIVE = '1' AS active, strptime(ESTYMD, '%m/%d/%Y')::DATE AS est,
               CASE WHEN ACTIVE = '1' THEN DATE '9999-12-31' ELSE strptime(ENDEFYMD, '%m/%d/%Y')::DATE END AS fin,
               try_cast(ASSET AS DOUBLE) * 1e3 AS asset, nullif(NAMEHCR, '') AS holder FROM institutions""")

# ---------- checks ----------
n_ev, n_ev_id, n_nogrp, n_out_mismatch, n_named, n_noacq, ev0, ev1 = q("""SELECT count(*), count(DISTINCT ID), count(*) FILTER (grp IS NULL),
    count(*) FILTER (out_u IS NOT NULL AND out_u <> o), count(*) FILTER (a IS NOT NULL), count(*) FILTER (a IS NULL), min(dt), max(dt) FROM ev""")[0]
n_inst, n_cert, n_u, n_active, n_coord = q("""SELECT count(*), count(DISTINCT CERT), count(DISTINCT u), count(*) FILTER (active),
    (SELECT count(*) FROM institutions WHERE try_cast(LATITUDE AS DOUBLE) <> 0) FROM inst""")[0]
n_open, open0 = q("SELECT count(*), min(EFFDATE[:4]) FROM events_100 WHERE CHANGECODE = '110'")[0]
if n_ev != n_ev_id or n_inst != n_cert or n_nogrp or n_out_mismatch:
    raise SystemExit(f"key check failed: events {n_ev}/{n_ev_id}, institutions {n_inst}/{n_cert}, ungrouped codes {n_nogrp}, "
                     f"events whose disappearing bank is not the event's own bank {n_out_mismatch}")
dup_out = q("SELECT count(*) FROM (SELECT o FROM ev GROUP BY 1 HAVING count(*) > 1)")[0][0]
n_inactive, n_inactive_ev = q("SELECT count(*), count(*) FILTER (u IN (SELECT o FROM ev)) FROM inst WHERE NOT active")[0]
ev_in_inst = q("SELECT count(*) FROM (SELECT DISTINCT o FROM ev) WHERE o IN (SELECT u FROM inst)")[0][0]
n_ev_banks = q("SELECT count(DISTINCT o) FROM ev")[0][0]
before70 = q("SELECT count(*) FROM ev WHERE dt < DATE '1970-01-01'")[0][0]
example = q("SELECT dt, name, city, st, descr, acq_name FROM ev WHERE a IS NOT NULL ORDER BY dt DESC, ID LIMIT 1")[0]

# ---------- counts by year ----------
alive = q("""SELECT y, count(*) FROM (SELECT unnest(range(1934, 2027)) y), inst WHERE est <= make_date(y, 12, 31) AND fin > make_date(y, 12, 31)
             GROUP BY 1 ORDER BY 1""")
alive_d = dict(alive)
peak_y = max(alive_d, key=alive_d.get)
by_year = q("SELECT year(dt) y, grp, count(*) FROM ev WHERE dt >= DATE '1970-01-01' GROUP BY ALL ORDER BY 1")
YEARS = list(range(1970, int(str(ev1)[:4]) + 1))
yr = {g: [0] * len(YEARS) for g in GROUPS}
for y, g, n in by_year:
    yr[g][y - 1970] = n
opens = dict(q("SELECT EFFDATE[:4]::INT, count(*) FROM events_100 WHERE CHANGECODE = '110' GROUP BY 1"))
code_rows = q("SELECT code, descr, grp, count(*), min(year(dt)), max(year(dt)) FROM ev GROUP BY ALL ORDER BY 4 DESC")
busiest = max(YEARS, key=lambda y: sum(yr[g][y - 1970] for g in GROUPS))

# ---------- family trees ----------
con.execute("CREATE TABLE e AS SELECT o, a, dt FROM ev WHERE a IS NOT NULL AND a <> o QUALIFY row_number() OVER (PARTITION BY o ORDER BY dt DESC) = 1")
con.execute("""CREATE TABLE lin AS WITH RECURSIVE w(anc, cur, steps) AS (
                 SELECT o, a, 1 FROM e UNION ALL SELECT w.anc, e.a, w.steps + 1 FROM w JOIN e ON e.o = w.cur WHERE w.steps < 60)
               SELECT anc, arg_max(cur, steps) AS fin, max(steps) AS steps FROM w GROUP BY 1""")
n_lin, n_fin, max_steps = q("SELECT count(*), count(DISTINCT fin), max(steps) FROM lin")[0]
if max_steps >= 60:
    raise SystemExit("a merger chain did not end within 60 steps: there is a loop in the events")
con.execute("""CREATE TABLE surv AS SELECT l.fin, count(*) AS n, max(l.steps) AS depth, any_value(i.NAME) AS name, any_value(i.active) AS active,
               any_value(i.CITY) AS city, any_value(i.STALP) AS st, any_value(i.asset) AS asset, any_value(i.holder) AS holder,
               (SELECT any_value(acq_name) FROM ev WHERE ev.a = l.fin) AS ev_name
               FROM lin l LEFT JOIN inst i ON i.u = l.fin GROUP BY 1""")
n_fin_active, in_active = q("SELECT count(*) FILTER (active), sum(n) FILTER (active) FROM surv")[0]
top = q("SELECT coalesce(name, ev_name), n, depth, active, asset, city, st FROM surv ORDER BY n DESC LIMIT 25")
top6 = sum(r[1] for r in top[:6])
surv = q(f"SELECT fin, coalesce(name, ev_name), n, active, city, st, asset FROM surv WHERE n >= {MIN_TREE} ORDER BY n DESC")
nodes = q(f"""SELECT l.fin, l.anc, e.a, ev.name, ev.city, ev.st, e.dt::VARCHAR, ev.grp, i.asset
              FROM lin l JOIN e ON e.o = l.anc JOIN ev ON ev.o = l.anc AND ev.dt = e.dt AND ev.a = e.a LEFT JOIN inst i ON i.u = l.anc
              WHERE l.fin IN (SELECT fin FROM surv WHERE n >= {MIN_TREE}) ORDER BY e.dt""")
trees = {s[0]: {"name": s[1], "n": s[2], "active": bool(s[3]), "where": f"{s[4] or ''}, {s[5] or ''}".strip(", "), "asset": s[6], "rows": []} for s in surv}
for fin, anc, par, name, city, st, dt, grp, asset in nodes:
    trees[fin]["rows"].append([anc, par, name, f"{city or ''}, {st or ''}".strip(", "), dt, grp[0], asset])
for k, t in trees.items():
    if len(t["rows"]) != t["n"]:
        raise SystemExit(f"tree {k} has {len(t['rows'])} rows, expected {t['n']}")


def money(x):
    if x is None:
        return ""
    return f"${x / 1e12:,.2f} trillion" if x >= 1e12 else f"${x / 1e9:,.1f} billion" if x >= 1e9 else f"${x / 1e6:,.0f} million"


# ---------- charts ----------
font = {"family": "system-ui, sans-serif", "size": 13, "color": "#1d1d1b"}
base = {"margin": {"l": 60, "r": 20, "t": 10, "b": 40}, "font": font, "plot_bgcolor": "#ffffff", "paper_bgcolor": "#ffffff",
        "legend": {"orientation": "h", "y": 1.12}}
grid = {"gridcolor": "#e3e1da", "rangemode": "tozero"}
charts = [
    ("c1", [{"x": [y for y, _ in alive], "y": [n for _, n in alive], "type": "scatter", "mode": "lines", "line": {"color": "#2563eb", "width": 2},
             "hovertemplate": "End of %{x}: %{y:,}<extra></extra>"}],
     {**base, "showlegend": False, "yaxis": {**grid, "title": {"text": "Institutions in existence"}}}),
    ("c2", [{"x": YEARS, "y": yr[g], "name": g, "type": "bar", "marker": {"color": GCOL[g]}, "hovertemplate": "%{x}: %{y:,}<extra>" + g + "</extra>"}
            for g in GROUPS],
     {**base, "barmode": "stack", "yaxis": {**grid, "title": {"text": "Institutions per year"}}}),
    ("c3", [{"y": [r[0] for r in top][::-1], "x": [r[1] for r in top][::-1], "type": "bar", "orientation": "h", "marker": {"color": "#2563eb"},
             "hovertemplate": "%{y}: %{x:,}<extra></extra>"}],
     {**base, "showlegend": False, "xaxis": {**grid, "title": {"text": "Institutions that ended up inside it, all generations"}},
      "yaxis": {"automargin": True, "dtick": 1}, "margin": {"l": 320, "r": 20, "t": 10, "b": 45}}),
]
static = "".join(f"Plotly.newPlot('{cid}', {json.dumps(tr)}, {json.dumps(lay)}, {{responsive:true}});" for cid, tr, lay in charts)
script = static + "const TREES = " + json.dumps(trees, separators=(",", ":")) + ";const BASE = " + json.dumps(base) + ";" + r"""
const GN = {M: 'Merger, no assistance', F: 'Failure or assisted', C: 'Closed, no acquirer named'};
const GC = {M: '#2563eb', F: '#dc2626', C: '#9ca3af'};
const sel = document.getElementById('pick'), find = document.getElementById('find');
const fmt = x => x == null ? '' : x >= 1e12 ? '$' + (x / 1e12).toFixed(2) + ' trillion' : x >= 1e9 ? '$' + (x / 1e9).toFixed(1) + ' billion' : '$' + Math.round(x / 1e6).toLocaleString() + ' million';
const safe = s => String(s == null ? '' : s).replace(/[&<>]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;'}[c]));
function fill() {
  const f = find.value.trim().toLowerCase(), keep = sel.value;
  sel.innerHTML = Object.entries(TREES).filter(([k, t]) => !f || t.name.toLowerCase().includes(f)).sort((x, y) => y[1].n - x[1].n)
    .map(([k, t]) => `<option value="${k}">${safe(t.name)} (${t.n.toLocaleString()})</option>`).join('');
  if ([...sel.options].some(o => o.value === keep)) sel.value = keep;
  draw();
}
function draw() {
  const t = TREES[sel.value];
  if (!t) return;
  const rows = t.rows, name = {}, par = {};
  rows.forEach(r => { name[r[0]] = r[2]; par[r[0]] = r[1]; });
  name[sel.value] = t.name;
  const gen = id => { let g = 0, c = id; while (par[c] !== undefined) { c = par[c]; g++; } return g; };
  const direct = rows.filter(r => r[1] === sel.value).length;
  document.getElementById('treehead').innerHTML = `<b>${safe(t.name)}</b>, ${safe(t.where)}. ${t.active ? 'Active' : 'No longer active'}` +
    (t.asset ? `, assets ${fmt(t.asset)}` : '') + `. ${t.n.toLocaleString()} institutions ended up inside it: ${direct.toLocaleString()} it absorbed itself, ` +
    `${(t.n - direct).toLocaleString()} that had already been absorbed by one of those. First ${rows[0][4]}, latest ${rows[rows.length - 1][4]}.`;
  Plotly.react('t1', [{x: rows.map(r => r[4]), y: rows.map((r, i) => i + 1), type: 'scatter', mode: 'lines', line: {shape: 'hv', color: '#2563eb', width: 2},
    text: rows.map(r => r[2]), hovertemplate: '%{x}: %{text}<br>%{y:,} so far<extra></extra>'}],
    {...BASE, showlegend: false, yaxis: {gridcolor: '#e3e1da', rangemode: 'tozero', title: {text: 'Institutions absorbed, running count'}}}, {responsive: true});
  Plotly.react('t2', [{type: 'treemap', ids: [sel.value].concat(rows.map(r => r[0])), parents: [''].concat(rows.map(r => r[1])),
    labels: [t.name].concat(rows.map(r => r[2])), customdata: [''].concat(rows.map(r => r[4] + ', ' + r[3])), maxdepth: 3,
    marker: {colors: ['#e5e7eb'].concat(rows.map(r => GC[r[5]])), line: {width: 1, color: '#ffffff'}},
    hovertemplate: '<b>%{label}</b><br>%{customdata}<br>%{value} below it<extra></extra>', textfont: {size: 11}}],
    {margin: {l: 0, r: 0, t: 0, b: 0}, font: BASE.font}, {responsive: true});
  document.getElementById('treerows').innerHTML = rows.slice().reverse().map(r =>
    `<tr><td>${r[4]}</td><td>${safe(r[2])}</td><td>${safe(r[3])}</td><td>${GN[r[5]]}</td><td>${safe(name[r[1]])}</td>` +
    `<td class=num>${gen(r[0])}</td><td class=num>${fmt(r[6])}</td></tr>`).join('');
}
find.addEventListener('input', fill); sel.addEventListener('change', draw); fill();
"""
div = "<div id={} style='height:{}px'></div>"

add = [
    ("Every event has its own ID", f"{n_ev:,} rows, {n_ev_id:,} IDs", "Disappearance events, change codes 200 to 299."),
    ("Every institution has its own number", f"{n_inst:,} rows, {n_cert:,} certificate numbers, {n_u:,} permanent numbers", "The institution file."),
    ("Banks with more than one disappearance event", f"{dup_out:,}", "Events grouped by the bank that disappeared. Where there are several, the family tree uses the latest one that names an acquirer."),
    ("Closed banks have an event", f"{n_inactive_ev:,} of {n_inactive:,} inactive institutions",
     "Institutions the file marks inactive, looked up in the events by permanent number."),
    ("Events point at known banks", f"{ev_in_inst:,} of {n_ev_banks:,} disappeared banks",
     "Banks in the events, looked up in the institution file. The rest closed before the institution file's coverage or were never insured."),
    ("Chains end", f"{n_lin:,} absorbed banks reach {n_fin:,} last acquirers; longest chain {max_steps} steps",
     "Each absorbed bank followed through its acquirers until one has no later event."),
]
add_rows = "".join(f"<tr><td>{esc(a)}</td><td>{esc(b)}</td><td>{esc(c)}</td></tr>" for a, b, c in add)
odd = [
    ("The merger record starts in 1970.",
     f"Only {before70} of {n_ev:,} disappearance events are dated before 1970, though openings go back to {open0}. "
     f"The count of institutions before 1970 is still right (it uses each bank's own opening and closing dates), but who absorbed whom is not recorded."),
    (f"The count peaked in {peak_y}.",
     f"{alive_d[peak_y]:,} institutions at the end of {peak_y}, {alive_d[1970]:,} in 1970, {alive_d[2000]:,} in 2000, {n_active:,} active now. "
     "This includes savings institutions, so it is higher than counts of commercial banks alone."),
    (f"The busiest year was {busiest}.",
     f"{sum(yr[g][busiest - 1970] for g in GROUPS):,} institutions disappeared in {busiest}: " + ", ".join(f"{yr[g][busiest - 1970]:,} {g.lower()}" for g in GROUPS) + "."),
    ("Six banks hold a quarter of everything ever absorbed.",
     f"The six largest family trees contain {top6:,} of the {n_lin:,} absorbed institutions ({top6 / n_lin * 100:.0f}%). "
     f"{in_active:,} absorbed institutions sit inside the {n_fin_active:,} last acquirers that are still active."),
    ("Not every last acquirer is alive.",
     f"{n_fin - n_fin_active:,} of the {n_fin:,} last acquirers are no longer active: they later failed or closed with no acquirer named, so their tree ends there."),
    ("This is the charter level, not ownership.",
     "A bank bought by a holding company and kept as a separate bank for years shows up here only when its charter is finally merged, "
     "and many mergers are a company combining banks it already owned. The ownership history is in Federal Reserve files that refused a scripted download."),
    ("The opening events are not usable by year.",
     f"{opens[max(opens, key=opens.get)]:,} of the {n_open:,} \"new institution\" events are dated {max(opens, key=opens.get)}. That many banks did not open "
     "in one year; it looks like a record-keeping date, and I have not checked why. Openings are left off the yearly chart; the count of institutions "
     "uses each bank's founding date from the institution file instead."),
    ("Events with no acquirer.",
     f"{n_noacq:,} events name no acquirer (closings, payoffs, some failures). Those banks appear in the yearly counts and in no family tree."),
]
odd_rows = "".join(f"<tr><td>{esc(a)}</td><td>{esc(b)}</td></tr>" for a, b in odd)
code_html = "".join(f"<tr><td class=num>{c}</td><td>{esc(d)}</td><td>{g}</td><td class=num>{n:,}</td><td class=num>{y0}</td><td class=num>{y1}</td></tr>"
                    for c, d, g, n, y0, y1 in code_rows)
top_html = "".join(f"<tr><td>{esc(r[0])}</td><td>{esc((r[5] or '') + ', ' + (r[6] or ''))}</td><td class=num>{r[1]:,}</td><td class=num>{r[2]}</td>"
                   f"<td>{'Active' if r[3] else 'Not active'}</td><td class=num>{money(r[4])}</td></tr>" for r in top)

body = f"""<h1>Bank mergers</h1>
<p class=sub>Temporary exploration page, built {now_et()}. Source: FDIC BankFind, every institution ever insured and every event that ended one.
Events run {ev0} to {ev1}.</p>

<h2>What was pulled</h2>
<table><tr><th>File</th><th>One row is</th><th>Rows</th><th>Notes</th></tr>
<tr><td>institutions.csv</td><td>One institution, ever insured</td><td class=num>{n_inst:,}</td><td>{n_active:,} active; {n_coord:,} have map coordinates</td></tr>
<tr><td>events_200.csv</td><td>One institution disappearing</td><td class=num>{n_ev:,}</td><td>{n_named:,} name an acquirer, {n_noacq:,} do not</td></tr>
<tr><td>events_100.csv</td><td>One institution opening</td><td class=num>{n_open:,}</td><td>Counted only; see What looks odd</td></tr></table>
<p class=sub>The latest event with an acquirer, as stored: {example[0]}, {esc(example[1])} ({esc(example[2])}, {example[3]}), "{esc(example[4])}", into {esc(example[5])}.</p>

<h2>Does it add up</h2>
<table><tr><th>Check</th><th>Result</th><th>What it compares</th></tr>{add_rows}</table>

<h2>What looks odd</h2>
<table><tr><th>Finding</th><th>Evidence</th></tr>{odd_rows}</table>

<h2>The count</h2>
<h3>Institutions in existence at the end of each year</h3>
{div.format('c1', 340)}
<h3>Institutions disappearing each year, by how</h3>
{div.format('c2', 380)}
<div class=wrap><table><tr><th>Code</th><th>FDIC's description</th><th>Group on this page</th><th>Events</th><th>First year</th><th>Last year</th></tr>{code_html}</table></div>

<h2>Who absorbed them</h2>
<h3>The 25 largest family trees</h3>
{div.format('c3', 620)}
<div class=wrap><table><tr><th>Last acquirer</th><th>Where</th><th>Institutions inside it</th><th>Longest chain, steps</th><th>Status</th>
<th>Assets in the institution file</th></tr>{top_html}</table></div>

<h2>One bank's family tree</h2>
<p>Type part of a name, then pick a bank. {len(trees):,} last acquirers with at least {MIN_TREE} institutions inside them are listed, largest first.</p>
<p><input id=find placeholder="Filter by name" style="padding:6px;width:260px;font:inherit"> <select id=pick style="padding:6px;font:inherit;max-width:560px"></select></p>
<p id=treehead></p>
<h3>Institutions absorbed over time</h3>
{div.format('t1', 300)}
<h3>The tree: each box is a bank, inside the bank that absorbed it</h3>
<p class=sub>Blue: merger with no assistance. Red: failure or assisted. Click a box to open it; click the bar at the top to go back. Three levels show at a time.</p>
{div.format('t2', 560)}
<h3>Every institution in the tree, latest first</h3>
<div class=wrap style="max-height:520px;overflow:auto"><table><thead><tr><th>Date</th><th>Institution</th><th>Where</th><th>How</th><th>Absorbed by</th>
<th>Steps from the survivor</th><th>Assets in the institution file</th></tr></thead><tbody id=treerows></tbody></table></div>
<p class=sub>"Assets in the institution file" is the one asset figure the FDIC's institution file carries for each bank; I have not confirmed which report date it is for closed banks.</p>"""

out = HERE / "bank-mergers.html"
out.write_text(page("Bank mergers", "Ideas", body, crumb="Bank mergers", head=f'<script src="{PLOTLY}"></script>')
               .replace("</main></body>", f"</main><script>{script}</script></body>"), encoding="utf-8")
print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB): {n_ev:,} events, {n_lin:,} absorbed -> {n_fin:,} last acquirers ({n_fin_active:,} active), "
      f"{len(trees):,} trees, peak {peak_y} {alive_d[peak_y]:,}, busiest {busiest}; inactive with event {n_inactive_ev:,}/{n_inactive:,}; dup {dup_out}")

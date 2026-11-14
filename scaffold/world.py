"""Scaffold: World GDP per person, charts for exploring it (quick lane, James 2026-11-14).

One IMF World Economic Outlook series for every country, fetched when the page is built: NGDPRPPPPC, GDP per person
at constant prices in purchasing-power-parity dollars (2021 international dollars). It is adjusted for inflation and
for price differences between countries, so a value compares across countries and across years; the current-US$
figures on the IMF page (weo.html) do neither. Population (bar widths, bubble sizes, region averages) and each
country's region come from the UN World Population Prospects file scaffold/population.py downloads; that page must
have been built once.

END is the United States' latest actual year. Years after END (forecasts) are dropped. A country whose own latest
actual year is before END still shows its IMF estimates up to END, drawn faded and marked in the hover text; the
one-year-drop chart uses actual years only. Nothing is stored. Writes world.html.
Run: uv run python scaffold/world.py"""
import csv
import json
from pathlib import Path

import duckdb
import imf
from common import esc, now_et, page

from pharos.paths import data_root

HERE = Path(__file__).parent
IND = "NGDPRPPPPC"
PLOTLY = "https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.35.2/plotly.min.js"
WPP = data_root() / "tmp" / "scaffold" / "wpp" / "WPP2024_TotalPopulationBySex.csv.gz"
# UN geographic regions, in the order their colours are assigned (fixed: a colour always means the same region).
REGIONS = ("Africa", "Asia", "Europe", "Latin America and the Caribbean", "Northern America", "Oceania")
COLORS = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300")

with open(HERE.parent / "config" / "countries.csv", encoding="utf-8") as f:
    country = {r["imf_code"]: r for r in csv.DictReader(f) if r["imf_code"]}

data, meta = imf.fetch([IND])
series = {c: obs for c, obs in data[IND].items() if c in country}
if len(series) < 190:
    raise SystemExit(f"world: only {len(series)} countries have {IND}")
END = imf.latest_actual(meta, IND, "USA")
if END is None:
    raise SystemExit("world: the United States has no latest actual year")
START = min(y for obs in series.values() for y in obs)
YEARS = list(range(START, END + 1))

if not WPP.exists():
    raise SystemExit(f"world: {WPP} is not on disk; run scaffold/population.py first (it downloads the file)")
con = duckdb.connect()
con.execute(f"CREATE TABLE f AS SELECT * FROM read_csv('{WPP.as_posix()}')")
people = {}
for code, y, v in con.execute("SELECT ISO3_code, Time, PopTotal FROM f WHERE LocTypeName = 'Country/Area' "
                              f"AND Variant = 'Medium' AND Time BETWEEN {START} AND {END}").fetchall():
    people.setdefault(code, {})[y] = v
# Each country's region: walk up the file's parent links to the first Geographic region.
loc = {str(i): (t, name, parent) for i, t, name, parent in con.execute(
    "SELECT DISTINCT LocID, LocTypeName, Location, ParentID FROM f").fetchall()}
iso_loc = dict(con.execute("SELECT DISTINCT ISO3_code, LocID::VARCHAR FROM f WHERE LocTypeName = 'Country/Area'").fetchall())


# The file gives Canada, the United States, Bermuda, Greenland and Saint Pierre and Miquelon the parent 918, which
# has no row of its own (the Northern America subregion; the region is listed as 905). Any other missing parent stops.
NO_ROW = {"918": "Northern America"}


def region(code):
    at = loc[iso_loc[code]][2]
    for _ in range(6):
        if at in NO_ROW:
            return REGIONS.index(NO_ROW[at])
        if at not in loc:
            break
        t, name, parent = loc[at]
        if t == "Geographic region":
            if name not in REGIONS:
                raise SystemExit(f"world: UN region {name!r} has no colour; known {REGIONS}")
            return REGIONS.index(name)
        at = parent
    raise SystemExit(f"world: {code} reaches no UN geographic region in {WPP}")


rows, no_people = [], []
for c, obs in sorted(series.items(), key=lambda kv: country[kv[0]]["name"]):
    code = country[c]["code"]
    if code not in people:
        no_people.append(country[c]["name"])
        continue
    if any(y in obs and y not in people[code] for y in YEARS):
        raise SystemExit(f"world: {code} has GDP per person for a year the UN file has no population for")
    rows.append({"c": code, "n": country[c]["name"], "r": region(code), "la": imf.latest_actual(meta, IND, c),
                 "v": [obs.get(y) for y in YEARS], "p": [people[code].get(y) for y in YEARS]})
if no_people:
    raise SystemExit(f"world: countries with {IND} but no UN population: {no_people}")
n_est = sum(1 for r in rows if r["la"] is None or r["la"] < END)
n_1980 = sum(1 for r in rows if all(v is not None for v in r["v"]))

SCRIPT = r"""
const C = __ROWS__, Y0 = __START__, END = __END__, REG = __REGIONS__, COL = __COLORS__;
const INK = '#0b0b0b', MUTED = '#898781', GRID = '#e1e0d9', SURF = '#ffffff';
const val = (c, y) => c.v[y - Y0], pop = (c, y) => c.p[y - Y0];
const est = (c, y) => c.la == null || y > c.la;
const fmt = v => Math.round(v).toLocaleString('en-US');
const base = (extra) => Object.assign({font: {family: 'system-ui, Segoe UI, sans-serif', size: 13, color: INK},
  paper_bgcolor: SURF, plot_bgcolor: SURF, showlegend: false, margin: {l: 60, r: 20, t: 10, b: 45}, hoverlabel: {align: 'left'}}, extra);
const ax = (extra) => Object.assign({gridcolor: GRID, linecolor: '#c3c2b7', zeroline: false, tickfont: {color: MUTED}}, extra);
const CFG = {responsive: true, displayModeBar: false};
const flag = (c, y) => est(c, y) ? `<br><i>${y} is an IMF estimate (latest actual ${c.la ?? 'not stated'})</i>` : '';

// 1. Where each country stands: one bar per country, height = GDP per person, width = population.
(function () {
  const rs = C.filter(c => val(c, END) != null).sort((a, b) => val(b, END) - val(a, END));
  let x = 0;
  const xs = [], ws = [], ys = [], cols = [], op = [], text = [], notes = [];
  for (const c of rs) {
    const w = pop(c, END) / 1000;
    xs.push(x + w / 2); ws.push(w); ys.push(val(c, END)); cols.push(COL[c.r]); op.push(est(c, END) ? 0.45 : 1);
    text.push(`<b>${c.n}</b><br>$${fmt(val(c, END))} per person<br>${fmt(w)} million people${flag(c, END)}`);
    if (w > 200) notes.push({x: x + w / 2, y: val(c, END), text: c.n, showarrow: false, yshift: 10, font: {size: 12, color: INK}});
    x += w;
  }
  Plotly.newPlot('snap', [{type: 'bar', x: xs, y: ys, width: ws, marker: {color: cols, opacity: op, line: {color: SURF, width: 0.4}},
    hovertext: text, hoverinfo: 'text'}],
    base({height: 430, bargap: 0, annotations: notes, margin: {l: 85, r: 20, t: 20, b: 45},
      xaxis: ax({title: {text: 'World population, millions, richest country at the left', font: {color: MUTED}}, showgrid: false}),
      yaxis: ax({title: {text: '$ per person', font: {color: MUTED}, standoff: 12}, tickformat: ','})}), CFG);
})();

// 2. Growth over a period: fastest, slowest, start level against growth, and the map.
function growth(a) {
  const n = END - a;
  return C.filter(c => val(c, a) != null && val(c, END) != null)
    .map(c => ({c, g: (Math.pow(val(c, END) / val(c, a), 1 / n) - 1) * 100, m: val(c, END) / val(c, a)}));
}
function bars(id, rs, a) {
  rs = rs.slice().reverse();
  const lo = Math.min(0, ...rs.map(r => r.g)) * 1.22, hi = Math.max(0, ...rs.map(r => r.g)) * 1.22;
  Plotly.react(id, [{type: 'bar', orientation: 'h', y: rs.map(r => r.c.n), x: rs.map(r => r.g),
    marker: {color: rs.map(r => COL[r.c.r]), opacity: rs.map(r => est(r.c, END) ? 0.45 : 1)},
    text: rs.map(r => (r.g >= 0 ? '+' : '') + r.g.toFixed(1) + '%'), textposition: 'outside', cliponaxis: false, textfont: {color: INK},
    hovertext: rs.map(r => `<b>${r.c.n}</b><br>${a}: $${fmt(val(r.c, a))}<br>${END}: $${fmt(val(r.c, END))}<br>`
      + `${r.m.toFixed(2)} times the ${a} level, ${r.g.toFixed(1)}% a year${flag(r.c, END)}`), hoverinfo: 'text'}],
    base({height: 30 + rs.length * 24, margin: {l: 190, r: 60, t: 10, b: 35}, bargap: 0.25,
      xaxis: ax({ticksuffix: '%', zeroline: true, zerolinecolor: '#c3c2b7', range: [lo, hi]}), yaxis: ax({showgrid: false, tickfont: {color: INK}})}), CFG);
}
function period(a) {
  const g = growth(a).sort((p, q) => q.g - p.g);
  document.getElementById('pn').textContent = `${g.length} countries have a value in both ${a} and ${END}.`;
  bars('fast', g.slice(0, 15), a);
  bars('slow', g.slice(-15), a);
  const smax = Math.max(...g.map(r => pop(r.c, END)));
  Plotly.react('scat', [{type: 'scatter', mode: 'markers', x: g.map(r => val(r.c, a)), y: g.map(r => r.g),
    marker: {color: g.map(r => COL[r.c.r]), opacity: g.map(r => est(r.c, END) ? 0.4 : 0.85), size: g.map(r => pop(r.c, END)),
      sizemode: 'area', sizeref: smax / 3600, sizemin: 4, line: {color: SURF, width: 1.5}},
    hovertext: g.map(r => `<b>${r.c.n}</b><br>${a}: $${fmt(val(r.c, a))}<br>${END}: $${fmt(val(r.c, END))}<br>`
      + `${r.g.toFixed(1)}% a year, ${r.m.toFixed(2)} times${flag(r.c, END)}`), hoverinfo: 'text'}],
    base({height: 470, margin: {l: 70, r: 20, t: 10, b: 50}, xaxis: ax({type: 'log', title: {text: `$ per person in ${a} (log scale)`, font: {color: MUTED}}, tickformat: ',', tickvals: [500, 1000, 2000, 5000, 10000, 20000, 50000, 100000, 200000]}),
      yaxis: ax({title: {text: `Growth a year, ${a} to ${END}`, font: {color: MUTED}}, ticksuffix: '%', zeroline: true, zerolinecolor: '#c3c2b7'})}), CFG);
  const abs = g.map(r => Math.abs(r.g)).sort((p, q) => p - q), lim = abs[Math.floor(abs.length * 0.9)];
  Plotly.react('map', [{type: 'choropleth', locations: g.map(r => r.c.c), z: g.map(r => r.g), zmin: -lim, zmax: lim,
    colorscale: [[0, '#d03b3b'], [0.5, '#f0efec'], [1, '#256abf']], marker: {line: {color: SURF, width: 0.4}},
    colorbar: {title: {text: '% a year'}, thickness: 12, len: 0.7, ticksuffix: '%'},
    hovertext: g.map(r => `<b>${r.c.n}</b><br>${r.g.toFixed(1)}% a year, ${a} to ${END}${flag(r.c, END)}`), hoverinfo: 'text'}],
    base({height: 470, margin: {l: 0, r: 0, t: 0, b: 0},
      geo: {projection: {type: 'natural earth'}, showframe: false, showcoastlines: false, showcountries: false, bgcolor: SURF, landcolor: '#e1e0d9'}}), CFG);
  for (const b of document.querySelectorAll('#periods button')) b.classList.toggle('on', +b.dataset.a === a);
}
for (const b of document.querySelectorAll('#periods button')) b.onclick = () => period(+b.dataset.a);
period(END - 10);

// 3. The top 10 and how it changed: rank at four years, five years apart.
(function () {
  const ys = [END - 15, END - 10, END - 5, END], rank = {};
  for (const y of ys) {
    C.filter(c => val(c, y) != null).sort((a, b) => val(b, y) - val(a, y)).forEach((c, i) => { (rank[c.c] = rank[c.c] || {})[y] = i + 1; });
  }
  const set = C.filter(c => ys.some(y => rank[c.c] && rank[c.c][y] <= 10));
  const worst = Math.max(...set.flatMap(c => ys.map(y => rank[c.c][y] || 0)));
  const notes = [];
  for (const c of set) {
    const first = ys.find(y => rank[c.c][y]), last = ys.slice().reverse().find(y => rank[c.c][y]);
    notes.push({x: first, y: rank[c.c][first], text: `${c.n} ${rank[c.c][first]}`, xanchor: 'right', xshift: -10, showarrow: false, font: {size: 12, color: INK}});
    notes.push({x: last, y: rank[c.c][last], text: `${rank[c.c][last]} ${c.n}`, xanchor: 'left', xshift: 10, showarrow: false, font: {size: 12, color: INK}});
  }
  Plotly.newPlot('bump', set.map(c => ({type: 'scatter', mode: 'lines+markers', x: ys, y: ys.map(y => rank[c.c][y] ?? null),
    line: {color: COL[c.r], width: 2}, marker: {size: 9, color: COL[c.r], line: {color: SURF, width: 2}},
    hovertext: ys.map(y => `<b>${c.n}</b><br>${y}: rank ${rank[c.c][y] ?? 'none'}, $${val(c, y) == null ? '' : fmt(val(c, y))}${flag(c, y)}`), hoverinfo: 'text'})),
    base({height: 60 + worst * 24, margin: {l: 200, r: 200, t: 10, b: 35}, annotations: notes,
      xaxis: ax({tickvals: ys, showgrid: false, range: [ys[0] - 0.4, END + 0.4]}),
      yaxis: ax({autorange: false, range: [worst + 0.7, 0.3], showticklabels: false, showgrid: false})}), CFG);
})();

// 4. Biggest one-year drops in the last ten years, actual years only.
(function () {
  const rs = [];
  for (const c of C) for (let y = END - 9; y <= END; y++)
    if (!est(c, y) && val(c, y) != null && val(c, y - 1) != null) rs.push({c, y, g: (val(c, y) / val(c, y - 1) - 1) * 100});
  const top = rs.sort((a, b) => a.g - b.g).slice(0, 20).reverse();
  Plotly.newPlot('drops', [{type: 'bar', orientation: 'h', y: top.map(r => `${r.c.n} ${r.y}`), x: top.map(r => r.g),
    marker: {color: top.map(r => COL[r.c.r])}, text: top.map(r => r.g.toFixed(1) + '%'), textposition: 'outside', cliponaxis: false, textfont: {color: INK},
    hovertext: top.map(r => `<b>${r.c.n}, ${r.y}</b><br>${r.y - 1}: $${fmt(val(r.c, r.y - 1))}<br>${r.y}: $${fmt(val(r.c, r.y))}<br>${r.g.toFixed(1)}%`), hoverinfo: 'text'}],
    base({height: 30 + top.length * 24, margin: {l: 230, r: 20, t: 10, b: 35}, bargap: 0.25,
      xaxis: ax({ticksuffix: '%', autorange: 'reversed'}), yaxis: ax({showgrid: false, side: 'left', tickfont: {color: INK}})}), CFG);
})();

// 5. Every country, every year: growth on the year before.
(function () {
  const rs = C.slice().sort((a, b) => a.r - b.r || (val(b, END) ?? 0) - (val(a, END) ?? 0)).reverse();
  const ys = []; for (let y = Y0 + 1; y <= END; y++) ys.push(y);
  const z = rs.map(c => ys.map(y => val(c, y) != null && val(c, y - 1) != null ? (val(c, y) / val(c, y - 1) - 1) * 100 : null));
  const text = rs.map((c, i) => ys.map((y, j) => z[i][j] == null ? '' : `<b>${c.n}, ${y}</b><br>${z[i][j] >= 0 ? '+' : ''}${z[i][j].toFixed(1)}%<br>$${fmt(val(c, y))}${flag(c, y)}`));
  const shapes = [], notes = [];
  let at = 0;
  for (let r = REG.length - 1; r >= 0; r--) {
    const n = rs.filter(c => c.r === r).length;
    if (!n) continue;
    shapes.push({type: 'rect', xref: 'paper', x0: -0.006, x1: -0.002, yref: 'y', y0: at - 0.5, y1: at + n - 0.5, fillcolor: COL[r], line: {width: 0}});
    at += n;
  }
  Plotly.newPlot('heat', [{type: 'heatmap', x: ys, y: rs.map(c => c.n), z, zmin: -10, zmax: 10, xgap: 1, ygap: 1,
    colorscale: [[0, '#d03b3b'], [0.5, '#f0efec'], [1, '#256abf']], hovertext: text, hoverinfo: 'text', hoverongaps: false,
    colorbar: {title: {text: '% on the year before'}, thickness: 12, len: 0.25, y: 1, yanchor: 'top', ticksuffix: '%'}}],
    base({height: 80 + rs.length * 14, margin: {l: 210, r: 20, t: 30, b: 30}, shapes,
      xaxis: ax({side: 'top', showgrid: false, dtick: 5}), yaxis: ax({showgrid: false, tickfont: {size: 11, color: INK}, dtick: 1})}), CFG);
})();

// 6. Regions over time: the average person in each region, countries with a value in every year.
(function () {
  const full = C.filter(c => c.v.every(v => v != null));
  const ys = []; for (let y = Y0; y <= END; y++) ys.push(y);
  const mean = rs => ys.map(y => rs.reduce((s, c) => s + val(c, y) * pop(c, y), 0) / rs.reduce((s, c) => s + pop(c, y), 0));
  const lines = REG.map((name, r) => ({name, col: COL[r], w: 2, rs: full.filter(c => c.r === r)})).filter(l => l.rs.length);
  lines.push({name: 'World', col: INK, w: 2, dash: 'dot', rs: full});
  const notes = [];
  const traces = lines.map(l => {
    const m = mean(l.rs);
    notes.push({x: END, y: Math.log10(m[m.length - 1]), text: `${l.name} (${l.rs.length})`, xanchor: 'left', xshift: 8, showarrow: false, font: {size: 12, color: INK}});
    return {type: 'scatter', mode: 'lines', name: l.name, x: ys, y: m, line: {color: l.col, width: l.w, dash: l.dash},
      hovertemplate: `<b>${l.name}</b> %{x}: $%{y:,.0f}<extra></extra>`};
  });
  Plotly.newPlot('regions', traces, base({height: 470, margin: {l: 70, r: 260, t: 10, b: 35}, annotations: notes, hovermode: 'x',
    xaxis: ax({showgrid: false}), yaxis: ax({type: 'log', title: {text: '$ per person (log scale)', font: {color: MUTED}}, tickformat: ','})}), CFG);
})();
"""

STYLE = f"""<style>
main, nav {{ max-width:1500px; }}
#legend {{ position:sticky; top:0; background:var(--bg); padding:8px 0; z-index:5; display:flex; flex-wrap:wrap; gap:6px 18px; border-bottom:1px solid var(--line); }}
#legend span {{ display:inline-flex; align-items:center; gap:6px; font-size:14px; }}
#legend i {{ width:12px; height:12px; border-radius:3px; display:inline-block; }}
#legend i.faded {{ background:#898781; opacity:.45; }}
#periods {{ display:flex; gap:6px; align-items:center; margin:8px 0; flex-wrap:wrap; }}
#periods button {{ font:inherit; padding:4px 14px; border:1px solid var(--line); background:var(--bg); border-radius:4px; cursor:pointer; }}
#periods button.on {{ background:var(--fg); color:var(--bg); border-color:var(--fg); }}
.two {{ display:grid; grid-template-columns:1fr 1fr; gap:24px; }}
@media (max-width:1100px) {{ .two {{ grid-template-columns:1fr; }} }}
</style><script src="{PLOTLY}"></script>"""

legend = "".join(f'<span><i style="background:{c}"></i>{esc(r)}</span>' for r, c in zip(REGIONS, COLORS))
spans = [(END - START, START), (25, END - 25), (15, END - 15), (10, END - 10), (5, END - 5), (3, END - 3)]
buttons = "".join(f'<button data-a="{a}">{n} years ({a} to {END})</button>' for n, a in spans)

body = f"""<div class="head"><h1>World GDP per person</h1>
<div class="stamps"><span>Fetched {now_et()}</span></div></div>
<p class=sub>GDP per person for {len(rows)} countries, {START} to {END}, adjusted for inflation and for price differences
between countries (IMF World Economic Outlook series {IND}, in 2021 international dollars), so a dollar buys the same
amount in every country and every year. The IMF's data starts in {START}: {END - START} years is the longest span
there is, and {n_1980} countries have a value in every year. The tables, in current US dollars, are on the
<a href="weo.html">IMF World Economic Outlook</a> page.</p>
<p class=sub>For {n_est} countries the IMF's latest actual year is before {END}; their later values are IMF estimates
and are drawn faded. Hover over anything for its figures.</p>
<div id="legend">{legend}<span><i class="faded"></i>IMF estimate, not yet actual</span></div>

<h2>Where each country stands, {END}</h2>
<p class=sub>One bar per country: height is GDP per person, width is population, richest at the left. A bar's area
is the country's whole economy. Population is the UN's figure for July 1, {END}.</p>
<div id="snap"></div>

<h2>Who has improved the most</h2>
<div id="periods"><span class=sub>Period</span>{buttons}</div>
<p class=sub><span id="pn"></span> Growth is the average rate a year over the period.</p>
<div class="two"><div><h3>Grew fastest</h3><div id="fast"></div></div><div><h3>Grew slowest, or shrank</h3><div id="slow"></div></div></div>
<h3>Where each country started against how fast it grew</h3>
<p class=sub>One bubble per country, sized by {END} population. Left is poorer at the start of the period; up is faster growth.</p>
<div id="scat"></div>
<h3>The same growth on a map</h3>
<div id="map"></div>

<h2>The top 10, and how it changed</h2>
<p class=sub>Rank by GDP per person in {END - 15}, {END - 10}, {END - 5} and {END}, for every country in the top 10 in
any of those years. The number beside a name is its rank.</p>
<div id="bump"></div>

<h2>Biggest one-year drops, {END - 9} to {END}</h2>
<p class=sub>The 20 largest falls in GDP per person from one year to the next. Actual years only; IMF estimates are left out.</p>
<div id="drops"></div>

<h2>Every country, every year</h2>
<p class=sub>Change in GDP per person on the year before: blue is growth, red is a fall, and the colour is full at 10%
either way. Countries are grouped by region (the coloured strip at the left) and ordered by {END} level within it.</p>
<div id="heat"></div>

<h2>Regions over time</h2>
<p class=sub>GDP per person for the average person in each region: each country weighted by its population. Only the
{n_1980} countries with a value in every year from {START} are in, so a line never moves because a country joined;
the number beside a region is how many of its countries that is.</p>
<div id="regions"></div>
<script>{SCRIPT.replace("__ROWS__", json.dumps(rows, separators=(",", ":")).replace("</", "<\\/"))
               .replace("__START__", str(START)).replace("__END__", str(END))
               .replace("__REGIONS__", json.dumps(REGIONS)).replace("__COLORS__", json.dumps(COLORS))}</script>"""

(HERE / "world.html").write_text(
    page("World GDP per person", "Catalog", body, crumb="World GDP per person",
         parent=("IMF World Economic Outlook", "weo.html"), head=STYLE), encoding="utf-8")
print(f"wrote {HERE / 'world.html'}: {len(rows)} countries, {START}-{END}, {n_est} with estimates at {END}, {n_1980} with every year")

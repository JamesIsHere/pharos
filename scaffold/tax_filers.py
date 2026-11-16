"""Temporary exploration page: who prepares US individual tax returns (James, 2026-11-16).

Reads only the IRS Statistics of Income state files (Historic Table 2) downloaded to <data root>/tmp/scaffold/irs/,
tax years 2012 and 2014 to 2023 (quick lane, nothing stored). One row per state and income band; counts of returns
with a paid preparer's signature, volunteer-prepared, e-filed. Every figure is computed here. Writes tax-filers.html.
Run: uv run python scaffold/tax_filers.py"""
import json
from pathlib import Path

import duckdb
from common import esc, now_et, num, page

from pharos.paths import data_root

HERE = Path(__file__).parent
IRS = data_root() / "tmp" / "scaffold" / "irs"
PLOTLY = "https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.35.2/plotly.min.js"
# Band labels as the tax year 2023 documentation guide (23incmdocguide.doc) gives them.
BANDS = {1: "Under $1", 2: "$1 to $10,000", 3: "$10,000 to $25,000", 4: "$25,000 to $50,000", 5: "$50,000 to $75,000",
         6: "$75,000 to $100,000", 7: "$100,000 to $200,000", 8: "$200,000 to $500,000", 9: "$500,000 to $1 million", 10: "$1 million or more"}
WANT = ("N1", "PREP", "TOTAL_VITA", "ELF", "CPREP", "DIR_DEP", "RAC")

con = duckdb.connect()
q = lambda s: con.execute(s).fetchall()
con.execute(f"CREATE TABLE t (yr INT, file VARCHAR, state VARCHAR, band INT, {', '.join(c + ' DOUBLE' for c in WANT)})")
files = sorted(IRS.glob("*.csv"))
if not files:
    raise SystemExit(f"no IRS state files in {IRS}")
for f in files:
    yr = 2000 + int(f.name[:2])
    con.execute(f"CREATE OR REPLACE TABLE r AS SELECT * FROM read_csv('{f.as_posix()}', header=true, all_varchar=true)")
    cols = {c[0].upper(): c[0] for c in q("DESCRIBE r")}
    for need in ("STATE", "AGI_STUB", "N1", "PREP"):
        if need not in cols:
            raise SystemExit(f"{f.name}: no {need} column")
    vals = ", ".join(f"""replace("{cols[c]}", ',', '')::DOUBLE""" if c in cols else "NULL" for c in WANT)
    con.execute(f"""INSERT INTO t SELECT {yr}, '{f.name}', trim("{cols['STATE']}"), "{cols['AGI_STUB']}"::INT, {vals} FROM r
        WHERE trim("{cols['STATE']}") <> ''""")
bad = q(f"SELECT yr, list(DISTINCT band ORDER BY band) FROM t GROUP BY 1 HAVING list(DISTINCT band ORDER BY band) <> {[0] + list(BANDS)}")
if bad:
    raise SystemExit(f"income bands are not 0 to 10 in: {bad}")
years = [y for y, in q("SELECT DISTINCT yr FROM t ORDER BY 1")]
Y0, Y1 = years[0], years[-1]
missing = [y for y in range(Y0, Y1 + 1) if y not in years]

pulled = q("""SELECT yr, any_value(file), count(*), count(DISTINCT state), count(*) FILTER (WHERE TOTAL_VITA IS NOT NULL) > 0,
    count(*) FILTER (WHERE ELF IS NOT NULL) > 0, count(*) FILTER (WHERE RAC IS NOT NULL) > 0,
    sum(N1) FILTER (WHERE state = 'US' AND band = 0), sum(N1) FILTER (WHERE state = 'US' AND band > 0),
    sum(N1) FILTER (WHERE state <> 'US' AND band = 0) FROM t GROUP BY 1 ORDER BY 1""")
yn = lambda b: "yes" if b else "no"
pulled_rows = "".join(f"<tr><td class=num>{y}</td><td>{f}</td><td class=num>{n}</td><td class=num>{a}</td><td>{yn(v)}</td><td>{yn(e)}</td>"
                      f"<td>{yn(r)}</td><td class=num>{num(round(us))}</td><td class=num>{bands - us:+,.0f}</td><td class=num>{st - us:+,.0f}</td></tr>"
                      for y, f, n, a, v, e, r, us, bands, st in pulled)
ex_rows = "".join(f"<tr><td>{s}</td><td class=num>{b}</td><td>{BANDS[b]}</td><td class=num>{num(round(n))}</td><td class=num>{num(round(p))}</td>"
                  f"<td class=num>{num(round(v))}</td><td class=num>{num(round(e))}</td></tr>"
                  for s, b, n, p, v, e in q(f"SELECT state, band, N1, PREP, TOTAL_VITA, ELF FROM t WHERE yr = {Y1} AND state = 'OH' AND band IN (3, 7, 10) ORDER BY band"))

usy = q("SELECT yr, N1, PREP, TOTAL_VITA, ELF FROM t WHERE state = 'US' AND band = 0 ORDER BY yr")
year_rows = "".join(f"<tr><td class=num>{y}</td><td class=num>{n / 1e6:.1f}</td><td class=num>{p / 1e6:.1f}</td><td class=num>{100 * p / n:.1f}%</td>"
                    f"<td class=num>{'' if v is None else f'{v / 1e6:.1f}'}</td><td class=num>{'' if v is None else f'{100 * v / n:.1f}%'}</td>"
                    f"<td class=num>{'' if v is None else f'{(n - p - v) / 1e6:.1f}'}</td><td class=num>{'' if v is None else f'{100 * (n - p - v) / n:.1f}%'}</td>"
                    f"<td class=num>{'' if e is None else f'{100 * e / n:.1f}%'}</td></tr>" for y, n, p, v, e in usy)
u = {y: (n, p, v) for y, n, p, v, _ in usy}
low_yr = min(u, key=lambda y: u[y][1] / u[y][0])

band = q(f"""SELECT band, N1, PREP, TOTAL_VITA, ELF, CPREP, RAC, DIR_DEP FROM t WHERE state = 'US' AND band > 0 AND yr = {Y1} ORDER BY band""")
paid_total = sum(b[2] for b in band)
band_rows = "".join(f"<tr><td>{BANDS[b]}</td><td class=num>{n / 1e6:.1f}</td><td class=num>{p / 1e6:.1f}</td><td class=num>{100 * p / n:.1f}%</td>"
                    f"<td class=num>{100 * p / paid_total:.1f}%</td><td class=num>{100 * v / n:.1f}%</td><td class=num>{100 * e / n:.1f}%</td>"
                    f"<td class=num>{100 * c / n:.1f}%</td><td class=num>{100 * r / n:.1f}%</td></tr>" for b, n, p, v, e, c, r, d in band)
bt = {(y, b): s for y, b, s in q("SELECT yr, band, 100 * PREP / N1 FROM t WHERE state = 'US' AND band > 0")}
SHOW = [y for y in (Y0, 2019, Y1) if y in years]
time_rows = "".join(f"<tr><td>{BANDS[b]}</td>" + "".join(f"<td class=num>{bt[(y, b)]:.1f}%</td>" for y in SHOW)
                    + f"<td class=num>{bt[(Y1, b)] - bt[(Y0, b)]:+.1f}</td></tr>" for b in BANDS)

states = q(f"""SELECT a.state, 100 * a.PREP / a.N1, a.N1, 100 * b.PREP / b.N1 FROM t a LEFT JOIN t b ON b.state = a.state AND b.band = 0 AND b.yr = {Y0}
    WHERE a.yr = {Y1} AND a.band = 0 AND a.state <> 'US' ORDER BY 2""")
us_sh = 100 * u[Y1][1] / u[Y1][0]
hi, lo = states[-1], states[0]
st50 = [s for s in states if s[0] not in ("PR", "OA")]

font = {"family": "system-ui, Segoe UI, sans-serif", "size": 13}
base = {"margin": {"l": 50, "r": 20, "t": 10, "b": 40}, "font": font, "plot_bgcolor": "#ffffff", "paper_bgcolor": "#ffffff",
        "legend": {"orientation": "h", "y": 1.12}}
pctaxis = {"ticksuffix": "%", "rangemode": "tozero", "gridcolor": "#e3e1da"}
labels = [BANDS[b[0]] for b in band]
charts = [
    ("c1", [{"x": [y for y, *_ in usy], "y": [round(100 * p / n, 2) for _, n, p, _, _ in usy], "name": "Paid preparer", "type": "scatter",
             "mode": "lines+markers", "line": {"color": "#2563eb", "width": 2}, "hovertemplate": "%{y:.1f}%"},
            {"x": [y for y, n, p, v, _ in usy if v is not None], "y": [round(100 * (n - p - v) / n, 2) for y, n, p, v, _ in usy if v is not None],
             "name": "Neither paid nor volunteer", "type": "scatter", "mode": "lines+markers", "line": {"color": "#c2410c", "width": 2},
             "hovertemplate": "%{y:.1f}%"},
            {"x": [y for y, n, p, v, _ in usy if v is not None], "y": [round(100 * v / n, 2) for y, n, p, v, _ in usy if v is not None],
             "name": "Volunteer-prepared", "type": "scatter", "mode": "lines+markers", "line": {"color": "#0f766e", "width": 2},
             "hovertemplate": "%{y:.1f}%"}],
     {**base, "yaxis": pctaxis, "xaxis": {"dtick": 1}, "hovermode": "x unified"}),
    ("c2", [{"x": labels, "y": [round(100 * b[2] / b[1], 2) for b in band], "type": "bar", "marker": {"color": "#2563eb"},
             "hovertemplate": "%{x}: %{y:.1f}%<extra></extra>"}],
     {**base, "yaxis": pctaxis, "showlegend": False, "margin": {"l": 50, "r": 20, "t": 10, "b": 90},
      "shapes": [{"type": "line", "x0": 0, "x1": 1, "xref": "paper", "y0": us_sh, "y1": us_sh, "line": {"color": "#1d1d1b", "dash": "dot"}}],
      "annotations": [{"x": 0, "xref": "paper", "y": us_sh, "text": f"All returns {us_sh:.1f}%", "showarrow": False, "xanchor": "left", "yshift": 10}]}),
    ("c3", [{"x": labels, "y": [round(b[2] / 1e6, 2) for b in band], "name": "Paid preparer", "type": "bar", "marker": {"color": "#2563eb"},
             "hovertemplate": "%{y:.1f} million"},
            {"x": labels, "y": [round((b[1] - b[2]) / 1e6, 2) for b in band], "name": "Everyone else", "type": "bar", "marker": {"color": "#d1d5db"},
             "hovertemplate": "%{y:.1f} million"}],
     {**base, "barmode": "stack", "yaxis": {"title": {"text": "Returns, millions"}, "gridcolor": "#e3e1da"}, "hovermode": "x unified",
      "margin": {"l": 60, "r": 20, "t": 10, "b": 90}}),
    ("c4", [{"y": [s[0] for s in states], "x": [round(s[1], 2) for s in states], "type": "bar", "orientation": "h",
             "marker": {"color": "#2563eb"}, "hovertemplate": "%{y}: %{x:.1f}%<extra></extra>"}],
     {**base, "xaxis": pctaxis, "yaxis": {"tickfont": {"size": 12}, "dtick": 1}, "showlegend": False, "margin": {"l": 40, "r": 20, "t": 10, "b": 40},
      "shapes": [{"type": "line", "y0": 0, "y1": 1, "yref": "paper", "x0": us_sh, "x1": us_sh, "line": {"color": "#1d1d1b", "dash": "dot"}}],
      "annotations": [{"y": 0, "yref": "paper", "x": us_sh, "text": f"United States {us_sh:.1f}%", "showarrow": False, "xanchor": "left", "xshift": 6, "yanchor": "bottom"}]}),
]
script = "".join(f"Plotly.newPlot('{cid}', {json.dumps(tr)}, {json.dumps(lay)}, {{responsive:true}});" for cid, tr, lay in charts)
div = "<div id={} style='height:{}px'></div>"
under1 = {y: n for y, n in q("SELECT yr, N1 FROM t WHERE state = 'US' AND band = 1")}

odd = (
    ("There is no column for people who prepare their own return",
     "The file counts returns with a paid preparer's signature and returns prepared by volunteers. \"Neither\" on this page is the rest, "
     "by subtraction: mostly people using software or paper themselves, plus any paid preparer who did not sign."),
    ("Nothing says who the preparer was", "A CPA, a national chain and a seasonal storefront all count the same. The file has no fee."),
    (f"{', '.join(map(str, missing)) or 'No year'} is missing", "The IRS page has that year as a spreadsheet only, and 2011 and earlier as spreadsheets back to 1997. Not read here."),
    ("Counts are rounded to the nearest 10", "So the income bands and the states do not add exactly to the US row; the two right-hand columns of the first table show by how much."),
    ("Income band labels were read for 2023 only", "Every year has bands numbered 1 to 10. That the dollar limits were the same in earlier years is assumed, not checked."),
    ("Puerto Rico appears from 2018", f"Files before 2018 have 53 areas, later files 54. \"OA\" is other areas (returns filed from abroad and territories)."),
    (f"The \"Under $1\" band doubled in 2020", f"{under1.get(2019, 0) / 1e6:.1f} million returns in 2019, {under1.get(2020, 0) / 1e6:.1f} million in 2020, "
     f"{under1.get(Y1, 0) / 1e6:.1f} million in {Y1}. 2020 and 2021 were the years of pandemic payments claimed on a return."),
)
odd_rows = "".join(f"<tr><td>{esc(a)}</td><td>{esc(b)}</td></tr>" for a, b in odd)

body = f"""<div class="head"><h1>Who prepares US tax returns</h1>
<div class="stamps"><span>Page built {now_et()}</span><span>Files downloaded 2026-11-16, not stored</span></div></div>
<p class=sub>Individual income tax returns, by tax year. Source: IRS Statistics of Income, Historic Table 2, one file per year,
one row per state and income band. Income is adjusted gross income on the return.</p>

<h2>What was pulled</h2>
<div class=wrap><table><tr><th>Tax year</th><th>File</th><th>Rows</th><th>Areas</th><th>Volunteer columns</th><th>E-file columns</th>
<th>Refund-check column</th><th>Returns, US row</th><th>Ten bands minus US row</th><th>States minus US row</th></tr>{pulled_rows}</table></div>
<p class=sub>Areas are 50 states, the District of Columbia, other areas, the United States, and Puerto Rico from 2018. Each area has 11 rows: all returns and ten income bands.</p>
<h3>Three rows as the file gives them: Ohio, {Y1}</h3>
<table><tr><th>STATE</th><th>AGI_STUB</th><th>Income band</th><th>N1, returns</th><th>PREP, paid preparer's signature</th>
<th>TOTAL_VITA, volunteer-prepared</th><th>ELF, e-filed</th></tr>{ex_rows}</table>

<h2>What looks odd</h2>
<table><tr><th>Finding</th><th>Evidence</th></tr>{odd_rows}</table>

<h2>United States, {Y0} to {Y1}</h2>
<p class=sub>Paid preparers signed {100 * u[Y0][1] / u[Y0][0]:.1f}% of returns in {Y0}, {100 * u[low_yr][1] / u[low_yr][0]:.1f}% in {low_yr} (the lowest year here)
and {us_sh:.1f}% in {Y1}.</p>
{div.format('c1', 340)}
<table><tr><th>Tax year</th><th>Returns, millions</th><th>Paid preparer, millions</th><th>Share</th><th>Volunteer, millions</th><th>Share</th>
<th>Neither, millions</th><th>Share</th><th>E-filed</th></tr>{year_rows}</table>

<h2>By income, {Y1}</h2>
<h3>Share of returns signed by a paid preparer</h3>
{div.format('c2', 360)}
<h3>Number of returns</h3>
<p class=sub>The share rises with income, but most paid-preparer returns are in the middle bands, because that is where the returns are.</p>
{div.format('c3', 380)}
<div class=wrap><table><tr><th>Income band</th><th>Returns, millions</th><th>Paid preparer, millions</th><th>Paid share of the band</th>
<th>Band's share of all paid returns</th><th>Volunteer-prepared</th><th>E-filed</th><th>Paper, prepared on a computer</th><th>Refund anticipation check</th></tr>{band_rows}</table></div>
<p class=sub>A refund anticipation check is a bank product that takes the preparation fee out of the refund.</p>
<h3>Paid share by income band over time</h3>
<table><tr><th>Income band</th>{''.join(f'<th>{y}</th>' for y in SHOW)}<th>Change {Y0} to {Y1}, points</th></tr>{time_rows}</table>

<h2>By state, {Y1}</h2>
<p class=sub>Share of returns signed by a paid preparer. Highest: {hi[0]} at {hi[1]:.1f}%. Lowest: {lo[0]} at {lo[1]:.1f}%.
Among the 50 states and DC: {st50[-1][0]} {st50[-1][1]:.1f}% down to {st50[0][0]} {st50[0][1]:.1f}%. OA is other areas, PR is Puerto Rico.</p>
{div.format('c4', 1000)}"""

out = HERE / "tax-filers.html"
out.write_text(page("Who prepares US tax returns", "Ideas", body, crumb="Who prepares US tax returns",
                    head=f'<script src="{PLOTLY}"></script>')
               .replace("</main></body>", f"</main><script>{script}</script></body>"), encoding="utf-8")
print(f"wrote {out}: tax years {years}, paid share {Y1} {us_sh:.1f}%")

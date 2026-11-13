"""Housing supply vs need (quick lane): is the US building homes as fast as households form? New homes completed
(Census) against households added (Census CPS), population (served, FRED POPTHM), vacancy rates, people per household,
and new-home price against household income. FRED series are fetched at build time and never stored.
Read-only. Writes housing-need.html. Run: uv run python scaffold/housing_need.py"""
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path

import duckdb

from common import esc, num, now_et, page
from pharos.paths import data_root

HERE = Path(__file__).parent
DATA = data_root()
version = (DATA / "serving" / "CURRENT").read_text(encoding="utf-8").strip()
OBS = (DATA / "serving" / version / "observations.parquet").as_posix()
PLOTLY = "https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.35.2/plotly.min.js"


def fred(path, **p):
    p.update(api_key=os.environ["FRED_API_KEY"], file_type="json")
    return json.load(urllib.request.urlopen(f"https://api.stlouisfed.org/fred/{path}?" + urllib.parse.urlencode(p)))


SERIES = {  # fetched from FRED at build
    "COMPUTNSA": "New homes completed, total (monthly, thousands, NSA)",
    "TTLHH": "Households (annual, thousands; Census CPS)",
    "ETOTALUSQ176N": "Housing units, total stock (quarterly, thousands)",
    "RRVRUSQ156N": "Rental vacancy rate (quarterly, %)",
    "RHVRUSQ156N": "Homeowner vacancy rate (quarterly, %)",
    "MSPUS": "Median sales price of new houses sold (quarterly, $)",
    "MEHOINUSA646N": "Median household income (annual, current $)",
}
con = duckdb.connect()
con.execute("CREATE TABLE m (sid VARCHAR, d DATE, v DOUBLE)")
upd = {}
for sid in SERIES:
    upd[sid] = fred("series", series_id=sid)["seriess"][0]["last_updated"][:10]
    rows = [(sid, r["date"], float(r["value"])) for r in fred("series/observations", series_id=sid)["observations"]
            if r["value"] != "."]
    if not rows:
        raise SystemExit(f"FRED {sid}: no observations")
    con.executemany("INSERT INTO m VALUES (?, ?, ?)", rows)
# population from the served version (latest vintage of each month)
pop = con.execute(f"""SELECT 'POPTHM', obs_date, value FROM '{OBS}' WHERE series_id = 'fred:POPTHM' AND value IS NOT NULL
                      QUALIFY row_number() OVER (PARTITION BY obs_date ORDER BY vintage DESC) = 1""").fetchall()
if not pop:
    raise SystemExit(f"served version {version} has no rows for fred:POPTHM; run a refresh first")
con.executemany("INSERT INTO m VALUES (?, ?, ?)", pop)


def q(sql):
    return con.execute(sql).fetchall()


# ---------------------------------------------------------------- one row per year
# completions: sum of the 12 months; population: average of the 12 months; households and income: the annual value;
# vacancy and price: average of the 4 quarters. A year missing a month or quarter is left empty.
con.execute("""CREATE TABLE yr AS
  WITH c AS (SELECT year(d) y, sum(v) comp FROM m WHERE sid = 'COMPUTNSA' GROUP BY 1 HAVING count(*) = 12),
       p AS (SELECT year(d) y, avg(v) pop FROM m WHERE sid = 'POPTHM' GROUP BY 1 HAVING count(*) = 12),
       h AS (SELECT year(d) y, v hh FROM m WHERE sid = 'TTLHH'),
       i AS (SELECT year(d) y, v inc FROM m WHERE sid = 'MEHOINUSA646N'),
       qv AS (SELECT year(d) y, avg(v) FILTER (WHERE sid = 'RRVRUSQ156N') rv, avg(v) FILTER (WHERE sid = 'RHVRUSQ156N') hv,
                     avg(v) FILTER (WHERE sid = 'MSPUS') price, count(*) FILTER (WHERE sid = 'MSPUS') np,
                     count(*) FILTER (WHERE sid = 'RRVRUSQ156N') nr, count(*) FILTER (WHERE sid = 'RHVRUSQ156N') nh
              FROM m WHERE sid IN ('RRVRUSQ156N', 'RHVRUSQ156N', 'MSPUS') GROUP BY 1)
  SELECT y, comp, pop, hh, hh - lag(hh) OVER (ORDER BY y) AS hh_add, pop / hh AS per_hh, comp / pop * 1000 AS comp_per_k,
         CASE WHEN nr = 4 THEN rv END AS rv, CASE WHEN nh = 4 THEN hv END AS hv, CASE WHEN np = 4 THEN price END AS price, inc
  FROM (SELECT DISTINCT y FROM (SELECT y FROM c UNION SELECT y FROM h UNION SELECT y FROM p UNION SELECT y FROM qv))
  LEFT JOIN c USING (y) LEFT JOIN p USING (y) LEFT JOIN h USING (y) LEFT JOIN i USING (y) LEFT JOIN qv USING (y)
  ORDER BY y""")
# cumulative completions less households added, from the first year both exist
first = q("SELECT min(y) FROM yr WHERE comp IS NOT NULL AND hh_add IS NOT NULL")[0][0]
last_full = q("SELECT max(y) FROM yr WHERE comp IS NOT NULL AND hh_add IS NOT NULL")[0][0]
con.execute(f"""CREATE TABLE gap AS SELECT y, sum(comp - hh_add) OVER (ORDER BY y) AS cum FROM yr
                WHERE y BETWEEN {first} AND {last_full}""")

# ---------------------------------------------------------------- the period table
PERIODS = [(1970, 1980), (1980, 1990), (1990, 2000), (2000, 2010), (2010, 2020), (2020, last_full)]


def period(a, b):
    """Per-year averages over [a, b): completions in years a..b-1; households, population and stock as change a to b."""
    n = b - a
    comp = q(f"SELECT sum(comp), count(comp) FROM yr WHERE y >= {a} AND y < {b}")[0]
    hh = q(f"SELECT (max(hh) FILTER (WHERE y = {b}) - max(hh) FILTER (WHERE y = {a})) FROM yr")[0][0]
    pp = q(f"SELECT (max(pop) FILTER (WHERE y = {b}) - max(pop) FILTER (WHERE y = {a})) FROM yr")[0][0]
    st = q(f"""SELECT avg(v) FILTER (WHERE year(d) = {b}) - avg(v) FILTER (WHERE year(d) = {a})
               FROM m WHERE sid = 'ETOTALUSQ176N'""")[0][0]
    c = comp[0] / n if comp[1] == n else None
    return (a, b, c, None if hh is None else hh / n, None if pp is None else pp / n, None if st is None else st / n)


per = [period(a, b) for a, b in PERIODS]


def k(x):
    return "" if x is None else f"{x:,.0f}"


rows_per = "".join(
    f"<tr><td class=nw>{a} to {b}</td><td class=num>{k(c)}</td><td class=num>{k(h)}</td>"
    f"<td class=num>{'' if c is None or h is None else f'{c - h:+,.0f}'}</td><td class=num>{k(p)}</td>"
    f"<td class=num>{k(s)}</td><td class=num>{'' if c is None or not p else f'{c / p * 1000:,.0f}'}</td></tr>"
    for a, b, c, h, p, s in per)

# ---------------------------------------------------------------- charts
FONT = {"family": "system-ui, Segoe UI, sans-serif", "size": 13}
COLORS = ["#1d4ed8", "#c2410c", "#15803d"]
scripts = []


def col(sql):
    rows = q(sql)
    return [list(c) for c in zip(*rows)] if rows else [[], []]


def chart(div, traces, suffix="", kind="scatter", zero=True):
    tr = []
    for i, (nm, x, y) in enumerate(traces):
        t = {"x": x, "y": y, "name": nm, "type": kind, "hovertemplate": "%{y:,.1f}" + suffix}
        if kind == "scatter":
            t.update(mode="lines", line={"color": COLORS[i], "width": 1.8})
        else:
            t.update(marker={"color": COLORS[i]})
        tr.append(t)
    lay = {"margin": {"l": 60, "r": 20, "t": 30, "b": 40}, "hovermode": "x unified", "font": FONT,
           "legend": {"orientation": "h", "y": 1.12, "x": 1, "xanchor": "right"}, "showlegend": len(traces) > 1,
           "yaxis": {"ticksuffix": suffix, "zeroline": True, "zerolinecolor": "#6b7280",
                     "rangemode": "tozero" if zero else "normal"},
           "plot_bgcolor": "#ffffff", "paper_bgcolor": "#ffffff"}
    scripts.append(f"Plotly.newPlot('{div}', {json.dumps(tr)}, {json.dumps(lay)}, {{responsive:true}});")


y1, comp = col(f"SELECT y, round(comp) FROM yr WHERE y BETWEEN {first} AND {last_full} ORDER BY y")
y2, hha = col(f"SELECT y, round(hh_add) FROM yr WHERE y BETWEEN {first} AND {last_full} ORDER BY y")
chart("c_flow", [("Homes completed", y1, comp), ("Households added", y2, hha)])
yg, cum = col("SELECT y, round(cum) FROM gap ORDER BY y")
chart("c_gap", [("Completions less households added, running total", yg, cum)], kind="bar", zero=False)
yp, cpk = col("SELECT y, round(comp_per_k, 2) FROM yr WHERE comp_per_k IS NOT NULL ORDER BY y")
chart("c_pc", [("Homes completed per 1,000 people", yp, cpk)])
yv, rv = col("SELECT y, round(rv, 2) FROM yr WHERE rv IS NOT NULL ORDER BY y")
yh, hv = col("SELECT y, round(hv, 2) FROM yr WHERE hv IS NOT NULL ORDER BY y")
chart("c_vac", [("Rental vacancy", yv, rv), ("Homeowner vacancy", yh, hv)], suffix="%")
yz, ph = col("SELECT y, round(per_hh, 2) FROM yr WHERE per_hh IS NOT NULL ORDER BY y")
chart("c_size", [("People per household", yz, ph)], zero=False)
yr_, pi = col("SELECT y, round(price / inc, 2) FROM yr WHERE price IS NOT NULL AND inc IS NOT NULL ORDER BY y")
chart("c_pi", [("Median new-home price / median household income", yr_, pi)], zero=False)

# ---------------------------------------------------------------- headline figures
g = dict(q("SELECT y, cum FROM gap"))
# the largest decline in the running total: the year furthest below its own earlier high, and that high
low_y, low, peak = q("""SELECT y, cum, max(cum) OVER (ORDER BY y) FROM gap ORDER BY max(cum) OVER (ORDER BY y) - cum DESC LIMIT 1""")[0]
peak_y = q(f"SELECT min(y) FROM gap WHERE cum = {peak}")[0][0]
ph_first, ph_last = q("SELECT min_by(per_hh, y), max_by(per_hh, y), min(y), max(y) FROM yr WHERE per_hh IS NOT NULL")[0][:2]
ph_y = q("SELECT min(y), max(y) FROM yr WHERE per_hh IS NOT NULL")[0]
pi_first = q("SELECT y, price / inc FROM yr WHERE price IS NOT NULL AND inc IS NOT NULL ORDER BY y LIMIT 1")[0]
pi_last = q("SELECT y, price / inc FROM yr WHERE price IS NOT NULL AND inc IS NOT NULL ORDER BY y DESC LIMIT 1")[0]
vac_low = q("SELECT y, hv FROM yr WHERE hv IS NOT NULL ORDER BY hv, y DESC LIMIT 1")[0]
vac_last = q("""SELECT d, max(v) FILTER (WHERE sid = 'RRVRUSQ156N'), max(v) FILTER (WHERE sid = 'RHVRUSQ156N') FROM m
                WHERE sid IN ('RRVRUSQ156N', 'RHVRUSQ156N') GROUP BY d HAVING count(*) = 2 ORDER BY d DESC LIMIT 1""")[0]
cpk_first = q("SELECT y, comp_per_k FROM yr WHERE comp_per_k IS NOT NULL ORDER BY y LIMIT 1")[0]
cpk_last = q("SELECT y, comp_per_k FROM yr WHERE comp_per_k IS NOT NULL ORDER BY y DESC LIMIT 1")[0]
src = "".join(f"<tr><td>{s}</td><td>{esc(t)}</td><td class=nw>{upd[s]}</td></tr>" for s, t in SERIES.items())

body = f"""<div class=head><div><h1>Housing supply vs need</h1>
<p class=sub>Quick lane: FRED series fetched when this page was built, never stored; population from served version {esc(version)}. Built {now_et()}.</p></div></div>

<p>Is the US building homes as fast as it forms households? Each new household needs a home, so homes completed should at least keep up with households added, plus whatever is torn down. Vacancy rates show how tight the stock is; household size and price against income show what a shortage does to people.</p>

<h2>Per year, by period (thousands)</h2>
<div class=wrap><table>
<tr><th>Period</th><th>Homes completed</th><th>Households added</th><th>Completed less households</th><th>Population added</th><th>Housing stock added</th><th>Completed per 1,000 people added</th></tr>
{rows_per}</table></div>
<p class=muted>Completions are summed over the years from the start of each period up to its end year, not including it; households, population and housing stock are the change from the start year to the end year, per year. Housing stock is the Census estimate, which starts in 2000. The last period ends at {last_full}, the latest year with both completions and households.</p>

<h2>Homes completed vs households added, each year</h2>
<div id="c_flow" style="height:340px"></div>
<p class=muted>Households come from an annual survey (Census CPS); the year-to-year change is noisy, so the running total below is the steadier read.</p>

<h2>Running total: homes completed less households added, from {first}</h2>
<div id="c_gap" style="height:340px"></div>
<p>The largest decline: from {peak:,.0f} thousand in {peak_y} to {low:,.0f} thousand in {low_y}, {(peak - low) / 1e3:,.1f} million fewer homes finished than households formed over those years. In {last_full} the total stood at {g[last_full]:,.0f} thousand. A falling stretch means fewer homes were finished than households formed. It does not count homes torn down or the manufactured homes that are not in Census completions, so its level is not a shortage estimate; its slope is the signal.</p>

<h2>Homes completed per 1,000 people</h2>
<div id="c_pc" style="height:300px"></div>
<p>{cpk_first[1]:.1f} in {cpk_first[0]}, {cpk_last[1]:.1f} in {cpk_last[0]}.</p>

<h2>Vacancy rates</h2>
<div id="c_vac" style="height:300px"></div>
<p>Low vacancy means a tight market. Yearly averages of the quarterly rates (full years only). Homeowner vacancy was lowest in {vac_low[0]} ({vac_low[1]:.1f}%). Latest quarter, {vac_last[0].year} Q{(vac_last[0].month - 1) // 3 + 1}: rental vacancy {vac_last[1]:.1f}%, homeowner vacancy {vac_last[2]:.1f}%.</p>

<h2>People per household</h2>
<div id="c_size" style="height:300px"></div>
<p>{ph_first:.2f} in {ph_y[0]}, {ph_last:.2f} in {ph_y[1]}. Smaller households mean more homes per person. When homes are short, young adults stay with parents and roommates double up, so households added (above) can understate the demand.</p>

<h2>New-home price against household income</h2>
<div id="c_pi" style="height:300px"></div>
<p>The median new house sold cost {pi_first[1]:.1f} years of median household income in {pi_first[0]} and {pi_last[1]:.1f} in {pi_last[0]}. New houses only; the price includes changes in what gets built (size, location), not only the price of the same house.</p>

<h2>Series</h2>
<div class=wrap><table><tr><th>FRED series</th><th>What</th><th>Last updated on FRED</th></tr>
{src}<tr><td>POPTHM</td><td>Population (monthly, thousands)</td><td>served version {esc(version)}</td></tr></table></div>
"""

out = HERE / "housing-need.html"
out.write_text(page("Housing supply vs need", "Catalog", body, crumb="Housing supply vs need", head=f'<script src="{PLOTLY}"></script>')
               .replace("</main></body>", f"</main><script>{''.join(scripts)}</script></body>"), encoding="utf-8")
print(f"wrote {out}: years {first} to {last_full}")

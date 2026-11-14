"""Housing pipeline (Census New Residential Construction, via FRED): building permits, housing starts, units under
construction and completions, read from the served version (stored by D171). One internal check (the
under-construction stock should move by starts less completions) and three comparisons, as year-over-year growth:
starts against residential construction spending (Census, served) and residential investment (BEA; PRFI, the one
series here not stored, fetched from FRED at build time), and single-family completions against public
homebuilders' revenue (SEC Company Facts, served).
Read-only. Writes housing.html. Run: uv run python scaffold/housing.py"""
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path

import duckdb

import sec_revenue
from common import esc, num, now_et, page, served_obs, served_vintage
from pharos.paths import data_root

HERE = Path(__file__).parent
DATA = data_root()
version = (DATA / "serving" / "CURRENT").read_text(encoding="utf-8").strip()
S = DATA / "serving" / version
OBS = (S / "observations.parquet").as_posix()
PLOTLY = "https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.35.2/plotly.min.js"
FETCHED = ("PRFI",)   # not in config/sources.yaml: fetched from FRED at build time


def fred(path, **p):
    p.update(api_key=os.environ["FRED_API_KEY"], file_type="json")
    return json.load(urllib.request.urlopen(f"https://api.stlouisfed.org/fred/{path}?" + urllib.parse.urlencode(p)))


def fred_obs(sid):
    rows = [(r["date"], float(r["value"])) for r in fred("series/observations", series_id=sid)["observations"]
            if r["value"] != "."]
    if not rows:
        raise SystemExit(f"FRED {sid}: no observations")
    return rows


con = duckdb.connect()
con.execute("SET memory_limit='6GB'")


def q(sql):
    return con.execute(sql).fetchall()


# ---------------------------------------------------------------- the series
PIPE = {  # monthly, thousands of units
    "PERMIT": "Permits, total (SAAR)", "HOUST": "Starts, total (SAAR)", "COMPUTSA": "Completions, total (SAAR)",
    "HOUST1F": "Starts, single-family (SAAR)", "HOUST5F": "Starts, 5+ unit buildings (SAAR)",
    "COMPU1USA": "Completions, single-family (SAAR)",
    "AUTHNOTTSA": "Authorized, not started (stock, SA)", "UNDCONTSA": "Under construction (stock, SA)",
    "HOUSTNSA": "Starts, total (NSA)", "COMPUTNSA": "Completions, total (NSA)", "UNDCONTNSA": "Under construction (stock, NSA)",
}
OTHER = {"PRRESCONS": "Residential construction spending, Census (monthly, $M SAAR)",
         "PRFI": "Private residential fixed investment, BEA (quarterly, $B SAAR)"}
con.execute("CREATE TABLE m (sid VARCHAR, d DATE, v DOUBLE)")
for sid in {**PIPE, **OTHER}:
    rows = fred_obs(sid) if sid in FETCHED else served_obs(con, OBS, sid)
    con.executemany("INSERT INTO m VALUES (?, ?, ?)", [(sid, d, v) for d, v in rows])
con.execute("CREATE TABLE wide AS PIVOT m ON sid USING first(v) GROUP BY d ORDER BY d")
pop = q("SELECT sid, count(*), min(d), max(d) FROM m GROUP BY sid")
pop = {r[0]: r[1:] for r in pop}

# ---------------------------------------------------------------- check: the stock moves by starts less completions
con.execute("""CREATE TABLE ident AS SELECT d, UNDCONTNSA - lag(UNDCONTNSA) OVER (ORDER BY d) AS dstock,
                 HOUSTNSA - COMPUTNSA AS flow, HOUSTNSA AS starts FROM wide
               WHERE UNDCONTNSA IS NOT NULL AND HOUSTNSA IS NOT NULL AND COMPUTNSA IS NOT NULL""")
id_n, id_med, id_max, id_pct, id_neg, id_lo, id_hi, id_sum, id_starts = q("""
  SELECT count(*), median(abs(dstock - flow)), max(abs(dstock - flow)), median(abs(dstock - flow) / starts) * 100,
         count(*) FILTER (WHERE dstock - flow < 0), min(d), max(d), sum(dstock - flow), sum(starts)
  FROM ident WHERE dstock IS NOT NULL""")[0]

# ---------------------------------------------------------------- comparisons, quarterly
con.execute("CREATE TABLE cmp (pair VARCHAR, side VARCHAR, q DATE, v DOUBLE)")


def quarterly(sid):
    """Quarterly average of a monthly rate (dropping a quarter missing a month), or the quarterly series as is."""
    return q(f"""SELECT date_trunc('quarter', d)::DATE, avg(v) FROM m WHERE sid = '{sid}' GROUP BY 1
                 HAVING count(*) = 3 OR '{sid}' = 'PRFI' ORDER BY 1""")


def add(pair, side, rows):
    con.executemany("INSERT INTO cmp VALUES (?, ?, ?, ?)", [(pair, side, d, v) for d, v in rows])


add("spend", "nrc", quarterly("HOUST"))
add("spend", "other", quarterly("PRRESCONS"))
add("prfi", "nrc", quarterly("HOUST"))
add("prfi", "other", quarterly("PRFI"))

# Homebuilders: companies filing under SIC 1531 (operative builders); quarterly revenue per sec_revenue.py.
SIC = ("1531",)
sec_revenue.build(con, S, SIC, "hb_q")
REV, XBRL_ALL = sec_revenue.REV, sec_revenue.XBRL_ALL
hb_pop = q("SELECT count(DISTINCT cik), count(*), count(*) FILTER (WHERE derived), min(q), max(q) FROM hb_q")[0]
hb_top = q("SELECT any_value(name), sum(v) FROM hb_q WHERE year(q) = 2025 GROUP BY cik ORDER BY 2 DESC LIMIT 5")
hb_yoy = sec_revenue.same_company_growth(con, "hb_q")
add("builders", "nrc", [(d, v) for d, v in quarterly("COMPU1USA") if str(d) >= XBRL_ALL])

PAIRS = [  # key, label, NRC series, other label
    ("spend", "Starts vs residential construction spending", "HOUST", "Construction spending (PRRESCONS)"),
    ("prfi", "Starts vs residential investment in GDP", "HOUST", "Residential investment (PRFI)"),
    ("builders", "Single-family completions vs homebuilder revenue", "COMPU1USA", "Homebuilder revenue (Company Facts)"),
]
con.execute("""DELETE FROM cmp c WHERE side = 'other'
               AND q < (SELECT min(q) FROM cmp x WHERE x.pair = c.pair AND x.side = 'nrc')""")
con.execute("""CREATE TABLE yoy AS SELECT a.pair, a.side, a.q, (a.v / b.v - 1) * 100 AS g
               FROM cmp a JOIN cmp b ON b.pair = a.pair AND b.side = a.side AND b.q = a.q - INTERVAL 1 YEAR""")
con.executemany("INSERT INTO yoy VALUES ('builders', 'other', ?, ?)", hb_yoy)   # same-company growth
fit = {r[0]: r[1:] for r in q("""
  WITH j AS (SELECT a.pair, a.q, a.g AS gn, b.g AS go FROM yoy a JOIN yoy b USING (pair, q)
             WHERE a.side = 'nrc' AND b.side = 'other')
  SELECT pair, count(*), min(q), max(q), corr(gn, go), median(abs(gn - go)), arg_max(gn, q), arg_max(go, q)
  FROM j GROUP BY pair""")}
missing = [k for k, *_ in PAIRS if k not in fit]
if missing:
    raise SystemExit(f"no overlapping quarters for {missing}")
# Review 2026-11-13: starts lead the dollar series, and the newest homebuilder quarter can rest on few companies.
lead = dict(q("""SELECT a.pair, corr(a.g, b.g) FROM yoy a JOIN yoy b ON b.pair = a.pair AND b.q = a.q + INTERVAL 3 MONTH
                 WHERE a.side = 'nrc' AND b.side = 'other' AND a.pair IN ('spend', 'prfi') GROUP BY a.pair"""))
hb_n = q("""SELECT a.q, count(*), sum(b.v) FROM hb_q a JOIN hb_q b ON b.cik = a.cik AND b.q = a.q - INTERVAL 1 YEAR
            GROUP BY a.q ORDER BY a.q DESC LIMIT 5""")
if len(hb_n) < 5:
    raise SystemExit("homebuilders: fewer than five quarters of same-company growth")
hb_quarters = q("SELECT count(DISTINCT q) FROM hb_q")[0][0]
mer_n = q("SELECT count(*) FROM hb_q WHERE name ILIKE 'Meritage Homes%'")[0][0]


def qlabel(d):
    d = str(d)
    return f"{d[:4]} Q{(int(d[5:7]) - 1) // 3 + 1}"


# ---------------------------------------------------------------- charts
FONT = {"family": "system-ui, Segoe UI, sans-serif", "size": 13}
RANGE = {"rangeselector": {"buttons": [{"count": 10, "label": "10y", "step": "year", "stepmode": "backward"},
                                       {"count": 25, "label": "25y", "step": "year", "stepmode": "backward"},
                                       {"step": "all", "label": "all"}]}}
COLORS = ["#1d4ed8", "#c2410c", "#15803d", "#7c3aed"]
scripts = []


def lines(div, traces, suffix=""):
    tr = [{"x": x, "y": y, "name": nm, "type": "scatter", "mode": "lines", "line": {"color": COLORS[i], "width": 1.6},
           "hovertemplate": "%{y:,.1f}" + suffix} for i, (nm, x, y) in enumerate(traces)]
    lay = {"margin": {"l": 55, "r": 20, "t": 30, "b": 40}, "hovermode": "x unified", "font": FONT,
           "legend": {"orientation": "h", "y": 1.12, "x": 1, "xanchor": "right"}, "xaxis": RANGE,
           "yaxis": {"ticksuffix": suffix, "zeroline": True, "zerolinecolor": "#6b7280", "rangemode": "tozero" if not suffix else "normal"},
           "plot_bgcolor": "#ffffff", "paper_bgcolor": "#ffffff"}
    scripts.append(f"Plotly.newPlot('{div}', {json.dumps(tr)}, {json.dumps(lay)}, {{responsive:true}});")


def monthly(sid):
    rows = q(f"SELECT d::VARCHAR, v FROM m WHERE sid = '{sid}' ORDER BY d")
    return [r[0] for r in rows], [r[1] for r in rows]


def growth(pair, side):
    rows = q(f"SELECT q::VARCHAR, round(g, 2) FROM yoy WHERE pair = '{pair}' AND side = '{side}' ORDER BY q")
    return [r[0] for r in rows], [r[1] for r in rows]


lines("c_flow", [(PIPE[s].replace(" (SAAR)", ""), *monthly(s)) for s in ("PERMIT", "HOUST", "COMPUTSA")])
lines("c_mix", [(PIPE[s].replace(" (SAAR)", ""), *monthly(s)) for s in ("HOUST1F", "HOUST5F")])
lines("c_stock", [(PIPE[s].split(" (")[0], *monthly(s)) for s in ("UNDCONTSA", "AUTHNOTTSA")])
for k, _lb, nrc, ol in PAIRS:
    lines(f"c_{k}", [(f"{nrc} growth", *growth(k, "nrc")), (ol, *growth(k, "other"))], suffix="%")

# ---------------------------------------------------------------- page
last = {s: q(f"SELECT d, v FROM m WHERE sid = '{s}' ORDER BY d DESC LIMIT 1")[0] for s in PIPE}
rows_pop = "".join(
    f"<tr><td>{s}</td><td>{esc(lbl)}</td><td class=num>{num(pop[s][0])}</td><td class=nw>{pop[s][1]}</td>"
    f"<td class=nw>{pop[s][2]}</td><td class=num>{last[s][1]:,.0f}</td></tr>" for s, lbl in PIPE.items())
rows_fit = "".join(
    f"<tr><td>{esc(lb)}</td><td>{nrc}</td><td>{esc(ol)}</td><td class=nw>{qlabel(fit[k][1])} to {qlabel(fit[k][2])}</td>"
    f"<td class=num>{fit[k][0]}</td><td class=num>{fit[k][3]:.2f}</td><td class=num>{fit[k][4]:.1f} pts</td>"
    f"<td class=num>{fit[k][5]:+.1f}%</td><td class=num>{fit[k][6]:+.1f}%</td></tr>" for k, lb, nrc, ol in PAIRS)
top = ", ".join(f"{esc(n)} (${v / 1e9:,.1f}B)" for n, v in hb_top)
upd = served_vintage(con, OBS, PIPE)
prior = hb_n[1:]
hb_note = (f"Newest quarter: the homebuilder line's {qlabel(hb_n[0][0])} point counts {hb_n[0][1]} companies, with ${hb_n[0][2] / 1e9:,.1f}B of revenue "
           f"a year earlier; the four quarters before it count {min(r[1] for r in prior)} to {max(r[1] for r in prior)} companies and "
           f"${min(r[2] for r in prior) / 1e9:,.1f}B to ${max(r[2] for r in prior) / 1e9:,.1f}B.")
if hb_n[0][1] < min(r[1] for r in prior):
    hb_note += (" It is still filling in, so the last point covers only part of the industry and can move. Builders whose "
                "fiscal year ends in September or October arrive last: their fourth quarter is worked out from the annual report.")

body = f"""<div class=head><div><h1>Housing pipeline (Census New Residential Construction)</h1>
<p class=sub>Read from served version {esc(version)}, each month at its latest vintage; the newest vintage of the Census series is {upd}. One series is not stored and is fetched from FRED when this page is built: residential investment (PRFI). Built {now_et()}.</p></div></div>

<p>Census follows each new privately owned housing unit through its stages: a building permit is issued, construction starts, the unit is under construction, and it is completed. Units are thousands of housing units. Monthly rates (SAAR) are seasonally adjusted and stated at an annual pace; the stocks (under construction, authorized but not started) are counts at month end.</p>

<h2>Series</h2>
<div class=wrap><table>
<tr><th>FRED series</th><th>What</th><th>Months</th><th>First month</th><th>Last month</th><th>Latest, thousands</th></tr>
{rows_pop}</table></div>

<h2>Permits, starts and completions</h2>
<div id="c_flow" style="height:380px"></div>
<h2>Starts: single-family and 5+ unit buildings</h2>
<div id="c_mix" style="height:340px"></div>
<h2>Units under construction and authorized but not started</h2>
<div id="c_stock" style="height:340px"></div>

<h2>Check: the stock moves by starts less completions</h2>
<p>Units under construction at the end of a month should equal the prior month's count plus that month's starts less its completions (not seasonally adjusted series). Over {num(id_n)} months, {id_lo} to {id_hi}, the miss is a median {id_med:,.1f} thousand units ({id_pct:.1f}% of the month's starts), at most {id_max:,.1f} thousand; in {num(id_neg)} of the months the stock grew by less than starts less completions. Added up over the whole period, the stock grew by {abs(id_sum):,.0f} thousand units {"less" if id_sum < 0 else "more"} than starts less completions, {abs(id_sum) / id_starts * 100:.1f}% of all units started. My read, not checked against Census: the three series are separate sample estimates, and units abandoned or converted mid-construction leave the stock without being completed.</p>

<h2>Comparisons: do they move together?</h2>
<div class=wrap><table>
<tr><th>Comparison</th><th>Census series</th><th>Compared with</th><th>Quarters</th><th>Count</th><th>Growth correlation</th><th>Growth gap</th><th>Census, latest</th><th>Other, latest</th></tr>
{rows_fit}</table></div>
<p class=muted>Growth is over the same quarter a year earlier; monthly rates are averaged to quarters. Growth correlation: 1 means the two move in lockstep. Growth gap: the median distance between the two growth rates, in percentage points. Construction spending and residential investment are in dollars, so they also carry price changes and spending on improvements; starts count units. The table compares the same quarter. Starts come first: set against the following quarter's dollars, the correlation is {lead["spend"]:.2f} for construction spending and {lead["prfi"]:.2f} for residential investment.</p>

<h3>Starts vs residential construction spending</h3>
<div id="c_spend" style="height:320px"></div>
<h3>Starts vs residential investment in GDP</h3>
<div id="c_prfi" style="height:320px"></div>
<h3>Single-family completions vs homebuilder revenue</h3>
<p>Homebuilders book revenue when a finished house closes, so their revenue should follow completions more than starts. The homebuilder line is same-company growth for companies filing under SIC 1531 (operative builders): {num(hb_pop[0])} companies, {num(hb_pop[1])} company-quarters ({num(hb_pop[2])} of them a fiscal fourth quarter worked out as the year less the three quarters reported inside it), {qlabel(hb_pop[3])} to {qlabel(hb_pop[4])}. Largest in 2025: {top}. Two large builders are missing: Lennar files under SIC 1520 and is not included, and Meritage Homes files under SIC 1531 but has a figure in only {mer_n} of the {hb_quarters} quarters, because it mostly reports revenue under a label outside the five used here. Revenue is the largest of {", ".join(REV)} reported for the calendar quarter (SEC's frame). The line starts in {qlabel(XBRL_ALL)}, when every SEC filer was reporting in XBRL.</p>
<div id="c_builders" style="height:320px"></div>
<p class=muted>{hb_note}</p>
"""

out = HERE / "housing.html"
out.write_text(page("Housing pipeline", "Catalog", body, crumb="Housing pipeline", head=f'<script src="{PLOTLY}"></script>')
               .replace("</main></body>", f"</main><script>{''.join(scripts)}</script></body>"), encoding="utf-8")
print(f"wrote {out}: {len(PIPE) + len(OTHER)} series, {len(PAIRS)} comparisons")

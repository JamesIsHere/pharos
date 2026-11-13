"""Quarterly Financial Report (Census QFR, via FRED; quick lane): QFR series fetched from FRED at build time, never
stored, charted against data Pharos already holds, to see whether the trends move together. Comparisons:
manufacturing sales and inventories against Census M3 (served), six industries' sales against their M3 industry
shipments (FRED, build time), and pharmaceutical sales against public pharma companies' revenue (SEC Company Facts,
served). Growth is year over year, which takes out QFR's seasonality (QFR is not seasonally adjusted; M3 is).
Read-only. Writes qfr.html. Run: uv run python scaffold/qfr.py"""
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path

import duckdb

import sec_revenue
from common import esc, num, now_et, page
from pharos.paths import data_root

HERE = Path(__file__).parent
DATA = data_root()
version = (DATA / "serving" / "CURRENT").read_text(encoding="utf-8").strip()
S = DATA / "serving" / version
PLOTLY = "https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.35.2/plotly.min.js"
QFR_RELEASE = 434   # FRED release id for the Quarterly Financial Report


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


# ---------------------------------------------------------------- the QFR release on FRED: population
release, off = [], 0
while True:
    d = fred("release/series", release_id=QFR_RELEASE, limit=1000, offset=off)
    release += d["seriess"]
    off += 1000
    if off >= d["count"]:
        break
live = [s for s in release if "DISCONTINUED" not in s["title"]]
industries = {s["title"].split(": ")[2] for s in live}
items = {": ".join(s["title"].split(": ")[3:]) for s in live}
r_first = min(s["observation_start"] for s in live)
r_last = max(s["observation_end"] for s in live)
r_updated = max(s["last_updated"] for s in live)[:10]

# ---------------------------------------------------------------- data: one table, quarterly
# cmp(pair, side, q, v): side 'qfr' or 'other'; q is the quarter's first day
con.execute("CREATE TABLE cmp (pair VARCHAR, side VARCHAR, q DATE, v DOUBLE)")


def add(pair, side, rows):
    con.executemany("INSERT INTO cmp VALUES (?, ?, ?, ?)", [(pair, side, d, v) for d, v in rows])


def m3_quarter(rows, how):
    """Monthly M3 to quarters: 'sum' of the three months (flows) or the 'end' month (stocks). A quarter
    missing a month is dropped."""
    con.execute("CREATE OR REPLACE TEMP TABLE m (d DATE, v DOUBLE)")
    con.executemany("INSERT INTO m VALUES (?, ?)", rows)
    agg = "sum(v)" if how == "sum" else "max_by(v, d)"
    return q(f"SELECT date_trunc('quarter', d)::DATE, {agg} FROM m GROUP BY 1 HAVING count(*) = 3 ORDER BY 1")


# M3 totals from the served version (latest vintage of each month)
OBS = (S / "observations.parquet").as_posix()
served = {s: q(f"""SELECT obs_date, value FROM '{OBS}' WHERE series_id = 'fred:{s}' AND value IS NOT NULL
                   QUALIFY row_number() OVER (PARTITION BY obs_date ORDER BY vintage DESC) = 1 ORDER BY obs_date""")
          for s in ("AMTMVS", "AMTMTI")}
for s, rows in served.items():
    if not rows:
        raise SystemExit(f"served version {version} has no rows for fred:{s}; run a refresh first")

PAIRS = [  # key, label, QFR series, other series, other label, how the other is built
    ("mfg_sales", "All manufacturing: sales vs M3 shipments", "QFR101MFGUSNO", "AMTMVS", "M3 shipments (served)", "sum"),
    ("mfg_inv", "All manufacturing: inventories vs M3 inventories", "QFR214MFGUSNO", "AMTMTI", "M3 inventories (served)", "end"),
    ("com", "Computer and electronic products", "QFR101COMUSNO", "A34SVS", "M3 shipments", "sum"),
    ("mach", "Machinery", "QFR101333USNO", "A33SVS", "M3 shipments", "sum"),
    ("tra", "Transportation equipment", "QFR101TRAUSNO", "A36SVS", "M3 shipments", "sum"),
    ("che", "Chemicals", "QFR101CHEUSNO", "A25SVS", "M3 shipments", "sum"),
    ("food", "Food", "QFR101311USNO", "A11SVS", "M3 shipments", "sum"),
    ("pet", "Petroleum and coal products", "QFR101324USNO", "A24SVS", "M3 shipments", "sum"),
]
for key, _lb, qid, oid, _ol, how in PAIRS:
    add(key, "qfr", fred_obs(qid))
    add(key, "other", m3_quarter(served[oid] if oid in served else fred_obs(oid), how))

# Pharma: QFR pharmaceuticals and medicines (NAICS 3254) vs public companies in the matching SIC codes, from Company
# Facts; quarterly revenue per sec_revenue.py, from the quarter every SEC filer was reporting in XBRL.
PHARMA_SIC = ("2833", "2834", "2835", "2836")
sec_revenue.build(con, S, PHARMA_SIC, "ph_q")
REV, XBRL_ALL = sec_revenue.REV, sec_revenue.XBRL_ALL
ph_pop = q("SELECT count(DISTINCT cik), count(*), count(*) FILTER (WHERE derived), min(q), max(q) FROM ph_q")[0]
ph_yoy = sec_revenue.same_company_growth(con, "ph_q")
ph_level = q("SELECT q, sum(v), count(*) FROM ph_q GROUP BY q ORDER BY q")
add("pharma", "qfr", fred_obs("QFR101385USNO"))
add("pharma", "other", [(d, v / 1e6) for d, v, _n in ph_level])   # Company Facts dollars -> QFR millions
con.execute(f"DELETE FROM cmp WHERE pair = 'pharma' AND q < DATE '{XBRL_ALL}'")
PAIRS.append(("pharma", "Pharmaceuticals: QFR sales vs public pharma revenue", "QFR101385USNO", "Company Facts",
              "Public pharma revenue (Company Facts, served)", "cf"))

# ---------------------------------------------------------------- growth and fit per pair
# each comparison covers only the quarters QFR has, so the charts share one time span
con.execute("""DELETE FROM cmp c WHERE side = 'other'
               AND q < (SELECT min(q) FROM cmp x WHERE x.pair = c.pair AND x.side = 'qfr')""")
con.execute("""CREATE TABLE yoy AS SELECT a.pair, a.side, a.q, (a.v / b.v - 1) * 100 AS g
               FROM cmp a JOIN cmp b ON b.pair = a.pair AND b.side = a.side AND b.q = a.q - INTERVAL 1 YEAR""")
# the pharma comparison uses same-company growth, not growth of the changing total
con.execute("DELETE FROM yoy WHERE pair = 'pharma' AND side = 'other'")
con.executemany("INSERT INTO yoy VALUES ('pharma', 'other', ?, ?)", ph_yoy)

fit = {r[0]: r[1:] for r in q("""
  WITH j AS (SELECT a.pair, a.q, a.g AS gq, b.g AS go FROM yoy a JOIN yoy b USING (pair, q)
             WHERE a.side = 'qfr' AND b.side = 'other'),
       lv AS (SELECT a.pair, a.q, a.v / b.v AS ratio FROM cmp a JOIN cmp b USING (pair, q)
              WHERE a.side = 'qfr' AND b.side = 'other')
  SELECT j.pair, count(*), min(j.q), max(j.q), corr(gq, go), median(abs(gq - go)),
         (SELECT arg_max(ratio, q) FROM lv WHERE lv.pair = j.pair),
         (SELECT min(ratio) FROM lv WHERE lv.pair = j.pair), (SELECT max(ratio) FROM lv WHERE lv.pair = j.pair),
         arg_max(gq, j.q), arg_max(go, j.q), max(j.q)
  FROM j GROUP BY j.pair""")}
missing = [k for k, *_ in PAIRS if k not in fit]
if missing:
    raise SystemExit(f"no overlapping quarters for {missing}")


def series(pair, side):
    rows = q(f"SELECT q::VARCHAR, g FROM yoy WHERE pair = '{pair}' AND side = '{side}' ORDER BY q")
    return [r[0] for r in rows], [round(r[1], 2) for r in rows]


def qlabel(d):
    d = str(d)
    return f"{d[:4]} Q{(int(d[5:7]) - 1) // 3 + 1}"


# ---------------------------------------------------------------- charts
BASE = {"margin": {"l": 50, "r": 20, "t": 30, "b": 40}, "hovermode": "x unified",
        "font": {"family": "system-ui, Segoe UI, sans-serif", "size": 13},
        "legend": {"orientation": "h", "y": 1.12, "x": 0},
        "yaxis": {"ticksuffix": "%", "zeroline": True, "zerolinecolor": "#6b7280"},
        "plot_bgcolor": "#ffffff", "paper_bgcolor": "#ffffff"}
QFR_C, OTHER_C = "#1d4ed8", "#c2410c"
scripts = []


def chart(div, pair, other_label, small=False):
    xq, yq = series(pair, "qfr")
    xo, yo = series(pair, "other")
    tr = [{"x": xq, "y": yq, "name": "QFR", "type": "scatter", "mode": "lines", "line": {"color": QFR_C, "width": 1.8},
           "hovertemplate": "%{y:.1f}%"},
          {"x": xo, "y": yo, "name": other_label, "type": "scatter", "mode": "lines",
           "line": {"color": OTHER_C, "width": 1.8}, "hovertemplate": "%{y:.1f}%"}]
    lay = dict(BASE, showlegend=not small)
    if small:
        lay = dict(lay, margin={"l": 40, "r": 10, "t": 10, "b": 30}, font={"family": BASE["font"]["family"], "size": 11})
    scripts.append(f"Plotly.newPlot('{div}', {json.dumps(tr)}, {json.dumps(lay)}, {{responsive:true, displayModeBar:false}});")


def f1(x):
    return "" if x is None else f"{x:,.1f}"


lab = {k: (lb, qid, oid, ol) for k, lb, qid, oid, ol, _h in PAIRS}
summary = "".join(
    f"<tr><td>{esc(lab[k][0])}</td><td>{lab[k][1]}</td><td>{esc(lab[k][2])}</td>"
    f"<td class=nw>{qlabel(fit[k][1])} to {qlabel(fit[k][2])}</td><td class=num>{fit[k][0]}</td>"
    f"<td class=num>{fit[k][3]:.2f}</td><td class=num>{fit[k][4]:.1f} pts</td>"
    f"<td class=num>{fit[k][5]:.2f}</td><td class=num>{fit[k][6]:.2f} to {fit[k][7]:.2f}</td>"
    f"<td class=num>{fit[k][8]:+.1f}%</td><td class=num>{fit[k][9]:+.1f}%</td></tr>" for k, *_ in PAIRS)

chart("c_sales", "mfg_sales", "M3 shipments")
chart("c_inv", "mfg_inv", "M3 inventories")
IND = ["com", "mach", "tra", "che", "food", "pet"]
for k in IND:
    chart(f"c_{k}", k, "M3 shipments", small=True)
chart("c_ph", "pharma", "Public pharma (Company Facts)")
grid = "".join(f"<div><h3>{esc(lab[k][0])} <span class=muted>(corr {fit[k][3]:.2f})</span></h3>"
               f"<div id='c_{k}' style='height:240px'></div></div>" for k in IND)

com_r = q("""SELECT a.q, a.v / b.v FROM cmp a JOIN cmp b USING (pair, q)
               WHERE pair = 'com' AND a.side = 'qfr' AND b.side = 'other' ORDER BY a.q""")
ph_last = ph_level[-1]
ph_ratio_q = q("""SELECT a.q, a.v / b.v FROM cmp a JOIN cmp b USING (pair, q)
                  WHERE pair = 'pharma' AND a.side = 'qfr' AND b.side = 'other' ORDER BY a.q DESC LIMIT 1""")[0]

body = f"""<div class=head><div><h1>Quarterly Financial Report (Census QFR)</h1>
<p class=sub>Quick lane: fetched from FRED (release {QFR_RELEASE}) when this page was built, never stored. Built {now_et()}; served version {esc(version)} for the M3 and Company Facts side.</p></div></div>

<p>The QFR is a Census survey of US corporations above a size threshold, public and private, in manufacturing, mining, wholesale and retail trade, information, and professional services. It counts domestic operations only: foreign subsidiaries are not consolidated. Each line below puts a QFR series next to data Pharos already has, as growth over the same quarter a year earlier, to see whether they move together. QFR is not seasonally adjusted; year-over-year growth takes the season out.</p>

<h2>What FRED has</h2>
<table class=facts>
<tr><td>Series</td><td class=num>{num(len(release))} ({num(len(live))} live, {num(len(release) - len(live))} discontinued)</td></tr>
<tr><td>Industries</td><td class=num>{len(industries)} live</td></tr>
<tr><td>Line items per industry</td><td class=num>{len(items)}: income statement, retained earnings, balance sheet</td></tr>
<tr><td>Quarters</td><td class=nw>{qlabel(r_first)} to {qlabel(r_last)}</td></tr>
<tr><td>Units</td><td>millions of dollars, quarterly, not seasonally adjusted</td></tr>
<tr><td>Last updated on FRED</td><td class=nw>{r_updated}</td></tr>
</table>

<h2>Do they move together?</h2>
<div class=wrap><table>
<tr><th>Comparison</th><th>QFR series</th><th>Compared with</th><th>Quarters</th><th>Count</th><th>Growth correlation</th><th>Growth gap</th><th>Level ratio, latest</th><th>Level ratio, range</th><th>QFR, latest</th><th>Other, latest</th></tr>
{summary}</table></div>
<p class=muted>Growth correlation: correlation of the two year-over-year growth rates over the quarters both have (1 means they move in lockstep). Growth gap: the median distance between the two growth rates, in percentage points. Level ratio: QFR's dollars divided by the other side's, each quarter; a steady ratio means the two measure the same thing at a different scale.</p>

<h2>All manufacturing: sales vs M3 shipments</h2>
<p>QFR net sales against M3 total shipments (served, summed to quarters). QFR counts a manufacturing company's whole sales, including any non-manufacturing business it runs; M3 counts what its plants ship.</p>
<div id="c_sales" style="height:360px"></div>

<h2>All manufacturing: inventories vs M3 inventories</h2>
<p>QFR inventories at quarter end against M3 total inventories (served) in the quarter's last month.</p>
<div id="c_inv" style="height:360px"></div>

<h2>Six industries: sales vs M3 shipments</h2>
<p>Each industry's QFR net sales against its M3 shipments (fetched from FRED at build, summed to quarters). <span style="color:{QFR_C};font-weight:600">QFR</span> and <span style="color:{OTHER_C};font-weight:600">M3</span>, growth over the same quarter a year earlier.</p>
<p>Computer and electronic products is the one that has come apart: QFR's sales were {com_r[0][1]:.1f} times M3's shipments in {qlabel(com_r[0][0])} and {com_r[-1][1]:.1f} times in {qlabel(com_r[-1][0])}. QFR places a company by what it sells; M3 counts what US plants ship. My read, not checked: a US company that designs chips or computers and has them made abroad shows up in QFR's sales but not in M3's shipments, and that is where the recent growth is.</p>
<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(380px,1fr));gap:12px">{grid}</div>

<h2>Pharmaceuticals: QFR sales vs public pharma revenue</h2>
<p>QFR pharmaceuticals and medicines net sales against the revenue of companies filing with the SEC under SIC {", ".join(PHARMA_SIC)} (Company Facts, served). The Company Facts line is same-company growth: only companies with a figure in both quarters count, so companies entering or leaving the filing population do not move it. It starts in {qlabel(XBRL_ALL)}, when every SEC filer was reporting in XBRL; before that Company Facts holds only the largest companies. The two measure different things: QFR is domestic operations, private firms included; the 10-K figures are worldwide. In {qlabel(ph_ratio_q[0])} QFR's sales were {ph_ratio_q[1]:.2f} times the public companies' total.</p>
<div id="c_ph" style="height:360px"></div>
<p class=muted>Company Facts side: {num(ph_pop[0])} companies, {num(ph_pop[1])} company-quarters ({num(ph_pop[2])} of them a fiscal fourth quarter worked out as the year less the three quarters reported inside it), {qlabel(ph_pop[3])} to {qlabel(ph_pop[4])}. Revenue is the largest of {", ".join(REV)} the company reported for the calendar quarter (SEC's frame). Latest quarter, {qlabel(ph_last[0])}: ${ph_last[1] / 1e9:,.1f}B from {num(ph_last[2])} companies; a quarter still being filed is partial in level, not in same-company growth.</p>
"""

out = HERE / "qfr.html"
out.write_text(page("Quarterly Financial Report", "Catalog", body, crumb="Census QFR",
                    head=f'<script src="{PLOTLY}"></script>')
               .replace("</main></body>", f"</main><script>{''.join(scripts)}</script></body>"), encoding="utf-8")
print(f"wrote {out}: {len(PAIRS)} comparisons")

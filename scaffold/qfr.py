"""Quarterly Financial Report (Census QFR, via FRED): nine QFR series read from the served version (stored by D171),
charted against other data Pharos holds, to see whether the trends move together. Comparisons: manufacturing sales
and inventories against Census M3, six industries' sales against their M3 industry shipments, and pharmaceutical
sales against public pharma companies' revenue (SEC Company Facts); all served. Only the count of what FRED has in
the QFR release is fetched from FRED at build time. Growth is year over year, which takes out QFR's seasonality
(QFR is not seasonally adjusted; M3 is). Read-only. Writes qfr.html. Run: uv run python scaffold/qfr.py"""
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path

import duckdb

import sec_revenue
from common import esc, num, now_et, page, served_obs
from pharos.paths import data_root

HERE = Path(__file__).parent
DATA = data_root()
version = (DATA / "serving" / "CURRENT").read_text(encoding="utf-8").strip()
S = DATA / "serving" / version
OBS = (S / "observations.parquet").as_posix()
PLOTLY = "https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.35.2/plotly.min.js"
QFR_RELEASE = 434   # FRED release id for the Quarterly Financial Report


def fred(path, **p):
    p.update(api_key=os.environ["FRED_API_KEY"], file_type="json")
    return json.load(urllib.request.urlopen(f"https://api.stlouisfed.org/fred/{path}?" + urllib.parse.urlencode(p)))


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


# Every QFR and M3 series below comes from the served version, each period at its latest vintage (common.served_obs)
PAIRS = [  # key, label, QFR series, other series, other label, how the other is built
    ("mfg_sales", "All manufacturing: sales vs M3 shipments", "QFR101MFGUSNO", "AMTMVS", "M3 shipments", "sum"),
    ("mfg_inv", "All manufacturing: inventories vs M3 inventories", "QFR214MFGUSNO", "AMTMTI", "M3 inventories", "end"),
    ("com", "Computer and electronic products", "QFR101COMUSNO", "A34SVS", "M3 shipments", "sum"),
    ("mach", "Machinery", "QFR101333USNO", "A33SVS", "M3 shipments", "sum"),
    ("tra", "Transportation equipment", "QFR101TRAUSNO", "A36SVS", "M3 shipments", "sum"),
    ("che", "Chemicals", "QFR101CHEUSNO", "A25SVS", "M3 shipments", "sum"),
    ("food", "Food", "QFR101311USNO", "A11SVS", "M3 shipments", "sum"),
    ("pet", "Petroleum and coal products", "QFR101324USNO", "A24SVS", "M3 shipments", "sum"),
]
for key, _lb, qid, oid, _ol, how in PAIRS:
    add(key, "qfr", served_obs(con, OBS, qid))
    add(key, "other", m3_quarter(served_obs(con, OBS, oid), how))

# Pharma: QFR pharmaceuticals and medicines (NAICS 3254) vs public companies in the matching SIC codes, from Company
# Facts; quarterly revenue per sec_revenue.py, from the quarter every SEC filer was reporting in XBRL.
PHARMA_SIC = ("2833", "2834", "2835", "2836")
sec_revenue.build(con, S, PHARMA_SIC, "ph_q")
REV, XBRL_ALL = sec_revenue.REV, sec_revenue.XBRL_ALL
ph_pop = q("SELECT count(DISTINCT cik), count(*), count(*) FILTER (WHERE derived), min(q), max(q) FROM ph_q")[0]
ph_yoy = sec_revenue.same_company_growth(con, "ph_q")
ph_level = q("SELECT q, sum(v), count(*) FROM ph_q GROUP BY q ORDER BY q")
add("pharma", "qfr", served_obs(con, OBS, "QFR101385USNO"))
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


# pharma weak points found in the 2026-11-13 review (step 3): the fit without calendar fourth quarters, and filing errors
ph_noq4 = q("""SELECT count(*), corr(a.g, b.g) FROM yoy a JOIN yoy b USING (pair, q)
               WHERE a.pair = 'pharma' AND a.side = 'qfr' AND b.side = 'other' AND quarter(a.q) <> 4""")[0]
ph_err = q("SELECT q, v FROM ph_q WHERE name ILIKE 'PERNIX THERAPEUTICS%' AND v > 1e9 ORDER BY q")   # filed 1,000 times too large
ph_err_txt = (" Filing errors are not screened out: Pernix Therapeutics, whose usual quarter is about $31 million, is counted at "
              + " and ".join(f"${v / 1e9:,.1f}B in {qlabel(d)}" for d, v in ph_err)
              + ", so growth in those quarters and a year after each is distorted.") if ph_err else ""


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

# The latest quarter against the long run, all manufacturing sales (review 2026-11-13): the table's correlation
# covers every quarter since 2001 and says little about the newest one.
g_rows = q("""SELECT a.q, a.g, b.g FROM yoy a JOIN yoy b USING (pair, q)
              WHERE pair = 'mfg_sales' AND a.side = 'qfr' AND b.side = 'other' ORDER BY a.q""")
g_q, g_qfr, g_m3 = g_rows[-1]
g_gap = g_qfr - g_m3
g_wider = sum(abs(a - b) > abs(g_gap) for _q, a, b in g_rows[:-1])
g_run = next((i for i, (_q, a, b) in enumerate(reversed(g_rows)) if (a - b) * g_gap <= 0), len(g_rows))
chg = dict(q(f"""SELECT a.pair, a.v - b.v FROM cmp a JOIN cmp b
                 ON b.pair = a.pair AND b.side = a.side AND b.q = a.q - INTERVAL 1 YEAR
                 WHERE a.side = 'qfr' AND a.q = DATE '{g_q}'"""))
latest_txt = (
    f"The newest quarter: in {qlabel(g_q)} QFR manufacturing sales grew {g_qfr:.1f}% on a year earlier and M3 shipments "
    f"{g_m3:.1f}%, {abs(g_gap):.1f} points apart against the table's median growth gap of {fit['mfg_sales'][4]:.1f}, "
    + (f"the widest gap of the {len(g_rows)} quarters compared" if g_wider == 0
       else f"a wider gap than in {len(g_rows) - 1 - g_wider} of the other {len(g_rows) - 1} quarters")
    + f". QFR has grown {'faster' if g_gap > 0 else 'slower'} than M3 for {g_run} quarter{'s' if g_run != 1 else ''} running.")
if g_gap > 0 and chg["mfg_sales"] > 0:
    top = sorted(IND, key=lambda k: -chg[k])[:2]
    latest_txt += (f" Of the ${chg['mfg_sales'] / 1e3:,.1f}B rise in QFR's sales over the year, "
                   + " and ".join(f"{lab[k][0].lower()} is {chg[k] / chg['mfg_sales'] * 100:.0f}%" for k in top)
                   + ", the two largest of the six industries charted below.")

com_r = q("""SELECT a.q, a.v / b.v FROM cmp a JOIN cmp b USING (pair, q)
               WHERE pair = 'com' AND a.side = 'qfr' AND b.side = 'other' ORDER BY a.q""")
ph_last = ph_level[-1]
ph_ratio_q = q("""SELECT a.q, a.v / b.v FROM cmp a JOIN cmp b USING (pair, q)
                  WHERE pair = 'pharma' AND a.side = 'qfr' AND b.side = 'other' ORDER BY a.q DESC LIMIT 1""")[0]

body = f"""<div class=head><div><h1>Quarterly Financial Report (Census QFR)</h1>
<p class=sub>The QFR, M3 and Company Facts figures are read from served version {esc(version)}, each period at its latest vintage. Only the "What FRED has" table is fetched from FRED (release {QFR_RELEASE}) when this page is built. Built {now_et()}.</p></div></div>

<p>The QFR is a Census survey of US corporations above a size threshold, public and private, in manufacturing, mining, wholesale and retail trade, information, and professional services. It counts domestic operations only: foreign subsidiaries are not consolidated. Each line below puts a QFR series next to data Pharos already has, as growth over the same quarter a year earlier, to see whether they move together. QFR is not seasonally adjusted; year-over-year growth takes the season out.</p>

<h2>What FRED has</h2>
<p class=muted>The whole QFR release on FRED. Pharos stores nine of these series: net sales for all manufacturing and seven industries, and manufacturing inventories.</p>
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
<p>{latest_txt}</p>

<h2>All manufacturing: sales vs M3 shipments</h2>
<p>QFR net sales against M3 total shipments (summed to quarters). QFR counts a manufacturing company's whole sales, including any non-manufacturing business it runs; M3 counts what its plants ship.</p>
<div id="c_sales" style="height:360px"></div>

<h2>All manufacturing: inventories vs M3 inventories</h2>
<p>QFR inventories at quarter end against M3 total inventories in the quarter's last month.</p>
<div id="c_inv" style="height:360px"></div>

<h2>Six industries: sales vs M3 shipments</h2>
<p>Each industry's QFR net sales against its M3 shipments (summed to quarters). <span style="color:{QFR_C};font-weight:600">QFR</span> and <span style="color:{OTHER_C};font-weight:600">M3</span>, growth over the same quarter a year earlier.</p>
<p>Computer and electronic products is the one that has come apart: QFR's sales were {com_r[0][1]:.1f} times M3's shipments in {qlabel(com_r[0][0])} and {com_r[-1][1]:.1f} times in {qlabel(com_r[-1][0])}. QFR places a company by what it sells; M3 counts what US plants ship. My read, not checked: a US company that designs chips or computers and has them made abroad shows up in QFR's sales but not in M3's shipments, and that is where the recent growth is.</p>
<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(380px,1fr));gap:12px">{grid}</div>

<h2>Pharmaceuticals: QFR sales vs public pharma revenue</h2>
<p>QFR pharmaceuticals and medicines net sales against the revenue of companies filing with the SEC under SIC {", ".join(PHARMA_SIC)} (Company Facts, served). The Company Facts line is same-company growth: only companies with a figure in both quarters count, so companies entering or leaving the filing population do not move it. Company Facts is used from {qlabel(XBRL_ALL)}, when every SEC filer was reporting in XBRL; before that it holds only the largest companies. Growth needs the same quarter a year earlier, so the line's first point is {qlabel(ph_yoy[0][0])}. The two measure different things: QFR is domestic operations, private firms included; the 10-K figures are worldwide. In {qlabel(ph_ratio_q[0])} QFR's sales were {ph_ratio_q[1]:.2f} times the public companies' total.</p>
<div id="c_ph" style="height:360px"></div>
<p class=muted>Company Facts side: {num(ph_pop[0])} companies, {num(ph_pop[1])} company-quarters ({num(ph_pop[2])} of them a fiscal fourth quarter worked out as the year less the three quarters reported inside it), {qlabel(ph_pop[3])} to {qlabel(ph_pop[4])}. Revenue is the largest of {", ".join(REV)} the company reported for the calendar quarter (SEC's frame). Latest quarter, {qlabel(ph_last[0])}: ${ph_last[1] / 1e9:,.1f}B from {num(ph_last[2])} companies; a quarter still being filed is partial: its total is low, and its same-company growth counts only the companies that have filed so far.</p>
<p class=muted>Weak points of the Company Facts line (review of 2026-11-13). Fourth quarters: where a company reported all four quarters and the year, the year less the first three quarters missed the reported fourth quarter by more than 5% of the year in 124 of 2,444 cases; one cause is a year restated after its quarters were filed (Pfizer 2018). Without calendar fourth quarters the correlation with QFR growth is {ph_noq4[1]:.2f} over {num(ph_noq4[0])} quarters, against {fit["pharma"][3]:.2f} over all {num(fit["pharma"][0])}.{ph_err_txt}</p>
"""

out = HERE / "qfr.html"
out.write_text(page("Quarterly Financial Report", "Catalog", body, crumb="Census QFR",
                    head=f'<script src="{PLOTLY}"></script>')
               .replace("</main></body>", f"</main><script>{''.join(scripts)}</script></body>"), encoding="utf-8")
print(f"wrote {out}: {len(PAIRS)} comparisons")

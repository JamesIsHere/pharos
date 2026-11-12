"""Manufacturing (Census M3, via FRED; D169): the six served series, charted at their latest vintage, plus the basic
checks run when they landed (2026-11-12). Everything is measured from the served version at build time. Read-only.
Writes m3.html. Run: uv run python scaffold/m3.py"""
import json
from pathlib import Path

import duckdb

from common import esc, num, now_et, page
from pharos.paths import data_root

HERE = Path(__file__).parent
DATA = data_root()
version = (DATA / "serving" / "CURRENT").read_text(encoding="utf-8").strip()
OBS = (DATA / "serving" / version / "observations.parquet").as_posix()
PLOTLY = "https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.35.2/plotly.min.js"

SERIES = {
    "AMTMNO": "New orders, total manufacturing",
    "AMTMVS": "Shipments, total manufacturing",
    "AMTMTI": "Inventories, total manufacturing",
    "AMTMUO": "Unfilled orders, total manufacturing",
    "DGORDER": "New orders, durable goods",
    "NEWORDER": "New orders, nondefense capital goods ex aircraft",
}
ids = ",".join(f"'fred:{s}'" for s in SERIES)

con = duckdb.connect()
con.execute(f"CREATE VIEW m3 AS SELECT replace(series_id, 'fred:', '') AS s, * EXCLUDE (series_id) "
            f"FROM '{OBS}' WHERE series_id IN ({ids})")
con.execute("""CREATE VIEW latest AS SELECT s, obs_date, value FROM m3
               QUALIFY row_number() OVER (PARTITION BY s, obs_date ORDER BY vintage DESC) = 1""")
con.execute("""CREATE VIEW first_print AS SELECT s, obs_date, value FROM m3 WHERE value IS NOT NULL
               QUALIFY row_number() OVER (PARTITION BY s, obs_date ORDER BY vintage) = 1""")
cols = ", ".join(SERIES)
con.execute(f"CREATE VIEW wide AS PIVOT (SELECT * FROM latest WHERE value IS NOT NULL) ON s IN ({cols}) "
            "USING first(value) GROUP BY obs_date ORDER BY obs_date")

missing = set(SERIES) - {r[0] for r in con.execute("SELECT DISTINCT s FROM m3").fetchall()}
if missing:
    raise SystemExit(f"served version {version} has no rows for {sorted(missing)}; run a refresh first")


def q(sql):
    return con.execute(sql).fetchall()


def col(sql):
    rows = q(sql)
    return [list(c) for c in zip(*rows)] if rows else []


# ---------------------------------------------------------------- tables
pop = q("""SELECT s, count(*), count(DISTINCT vintage), min(obs_date), max(obs_date), max(vintage),
                  count(*) FILTER (WHERE value IS NULL) FROM m3 GROUP BY s""")
pop = sorted(pop, key=lambda r: list(SERIES).index(r[0]))

last = {r[0]: r[1:] for r in q("""SELECT s, obs_date, value, (value / lag(value) OVER w - 1) * 100 FROM latest
                                   WHERE value IS NOT NULL WINDOW w AS (PARTITION BY s ORDER BY obs_date)
                                   QUALIFY obs_date = max(obs_date) OVER (PARTITION BY s)""")}

nest = q("""SELECT count(*), count(*) FILTER (WHERE NEWORDER > DGORDER), count(*) FILTER (WHERE DGORDER > AMTMNO),
                   min(DGORDER / AMTMNO) * 100, max(DGORDER / AMTMNO) * 100
            FROM wide WHERE AMTMNO IS NOT NULL AND DGORDER IS NOT NULL AND NEWORDER IS NOT NULL""")[0]

con.execute("""CREATE VIEW gap AS SELECT obs_date,
                 AMTMNO - (AMTMVS + AMTMUO - lag(AMTMUO) OVER (ORDER BY obs_date)) AS gap FROM wide""")
gap_n, gap_med, gap_max = q("SELECT count(gap), median(abs(gap)), max(abs(gap)) FROM gap")[0]

rev = {r[0]: r[1:] for r in q("""SELECT f.s, count(*), median(abs(l.value / f.value - 1)) * 100,
                                         max(abs(l.value / f.value - 1)) * 100
                                  FROM first_print f JOIN latest l USING (s, obs_date)
                                  WHERE obs_date >= DATE '2015-01-01' GROUP BY f.s""")}

# ---------------------------------------------------------------- chart data ($ billions)
d, no, vs, ti, uo, dg, core = col("""SELECT obs_date::VARCHAR, AMTMNO / 1e3, AMTMVS / 1e3, AMTMTI / 1e3, AMTMUO / 1e3,
                                            DGORDER / 1e3, NEWORDER / 1e3 FROM wide WHERE obs_date >= DATE '1992-01-01'""")
fp_d, fp_v = col("SELECT obs_date::VARCHAR, value / 1e3 FROM first_print WHERE s = 'AMTMNO' ORDER BY obs_date")
r_d, inv_ratio, backlog = col("""SELECT obs_date::VARCHAR, AMTMTI / AMTMVS, AMTMUO / AMTMVS FROM wide
                                  WHERE AMTMVS IS NOT NULL AND AMTMTI IS NOT NULL ORDER BY obs_date""")
g_d, g_v = col("SELECT obs_date::VARCHAR, gap / 1e3 FROM gap WHERE gap IS NOT NULL ORDER BY obs_date")

BLUE, AMBER, GREEN, GREY = "#2563eb", "#b45309", "#15803d", "#9ca3af"


def line(x, y, name, color, dash="solid"):
    return {"x": x, "y": y, "name": name, "type": "scatter", "mode": "lines",
            "line": {"color": color, "width": 1.6, "dash": dash}}


CHARTS = [
    ("c1", "New orders and shipments, total manufacturing ($ billions, monthly, seasonally adjusted)",
     [line(d, no, "New orders (latest)", BLUE), line(fp_d, fp_v, "New orders (first print)", GREY, "dot"),
      line(d, vs, "Shipments (latest)", AMBER)]),
    ("c2", "Durable goods orders and core capital goods orders ($ billions)",
     [line(d, dg, "Durable goods new orders", BLUE), line(d, core, "Nondefense capital goods ex aircraft", GREEN)]),
    ("c3", "Months of shipments: inventories and unfilled orders, each divided by monthly shipments",
     [line(r_d, inv_ratio, "Inventories / shipments", AMBER), line(r_d, backlog, "Unfilled orders / shipments", BLUE)]),
    ("c4", "Backlog identity gap: new orders minus (shipments + change in unfilled orders), $ billions",
     [line(g_d, g_v, "Gap", BLUE)]),
]


def chart_div(cid, title):
    return f'<h3>{esc(title)}</h3><div id="{cid}" style="height:360px"></div>'


layout = {"margin": {"l": 50, "r": 20, "t": 10, "b": 60}, "hovermode": "x unified",
          "legend": {"orientation": "h", "y": -0.12}, "font": {"family": "system-ui, Segoe UI, sans-serif", "size": 13},
          "xaxis": {"rangeselector": {"buttons": [{"count": 5, "label": "5y", "step": "year", "stepmode": "backward"},
                                                  {"count": 10, "label": "10y", "step": "year", "stepmode": "backward"},
                                                  {"step": "all", "label": "all"}]}},
          "plot_bgcolor": "#ffffff", "paper_bgcolor": "#ffffff"}
script = "".join(f"Plotly.newPlot('{cid}', {json.dumps(tr)}, {json.dumps(layout)}, {{responsive:true}});"
                 for cid, _t, tr in CHARTS)


# ---------------------------------------------------------------- page
def f1(x):
    return "" if x is None else f"{x:,.1f}"


rows_pop = "".join(
    f"<tr><td>{s}</td><td>{esc(SERIES[s])}</td><td class=num>{num(n)}</td><td class=num>{num(v)}</td>"
    f"<td class=nw>{a}</td><td class=nw>{b}</td><td class=nw>{lv}</td><td class=num>{num(nl)}</td></tr>"
    for s, n, v, a, b, lv, nl in pop)
rows_last = "".join(
    f"<tr><td>{s}</td><td class=nw>{last[s][0]}</td><td class=num>{last[s][1] / 1e3:,.1f}</td>"
    f"<td class=num>{last[s][2]:+.2f}%</td><td class=num>{rev[s][1]:.2f}%</td><td class=num>{rev[s][2]:.2f}%</td></tr>"
    for s in SERIES)
ex = q("SELECT s, obs_date, value, units, available_date, vintage, run_id FROM m3 WHERE s = 'AMTMNO' "
       "ORDER BY vintage DESC, obs_date DESC LIMIT 1")[0]

body = f"""<div class=head><div><h1>Manufacturing (Census M3)</h1>
<p class=sub>Six Census M3 series pulled through FRED with every vintage (D169). Served version {esc(version)}; built {now_et()}.</p></div></div>

<h2>Population</h2>
<div class=wrap><table>
<tr><th>Series</th><th>What</th><th>Rows</th><th>Vintages</th><th>First month</th><th>Last month</th><th>Last vintage</th><th>Withdrawn rows</th></tr>
{rows_pop}</table></div>
<p class=muted>Withdrawn rows: months the latest vintage marks as no longer existing (null). For durable goods and core capital goods orders they are every month through 1992-01, where the total new orders series starts (1992-02). Health counts them under C16 as warnings.</p>
<p class=muted>Example row: {esc(ex[0])}, {ex[1]}, value {ex[2]:,.0f} ({esc(ex[3])}), available {ex[4]}, vintage {ex[5]}, run {esc(ex[6])}.</p>

<h2>Charts</h2>
<p class=muted>Values are each month's latest vintage. The dotted grey line in the first chart is each month's first print, so the space between it and the blue line is the later revision.</p>
{"".join(chart_div(cid, t) for cid, t, _ in CHARTS)}

<h2>Basic checks</h2>
<h3>Latest month and revision size</h3>
<div class=wrap><table>
<tr><th>Series</th><th>Latest month</th><th>$ billions</th><th>Change from prior month</th><th>Median revision since 2015</th><th>Largest revision since 2015</th></tr>
{rows_last}</table></div>
<p class=muted>Revision: the absolute percentage difference between a month's first print and its latest value.</p>
<h3>Nesting</h3>
<p>Core capital goods orders never exceed durable goods orders, and durable goods orders never exceed total orders: {num(nest[1])} and {num(nest[2])} violations in {num(nest[0])} months. Durable goods are {f1(nest[3])}% to {f1(nest[4])}% of total orders.</p>
<h3>Backlog identity</h3>
<p>New orders should equal shipments plus the change in unfilled orders. They miss in every one of {num(gap_n)} months: median ${gap_med / 1e3:,.1f}B, largest ${gap_max / 1e3:,.1f}B (fourth chart). A spot test on FRED's live data (2026-11-12, the six months to 2026-09) found the same gap, to the million, in durable goods and in computers and electronic products (NAICS 334), and a similar gap in the not seasonally adjusted series: in those months it sits entirely inside NAICS 334. Earlier years were not tested. The likely reason, not yet confirmed against Census documentation: Census excludes semiconductors from new and unfilled orders but includes them in shipments.</p>
"""

out = HERE / "m3.html"
out.write_text(page("Manufacturing (M3)", None, body, crumb="Manufacturing (M3)", head=f'<script src="{PLOTLY}"></script>')
               .replace("</main></body>", f"</main><script>{script}</script></body>"), encoding="utf-8")
print(f"wrote {out}")

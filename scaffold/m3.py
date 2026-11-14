"""Manufacturing (Census M3, via FRED; D169): the six served series, charted at their latest vintage, plus the basic
checks run when they landed (2026-11-12). Everything is measured from the served version at build time. Read-only.
Writes m3.html. Run: uv run python scaffold/m3.py"""
import json
from pathlib import Path

import duckdb

from common import esc, num, now_et, page, served_obs
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
    "NEWORDER": "Core capital goods orders: new orders, nondefense capital goods excluding aircraft",
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

# ---------------------------------------------------------------- the chart
# Core capital goods orders, real growth: each month against the same month a year earlier, after removing the
# change in capital equipment prices (PPI, final demand, private capital equipment: WPSFD41312, SA). The PPI is
# read from the served version, latest vintage (stored by D171).
def real_growth(sid):
    """NEWORDER over the same month a year earlier, deflated by FRED price index sid. Returns the months, the real
    growth in each, the last month's dollar growth and price rise, and the last earlier-year month at least as high."""
    con.execute(f"CREATE TABLE ppi_{sid} (obs_date DATE, value DOUBLE)")
    con.executemany(f"INSERT INTO ppi_{sid} VALUES (?, ?)", served_obs(con, OBS, sid))
    joins = f"""FROM wide a JOIN wide b ON b.obs_date = a.obs_date - INTERVAL 12 MONTH
                JOIN ppi_{sid} pa ON pa.obs_date = a.obs_date JOIN ppi_{sid} pb ON pb.obs_date = b.obs_date"""
    got = col(f"""SELECT a.obs_date::VARCHAR, ((a.NEWORDER / b.NEWORDER) / (pa.value / pb.value) - 1) * 100 {joins}
                  WHERE a.NEWORDER IS NOT NULL AND b.NEWORDER IS NOT NULL ORDER BY a.obs_date""")
    if not got:
        raise SystemExit(f"real core capital goods orders: no months where orders and {sid} overlap")
    d, real = got
    nom, ppi = q(f"""SELECT (a.NEWORDER / b.NEWORDER - 1) * 100, (pa.value / pb.value - 1) * 100 {joins}
                     WHERE a.obs_date = DATE '{d[-1]}'""")[0]
    prev = next((d_ for d_, v_ in zip(reversed(d), reversed(real)) if d_ < d[-1][:4] and v_ >= real[-1]), None)
    return d, real, nom, ppi, prev


# The answer depends on the price index (review 2026-11-13), so the page states both. The chart uses the first.
c_d, c_real, c_nom, c_ppi, c_prev = real_growth("WPSFD41312")
m_d, m_real, _m_nom, m_ppi, m_prev = real_growth("WPU11")
c_last_d, c_last, m_last = c_d[-1], c_real[-1], m_real[-1]
if m_d[-1] != c_last_d:
    raise SystemExit(f"real core capital goods orders: WPSFD41312 ends {c_last_d}, WPU11 ends {m_d[-1]}")
prev_txt = " and ".join(f"{p[:7]} ({sid})" for sid, p in (("WPSFD41312", c_prev), ("WPU11", m_prev)) if p)

layout = {"margin": {"l": 50, "r": 20, "t": 10, "b": 40}, "hovermode": "x unified", "showlegend": False,
          "font": {"family": "system-ui, Segoe UI, sans-serif", "size": 13},
          "yaxis": {"ticksuffix": "%", "zeroline": True, "zerolinecolor": "#6b7280"},
          "xaxis": {"rangeselector": {"buttons": [{"count": 5, "label": "5y", "step": "year", "stepmode": "backward"},
                                                  {"count": 10, "label": "10y", "step": "year", "stepmode": "backward"},
                                                  {"step": "all", "label": "all"}]}},
          "plot_bgcolor": "#ffffff", "paper_bgcolor": "#ffffff"}
trace = {"x": c_d, "y": c_real, "name": "Real growth", "type": "scatter", "mode": "lines",
         "line": {"color": "#15803d", "width": 1.6}, "hovertemplate": "%{y:.1f}%<extra></extra>"}
script = f"Plotly.newPlot('c1', {json.dumps([trace])}, {json.dumps(layout)}, {{responsive:true}});"


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
# What's inside total shipments: the M3 industry lines, each one's latest month from the served version (stored by
# D171). Parts must sum to their half and the halves to the total, or the build stops.
def fred_last(sid):
    return served_obs(con, OBS, sid)[-1]


HALVES = {
    "AMDMVS": ("Durable", {"A36SVS": "Transportation equipment", "A33SVS": "Machinery", "A32SVS": "Fabricated metal",
                           "A34SVS": "Computers and electronic", "A31SVS": "Primary metals",
                           "A35SVS": "Electrical equipment and appliances", "A39SVS": "Miscellaneous (incl. medical devices)",
                           "A27SVS": "Nonmetallic mineral (glass, cement)", "A21SVS": "Wood", "A37SVS": "Furniture"}),
    "AMNMVS": ("Nondurable", {"A11SVS": "Food", "A25SVS": "Chemicals (incl. pharma)", "A24SVS": "Petroleum and coal",
                              "A26SVS": "Plastics and rubber", "A22SVS": "Paper", "A12SVS": "Beverage and tobacco",
                              "A23SVS": "Printing", "A13SVS": "Textile mills", "A14SVS": "Textile products",
                              "A15SVS": "Apparel", "A16SVS": "Leather"}),
}
mix_month, mix_total = fred_last("AMTMVS")
mix_rows, half_sum = [], 0.0
for hid, (hname, parts) in HALVES.items():
    hm, hv = fred_last(hid)
    got = [(sid, nm, *fred_last(sid)) for sid, nm in parts.items()]
    if {hm, *(g[2] for g in got)} != {mix_month}:
        raise SystemExit(f"industry mix: months differ from {mix_month} in {hname}")
    if abs(sum(g[3] for g in got) - hv) > 1:
        raise SystemExit(f"industry mix: {hname} parts sum to {sum(g[3] for g in got):,.0f}, half is {hv:,.0f}")
    half_sum += hv
    mix_rows += [(hname, nm, sid, v) for sid, nm, _d, v in sorted(got, key=lambda g: -g[3])]
    mix_rows.append((hname, "Total", hid, hv))
if abs(half_sum - mix_total) > 1:
    raise SystemExit(f"industry mix: halves sum to {half_sum:,.0f}, total is {mix_total:,.0f}")
rows_mix = "".join(
    f"<tr><td>{h}</td><td>{'<b>Total</b>' if nm == 'Total' else esc(nm)}</td><td>{sid}</td>"
    f"<td class=num>{v / 1e3:,.1f}</td><td class=num>{v / mix_total * 100:.1f}%</td></tr>" for h, nm, sid, v in mix_rows)

ex = q("SELECT s, obs_date, value, units, available_date, vintage, run_id FROM m3 WHERE s = 'AMTMNO' "
       "ORDER BY vintage DESC, obs_date DESC LIMIT 1")[0]

body = f"""<div class=head><div><h1>Manufacturing (Census M3)</h1>
<p class=sub>Six Census M3 series pulled through FRED with every vintage (D169). Served version {esc(version)}; built {now_et()}.</p></div></div>

<div style="border:1px solid #15803d;background:#f0fdf4;border-radius:6px;padding:10px 14px;margin:14px 0 6px"><b>Extra information:</b> <a href="core-capex-in-gdp.html">Where core capital goods orders sit in the economy</a>: how an equipment purchase reaches GDP, how much weight equipment carries in GDP growth, and the companies doing the buying (an exploration with figures fixed at 2026-11-12).</div>

<h2>Population</h2>
<div class=wrap><table>
<tr><th>Series</th><th>What</th><th>Rows</th><th>Vintages</th><th>First month</th><th>Last month</th><th>Last vintage</th><th>Withdrawn rows</th></tr>
{rows_pop}</table></div>
<p class=muted>Withdrawn rows: months the latest vintage marks as no longer existing (null). For durable goods and core capital goods orders they are every month through 1992-01, where the total new orders series starts (1992-02). Health counts them under C16 as warnings.</p>
<p class=muted>Example row: {esc(ex[0])}, {ex[1]}, value {ex[2]:,.0f} ({esc(ex[3])}), available {ex[4]}, vintage {ex[5]}, run {esc(ex[6])}.</p>

<h2>Core capital goods orders (NEWORDER), growth after inflation</h2>
<p>Core capital goods orders are new orders for business equipment, excluding defense and aircraft: the M3 series most watched as an early read on business investment. The line is growth over the same month a year earlier, after taking out the rise in private capital equipment prices (WPSFD41312). In {c_last_d[:7]} orders were up {c_nom:.1f}% in dollars and {min(c_last, m_last):.1f}% to {max(c_last, m_last):.1f}% after inflation, depending on the price index: {c_last:.1f}% with private capital equipment prices (WPSFD41312, up {c_ppi:.1f}%), {m_last:.1f}% with machinery and equipment prices (WPU11, up {m_ppi:.1f}%){f"; before this year real growth was last this high in {prev_txt}" if prev_txt else ""}. My read: this is the one strong signal in M3 right now. Total factory shipments are up about 10% in dollars but close to flat once each industry's own price rise is removed (a one-off calculation on 2026-11-12, not on this page), so the strength is in business equipment, not manufacturing as a whole.</p>
<div id="c1" style="height:380px"></div>
<p class=muted>Price indexes: producer price index, final demand, private capital equipment (WPSFD41312, the line above) and producer price index, machinery and equipment (WPU11), both read from served version {esc(version)}, each month's latest vintage, like the orders.</p>

<h2>Basic checks</h2>
<h3>Latest month and revision size</h3>
<div class=wrap><table>
<tr><th>Series</th><th>Latest month</th><th>$ billions</th><th>Change from prior month</th><th>First print to latest value, median since 2015</th><th>First print to latest value, largest since 2015</th></tr>
{rows_last}</table></div>
<p class=muted>Revision: the absolute percentage difference between a month's first print and its latest value.</p>
<h3>Nesting</h3>
<p>Core capital goods orders never exceed durable goods orders, and durable goods orders never exceed total orders: {num(nest[1])} and {num(nest[2])} violations in {num(nest[0])} months. Durable goods are {f1(nest[3])}% to {f1(nest[4])}% of total orders.</p>
<h3>Backlog identity</h3>
<p>New orders should equal shipments plus the change in unfilled orders. They miss in every one of {num(gap_n)} months: median ${gap_med / 1e3:,.1f}B, largest ${gap_max / 1e3:,.1f}B. A spot test on FRED's live data (2026-11-12, the six months to 2026-09) found the same gap, to the million, in durable goods and in computers and electronic products (NAICS 334), and a similar gap in the not seasonally adjusted series: in those months it sits entirely inside NAICS 334. Earlier years were not tested. The reason, from Census's M3 report notes: figures on new and unfilled orders exclude semiconductor manufacturing, while shipments include it.</p>

<h2>What's in these numbers</h2>
<p>M3 estimates all US manufacturing, public or private, US- or foreign-owned, and not factories abroad. About 4,700 reporting units (divisions or whole companies, about 3,000 companies in all) answer each month, and Census scales their month-to-month changes to the whole sector. Each unit sits in the industry of what it makes, so one company can appear in several lines below; Census does not publish who reports. In Census's report the nondurable new orders figures are the same as nondurable shipments, so only the durable half says anything separate about orders. Values are nominal dollars per month, so the petroleum line (mostly refineries) moves with the price of crude.</p>
<h3>Shipments by industry, {mix_month[:7]}: ${mix_total / 1e3:,.1f}B total manufacturing</h3>
<div class=wrap><table>
<tr><th>Half</th><th>Industry</th><th>FRED series</th><th>$ billions</th><th>Share of total</th></tr>
{rows_mix}</table></div>
<p class=muted>Each series' latest month, read from served version {esc(version)}. The industries add up to each half and the two halves add up to the total; the build stops if they don't.</p>
"""

out = HERE / "m3.html"
out.write_text(page("Manufacturing (M3)", "Catalog", body, crumb="Manufacturing (M3)", parent=("FRED / ALFRED", "fred.html"), head=f'<script src="{PLOTLY}"></script>')
               .replace("</main></body>", f"</main><script>{script}</script></body>"), encoding="utf-8")
print(f"wrote {out}")

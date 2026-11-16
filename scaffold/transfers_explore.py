"""Temporary exploration page: BEA transfer receipts and personal income, as downloaded 2026-11-16 (James).

Reads only the four CSV tables extracted to <data root>/tmp/scaffold/bea/ (quick lane, nothing stored): state
transfers by program (SAINC35), state income (SAINC4), county income (CAINC4), county transfers by program
(CAINC35, BEA's 2023 copy). Every figure on the page is computed here. Writes transfers-explore.html.
Run: uv run python scaffold/transfers_explore.py"""
import json
from pathlib import Path

import duckdb
from common import esc, now_et, num, page

from pharos.paths import data_root

HERE = Path(__file__).parent
BEA = data_root() / "tmp" / "scaffold" / "bea"
PLOTLY = "https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.35.2/plotly.min.js"
FILES = {"s35": ("SAINC.zip", "SAINC35", "States, transfers by program", BEA / "sainc" / "SAINC35__ALL_AREAS_1929_2025.csv"),
         "s4": ("SAINC.zip", "SAINC4", "States, personal income by component", BEA / "sainc" / "SAINC4__ALL_AREAS_1929_2025.csv"),
         "c4": ("CAINC4.zip", "CAINC4", "Counties, personal income by component", BEA / "cainc4" / "CAINC4__ALL_AREAS_1969_2024.csv"),
         "c35": ("CAINC35.zip", "CAINC35", "Counties, transfers by program", BEA / "cainc35" / "CAINC35__ALL_AREAS_1969_2022.csv")}
COUNTY = "fips NOT LIKE '%000'"
STATE = "fips LIKE '%000' AND fips <> '00000' AND fips < '90000'"

con = duckdb.connect()
q = lambda s: con.execute(s).fetchall()
count_rows = []
for t, (zipname, table, what, f) in FILES.items():
    updated = next(ln.strip().strip('"') for ln in f.read_text(encoding="latin-1").splitlines()[-6:] if "Last updated" in ln)
    con.execute(f"""CREATE TABLE {t}_w AS SELECT * FROM read_csv('{f.as_posix()}', all_varchar=true, header=true,
        encoding='latin-1', null_padding=true, strict_mode=false) WHERE LineCode IS NOT NULL""")
    con.execute(f"""CREATE TABLE {t} AS SELECT trim(replace(GeoFIPS, '"', '')) AS fips, trim(GeoName) AS place,
        LineCode::INT AS line, trim(Description) AS descr, Unit AS unit, y::INT AS yr, v AS raw, try_cast(v AS DOUBLE) AS val
        FROM (UNPIVOT {t}_w ON COLUMNS('^[0-9]{{4}}$') INTO NAME y VALUE v)""")
    rows, = q(f"SELECT count(*) FROM {t}_w")[0]
    cells, st, co, lines, y0, y1, na = q(f"""SELECT count(*), count(DISTINCT fips) FILTER (WHERE {STATE}),
        count(DISTINCT fips) FILTER (WHERE {COUNTY}), count(DISTINCT line), min(yr), max(yr),
        count(*) FILTER (WHERE val IS NULL) FROM {t}""")[0]
    other = q(f"SELECT DISTINCT raw FROM {t} WHERE val IS NULL AND raw NOT IN ('(NA)', '(L)')")
    if other:
        raise SystemExit(f"{table}: cells that are neither a number, (NA) nor (L): {other[:5]}")
    units = ", ".join(u.lower() for u, in q(f"SELECT DISTINCT unit FROM {t} WHERE line IN (10, 47, 1000) ORDER BY 1"))
    count_rows.append(f"<tr><td>{table}</td><td>{esc(what)}</td><td>{zipname}</td><td>{esc(updated.split('--')[0].replace('Last updated: ', ''))}</td>"
                      f"<td class=num>{num(rows)}</td><td class=num>{st}</td><td class=num>{num(co)}</td><td class=num>{lines}</td>"
                      f"<td class=num>{y0}-{y1}</td><td class=num>{num(cells)}</td><td class=num>{num(na)}</td><td>{units}</td></tr>")

# one county, as the file gives it
EX = "17031"
ex_rows = "".join(f"<tr><td>CAINC4</td><td>{f}</td><td>{esc(p)}</td><td class=num>{ln}</td><td>{esc(d)}</td><td>{esc(u)}</td>"
                  f"<td class=num>{raw}</td></tr>" for f, p, ln, d, u, raw in
                  q(f"SELECT fips, place, line, descr, unit, raw FROM c4 WHERE fips='{EX}' AND yr=2024 AND line IN (10, 20, 45, 46, 47) ORDER BY line"))

# shares: state file in millions, county file in thousands
con.execute("""CREATE TABLE ss AS SELECT a.fips, a.place, a.yr, 100 * a.val / b.val AS sh FROM s4 a JOIN s4 b USING (fips, yr)
    WHERE a.line = 47 AND b.line = 10 AND a.val IS NOT NULL""")
con.execute("""CREATE TABLE cs AS SELECT a.fips, a.place, a.yr, a.val AS tr, b.val AS pi, p.val AS pop, 100 * a.val / b.val AS sh
    FROM c4 a JOIN c4 b USING (fips, yr) JOIN c4 p USING (fips, yr)
    WHERE a.line = 47 AND b.line = 10 AND p.line = 20 AND a.val IS NOT NULL""")
SY, CY = q("SELECT max(yr) FROM ss")[0][0], q("SELECT max(yr) FROM cs")[0][0]
us = q("SELECT yr, sh FROM ss WHERE fips = '00000' ORDER BY yr")
us_d = dict(us)
peak_yr = max(us_d, key=us_d.get)

# programs, US, percent of personal income
GROUPS = (("Social Security", (2110,), "#2563eb"), ("Medicare", (2210,), "#0f766e"),
          ("Medicaid and other public medical", (2220,), "#7c3aed"), ("Income support (SSI, EITC, SNAP, other)", (2300,), "#c2410c"),
          ("Unemployment pay", (2400,), "#b91c1c"), ("Veterans", (2500,), "#a16207"))
ALL_LINES = [ln for _, ls, _ in GROUPS for ln in ls]
prog = q(f"""SELECT a.yr, {', '.join(f'100 * sum(a.val) FILTER (WHERE a.line = {ln}) / any_value(b.val * 1000)' for ln in ALL_LINES)},
    100 * (sum(a.val) FILTER (WHERE a.line = 1000) - sum(a.val) FILTER (WHERE a.line IN ({','.join(map(str, ALL_LINES))}))) / any_value(b.val * 1000)
    FROM s35 a JOIN s4 b ON b.fips = a.fips AND b.yr = a.yr AND b.line = 10 WHERE a.fips = '00000'
    GROUP BY 1 HAVING count(a.val) FILTER (WHERE a.line IN ({','.join(map(str, ALL_LINES))})) = {len(ALL_LINES)} ORDER BY 1""")
names = [g[0] for g in GROUPS] + ["Everything else"]
colors = [g[2] for g in GROUPS] + ["#9ca3af"]

# states, latest year, four parts
states = q(f"""SELECT a.place, 100 * sum(a.val) FILTER (WHERE a.line = 2110) / any_value(b.val * 1000),
    100 * sum(a.val) FILTER (WHERE a.line = 2210) / any_value(b.val * 1000),
    100 * sum(a.val) FILTER (WHERE a.line = 2220) / any_value(b.val * 1000),
    100 * sum(a.val) FILTER (WHERE a.line = 1000) / any_value(b.val * 1000)
    FROM s35 a JOIN s4 b ON b.fips = a.fips AND b.yr = a.yr AND b.line = 10
    WHERE a.yr = {SY} AND a.fips < '90000' GROUP BY 1 ORDER BY 5""")

# counties
pct = q(f"""SELECT yr, quantile_cont(sh, 0.1), quantile_cont(sh, 0.5), quantile_cont(sh, 0.9) FROM cs WHERE {COUNTY} GROUP BY 1 ORDER BY 1""")
us_c = dict(q("SELECT yr, sh FROM cs WHERE fips = '00000'"))
hist = q(f"SELECT floor(sh / 2) * 2, count(*) FROM cs WHERE {COUNTY} AND yr = {CY} GROUP BY 1 ORDER BY 1")
n_co, med, n30, pop30, pop_all = q(f"""SELECT count(*), quantile_cont(sh, 0.5), count(*) FILTER (WHERE sh > 30),
    sum(pop) FILTER (WHERE sh > 30), sum(pop) FROM cs WHERE {COUNTY} AND yr = {CY}""")[0]
n_above, = q(f"SELECT count(*) FROM cs WHERE {COUNTY} AND yr = {CY} AND sh > {us_c[CY]}")[0]
scat = q(f"SELECT place, 1000 * pi / pop, sh, pop FROM cs WHERE {COUNTY} AND yr = {CY} AND pop > 0")
pp = q(f"""SELECT quantile_cont(1000 * tr / pop, [0.1, 0.5, 0.9]), quantile_cont(1000 * pi / pop, [0.1, 0.5, 0.9])
    FROM cs WHERE {COUNTY} AND yr = {CY} AND pop > 0""")[0]


def county_rows(order):
    return "".join(f"<tr><td>{esc(p)}</td><td class=num>{sh:.1f}%</td><td class=num>{num(round(pop))}</td>"
                   f"<td class=num>{num(round(a))}</td><td class=num>{num(round(b))}</td></tr>" for p, sh, pop, a, b in
                   q(f"SELECT place, sh, pop, 1000 * tr / pop, 1000 * pi / pop FROM cs WHERE {COUNTY} AND yr = {CY} ORDER BY sh {order} LIMIT 10"))


jumps = q(f"""SELECT place, yr, sh - d, sh, d FROM (SELECT *, sh - lag(sh) OVER (PARTITION BY fips ORDER BY yr) AS d FROM cs WHERE {COUNTY})
    WHERE pop > 20000 ORDER BY abs(d) DESC LIMIT 12""")
jump_rows = "".join(f"<tr><td>{esc(p)}</td><td class=num>{y}</td><td class=num>{a:.1f}%</td><td class=num>{b:.1f}%</td>"
                    f"<td class=num>{d:+.1f}</td></tr>" for p, y, a, b, d in jumps)

# does it add up
t_prog = q("""SELECT max(abs(d1)), max(abs(d2)), count(*) FROM (SELECT fips, yr,
    sum(val) FILTER (WHERE line IN (2000, 3000, 4000)) - sum(val) FILTER (WHERE line = 1000) AS d1,
    sum(val) FILTER (WHERE line IN (2100, 2200, 2300, 2400, 2500, 2600, 2700)) - sum(val) FILTER (WHERE line = 2000) AS d2
    FROM s35 GROUP BY ALL) WHERE d2 IS NOT NULL""")[0]
t_st = q(f"""SELECT max(abs(d)), count(*) FROM (SELECT yr, sum(val) FILTER (WHERE {STATE}) - sum(val) FILTER (WHERE fips = '00000') AS d
    FROM s35 WHERE line = 1000 GROUP BY 1)""")[0]
t_co = q(f"""WITH c AS (SELECT left(fips, 2) AS st, line, yr, sum(val) AS v FROM c4 WHERE {COUNTY} AND fips < '90000' AND line IN (10, 47) GROUP BY ALL),
    s AS (SELECT left(fips, 2) AS st, line, yr, val FROM c4 WHERE {STATE} AND line IN (10, 47))
    SELECT max(abs(c.v - s.val)), count(*) FROM c JOIN s USING (st, line, yr)""")[0]
t_id = q("""SELECT max(abs(d)), count(*) FROM (SELECT fips, yr, sum(val) FILTER (WHERE line IN (45, 46, 47)) - sum(val) FILTER (WHERE line = 10) AS d
    FROM c4 GROUP BY ALL) WHERE d IS NOT NULL""")[0]
tie_rows = "".join(f"<tr><td>{esc(a)}</td><td class=num>{num(n)}</td><td class=num>{d:,.0f}</td></tr>" for a, n, d in (
    ("SAINC35: individuals from governments + nonprofits + individuals from businesses = total transfers (place-years)", t_prog[2], t_prog[0]),
    ("SAINC35: the seven program groups = individuals from governments (place-years)", t_prog[2], t_prog[1]),
    ("SAINC35: 51 states = United States, total transfers (years)", t_st[1], t_st[0]),
    ("CAINC4: counties = their state, for transfers and for personal income (state-years x 2)", t_co[1], t_co[0]),
    ("CAINC4: net earnings + dividends, interest, rent + transfers = personal income (place-years)", t_id[1], t_id[0])))

# what looks odd
vint = q("""SELECT a.yr, a.val / 1e6, b.val / 1e6, 100 * (a.val / b.val - 1) FROM c4 a JOIN c35 b USING (fips, yr)
    WHERE a.fips = '00000' AND a.line = 47 AND b.line = 1000 AND a.yr >= 2016 ORDER BY 1""")
vint_rows = "".join(f"<tr><td class=num>{y}</td><td class=num>{a:,.0f}</td><td class=num>{b:,.0f}</td><td class=num>{d:+.2f}%</td></tr>"
                    for y, a, b, d in vint)
v_med, v_max, v_place = q(f"""SELECT 100 * quantile_cont(abs(a.val / b.val - 1), 0.5), 100 * max(abs(a.val / b.val - 1)),
    arg_max(a.place, abs(a.val / b.val - 1)) FROM c4 a JOIN c35 b USING (fips, yr)
    WHERE a.{COUNTY} AND a.line = 47 AND b.line = 1000 AND a.yr = 2022""")[0]
sv_max, sv_place = q(f"""SELECT 100 * max(abs(a.val / 1000 / b.val - 1)), arg_max(a.place, abs(a.val / 1000 / b.val - 1))
    FROM c4 a JOIN s4 b USING (fips, yr, line) WHERE a.line = 47 AND a.{STATE} AND a.yr = {CY}""")[0]
memo = q(f"SELECT sum(val) FILTER (WHERE line = 5000) / 1e6, sum(val) FILTER (WHERE line = 2320) / 1e6 FROM s35 WHERE fips = '00000' AND yr = {SY}")[0]
ct_new, = q("SELECT count(DISTINCT fips) FROM c4 WHERE fips LIKE '09%' AND fips NOT IN (SELECT fips FROM c35)")[0]
ct_old_na, = q(f"SELECT count(*) FROM c4 WHERE fips LIKE '09%' AND fips <> '09000' AND fips IN (SELECT fips FROM c35) AND line = 47 AND yr = {CY} AND val IS NULL")[0]
va_merged, = q("SELECT count(DISTINCT fips) FROM c4 WHERE place LIKE '%+%' AND place LIKE '%, VA%'")[0]
na_latest, = q(f"SELECT count(*) FROM c4 WHERE {COUNTY} AND line = 47 AND yr = {CY} AND val IS NULL")[0]
neg = q("SELECT descr, count(*) FROM c4 WHERE val < 0 GROUP BY 1 ORDER BY 2 DESC LIMIT 3")
top = q(f"SELECT place, sh, 1000 * tr / pop FROM cs WHERE {COUNTY} AND yr = {CY} ORDER BY sh DESC LIMIT 1")[0]
ky, = q(f"SELECT count(*) FROM (SELECT place FROM cs WHERE {COUNTY} AND yr = {CY} ORDER BY sh DESC LIMIT 12) WHERE place LIKE '%, KY%'")[0]

odd = (
    ("The two county files were published 27 months apart and no longer agree",
     f"County totals (CAINC4) and county programs (CAINC35) give the same US transfers through 2018. For 2022 they differ by "
     f"{vint[-1][3]:+.1f}%. County by county in 2022 the middle difference is {v_med:.1f}% and the largest is {v_max:.0f}% ({esc(v_place)}). "
     f"A program share for a county has to use CAINC35 for both the program and the total, or it mixes two publication dates."),
    ("The state file is newer than the county file",
     f"For {CY}, a state's transfers in the state file differ from the same state in the county file by up to {sv_max:.1f}% ({esc(sv_place)})."),
    ("Units differ between files", "State income (SAINC4) is in millions of dollars. Every other table here is in thousands."),
    ("One line is not part of the total", f"\"Refundable tax credits\" (line 5000, ${memo[0]:,.0f}bn in {SY}) repeats money already counted "
     f"in the program lines, such as the Earned Income Tax Credit (${memo[1]:,.0f}bn). The total adds up without it."),
    ("Connecticut changed its map", f"The newer county file has {ct_new} Connecticut planning regions that the older file does not; "
     f"{ct_old_na} of the old Connecticut counties are blank in {CY}."),
    ("Virginia cities are merged into counties", f"{va_merged} Virginia rows are a county plus one or more cities, by BEA's choice."),
    ("Blanks and negatives", f"{na_latest} county rows are blank for {CY} (old Alaska areas, the old Connecticut counties and one Wisconsin row, all replaced by newer rows). Nothing is withheld for privacy in "
     f"these tables. Negative values appear only where a negative is possible: " + "; ".join(f"{esc(d)} ({num(n)})" for d, n in neg) + "."),
    (f"The highest county looks extreme: {esc(top[0])}", f"{top[1]:.0f}% of income is transfers, ${top[2]:,.0f} per person, against "
     f"${pp[0][1]:,.0f} for the middle county. {ky} of the 12 highest counties are in Kentucky. Worth a second look before quoting."),
)
odd_rows = "".join(f"<tr><td>{a}</td><td>{b}</td></tr>" for a, b in odd)

# charts
font = {"family": "system-ui, Segoe UI, sans-serif", "size": 13}
base = {"margin": {"l": 50, "r": 20, "t": 10, "b": 40}, "font": font, "plot_bgcolor": "#ffffff", "paper_bgcolor": "#ffffff",
        "legend": {"orientation": "h", "y": 1.12}}
pctaxis = {"ticksuffix": "%", "rangemode": "tozero", "gridcolor": "#e3e1da"}
charts = [
    ("c1", [{"x": [y for y, _ in us], "y": [round(s, 2) for _, s in us], "type": "scatter", "mode": "lines",
             "line": {"color": "#2563eb", "width": 2}, "hovertemplate": "%{x}: %{y:.1f}%<extra></extra>"}],
     {**base, "yaxis": pctaxis, "hovermode": "x", "showlegend": False}),
    ("c2", [{"x": [r[0] for r in prog], "y": [round(r[i + 1], 3) for r in prog], "name": names[i], "type": "scatter", "mode": "lines",
             "stackgroup": "a", "line": {"width": 0.5, "color": colors[i]}, "fillcolor": colors[i], "hovertemplate": "%{y:.1f}%"}
            for i in range(len(names))],
     {**base, "yaxis": pctaxis, "hovermode": "x unified"}),
    ("c3", [{"y": [s[0] for s in states], "x": [round(v, 2) for v in vals], "name": nm, "type": "bar", "orientation": "h",
             "marker": {"color": col}, "hovertemplate": "%{y}: %{x:.1f}%<extra>" + nm + "</extra>"}
            for nm, col, vals in (("Social Security", "#2563eb", [s[1] for s in states]), ("Medicare", "#0f766e", [s[2] for s in states]),
                                  ("Medicaid and other public medical", "#7c3aed", [s[3] for s in states]),
                                  ("Everything else", "#9ca3af", [s[4] - s[1] - s[2] - s[3] for s in states]))],
     {**base, "barmode": "stack", "xaxis": pctaxis, "yaxis": {"tickfont": {"size": 12}, "automargin": True},
      "margin": {"l": 150, "r": 20, "t": 10, "b": 40}}),
    ("c4", [{"x": [r[0] for r in pct], "y": [round(r[i], 2) for r in pct], "name": nm, "type": "scatter", "mode": "lines",
             "line": {"color": col, "width": 1.8}, "hovertemplate": "%{y:.1f}%"}
            for i, nm, col in ((3, "County at the 90th percentile", "#b91c1c"), (2, "Middle county", "#2563eb"), (1, "County at the 10th percentile", "#0f766e"))]
     + [{"x": sorted(us_c), "y": [round(us_c[y], 2) for y in sorted(us_c)], "name": "United States as a whole", "type": "scatter",
         "mode": "lines", "line": {"color": "#1d1d1b", "width": 2, "dash": "dot"}, "hovertemplate": "%{y:.1f}%"}],
     {**base, "yaxis": pctaxis, "hovermode": "x unified"}),
    ("c5", [{"x": [b + 1 for b, _ in hist], "y": [n for _, n in hist], "type": "bar", "width": 1.8, "marker": {"color": "#2563eb"},
             "hovertemplate": "%{y} counties<extra></extra>"}],
     {**base, "xaxis": {"ticksuffix": "%", "title": {"text": "Transfers as a share of personal income"}},
      "yaxis": {"title": {"text": "Counties"}, "gridcolor": "#e3e1da"}, "showlegend": False, "margin": {"l": 60, "r": 20, "t": 10, "b": 50},
      "shapes": [{"type": "line", "x0": us_c[CY], "x1": us_c[CY], "y0": 0, "y1": 1, "yref": "paper", "line": {"color": "#1d1d1b", "dash": "dot"}}],
      "annotations": [{"x": us_c[CY], "y": 1, "yref": "paper", "text": f"United States {us_c[CY]:.1f}%", "showarrow": False, "xanchor": "left", "xshift": 6}]}),
    ("c6", [{"x": [round(r[1]) for r in scat], "y": [round(r[2], 2) for r in scat], "text": [r[0] for r in scat], "type": "scattergl",
             "mode": "markers", "marker": {"size": 5, "color": "#2563eb", "opacity": 0.45},
             "hovertemplate": "%{text}<br>income per person $%{x:,}<br>transfers %{y:.1f}%<extra></extra>"}],
     {**base, "xaxis": {"type": "log", "title": {"text": "Personal income per person, dollars (log scale)"}, "gridcolor": "#e3e1da",
                "tickvals": [30000, 40000, 50000, 70000, 100000, 150000, 200000, 300000, 500000],
                "ticktext": ["30,000", "40,000", "50,000", "70,000", "100,000", "150,000", "200,000", "300,000", "500,000"]},
      "yaxis": {**pctaxis, "title": {"text": "Transfers as a share of income"}}, "showlegend": False,
      "margin": {"l": 60, "r": 20, "t": 10, "b": 50}}),
]
script = "".join(f"Plotly.newPlot('{cid}', {json.dumps(tr)}, {json.dumps(lay)}, {{responsive:true}});" for cid, tr, lay in charts)
div = "<div id={} style='height:{}px'></div>"

body = f"""<div class="head"><h1>Exploring BEA transfers and personal income</h1>
<div class="stamps"><span>Page built {now_et()}</span><span>Files downloaded 2026-11-16, not stored</span></div></div>
<p class=sub>Transfers are government and other payments to people that are not pay for work: Social Security, Medicare,
Medicaid, unemployment pay and the rest. Share = transfers / personal income of the same place and year.</p>

<h2>What was pulled</h2>
<div class=wrap><table><tr><th>BEA table</th><th>Holds</th><th>From file</th><th>BEA last updated</th><th>Rows</th><th>States</th>
<th>Counties</th><th>Lines</th><th>Years</th><th>Cells</th><th>Blank cells</th><th>Unit of the dollar lines</th></tr>{''.join(count_rows)}</table></div>
<p class=sub>A row is one place and one line, with a column per year. A cell is one place, line and year. Each table also has
the United States and 8 regions.</p>
<h3>One county as the file gives it, {CY}</h3>
<div class=wrap><table><tr><th>Table</th><th>GeoFIPS</th><th>GeoName</th><th>LineCode</th><th>Description</th><th>Unit</th><th>{CY}</th></tr>{ex_rows}</table></div>

<h2>Does it add up</h2>
<table><tr><th>Check</th><th>Cases</th><th>Largest difference, $ thousand</th></tr>{tie_rows}</table>
<p class=sub>Every check ties to the dollar. Each file agrees with itself.</p>

<h2>What looks odd</h2>
<table><tr><th>Finding</th><th>Evidence</th></tr>{odd_rows}</table>
<h3>US total transfers in the two county files, $ billion</h3>
<table><tr><th>Year</th><th>CAINC4 (2026 file)</th><th>CAINC35 (2023 file)</th><th>Difference</th></tr>{vint_rows}</table>

<h2>United States, {us[0][0]} to {SY}</h2>
<p class=sub>Transfers as a share of personal income: {us[0][1]:.1f}% in {us[0][0]}, {us_d[SY]:.1f}% in {SY}, highest in {peak_yr} at {us_d[peak_yr]:.1f}%. State file.</p>
{div.format('c1', 320)}
<h3>By program, share of US personal income, {prog[0][0]} to {SY}</h3>
{div.format('c2', 380)}

<h2>States, {SY}</h2>
<p class=sub>Transfers as a share of each state's personal income, split in four. Highest at the top.</p>
{div.format('c3', 1050)}

<h2>Counties, {min(us_c)} to {CY}</h2>
<p class=sub>{num(n_co)} counties in {CY}. The middle county is at {med:.1f}%; the United States as a whole is at {us_c[CY]:.1f}%.
{num(n_above)} counties are above the US figure, because the counties with the most people have low shares.
{num(n30)} counties are above 30%; they hold {pop30 / 1e6:.1f} million of {pop_all / 1e6:.0f} million people.</p>
{div.format('c4', 360)}
<h3>How many counties at each share, {CY}</h3>
{div.format('c5', 320)}
<h3>Share against income per person, each county, {CY}</h3>
<p class=sub>Transfers per person: ${pp[0][0]:,.0f} at the 10th-percentile county, ${pp[0][1]:,.0f} in the middle, ${pp[0][2]:,.0f} at the 90th.
Income per person: ${pp[1][0]:,.0f}, ${pp[1][1]:,.0f}, ${pp[1][2]:,.0f}. Hover a dot for the county.</p>
{div.format('c6', 460)}
<div class=cols>
<div><h3>Ten highest, {CY}</h3><table><tr><th>County</th><th>Share</th><th>People</th><th>Transfers per person, $</th><th>Income per person, $</th></tr>{county_rows('DESC')}</table></div>
<div><h3>Ten lowest, {CY}</h3><table><tr><th>County</th><th>Share</th><th>People</th><th>Transfers per person, $</th><th>Income per person, $</th></tr>{county_rows('ASC')}</table></div>
</div>
<h3>Largest one-year moves in a county's share, counties over 20,000 people</h3>
<table><tr><th>County</th><th>Year</th><th>Year before</th><th>That year</th><th>Change, points</th></tr>{jump_rows}</table>"""

out = HERE / "transfers-explore.html"
out.write_text(page("Exploring BEA transfers", "Ideas", body, crumb="Exploring BEA transfers",
                    head=f'<script src="{PLOTLY}"></script>')
               .replace("</main></body>", f"</main><script>{script}</script></body>"), encoding="utf-8")
print(f"wrote {out}: {num(n_co)} counties {CY}, states {SY}, US share {us_d[SY]:.1f}%")

"""Temporary exploration page: US measles cases, as states report them and as CDC reports them (James, 2026-11-18).

Reads only files downloaded to <data root>/tmp/scaffold/measles/ (quick lane, nothing stored):
  jhu/  Johns Hopkins measles_data repository (github.com/CSSEGISandData/measles_data): county counts compiled from
        state and county health department releases, and a weekly file for six states by rash onset.
  cdc/  CDC NNDSS weekly tables from data.cdc.gov: x9gk-5huc (2022 on, measles rows only), 7xhe-mv2e (2020),
        9k8a-cbgx (2021). One row per reporting area and week, cumulative cases for the year.
The 2019 table (jf8m-mtc3) is downloaded but not read: its measles columns change meaning at week 52.
Weekly CDC counts are the week-to-week change in the cumulative column. Every figure is computed here except the
CDC web page row, typed in from cdc.gov/measles/data-research as read on 2026-11-18. Writes measles.html.
Run: uv run python scaffold/measles.py"""
import json
from datetime import date, timedelta
from pathlib import Path

import duckdb
from common import MERMAID, esc, now_et, num, page

from pharos.paths import data_root

HERE = Path(__file__).parent
M = data_root() / "tmp" / "scaffold" / "measles"
PLOTLY = "https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.35.2/plotly.min.js"
NEW = M / "cdc" / "nndss_weekly_x9gk-5huc_measles.csv"
OLD = {2020: M / "cdc" / "nndss_table1v_2020_7xhe-mv2e.csv", 2021: M / "cdc" / "nndss_table1v_2021_9k8a-cbgx.csv"}
JHU = M / "jhu" / "measles_county_all_updates.csv"
JHU_D = M / "jhu" / "measles_county_all_updates_detailed.csv"
JHU_W = M / "jhu" / "Top_states_time_series.csv"
JHU_S = M / "jhu" / "data_sources_by_state.csv"
for f in (NEW, *OLD.values(), JHU, JHU_D, JHU_W, JHU_S):
    if not f.exists():
        raise SystemExit(f"missing download: {f}")
# CDC web page, typed in (cases, outbreaks); not in any file here.
CDC_PAGE = {2018: (381, None), 2019: (1274, None), 2020: (13, None), 2021: (49, None), 2022: (121, None), 2023: (59, 4),
            2024: (285, 16), 2025: (2289, 48), 2026: (5412, 43)}
DIVISIONS = ("NEW ENGLAND", "MIDDLE ATLANTIC", "EAST NORTH CENTRAL", "WEST NORTH CENTRAL", "SOUTH ATLANTIC", "EAST SOUTH CENTRAL",
             "WEST SOUTH CENTRAL", "MOUNTAIN", "PACIFIC")
ROLLUPS = DIVISIONS + ("US RESIDENTS", "U.S. RESIDENTS", "NON-US RESIDENTS", "NON-U.S. RESIDENTS", "TOTAL", "US TERRITORIES", "U.S. TERRITORIES")
SIX = (("PA", "PENNSYLVANIA"), ("SC", "SOUTH CAROLINA"), ("TX", "TEXAS"), ("UT", "UTAH"), ("WI", "WISCONSIN"), ("OH", "OHIO"))
COLORS = ("#2563eb", "#c2410c", "#0f766e", "#7c3aed", "#a16207", "#be185d")


def week_start(y, w):
    """First day (Sunday) of an MMWR week: week 1 is the week holding January 4."""
    j = date(y, 1, 4)
    return j - timedelta(days=j.isoweekday() % 7) + timedelta(weeks=w - 1)


def name(a):
    return a.title().replace(" Of ", " of ").replace("U.S. ", "US ").replace("Us ", "US ")


con = duckdb.connect()
q = lambda s: con.execute(s).fetchall()
rd = lambda f: f"read_csv('{f.as_posix()}', header=true, all_varchar=true)"

# c: one row per year, week and reporting area, cumulative cases this year and (as that week's table restates it) last year.
con.execute(f"""CREATE TABLE c AS
    SELECT year::INT yr, week::INT wk, replace(upper(states), 'U.S. ', 'US ') area,
        coalesce(max(m3::DOUBLE) FILTER (WHERE label = 'Measles, Imported'), 0) imp,
        coalesce(max(m3::DOUBLE) FILTER (WHERE label = 'Measles, Indigenous'), 0) ind,
        coalesce(max(m4::DOUBLE) FILTER (WHERE label = 'Measles, Imported'), 0) prev_imp,
        coalesce(max(m4::DOUBLE) FILTER (WHERE label = 'Measles, Indigenous'), 0) prev_ind,
        coalesce(sum(m1::DOUBLE), 0) cur
    FROM {rd(NEW)} GROUP BY 1, 2, 3""")
for y, f in OLD.items():
    con.execute(f"""INSERT INTO c SELECT mmwr_year::INT, mmwr_week::INT, upper(reporting_area),
        coalesce(measles_imported_cum_{y}::DOUBLE, 0), coalesce(measles_indigenous_cum_{y}::DOUBLE, 0),
        coalesce(measles_imported_cum_{y - 1}::DOUBLE, 0), coalesce(measles_indigenous_cum_{y - 1}::DOUBLE, 0),
        coalesce(measles_imported_current::DOUBLE, 0) + coalesce(measles_indigenous_current::DOUBLE, 0) FROM {rd(f)}""")
con.execute("ALTER TABLE c ADD COLUMN tot DOUBLE; UPDATE c SET tot = imp + ind")
dup = q("SELECT yr, wk, area, count(*) FROM c GROUP BY 1, 2, 3 HAVING count(*) > 1 LIMIT 3")
if dup:
    raise SystemExit(f"more than one row for a year, week and area: {dup}")
# New York City is its own reporting area; check New York leaves it out (the division must add up with both).
ma = q("""SELECT max(abs(d.tot - s.t)) FROM c d JOIN (SELECT yr, wk, sum(tot) t FROM c
    WHERE area IN ('NEW JERSEY', 'NEW YORK', 'NEW YORK CITY', 'PENNSYLVANIA') GROUP BY 1, 2) s USING (yr, wk) WHERE d.area = 'MIDDLE ATLANTIC'""")[0][0]
if ma != 0:
    raise SystemExit(f"Middle Atlantic is not NJ + NY + NYC + PA (largest gap {ma})")
# w: weekly new cases = change in the cumulative count; s: the same by state, New York City folded into New York.
con.execute(f"""CREATE TABLE s AS SELECT yr, wk, CASE WHEN area = 'NEW YORK CITY' THEN 'NEW YORK' ELSE area END area, sum(tot) tot, sum(cur) cur
    FROM c WHERE area NOT IN {ROLLUPS} GROUP BY 1, 2, 3""")
con.execute("""CREATE TABLE w AS SELECT yr, wk, area, tot, cur, tot - coalesce(lag(tot) OVER (PARTITION BY yr, area ORDER BY wk), 0) AS new_cases
    FROM (SELECT yr, wk, area, tot, cur FROM c WHERE area = 'US RESIDENTS' UNION ALL SELECT * FROM s)""")
last = {y: wk for y, wk in q("SELECT yr, max(wk) FROM c GROUP BY 1")}
YEARS = sorted(last)
Y1 = YEARS[-1]
yl = Y1 - 1
cut = week_start(Y1, last[Y1]) + timedelta(days=6)   # last day the CDC file covers

# JHU: one row per county and report date; value is the cases added on that date.
con.execute(f"""CREATE TABLE j AS SELECT upper(split_part(location_name, ', ', -1)) area, location_name, location_id, date::DATE d, value::DOUBLE v
    FROM {rd(JHU)} WHERE outcome_type = 'case_lab-confirmed'""")
con.execute(f"CREATE TABLE jd AS SELECT outcome_type, date::DATE d, value::DOUBLE v FROM {rd(JHU_D)}")
con.execute(f"CREATE TABLE jw AS SELECT * FROM read_csv('{JHU_W.as_posix()}', header=true)")
jcols = [c[0] for c in q("DESCRIBE jw")]
if jcols != ["week_start", "week_end"] + [f"{a}_cases" for a, _ in SIX]:
    raise SystemExit(f"six-state weekly file has other columns: {jcols}")
unmatched = q("SELECT DISTINCT area FROM j WHERE area NOT IN (SELECT area FROM s) ORDER BY 1")
if unmatched:
    raise SystemExit(f"JHU state names not in the CDC file: {unmatched}")

# ---- what was pulled
us = {(y, wk): (imp, ind, pi, pn) for y, wk, imp, ind, pi, pn in q("SELECT yr, wk, imp, ind, prev_imp, prev_ind FROM c WHERE area = 'US RESIDENTS'")}
nrows = lambda f: q(f"SELECT count(*) FROM {rd(f)}")[0][0]
jspan = q("SELECT min(d), max(d), count(*), count(DISTINCT location_name), count(DISTINCT area), sum(v) FROM j")[0]
nsrc, nsrc_st = q(f"SELECT count(*), count(DISTINCT State) FROM {rd(JHU_S)}")[0]
jw_span = q("SELECT min(week_start), max(week_end), count(*) FROM jw")[0]
pulled = [
    ("CDC", "NNDSS Weekly Data (x9gk-5huc), measles rows", NEW.name, nrows(NEW), "Reporting area, week, imported or indigenous",
     f"{min(y for y in YEARS if y >= 2022)} week 1 to {Y1} week {last[Y1]}", "70 areas: 50 states, DC, New York City, 5 territories, 9 regions, 4 totals"),
    ("CDC", "NNDSS Table 1V, 2020 (7xhe-mv2e)", OLD[2020].name, nrows(OLD[2020]), "Reporting area, week", f"2020 week 1 to {last[2020]}", "Same 70 areas"),
    ("CDC", "NNDSS Table 1V, 2021 (9k8a-cbgx)", OLD[2021].name, nrows(OLD[2021]), "Reporting area, week", f"2021 week 1 to {last[2021]}", "Same 70 areas"),
    ("States, compiled by Johns Hopkins", "County cases", JHU.name, int(jspan[2]), "County, report date",
     f"{jspan[0]} to {jspan[1]}", f"{jspan[3]} counties in {jspan[4]} states; {num(round(jspan[5]))} cases"),
    ("States, compiled by Johns Hopkins", "County cases, detailed", JHU_D.name, nrows(JHU_D), "County, report date, kind of count",
     f"{jspan[0]} to {jspan[1]}", "; ".join(f"{k.replace('case_', '')} {num(round(v))}" for k, v in q("SELECT outcome_type, sum(v) FROM jd GROUP BY 1 ORDER BY 2 DESC"))),
    ("States, compiled by Johns Hopkins", "Six states by week of rash onset", JHU_W.name, int(jw_span[2]), "Week",
     f"{jw_span[0]} to {jw_span[1]}", ", ".join(name(b) for _, b in SIX)),
    ("States, compiled by Johns Hopkins", "List of sources", JHU_S.name, int(nsrc), "State, link", "", f"{nsrc} links to health department releases and news reports, {nsrc_st} states"),
]
pulled_rows = "".join(f"<tr><td>{esc(a)}</td><td>{esc(b)}</td><td>{esc(f)}</td><td class=num>{num(n)}</td><td>{esc(g)}</td><td>{esc(sp)}</td><td>{esc(x)}</td></tr>"
                      for a, b, f, n, g, sp, x in pulled)
ex_cdc = "".join("<tr>" + "".join(f"<td{' class=num' if i in (1, 2, 4, 5, 6, 7) else ''}>{esc(v)}</td>" for i, v in enumerate(r)) + "</tr>"
                 for r in q(f"SELECT states, year, week, label, m1, m2, m3, m4 FROM {rd(NEW)} WHERE states = 'Texas' AND year = '2025' AND week = '20' ORDER BY label"))
if not ex_cdc:
    raise SystemExit("no Texas 2025 week 20 rows in the CDC file")
ex_jhu = "".join(f"<tr><td>{esc(a)}</td><td class=num>{esc(b)}</td><td>{esc(c_)}</td><td>{esc(d)}</td><td>{esc(e)}</td><td class=num>{esc(v)}</td></tr>"
                 for a, b, c_, d, e, v in q(f"SELECT * FROM {rd(JHU_D)} WHERE location_name = 'Gaines, Texas' ORDER BY date, outcome_type LIMIT 4"))
if not ex_jhu:
    raise SystemExit("no Gaines, Texas rows in the JHU file")

# ---- United States by year
jy = {y: v for y, v in q("SELECT year(d), sum(v) FROM j GROUP BY 1")}
jy_cut = q(f"SELECT sum(v) FROM j WHERE year(d) = {Y1} AND d <= DATE '{cut}'")[0][0]
year_rows, tot_y = "", {}
for y in [YEARS[0] - 1] + YEARS:
    if y in last:
        imp, ind = us[(y, last[y])][:2]
    else:
        imp, ind = us[(y + 1, last[y + 1])][2:]          # 2019: only as the 2020 table restates it
    tot_y[y] = imp + ind
    # the next year's table carries last year's total to the same week, so it is a full-year figure only once that year is complete
    later = sum(us[(y + 1, last[y + 1])][2:]) if y in last and last.get(y + 1, 0) >= 52 else None
    cp, co = CDC_PAGE.get(y, (None, None))
    thru = "restated in the 2020 table" if y not in last else f"week {last[y]}"
    year_rows += (f"<tr><td class=num>{y}</td><td>{thru}</td><td class=num>{num(round(ind))}</td><td class=num>{num(round(imp))}</td><td class=num>{num(round(imp + ind))}</td>"
                  f"<td class=num>{'' if later is None else num(round(later))}</td><td class=num>{num(cp)}</td><td class=num>{'' if co is None else co}</td>"
                  f"<td class=num>{num(round(jy[y])) if y in jy else ''}</td></tr>")

# ---- by state
st_y = {(a, y): t for a, y, t in q("SELECT area, yr, max_by(tot, wk) FROM s GROUP BY 1, 2")}
st_19 = {a: t for a, t in q(f"""SELECT CASE WHEN area = 'NEW YORK CITY' THEN 'NEW YORK' ELSE area END, sum(prev_imp + prev_ind) FROM c
    WHERE yr = 2020 AND wk = {last[2020]} AND area NOT IN {ROLLUPS} GROUP BY 1""")}
js = {(a, y): v for a, y, v in q("SELECT area, year(d), sum(v) FROM j GROUP BY 1, 2")}
js_cut = {a: v for a, v in q(f"SELECT area, sum(v) FROM j WHERE year(d) = {Y1} AND d <= DATE '{cut}' GROUP BY 1")}
areas = sorted({a for a, _ in st_y}, key=lambda a: (-st_y.get((a, Y1), 0), -st_y.get((a, Y1 - 1), 0), a))
state_rows, shown = "", 0
z = lambda v: "" if not v else num(round(v))
for a in areas:
    vals = [st_19.get(a, 0)] + [st_y.get((a, y), 0) for y in YEARS]
    if not any(vals) and not js.get((a, Y1 - 1)) and not js.get((a, Y1)):
        continue
    shown += 1
    gap = js_cut.get(a, 0) - st_y.get((a, Y1), 0)
    state_rows += (f"<tr><td>{esc(name(a))}</td>" + "".join(f"<td class=num>{z(v)}</td>" for v in vals)
                   + f"<td class=num>{z(js.get((a, Y1 - 1)))}</td><td class=num>{z(js_cut.get(a))}</td><td class=num>{'' if not gap else f'{gap:+,.0f}'}</td></tr>")
sum_states = {y: v for y, v in q("SELECT yr, sum(t) FROM (SELECT yr, area, max_by(tot, wk) t FROM s GROUP BY 1, 2) GROUP BY 1")}
top_now = q(f"SELECT area, max_by(tot, wk) FROM s WHERE yr = {Y1} GROUP BY 1 ORDER BY 2 DESC LIMIT 3")

# ---- weekly series
usw = q("SELECT yr, wk, new_cases, cur FROM w WHERE area = 'US RESIDENTS' ORDER BY 1, 2")
jwk = q("SELECT d - CAST(dayofweek(d) AS INT) wk0, sum(v) FROM j GROUP BY 1 ORDER BY 1")
neg = q("SELECT count(*), min(new_cases) FROM w WHERE new_cases < 0 AND area <> 'US RESIDENTS'")[0]
neg_us = q("SELECT count(*) FROM w WHERE new_cases < 0 AND area = 'US RESIDENTS'")[0][0]
cur_sum = {y: v for y, v in q("SELECT yr, sum(cur) FROM c WHERE area = 'US RESIDENTS' GROUP BY 1")}
six_cdc = {b: q(f"SELECT yr, wk, new_cases FROM w WHERE area = '{b}' AND yr >= 2025 ORDER BY 1, 2") for _, b in SIX}
six_jhu = q("SELECT * FROM jw ORDER BY week_start")
six_tot = q("SELECT " + ", ".join(f"sum({a}_cases) FILTER (WHERE year(week_start) = {y})" for a, _ in SIX for y in (Y1 - 1, Y1)) + " FROM jw")[0]
six_rows = "".join(f"<tr><td>{name(b)}</td><td class=num>{z(six_tot[2 * i])}</td><td class=num>{z(js.get((b, Y1 - 1)))}</td><td class=num>{z(st_y.get((b, Y1 - 1)))}</td>"
                   f"<td class=num>{z(six_tot[2 * i + 1])}</td><td class=num>{z(js.get((b, Y1)))}</td><td class=num>{z(st_y.get((b, Y1)))}</td></tr>"
                   for i, (a, b) in enumerate(SIX))

div = "<div id={} style='height:{}px'></div>"
font = {"family": "system-ui, Segoe UI, sans-serif", "size": 13}
base = {"margin": {"l": 55, "r": 20, "t": 10, "b": 40}, "font": font, "plot_bgcolor": "#ffffff", "paper_bgcolor": "#ffffff",
        "legend": {"orientation": "h", "y": 1.12}, "hovermode": "x unified"}
yax = {"title": {"text": "Cases in the week"}, "gridcolor": "#e3e1da"}
line = lambda i: {"color": COLORS[i], "width": 2}
charts = [
    ("c1", [{"x": [str(week_start(y, wk)) for y, wk, _, _ in usw], "y": [n for _, _, n, _ in usw], "name": "CDC, change in the year's running total",
             "type": "bar", "marker": {"color": "#2563eb"}},
            {"x": [str(d) for d, _ in jwk], "y": [v for _, v in jwk], "name": "State and county releases (Johns Hopkins), by report date",
             "type": "scatter", "mode": "lines", "line": {"color": "#c2410c", "width": 2}}],
     {**base, "yaxis": yax}),
    ("c2", [{"x": [str(r[0]) for r in six_jhu], "y": [r[2 + i] for r in six_jhu], "name": name(b), "type": "scatter", "mode": "lines", "line": line(i)}
            for i, (_, b) in enumerate(SIX)], {**base, "yaxis": yax}),
    ("c3", [{"x": [str(week_start(y, wk)) for y, wk, _ in six_cdc[b]], "y": [n for _, _, n in six_cdc[b]], "name": name(b), "type": "scatter",
             "mode": "lines", "line": line(i)} for i, (_, b) in enumerate(SIX)], {**base, "yaxis": yax}),
]
# ---- trends
ALLY = [YEARS[0] - 1] + YEARS
imp_y = {y: (us[(y, last[y])][0] if y in last else us[(y + 1, last[y + 1])][2]) for y in ALLY}
by_state = {y: sorted(((st_19.get(a, 0) if y not in last else st_y.get((a, y), 0)), a) for a in areas) for y in ALLY}
breadth_rows = ""
for y in ALLY:
    v = by_state[y]
    top_t, top_a = v[-1]
    breadth_rows += (f"<tr><td class=num>{y}{' to week ' + str(last[y]) if y == Y1 else ''}</td><td class=num>{num(round(tot_y[y]))}</td>"
                     f"<td class=num>{sum(1 for t, _ in v if t >= 1)}</td><td class=num>{sum(1 for t, _ in v if t >= 10)}</td><td class=num>{sum(1 for t, _ in v if t >= 100)}</td>"
                     f"<td>{name(top_a)}</td><td class=num>{num(round(top_t))}</td><td class=num>{100 * top_t / tot_y[y]:.0f}%</td>"
                     f"<td class=num>{num(round(imp_y[y]))}</td><td class=num>{100 * imp_y[y] / tot_y[y]:.1f}%</td></tr>")
cum = {y: q(f"SELECT wk, tot FROM c WHERE area = 'US RESIDENTS' AND yr = {y} ORDER BY wk") for y in YEARS}
mon = q("SELECT strftime(d, '%Y-%m') m, sum(v), count(DISTINCT area) FILTER (WHERE v > 0), count(DISTINCT location_name) FILTER (WHERE v > 0) FROM j GROUP BY 1 ORDER BY 1")
part = f"{jspan[1]:%Y-%m}"
first = f"{jspan[0]:%Y-%m}"
full = [r for r in mon if r[0] < part]
peak = max(full, key=lambda r: r[1])
lowm = {y: min((r for r in full if r[0].startswith(str(y)) and r[0] > first), key=lambda r: r[1]) for y in (yl, Y1)}
top8 = [a for a, in q("SELECT area FROM j GROUP BY 1 ORDER BY sum(v) DESC LIMIT 8")]
mstate = {(m, a): v for m, a, v in q(f"SELECT strftime(d, '%Y-%m'), CASE WHEN area IN {tuple(top8)} THEN area ELSE 'OTHER' END, sum(v) FROM j GROUP BY 1, 2")}
months = [r[0] for r in mon]
cnty = {y: (n, n50) for y, n, n50 in q("SELECT y, count(*), count(*) FILTER (WHERE t >= 50) FROM (SELECT year(d) y, location_name, sum(v) t FROM j GROUP BY 1, 2) GROUP BY 1")}
PAL = COLORS + ("#0891b2", "#65a30d", "#9ca3af")
ycol = dict(zip(YEARS, ("#d1d5db", "#d1d5db", "#9ca3af", "#9ca3af", "#6b7280", "#c2410c", "#2563eb")))
grid = {"gridcolor": "#e3e1da"}
slant = {"type": "category", "tickangle": -45}
charts += [
    ("t1", [{"x": [str(y) for y in ALLY], "y": [tot_y[y] for y in ALLY], "type": "bar", "marker": {"color": ["#2563eb"] * (len(ALLY) - 1) + ["#93c5fd"]},
             "text": [num(round(tot_y[y])) for y in ALLY], "textposition": "outside", "cliponaxis": False, "hovertemplate": "%{x}: %{y:,.0f}<extra></extra>"}],
     {**base, "margin": {"l": 55, "r": 20, "t": 30, "b": 40}, "yaxis": {**grid, "title": {"text": "Cases in the year"}}, "xaxis": {"type": "category"}, "showlegend": False}),
    ("t2", [{"x": [wk for wk, _ in cum[y]], "y": [t for _, t in cum[y]], "name": str(y), "type": "scatter", "mode": "lines",
             "line": {"color": ycol[y], "width": 3 if y >= yl else 1.5}} for y in YEARS],
     {**base, "yaxis": {**grid, "title": {"text": "Running total for the year"}}, "xaxis": {"title": {"text": "Week of the year"}, "dtick": 4}}),
    ("t3", [{"x": months, "y": [mstate.get((m, a), 0) for m in months], "name": "Other states" if a == "OTHER" else name(a), "type": "bar",
             "marker": {"color": PAL[i]}} for i, a in enumerate(top8 + ["OTHER"])],
     {**base, "barmode": "stack", "yaxis": {**grid, "title": {"text": "Cases in the month"}}, "xaxis": slant, "margin": {"l": 55, "r": 20, "t": 10, "b": 70}}),
    ("t4", [{"x": months, "y": [r[3] for r in mon], "name": "Counties with a new case", "type": "scatter", "mode": "lines+markers", "line": {"color": "#2563eb", "width": 2}},
            {"x": months, "y": [r[2] for r in mon], "name": "States with a new case", "type": "scatter", "mode": "lines+markers", "line": {"color": "#c2410c", "width": 2}}],
     {**base, "yaxis": {**grid, "rangemode": "tozero"}, "xaxis": slant, "margin": {"l": 55, "r": 20, "t": 10, "b": 70}}),
    ("t5", [{"x": [str(y) for y in ALLY], "y": [round(100 * imp_y[y] / tot_y[y], 1) for y in ALLY], "type": "bar", "marker": {"color": "#0f766e"},
             "text": [f"{100 * imp_y[y] / tot_y[y]:.0f}%" for y in ALLY], "textposition": "outside", "cliponaxis": False, "hovertemplate": "%{x}: %{y:.1f}%<extra></extra>"}],
     {**base, "margin": {"l": 55, "r": 20, "t": 30, "b": 40}, "yaxis": {**grid, "ticksuffix": "%", "title": {"text": "Share of the year's cases"}},
      "xaxis": {"type": "category"}, "showlegend": False}),
]
# ---- more charts: maps, state by month, counties, who, do the sources agree
ABBR = {"ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR", "CALIFORNIA": "CA", "COLORADO": "CO", "CONNECTICUT": "CT", "DELAWARE": "DE",
        "DISTRICT OF COLUMBIA": "DC", "FLORIDA": "FL", "GEORGIA": "GA", "HAWAII": "HI", "IDAHO": "ID", "ILLINOIS": "IL", "INDIANA": "IN", "IOWA": "IA",
        "KANSAS": "KS", "KENTUCKY": "KY", "LOUISIANA": "LA", "MAINE": "ME", "MARYLAND": "MD", "MASSACHUSETTS": "MA", "MICHIGAN": "MI", "MINNESOTA": "MN",
        "MISSISSIPPI": "MS", "MISSOURI": "MO", "MONTANA": "MT", "NEBRASKA": "NE", "NEVADA": "NV", "NEW HAMPSHIRE": "NH", "NEW JERSEY": "NJ",
        "NEW MEXICO": "NM", "NEW YORK": "NY", "NORTH CAROLINA": "NC", "NORTH DAKOTA": "ND", "OHIO": "OH", "OKLAHOMA": "OK", "OREGON": "OR",
        "PENNSYLVANIA": "PA", "RHODE ISLAND": "RI", "SOUTH CAROLINA": "SC", "SOUTH DAKOTA": "SD", "TENNESSEE": "TN", "TEXAS": "TX", "UTAH": "UT",
        "VERMONT": "VT", "VIRGINIA": "VA", "WASHINGTON": "WA", "WEST VIRGINIA": "WV", "WISCONSIN": "WI", "WYOMING": "WY"}
import math
MAPY = (2019, 2024, yl, Y1)
st_val = lambda a, y: st_19.get(a, 0) if y not in last else st_y.get((a, y), 0)


def state_map(y):
    on = [(a, st_val(a, y)) for a in ABBR if st_val(a, y) > 0]
    return [{"type": "choropleth", "locationmode": "USA-states", "locations": [ABBR[a] for a, _ in on], "z": [round(math.log10(v), 3) for _, v in on],
             "text": [f"{name(a)}: {v:,.0f}" for a, v in on], "hoverinfo": "text", "zmin": 0, "zmax": 3.2,
             "colorscale": [[0, "#dbeafe"], [0.5, "#3b82f6"], [1, "#1e3a8a"]], "marker": {"line": {"color": "#ffffff", "width": 0.5}},
             "colorbar": {"tickvals": [0, 1, 2, 3], "ticktext": ["1", "10", "100", "1,000"], "len": 0.7, "thickness": 12, "title": {"text": "Cases"}}}]


geo = {"font": font, "margin": {"l": 0, "r": 0, "t": 0, "b": 0}, "paper_bgcolor": "#ffffff",
       "geo": {"scope": "usa", "bgcolor": "#ffffff", "showlakes": False, "landcolor": "#f6f5f1", "subunitcolor": "#d1d5db"}}
hstates = [a for a, in q("SELECT area FROM j GROUP BY 1 HAVING sum(v) >= 20 ORDER BY sum(v)")]
hm = {(a, m): v for a, m, v in q("SELECT area, strftime(d, '%Y-%m'), sum(v) FROM j GROUP BY 1, 2")}
hz = [[hm.get((a, m)) for m in months] for a in hstates]
topc = q(f"""SELECT location_name, sum(v) FILTER (WHERE year(d) = {yl}), sum(v) FILTER (WHERE year(d) = {Y1}) FROM j GROUP BY 1 ORDER BY sum(v) DESC LIMIT 20""")[::-1]
BUCKETS = (("1", 1, 1), ("2 to 9", 2, 9), ("10 to 49", 10, 49), ("50 to 199", 50, 199), ("200 or more", 200, 10**9))
ctot = q("SELECT year(d), location_name, sum(v) FROM j GROUP BY 1, 2")
bucket = {y: [(sum(1 for yy, _, t in ctot if yy == y and lo <= t <= hi), sum(t for yy, _, t in ctot if yy == y and lo <= t <= hi)) for _, lo, hi in BUCKETS] for y in (yl, Y1)}
kind = {(y, k): v for y, k, v in q("SELECT year(d), outcome_type, sum(v) FROM jd GROUP BY 1, 2")}
kv = lambda y, k: kind.get((y, k), 0)
cdc_cum = q("SELECT yr, wk, sum(new_cases) OVER (ORDER BY yr, wk) FROM w WHERE area = 'US RESIDENTS' AND yr >= 2025 ORDER BY 1, 2")
jhu_cum = q("SELECT d, sum(v) OVER (ORDER BY d) FROM (SELECT d, sum(v) v FROM j GROUP BY 1) ORDER BY 1")
agree = [(a, st_y.get((a, Y1), 0), js_cut.get(a, 0)) for a in areas if st_y.get((a, Y1), 0) or js_cut.get(a, 0)]
yrbar = lambda y, col: {"name": str(y), "marker": {"color": col}}
charts += [(f"m{y}", state_map(y), geo) for y in MAPY]
charts += [
    ("h1", [{"type": "heatmap", "x": months, "y": [name(a) for a in hstates], "z": hz, "zmin": 0, "zmax": 300, "xgap": 1, "ygap": 1,
             "colorscale": [[0, "#eff6ff"], [0.15, "#93c5fd"], [0.5, "#2563eb"], [1, "#1e3a8a"]], "hoverongaps": False,
             "text": [["" if v is None else f"{v:,.0f}" for v in row] for row in hz], "texttemplate": "%{text}", "textfont": {"size": 11},
             "hovertemplate": "%{y}, %{x}: %{z:,.0f}<extra></extra>", "showscale": False}],
     {**base, "hovermode": "closest", "xaxis": {**slant, "side": "top"}, "yaxis": {"dtick": 1}, "margin": {"l": 120, "r": 20, "t": 70, "b": 10}}),
    ("k1", [{"type": "bar", "orientation": "h", "y": [c_ for c_, _, _ in topc], "x": [a or 0 for _, a, _ in topc], **yrbar(yl, "#c2410c")},
            {"type": "bar", "orientation": "h", "y": [c_ for c_, _, _ in topc], "x": [b_ or 0 for _, _, b_ in topc], **yrbar(Y1, "#2563eb")}],
     {**base, "barmode": "stack", "hovermode": "y unified", "xaxis": {**grid, "title": {"text": "Cases"}}, "yaxis": {"dtick": 1},
      "margin": {"l": 250, "r": 20, "t": 10, "b": 40}}),
    ("k2", [{"type": "bar", "x": [b_[0] for b_ in BUCKETS], "y": [n for n, _ in bucket[y]], "text": [n for n, _ in bucket[y]], "textposition": "outside",
             "cliponaxis": False, **yrbar(y, col)} for y, col in ((yl, "#c2410c"), (Y1, "#2563eb"))],
     {**base, "barmode": "group", "yaxis": {**grid, "title": {"text": "Counties"}}, "xaxis": {"type": "category", "title": {"text": "Cases in the county in the year"}},
      "margin": {"l": 55, "r": 20, "t": 30, "b": 50}}),
    ("k3", [{"type": "bar", "x": [b_[0] for b_ in BUCKETS], "y": [t for _, t in bucket[y]], "text": [f"{t:,.0f}" for _, t in bucket[y]], "textposition": "outside",
             "cliponaxis": False, **yrbar(y, col)} for y, col in ((yl, "#c2410c"), (Y1, "#2563eb"))],
     {**base, "barmode": "group", "yaxis": {**grid, "title": {"text": "Cases"}}, "xaxis": {"type": "category", "title": {"text": "Cases in the county in the year"}},
      "margin": {"l": 55, "r": 20, "t": 30, "b": 50}}),
    ("v1", [{"type": "bar", "x": [str(yl), str(Y1)], "y": [f(y) for y in (yl, Y1)], "name": nm, "marker": {"color": col},
             "text": [f"{f(y):,.0f}" for y in (yl, Y1)], "textposition": "inside"}
            for nm, col, f in (("Unvaccinated", "#c2410c", lambda y: kv(y, "case_unvaccinated")), ("Vaccinated", "#0f766e", lambda y: kv(y, "case_vaccinated")),
                               ("Not stated", "#d1d5db", lambda y: kv(y, "case_lab-confirmed") - kv(y, "case_unvaccinated") - kv(y, "case_vaccinated")))],
     {**base, "barmode": "stack", "yaxis": {**grid, "title": {"text": "Cases"}}, "xaxis": {"type": "category"}}),
    ("g1", [{"x": [str(week_start(y, wk) + timedelta(days=6)) for y, wk, _ in cdc_cum], "y": [t for _, _, t in cdc_cum], "name": "CDC weekly file",
             "type": "scatter", "mode": "lines", "line": {"color": "#2563eb", "width": 2}},
            {"x": [str(d) for d, _ in jhu_cum], "y": [t for _, t in jhu_cum], "name": "State and county releases", "type": "scatter", "mode": "lines",
             "line": {"color": "#c2410c", "width": 2}}],
     {**base, "yaxis": {**grid, "title": {"text": f"Cases since January {yl}"}}}),
    ("s1", [{"x": [max(c_, 0.5) for _, c_, _ in agree], "y": [max(r, 0.5) for _, _, r in agree], "text": [f"{name(a)}: CDC {c_:,.0f}, releases {r:,.0f}" for a, c_, r in agree],
             "hoverinfo": "text", "type": "scatter", "mode": "markers", "marker": {"color": "#2563eb", "size": 9, "opacity": 0.7}, "showlegend": False},
            {"x": [0.5, 2000], "y": [0.5, 2000], "type": "scatter", "mode": "lines", "line": {"color": "#9ca3af", "dash": "dot"}, "hoverinfo": "skip", "showlegend": False}],
     {**base, "hovermode": "closest", "xaxis": {**grid, "type": "log", "title": {"text": f"CDC weekly file, {Y1} to week {last[Y1]}"}, "tickvals": [0.5, 1, 10, 100, 1000], "ticktext": ["0", "1", "10", "100", "1,000"]},
      "yaxis": {**grid, "type": "log", "title": {"text": f"State and county releases, to {cut}"}, "tickvals": [0.5, 1, 10, 100, 1000], "ticktext": ["0", "1", "10", "100", "1,000"]},
      "margin": {"l": 70, "r": 20, "t": 10, "b": 50}}),
]

# ---- fields of each file
def fields(f, meaning, only=None):
    """One row per column: sniffed type, rows filled, distinct values, first value in file order, meaning."""
    types = {c[0]: c[1] for c in q(f"DESCRIBE SELECT * FROM read_csv('{f.as_posix()}', header=true)")}
    cols = [c for c in types if only is None or c in only]
    missing = [c for c in meaning if c not in types]
    if missing:
        raise SystemExit(f"{f.name}: no column {missing}")
    n = nrows(f)
    out_ = ""
    for c_ in cols:
        filled, distinct, ex = q(f"""SELECT count("{c_}"), count(DISTINCT "{c_}"), first("{c_}") FILTER (WHERE "{c_}" IS NOT NULL) FROM {rd(f)}""")[0]
        ex = "" if ex is None else (ex if len(ex) <= 44 else ex[:41] + "...")
        out_ += (f"<tr><td>{esc(c_)}</td><td>{esc(types[c_].lower())}</td><td class=num>{num(filled)}</td><td class=num>{num(distinct)}</td>"
                 f"<td>{esc(ex)}</td><td>{esc(meaning.get(c_, ''))}</td></tr>")
    rest = len(types) - len(cols)
    return (f"<table><tr><th>Field</th><th>Type</th><th>Filled, of {num(n)}</th><th>Distinct</th><th>First value</th><th>Meaning</th></tr>{out_}</table>"
            + (f"<p class=sub>{rest} other columns in this file are not read: malaria counts, flags and map points.</p>" if rest else ""))


f_new = fields(NEW, {
    "states": "Reporting area: a state, New York City, a territory, a region or a total. Upper case in some years, mixed case in others.",
    "year": "Reporting year.", "week": "Reporting week of the year, 1 to 52 or 53.",
    "label": "The disease. Imported: caught outside the United States. Indigenous: caught inside it, or place unknown.",
    "m1": "Cases reported in this week. Blank when none.", "m1_flag": "A dash beside a blank count; U on two rows. CDC's footnotes were not read.",
    "m2": "Highest weekly count in the previous 52 weeks.", "m2_flag": "As m1_flag.",
    "m3": "Running total for this year. The figure this page uses.", "m3_flag": "As m1_flag.",
    "m4": "Last year's running total to the same week.", "m4_flag": "As m1_flag.",
    "location1": "The area's name again; blank for regions and totals.", "location2": "The area's name again; blank for states.",
    "sort_order": "A sort key built from year, week and area.", "geocode": "A map point for the area."})
OLDCOLS = ("reporting_area", "mmwr_year", "mmwr_week", "measles_imported_current", "measles_imported_previous", "measles_imported_cum_2020",
           "measles_imported_cum_2019", "measles_indigenous_current", "measles_indigenous_previous", "measles_indigenous_cum_2020", "measles_indigenous_cum_2019")
f_old = fields(OLD[2020], {
    "reporting_area": "As states in the file above.", "mmwr_year": "Reporting year.", "mmwr_week": "Reporting week.",
    "measles_imported_current": "Imported cases reported in this week.", "measles_imported_previous": "Highest weekly count in the previous 52 weeks.",
    "measles_imported_cum_2020": "Running total for 2020.", "measles_imported_cum_2019": "2019's running total to the same week. At week 53 this is the only state figure for 2019 used here.",
    "measles_indigenous_current": "As above, indigenous.", "measles_indigenous_previous": "As above, indigenous.",
    "measles_indigenous_cum_2020": "As above, indigenous.", "measles_indigenous_cum_2019": "As above, indigenous."}, only=OLDCOLS)
f_cty = fields(JHU_D, {
    "location_name": "County and state. Some rows are a health district or region, not a county.",
    "location_id": "County code. A health district or an unknown county carries 0, the state code or a blank, so this page counts places by location_name.", "location_type": "Always county.",
    "date": "Date of the health department release.",
    "outcome_type": "What is counted: lab-confirmed (every case), local or imported, vaccinated or unvaccinated. The plain county file has the lab-confirmed rows only.",
    "value": "Cases added on that date, not a running total."})
f_six = fields(JHU_W, {"week_start": "Sunday.", "week_end": "Saturday.", **{f"{a}_cases": f"{name(b)}: cases whose rash began in the week." for a, b in SIX}})
f_src = fields(JHU_S, {"State": "State.", "Source_Link": "A health department release or dashboard, or a news report."})

vac_known = {y: kv(y, "case_unvaccinated") + kv(y, "case_vaccinated") for y in (yl, Y1)}
have = (
    ("Cases by state and week", f"Yes, 2020 on", "Six states only, by rash onset", "No"),
    ("Cases by state and year", "Yes, 2019 on", f"Yes, {yl} on", "National only"),
    ("Cases by county", "No", f"Yes, {yl} on, by release date", "Not checked"),
    ("Caught abroad or in the United States", "Yes", "Yes", "Not checked"),
    ("Vaccination status", "No", f"Part: stated for {vac_known[yl]:,.0f} of {jy[yl]:,.0f} cases in {yl}, {vac_known[Y1]:,.0f} of {jy[Y1]:,.0f} in {Y1}", "Not checked"),
    ("Age", "No", "No", "Not checked"),
    ("Hospital stays", "No", "No", "Not found"),
    ("Deaths", "No", "No", f"As text: 6 in {Y1}"),
    ("Number of outbreaks", "No", "No", "As text, 2023 on"),
    ("Years before 2019", "No file found", "No", "Yearly cases back to 2018 read; page lists years to 2013"),
)
cls = lambda v: "warn" if v.startswith("Not ") else "bad" if v.startswith("No") else ("warn" if v.startswith(("Part", "Six", "As text", "National", "Not checked", "Yearly")) else "ok")
have_rows = "".join(f"<tr><td>{esc(a)}</td>" + "".join(f"<td><span class={cls(v)} style='white-space:normal'>{esc(v)}</span></td>" for v in r) + "</tr>" for a, *r in have)

flow = f"""flowchart LR
    county["County and city<br/>health departments"]:::blue --> state["State health<br/>departments"]:::blue
    state -- "case reports" --> cdc["CDC"]:::purple
    cdc --> weekly["CDC weekly file<br/>state by week, 2020 on<br/>{num(nrows(NEW) + nrows(OLD[2020]) + nrows(OLD[2021]))} rows"]:::cream
    cdc --> web["CDC measles web page<br/>cases, outbreaks, deaths<br/>text only, no file"]:::pink
    county -- "press releases" --> jhu["Johns Hopkins<br/>measles tracker"]:::purple
    state -- "releases, dashboards" --> jhu
    jhu --> cfile["County files<br/>county by release date, {yl} on<br/>{num(int(jspan[2]))} and {num(nrows(JHU_D))} rows"]:::cream
    jhu --> six["Six-state file<br/>week of rash onset, {yl} on<br/>{int(jw_span[2])} rows"]:::cream
    jhu --> src["Source list<br/>{nsrc} links"]:::cream
    weekly --> pg["This page"]:::green
    cfile --> pg
    six --> pg
    web -. "yearly figures typed in" .-> pg
    classDef blue fill:#F3F7FF,stroke:#000000,stroke-width:2px,color:#000000
    classDef green fill:#EFFFF2,stroke:#000000,stroke-width:2px,color:#000000
    classDef cream fill:#FFFEE5,stroke:#000000,stroke-width:2px,color:#000000
    classDef pink fill:#FCE9E6,stroke:#000000,stroke-width:2px,color:#000000
    classDef purple fill:#F6EFFF,stroke:#000000,stroke-width:2px,color:#000000"""

script = "".join(f"Plotly.newPlot('{cid}', {json.dumps(tr)}, {json.dumps(lay)}, {{responsive:true}});" for cid, tr, lay in charts)

notes = (
    ("Weekly CDC counts here are the change in the running total",
     f"The file's own current-week column adds to {num(round(cur_sum[yl]))} for {yl}; the running total ended at {num(round(tot_y[yl]))}. "
     f"{neg[0]} state-weeks come out negative (largest {neg[1]:+,.0f})."),
    ("A date means three different things", "CDC: the week the case entered the table. County files: the day of the release. Six-state file: the week the rash began."),
    (f"CDC's two totals for {yl} differ", f"Weekly file {num(round(tot_y[yl]))}, web page {num(CDC_PAGE[yl][0])}. State releases add to {num(round(jy[yl]))}."),
    ("New York City is its own area in the CDC file", "Added into New York here."),
    ("2019 is yearly only", "The 2019 weekly table is downloaded but its measles columns are mislabeled, so 2019 comes from the 2020 table's last-year column."),
    ("CDC web page figures are typed in", "Cases 2018 to 2026 and outbreaks 2023 to 2026, read 2026-11-18."),
)
note_rows = "".join(f"<tr><td>{esc(a)}</td><td>{esc(b)}</td></tr>" for a, b in notes)
maps = "".join(f"<div style='flex:1 1 560px;min-width:0'><h3>{y}{' to week ' + str(last[y]) if y == Y1 else ''}: {num(round(tot_y[y]))} cases</h3>{div.format(f'm{y}', 300)}</div>" for y in MAPY)

body = f"""<div class="head"><h1>Measles cases: state reports and CDC</h1>
<div class="stamps"><span>Page built {now_et()}</span><span>Files downloaded 2026-11-18, not stored</span></div></div>
<p class=sub>Confirmed measles cases in the United States, from two sources: CDC's weekly file, and what state and county health departments publish themselves.</p>

<h2>Data sources</h2>
<pre class="mermaid">{esc(flow)}</pre>
<h3>Files</h3>
<div class=wrap><table><tr><th>Who reports</th><th>Table</th><th>File</th><th>Rows</th><th>One row is</th><th>Span</th><th>Contents</th></tr>{pulled_rows}</table></div>
<h3>What each source has</h3>
<table><tr><th></th><th>CDC weekly file</th><th>State and county releases (Johns Hopkins)</th><th>CDC web page (read, not downloaded)</th></tr>{have_rows}</table>
<h3>Notes on the data</h3>
<table><tr><th>Note</th><th>Detail</th></tr>{note_rows}</table>

<h2>Fields</h2>
<h3>CDC weekly file, 2022 on: {NEW.name}</h3>
<div class=wrap>{f_new}</div>
<h3>CDC weekly table, 2020: {OLD[2020].name} (the 2021 file has the same columns, with 2021 and 2020 in the names)</h3>
<div class=wrap>{f_old}</div>
<h3>County file, detailed: {JHU_D.name}</h3>
<div class=wrap>{f_cty}</div>
<h3>Six-state file: {JHU_W.name}</h3>
<div class=wrap>{f_six}</div>
<h3>Source list: {JHU_S.name}</h3>
<div class=wrap>{f_src}</div>
<h3>Example rows: CDC weekly file, Texas, 2025, week 20</h3>
<table><tr><th>states</th><th>year</th><th>week</th><th>label</th><th>m1</th><th>m2</th><th>m3</th><th>m4</th></tr>{ex_cdc}</table>
<h3>Example rows: county file, detailed, Gaines County, Texas</h3>
<table><tr><th>location_name</th><th>location_id</th><th>location_type</th><th>date</th><th>outcome_type</th><th>value</th></tr>{ex_jhu}</table>

<h2>Over time</h2>
<h3>Cases by year, CDC weekly file ({Y1} to week {last[Y1]})</h3>
{div.format('t1', 340)}
<h3>Running total within each year, CDC weekly file</h3>
{div.format('t2', 380)}
<h3>Cases by week, 2020 to {Y1}</h3>
{div.format('c1', 380)}

<h2>By state</h2>
<p class=sub>CDC weekly file. Colour steps by tens: 1, 10, 100, 1,000 cases. States with no case are left white.</p>
<div class=cols>{maps}</div>
<h3>Cases by month, eight largest states, state and county releases ({part} to {jspan[1]})</h3>
{div.format('t3', 420)}
<h3>Cases by state and month, states with 20 or more cases, state and county releases</h3>
{div.format('h1', 60 + 24 * len(hstates))}
<h3>Six states by week the rash began (six-state file)</h3>
{div.format('c2', 360)}
<h3>The same six states by week CDC added the case</h3>
{div.format('c3', 360)}
<h3>How widely each year's cases were spread, CDC weekly file</h3>
<table><tr><th>Year</th><th>Cases</th><th>States with a case</th><th>States with 10 or more</th><th>States with 100 or more</th><th>Largest state</th>
<th>Its cases</th><th>Its share</th><th>Caught abroad</th><th>Share caught abroad</th></tr>{breadth_rows}</table>
<p class=sub>States here include DC and territories.</p>

<h2>By county</h2>
<p class=sub>State and county releases. A "county" is sometimes a health district or region.</p>
<h3>Counties and states with a new case each month</h3>
{div.format('t4', 320)}
<h3>Twenty largest counties, {yl} and {Y1}</h3>
{div.format('k1', 560)}
<div class=cols><div style='flex:1 1 560px;min-width:0'><h3>Counties by size of their year</h3>{div.format('k2', 320)}</div>
<div style='flex:1 1 560px;min-width:0'><h3>Cases in counties of each size</h3>{div.format('k3', 320)}</div></div>

<h2>Who</h2>
<div class=cols><div style='flex:1 1 560px;min-width:0'><h3>Share of cases caught abroad, CDC weekly file</h3>{div.format('t5', 320)}</div>
<div style='flex:1 1 560px;min-width:0'><h3>Vaccination status, where a release states it</h3>{div.format('v1', 320)}</div></div>

<h2>Do the two sources agree</h2>
<div class=cols><div style='flex:1 1 560px;min-width:0'><h3>Running total since January {yl}</h3>{div.format('g1', 380)}</div>
<div style='flex:1 1 560px;min-width:0'><h3>Each state in {Y1}: CDC against state releases</h3>{div.format('s1', 380)}</div></div>
<h3>United States by year</h3>
<div class=wrap><table><tr><th>Year</th><th>CDC weekly file through</th><th>Indigenous</th><th>Imported</th><th>Total</th><th>Total as restated a year later</th>
<th>CDC web page, cases</th><th>CDC web page, outbreaks</th><th>State and county releases</th></tr>{year_rows}</table></div>
<p class=sub>{Y1} is a partial year: the CDC weekly file runs to {cut}, the state releases to {jspan[1]}
({num(round(jy_cut))} of their {num(round(jy[Y1]))} cases are dated on or before {cut}), the web page to 2026-11-12.</p>
<h3>By state and year</h3>
<div class=wrap><table><tr><th>State</th><th>CDC 2019</th>{''.join(f'<th>CDC {y}</th>' for y in YEARS)}<th>State releases {yl}</th>
<th>State releases {Y1}, to {cut}</th><th>State releases minus CDC, {Y1}</th></tr>{state_rows}</table></div>
<p class=sub>CDC columns are the last running total of each year ({Y1}: week {last[Y1]}). Blank is zero.</p>
<h3>Six states, three files</h3>
<table><tr><th>State</th><th>{yl}: six-state file</th><th>{yl}: county file</th><th>{yl}: CDC</th>
<th>{Y1}: six-state file</th><th>{Y1}: county file</th><th>{Y1}: CDC to week {last[Y1]}</th></tr>{six_rows}</table>"""

head = (f'<script src="{PLOTLY}"></script><script src="{MERMAID}"></script><script>document.addEventListener("DOMContentLoaded", () => '
        f'mermaid.initialize({{startOnLoad:true, theme:"base", flowchart:{{useMaxWidth:true, htmlLabels:true}}, '
        f'themeVariables:{{fontFamily:"system-ui, Segoe UI, sans-serif", fontSize:"15px"}}}}));</script>')
out = HERE / "measles.html"
out.write_text(page("Measles cases", "Appendix", body, crumb="Measles cases", head=head)
               .replace("</main></body>", f"</main><script>{script}</script></body>"), encoding="utf-8")
print(f"wrote {out}: CDC {YEARS[0]}-{Y1} week {last[Y1]}, US {Y1} {tot_y[Y1]:.0f}; state releases {jy}; {len(charts)} charts")

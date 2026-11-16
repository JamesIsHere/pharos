"""Temporary exploration page: jobs and pay in US tax preparation (James, 2026-11-16).

Reads only BLS files downloaded to <data root>/tmp/scaffold/bls/ (quick lane, nothing stored):
  api/ces_*.json       monthly payroll jobs in tax preparation services (series CEU6054121301), 2007 on
  api/*national*.json  occupation survey, tax preparers (13-2082) and accountants (13-2011), national
  api/oe_states.json   occupation survey, tax preparers, jobs and median hourly pay by state
  qcew/*.csv           quarterly census of employers, industry 541213, every area, 2015 Q1 on
Every figure is computed here. Writes tax-jobs.html.  Run: uv run python scaffold/tax_jobs.py"""
import json
from pathlib import Path

import duckdb
from common import esc, now_et, num, page

from pharos.paths import data_root

HERE = Path(__file__).parent
BLS = data_root() / "tmp" / "scaffold" / "bls"
PLOTLY = "https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.35.2/plotly.min.js"
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
STATES = {"01": "Alabama", "02": "Alaska", "04": "Arizona", "05": "Arkansas", "06": "California", "08": "Colorado", "09": "Connecticut",
          "10": "Delaware", "11": "District of Columbia", "12": "Florida", "13": "Georgia", "15": "Hawaii", "16": "Idaho", "17": "Illinois",
          "18": "Indiana", "19": "Iowa", "20": "Kansas", "21": "Kentucky", "22": "Louisiana", "23": "Maine", "24": "Maryland",
          "25": "Massachusetts", "26": "Michigan", "27": "Minnesota", "28": "Mississippi", "29": "Missouri", "30": "Montana", "31": "Nebraska",
          "32": "Nevada", "33": "New Hampshire", "34": "New Jersey", "35": "New Mexico", "36": "New York", "37": "North Carolina",
          "38": "North Dakota", "39": "Ohio", "40": "Oklahoma", "41": "Oregon", "42": "Pennsylvania", "44": "Rhode Island",
          "45": "South Carolina", "46": "South Dakota", "47": "Tennessee", "48": "Texas", "49": "Utah", "50": "Vermont", "51": "Virginia",
          "53": "Washington", "54": "West Virginia", "55": "Wisconsin", "56": "Wyoming"}


def series(path):
    d = json.loads(path.read_text(encoding="utf-8"))
    return d["Results"]["series"] if isinstance(d, dict) else d


# monthly payroll jobs, thousands
ces = {}
for f in sorted((BLS / "api").glob("ces_*.json")):
    for s in series(f):
        if s["seriesID"] == "CEU6054121301":
            for x in s["data"]:
                ces[(int(x["year"]), int(x["period"][1:]))] = float(x["value"])
if not ces:
    raise SystemExit("no monthly series CEU6054121301 in the downloaded files")
keys = sorted(ces)
years = sorted({y for y, _ in keys})
full = [y for y in years if all((y, m) in ces for m in range(1, 13))]
last = keys[-1]


def season(y):
    """(peak, peak month, lowest of Jun-Oct) for a year that has those months."""
    peak_m = max(range(1, 5), key=lambda m: ces[(y, m)])
    return ces[(y, peak_m)], peak_m, min(ces[(y, m)] for m in range(6, 11) if (y, m) in ces)


seas = {y: season(y) for y in years if all((y, m) in ces for m in range(1, 10))}
Y0, Y1 = min(seas), max(seas)
season_rows = "".join(f"<tr><td class=num>{y}</td><td class=num>{p:,.1f}</td><td>{MONTHS[pm - 1]}</td><td class=num>{lo:,.1f}</td>"
                      f"<td class=num>{p - lo:,.1f}</td><td class=num>{p / lo:.1f}x</td></tr>" for y, (p, pm, lo) in seas.items())
ramp_y = max(full)
ramp_rows = "".join(f"<tr><td>{MONTHS[m - 1]} {y}</td><td class=num>{ces[(y, m)]:,.1f}</td><td class=num>{ces[(y, m)] - prev:+,.1f}</td></tr>"
                    for (y, m), prev in (((yy, mm), ces[keys[keys.index((yy, mm)) - 1]]) for yy, mm in keys if (yy, mm) >= (ramp_y, 8))
                    )

# occupation survey
oe = {}
for f in sorted((BLS / "api").glob("*national*.json")):
    for s in series(f):
        if s["seriesID"].startswith("OEUN") and s["data"]:
            oe[(s["seriesID"][-8:-2], s["seriesID"][-2:])] = (float(s["data"][0]["value"]), s["data"][0]["year"])
OE_YEAR = {y for _, y in oe.values()}
if len(OE_YEAR) != 1:
    raise SystemExit(f"occupation survey figures are from more than one year: {OE_YEAR}")
OE_YEAR = OE_YEAR.pop()
o = lambda occ, dt: oe[(occ, dt)][0]
tp, ac = "132082", "132011"
pay_rows = (f"<tr><td>Tax preparers</td><td class=num>{num(round(o(tp, '01')))}</td>" + "".join(f"<td class=num>{o(tp, d):.2f}</td>" for d in ("06", "07", "08", "09", "10"))
            + f"<td class=num>{o(tp, '03'):.2f}</td><td class=num>{num(round(o(tp, '13')))}</td><td class=num>{num(round(o(tp, '04')))}</td></tr>"
            + f"<tr><td>Accountants and auditors</td><td class=num>{num(round(o(ac, '01')))}</td><td></td><td></td><td class=num>{o(ac, '08'):.2f}</td><td></td><td></td>"
            + f"<td class=num>{o(ac, '03'):.2f}</td><td class=num>{num(round(o(ac, '13')))}</td><td class=num>{num(round(o(ac, '04')))}</td></tr>")
st = {}
for s in series(BLS / "api" / "oe_states.json"):
    if s["data"] and s["data"][0]["value"] != "-":   # "-" is BLS's mark for a figure it does not publish
        st.setdefault(s["seriesID"][4:6], {})[s["seriesID"][-2:]] = float(s["data"][0]["value"])
st_missing = sorted(STATES[f] for f in STATES if len(st.get(f, {})) < 2)

# quarterly census of employers
con = duckdb.connect()
q = lambda s: con.execute(s).fetchall()
con.execute(f"""CREATE TABLE qc AS SELECT * FROM read_csv('{(BLS / 'qcew').as_posix()}/*.csv', header=true, union_by_name=true,
    types={{'area_fips': 'VARCHAR', 'own_code': 'VARCHAR', 'agglvl_code': 'VARCHAR', 'industry_code': 'VARCHAR', 'disclosure_code': 'VARCHAR'}})""")
n_rows, n_areas, n_q = q("SELECT count(*), count(DISTINCT area_fips), count(DISTINCT (year, qtr)) FROM qc")[0]
qy0, qq0, qy1, qq1 = q("SELECT min(year), arg_min(qtr, year * 10 + qtr), max(year), arg_max(qtr, year * 10 + qtr) FROM qc")[0]
nat = q("""SELECT year, qtr, qtrly_estabs, month1_emplvl, month2_emplvl, month3_emplvl, avg_wkly_wage, total_qtrly_wages FROM qc
    WHERE area_fips = 'US000' AND own_code = '5' ORDER BY 1, 2""")
if any(r[4] == 0 for r in nat):
    raise SystemExit("a national quarter has zero jobs")
QY = max(y for y, qt, *_ in nat if qt == 4)   # last year with four quarters
natq = {(y, qt): (e, m1, m2, m3, w, tot) for y, qt, e, m1, m2, m3, w, tot in nat}
wage_rows = "".join(f"<tr><td>{QY} Q{k}</td><td>{', '.join(MONTHS[3 * k - 3:3 * k])}</td><td class=num>{num(natq[(QY, k)][0])}</td>"
                    f"<td class=num>{num(round(sum(natq[(QY, k)][1:4]) / 3))}</td><td class=num>{natq[(QY, k)][5] / 1e6:,.0f}</td>"
                    f"<td class=num>{num(natq[(QY, k)][4])}</td></tr>" for k in (1, 2, 3, 4))
chk_m = [(y, m) for (y, m) in keys if y == QY]
gap = {m: 100 * (natq[(y, (m - 1) // 3 + 1)][1 + (m - 1) % 3] / 1000 / ces[(y, m)] - 1) for y, m in chk_m}
gap_season = max(abs(gap[m]) for m in (1, 2, 3, 4))
gap_m = max(gap, key=lambda m: abs(gap[m]))
sup = q(f"""SELECT count(*) FILTER (WHERE agglvl_code = '58'), count(*) FILTER (WHERE agglvl_code = '58' AND disclosure_code = 'N'),
    count(*) FILTER (WHERE agglvl_code = '78'), count(*) FILTER (WHERE agglvl_code = '78' AND disclosure_code = 'N')
    FROM qc WHERE own_code = '5' AND year = {qy1} AND qtr = {qq1}""")[0]
stq = q(f"""SELECT left(a.area_fips, 2), a.month2_emplvl, b.month2_emplvl, a.qtrly_estabs, a.avg_wkly_wage FROM qc a
    JOIN qc b ON b.area_fips = a.area_fips AND b.own_code = '5' AND b.year = {QY} AND b.qtr = 3
    WHERE a.agglvl_code = '58' AND a.own_code = '5' AND a.year = {QY} AND a.qtr = 1 AND a.disclosure_code IS DISTINCT FROM 'N'
    AND b.disclosure_code IS DISTINCT FROM 'N' AND b.month2_emplvl > 0""")
stq = {f: r for f, *r in stq if f in STATES}
state_rows = "".join(
    f"<tr><td>{STATES[f]}</td><td class=num>{num(feb)}</td><td class=num>{num(aug)}</td><td class=num>{num(feb - aug)}</td><td class=num>{feb / aug:.1f}x</td>"
    f"<td class=num>{num(est)}</td><td class=num>{num(round(st[f]['01'])) if '01' in st.get(f, {}) else ''}</td>"
    f"<td class=num>{st[f]['08']:.2f}</td></tr>" if '08' in st.get(f, {}) else
    f"<tr><td>{STATES[f]}</td><td class=num>{num(feb)}</td><td class=num>{num(aug)}</td><td class=num>{num(feb - aug)}</td><td class=num>{feb / aug:.1f}x</td>"
    f"<td class=num>{num(est)}</td><td></td><td></td></tr>"
    for f, (feb, aug, est, w) in sorted(stq.items(), key=lambda kv: -(kv[1][0] - kv[1][1])))
pay_states = sorted(((STATES[f], v["08"]) for f, v in st.items() if "08" in v), key=lambda r: r[1])

p0, _, lo0 = seas[Y0]
p1, _, lo1 = seas[Y1]
odd = (
    ("An industry is not an occupation",
     f"Tax preparation services (the industry) had {p1:,.1f} thousand payroll jobs at the {Y1} peak, counting everyone the firms employ. "
     f"Tax preparers (the occupation) number {num(round(o(tp, '01')))} in the {OE_YEAR} occupation survey, wherever they work. "
     "The occupation survey samples May and November (from memory, not checked), which misses the season."),
    ("The self-employed are in none of this", "All three sources count payroll jobs. A CPA or enrolled agent working alone, or paid as a contractor, is not counted."),
    (f"The two job counts agree in season, less so in {MONTHS[gap_m - 1]}",
     f"In January to April {QY} the quarterly census of employers is within {gap_season:.1f}% of the monthly survey. "
     f"In {MONTHS[gap_m - 1]} it is {gap[gap_m]:+.1f}% against the survey, the largest gap of the year."),
    ("Pay per week is lowest in the busy quarter", f"Average weekly pay per job was ${num(natq[(QY, 1)][4])} in {QY} Q1 and ${num(natq[(QY, 3)][4])} in Q3. "
     "Q1 adds many seasonal and part-time jobs; the figure is pay per job, not per hour."),
    ("Small places are withheld", f"In {qy1} Q{qq1}, {sup[1]} of {sup[0]} state rows and {num(sup[3])} of {num(sup[2])} county rows have their figures withheld by BLS."),
    ("States missing an occupation figure", f"{', '.join(st_missing) or 'None'}: BLS does not publish the tax preparer job count, the median pay, or both."),
    ("No hourly pay for the industry", "BLS has no average hourly earnings series for tax preparation services; three series IDs tried do not exist."),
)
odd_rows = "".join(f"<tr><td>{esc(a)}</td><td>{esc(b)}</td></tr>" for a, b in odd)

font = {"family": "system-ui, Segoe UI, sans-serif", "size": 13}
base = {"margin": {"l": 55, "r": 20, "t": 10, "b": 40}, "font": font, "plot_bgcolor": "#ffffff", "paper_bgcolor": "#ffffff",
        "legend": {"orientation": "h", "y": 1.12}}
grid = {"gridcolor": "#e3e1da", "rangemode": "tozero"}
prof_years = [y for y in (Y0, 2012, 2016, 2019, Y1) if y in years]
prof_cols = ["#9ca3af", "#a16207", "#0f766e", "#7c3aed", "#2563eb"]
charts = [
    ("c1", [{"x": [f"{y}-{m:02d}-01" for y, m in keys], "y": [ces[k] for k in keys], "type": "scatter", "mode": "lines",
             "line": {"color": "#2563eb", "width": 1.6}, "hovertemplate": "%{x|%b %Y}: %{y:.1f} thousand<extra></extra>"}],
     {**base, "yaxis": {**grid, "title": {"text": "Jobs, thousands"}}, "showlegend": False}),
    ("c2", [{"x": MONTHS, "y": [ces.get((y, m)) for m in range(1, 13)], "name": str(y), "type": "scatter", "mode": "lines+markers",
             "line": {"color": c, "width": 2}, "marker": {"size": 5}, "hovertemplate": "%{y:.1f} thousand"}
            for y, c in zip(prof_years, prof_cols)],
     {**base, "yaxis": {**grid, "title": {"text": "Jobs, thousands"}}, "hovermode": "x unified"}),
    ("c3", [{"x": [f"{y} Q{k}" for y, k, *_ in nat], "y": [r[6] for r in nat], "type": "bar",
             "marker": {"color": ["#2563eb" if r[1] == 1 else "#9ca3af" for r in nat]}, "hovertemplate": "%{x}: $%{y:,}<extra></extra>"}],
     {**base, "yaxis": {**grid, "tickprefix": "$", "title": {"text": "Average weekly pay per job"}}, "showlegend": False,
      "xaxis": {"tickangle": -60, "dtick": 4}, "margin": {"l": 70, "r": 20, "t": 10, "b": 70}}),
    ("c4", [{"y": [s for s, _ in pay_states], "x": [v for _, v in pay_states], "type": "bar", "orientation": "h", "marker": {"color": "#2563eb"},
             "hovertemplate": "%{y}: $%{x:.2f}<extra></extra>"}],
     {**base, "xaxis": {**grid, "tickprefix": "$"}, "yaxis": {"tickfont": {"size": 12}, "dtick": 1, "automargin": True}, "showlegend": False,
      "margin": {"l": 150, "r": 20, "t": 10, "b": 40},
      "shapes": [{"type": "line", "y0": 0, "y1": 1, "yref": "paper", "x0": o(tp, "08"), "x1": o(tp, "08"), "line": {"color": "#1d1d1b", "dash": "dot"}}],
      "annotations": [{"y": 0, "yref": "paper", "x": o(tp, "08"), "text": f"United States ${o(tp, '08'):.2f}", "showarrow": False,
                       "xanchor": "left", "xshift": 6, "yanchor": "bottom"}]}),
]
script = "".join(f"Plotly.newPlot('{cid}', {json.dumps(tr)}, {json.dumps(lay)}, {{responsive:true}});" for cid, tr, lay in charts)
div = "<div id={} style='height:{}px'></div>"

body = f"""<div class="head"><h1>Jobs and pay in tax preparation</h1>
<div class="stamps"><span>Page built {now_et()}</span><span>Files downloaded 2026-11-16, not stored</span></div></div>
<p class=sub>Payroll jobs only. Source: US Bureau of Labor Statistics (BLS), three surveys.</p>

<h2>What was pulled</h2>
<table><tr><th>BLS source</th><th>What it counts</th><th>Covers</th><th>Size</th></tr>
<tr><td>Monthly payroll survey, series CEU6054121301</td><td>Jobs at tax preparation firms, every month, not seasonally adjusted</td>
<td>{MONTHS[keys[0][1] - 1]} {keys[0][0]} to {MONTHS[last[1] - 1]} {last[0]}</td><td class=num>{len(ces)} months</td></tr>
<tr><td>Occupation survey, occupations 13-2082 and 13-2011</td><td>People working as tax preparers, and as accountants and auditors: jobs and pay percentiles</td>
<td>{OE_YEAR}, national; tax preparers also by state</td><td class=num>{len(oe)} national figures, {len(st)} states</td></tr>
<tr><td>Quarterly census of employers, industry 541213</td><td>Tax preparation firms: establishments, jobs in each month, total pay, by nation, state and county</td>
<td>{qy0} Q{qq0} to {qy1} Q{qq1}</td><td class=num>{num(n_rows)} rows, {num(n_areas)} areas, {n_q} quarters</td></tr></table>

<h2>What looks odd</h2>
<table><tr><th>Finding</th><th>Evidence</th></tr>{odd_rows}</table>

<h2>The season</h2>
<p class=sub>Jobs at tax preparation firms, thousands. The winter peak was {p0:,.1f} thousand in {Y0} and {p1:,.1f} thousand in {Y1}.
The summer low was {lo0:,.1f} thousand in {Y0} and {lo1:,.1f} thousand in {Y1}. Seasonal jobs added each winter: {p0 - lo0:,.1f} thousand then, {p1 - lo1:,.1f} thousand now.</p>
{div.format('c1', 340)}
<h3>Month by month, selected years</h3>
{div.format('c2', 360)}
<div class=cols>
<div><h3>Each year's peak and low</h3>
<table><tr><th>Year</th><th>Peak, thousands</th><th>Peak month</th><th>Lowest of June to October, thousands</th><th>Difference</th><th>Peak over low</th></tr>{season_rows}</table></div>
<div><h3>The latest ramp, month by month</h3>
<table><tr><th>Month</th><th>Jobs, thousands</th><th>Change from the month before</th></tr>{ramp_rows}</table></div>
</div>

<h2>Pay</h2>
<h3>Hourly and yearly pay, {OE_YEAR} occupation survey</h3>
<div class=wrap><table><tr><th>Occupation</th><th>Jobs</th><th>Hourly, 10th percentile</th><th>25th</th><th>Median</th><th>75th</th><th>90th</th>
<th>Hourly, average</th><th>Yearly, median</th><th>Yearly, average</th></tr>{pay_rows}</table></div>
<p class=sub>Dollars. Yearly pay is the hourly rate times 2,080 hours, so it overstates what a seasonal preparer earns in a year.</p>
<h3>Tax preparation firms by quarter, {QY}</h3>
<table><tr><th>Quarter</th><th>Months</th><th>Establishments</th><th>Jobs, average of the three months</th><th>Total pay, $ million</th><th>Average weekly pay per job, $</th></tr>{wage_rows}</table>
<h3>Average weekly pay per job, every quarter (first quarters in blue)</h3>
{div.format('c3', 340)}

<h2>By state</h2>
<h3>Seasonal jobs at tax preparation firms, {QY}, and tax preparer pay, {OE_YEAR}</h3>
<div class=wrap><table><tr><th>State</th><th>Jobs, February {QY}</th><th>Jobs, August {QY}</th><th>Difference</th><th>February over August</th>
<th>Establishments, Q1</th><th>Tax preparers (occupation)</th><th>Median hourly pay, $</th></tr>{state_rows}</table></div>
<p class=sub>Sorted by the February-to-August difference. States whose figures BLS withholds in either quarter are left out.</p>
<h3>Median hourly pay of tax preparers by state, {OE_YEAR}</h3>
{div.format('c4', 980)}"""

out = HERE / "tax-jobs.html"
out.write_text(page("Jobs and pay in tax preparation", "Ideas", body, crumb="Jobs and pay in tax preparation",
                    head=f'<script src="{PLOTLY}"></script>')
               .replace("</main></body>", f"</main><script>{script}</script></body>"), encoding="utf-8")
print(f"wrote {out}: {len(ces)} months, peak {Y1} {p1:,.1f}k, low {lo1:,.1f}k, {len(stq)} states")

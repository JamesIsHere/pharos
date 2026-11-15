"""Scaffold: returns on capital across public companies, from SEC Company Facts (2026-11-15).

A question from the antitrust work: do high returns get competed away. Return on capital here is a company's annual
operating income (us-gaap OperatingIncomeLoss, SEC calendar-year frame CYyyyy) divided by its capital at the start of
that year: total assets less current liabilities, read at the day before the income period starts, each at its
latest-filed value. A company-year needs all three and positive capital. Two filers with the same year, operating
income and opening capital are one business filed twice (a parent and its subsidiary): one is kept, the filer with a
ticker first, then the lower CIK. Industries are the SIC code on SEC submissions today, one per company.
Percentiles are weighted by capital. A cohort sorts one year's companies into five groups holding equal capital and
follows each group without re-sorting; a group's return is its total operating income over its total capital.
Everything is measured from the served version at build time. Read-only. Writes returns.html.
Run: uv run python scaffold/returns.py"""
import json
from pathlib import Path

import duckdb
from common import esc, now_et, num, page

from pharos.paths import data_root

HERE = Path(__file__).parent
DATA = data_root()
version = (DATA / "serving" / "CURRENT").read_text(encoding="utf-8").strip()
S = DATA / "serving" / version
CF = f"read_parquet('{(S / 'company_facts.parquet').as_posix()}/**/*.parquet', hive_partitioning=false)"
SD = f"read_parquet('{(S / 'submission_details.parquet').as_posix()}')"
PLOTLY = "https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.35.2/plotly.min.js"
FIRST, BASES, TRACK = 2011, (2011, 2018), 6
# Large companies the measure cannot see, shown with what they do report
ABSENT = {34088: "Exxon Mobil", 93410: "Chevron", 1067983: "Berkshire Hathaway", 200406: "Johnson & Johnson"}

con = duckdb.connect()


def q(sql):
    return con.execute(sql).fetchall()


con.execute(f"""CREATE TABLE f AS SELECT cik, concept, start_date, end_date, val, filed, frame FROM {CF}
                WHERE taxonomy = 'us-gaap' AND unit = 'USD'
                  AND concept IN ('OperatingIncomeLoss', 'Assets', 'LiabilitiesCurrent')""")
con.execute(r"""CREATE TABLE inc AS SELECT cik, CAST(substr(frame, 3, 4) AS INT) AS yr, start_date, val AS oi
                FROM f WHERE concept = 'OperatingIncomeLoss' AND regexp_matches(frame, '^CY[0-9]{4}$')""")
if q("SELECT count(*) FROM (SELECT cik, yr FROM inc GROUP BY ALL HAVING count(*) > 1)")[0][0]:
    raise SystemExit("Company Facts: a company carries two annual operating income facts for one calendar-year frame")
con.execute("""CREATE TABLE bal AS SELECT cik, concept, end_date, arg_max(val, filed) AS val
               FROM f WHERE concept <> 'OperatingIncomeLoss' GROUP BY ALL""")
# The last complete calendar year before the served version: a year still in progress holds only the companies
# whose fiscal year has already ended.
LAST = min(q("SELECT max(yr) FROM inc")[0][0], int(version[:4]) - 1)
con.execute(f"""CREATE TABLE r0 AS SELECT i.cik, i.yr, i.oi, a.val - l.val AS cap
                FROM inc i JOIN bal a ON a.cik = i.cik AND a.concept = 'Assets' AND a.end_date = i.start_date - 1
                JOIN bal l ON l.cik = i.cik AND l.concept = 'LiabilitiesCurrent' AND l.end_date = i.start_date - 1
                WHERE i.yr BETWEEN {FIRST} AND {LAST}""")
n_income, n_matched, n_nocap = q(f"""SELECT (SELECT count(*) FROM inc WHERE yr BETWEEN {FIRST} AND {LAST}),
                                            count(*), count(*) FILTER (WHERE cap <= 0) FROM r0""")[0]
con.execute(f"""CREATE TABLE r AS SELECT * EXCLUDE (rn) FROM (
                  SELECT r0.*, d.name, d.sic, d.sic_description AS industry,
                         row_number() OVER (PARTITION BY yr, oi, cap ORDER BY coalesce(len(d.tickers), 0) DESC, r0.cik) AS rn
                  FROM r0 LEFT JOIN {SD} AS d USING (cik) WHERE cap > 0) WHERE rn = 1""")
n_kept = q("SELECT count(*) FROM r")[0][0]
if not n_kept:
    raise SystemExit(f"served version {version}: no company-year has operating income and opening capital")

by_year = q("""WITH w AS (SELECT yr, oi / cap AS roce,
                                 sum(cap) OVER (PARTITION BY yr ORDER BY oi / cap) / sum(cap) OVER (PARTITION BY yr) AS cum FROM r),
                    p AS (SELECT yr, min(roce) FILTER (cum >= 0.10) AS p10, min(roce) FILTER (cum >= 0.25) AS p25,
                                 min(roce) FILTER (cum >= 0.50) AS p50, min(roce) FILTER (cum >= 0.75) AS p75,
                                 min(roce) FILTER (cum >= 0.90) AS p90 FROM w GROUP BY yr)
               SELECT r.yr, count(*), sum(cap) / 1e12, sum(oi) / sum(cap) * 100, any_value(p10) * 100, any_value(p25) * 100,
                      any_value(p50) * 100, any_value(p75) * 100, any_value(p90) * 100
               FROM r JOIN p USING (yr) GROUP BY r.yr ORDER BY r.yr""")

con.execute("CREATE TABLE g (base INT, cik BIGINT, grp INT)")
for base in BASES:
    con.execute(f"""INSERT INTO g SELECT {base}, cik,
                      least(4, floor(5 * (sum(cap) OVER (ORDER BY oi / cap) - cap) / sum(cap) OVER ()))::INT + 1
                    FROM r WHERE yr = {base}""")
cohort = {}
for base, grp, yr, n, roce in q(f"""SELECT g.base, g.grp, r.yr, count(*), sum(oi) / sum(cap) * 100 FROM r JOIN g USING (cik)
                                    WHERE r.yr BETWEEN g.base AND g.base + {TRACK} GROUP BY ALL ORDER BY ALL"""):
    cohort.setdefault(base, {}).setdefault(grp, {})[yr] = (n, roce)
for base in BASES:
    if set(cohort.get(base, {})) != {1, 2, 3, 4, 5}:
        raise SystemExit(f"cohort {base}: the five groups are not all present")

industries = q("""SELECT sic, any_value(industry), count(DISTINCT cik) FILTER (yr >= 2021), sum(cap) FILTER (yr >= 2021) / 5 / 1e9,
                         sum(oi) FILTER (yr <= 2015) / sum(cap) FILTER (yr <= 2015) * 100,
                         sum(oi) FILTER (yr BETWEEN 2016 AND 2020) / sum(cap) FILTER (yr BETWEEN 2016 AND 2020) * 100,
                         sum(oi) FILTER (yr >= 2021) / sum(cap) FILTER (yr >= 2021) * 100
                  FROM r WHERE sic IS NOT NULL AND sic <> '' GROUP BY sic ORDER BY 4 DESC NULLS LAST LIMIT 25""")
top = q("""WITH b AS (SELECT r.sic, any_value(r.industry) AS industry, count(*) AS n, sum(cap) AS cap, sum(oi) / sum(cap) * 100 AS roce,
                             arg_max(r.name, cap) AS big FROM r JOIN g USING (cik) WHERE g.base = 2018 AND g.grp = 5 AND yr = 2018 GROUP BY r.sic),
                e AS (SELECT r.sic, count(*) AS n, sum(oi) / sum(cap) * 100 AS roce FROM r JOIN g USING (cik)
                      WHERE g.base = 2018 AND g.grp = 5 AND yr = 2024 GROUP BY r.sic)
           SELECT b.sic, b.industry, b.big, b.n, b.cap / 1e9, b.cap / sum(b.cap) OVER () * 100, b.roce, e.n, e.roce
           FROM b LEFT JOIN e USING (sic) ORDER BY b.cap DESC LIMIT 15""")
absent = q(f"""SELECT cik, count(*) FILTER (concept = 'OperatingIncomeLoss' AND regexp_matches(frame, '^CY[0-9]{{4}}$')),
                      count(*) FILTER (concept = 'Assets'), count(*) FILTER (concept = 'LiabilitiesCurrent'),
                      (SELECT count(DISTINCT yr) FROM r WHERE r.cik = f.cik)
               FROM f WHERE cik IN ({', '.join(map(str, ABSENT))}) GROUP BY cik""")


# ---------------------------------------------------------------- page
def f1(x):
    return "" if x is None else f"{x:,.1f}"


year_rows = "".join(f"<tr><td>{r[0]}</td><td class=num>{num(r[1])}</td><td class=num>{r[2]:,.1f}</td>"
                    + "".join(f"<td class=num>{f1(v)}</td>" for v in r[3:]) + "</tr>" for r in by_year)


def cohort_table(base):
    years = list(range(base, base + TRACK + 1))
    rows = ""
    for grp in range(1, 6):
        c = cohort[base][grp]
        label = {1: "1 (lowest return)", 5: "5 (highest return)"}.get(grp, str(grp))
        rows += (f"<tr><td class=nw>{label}</td>" + "".join(f"<td class=num>{f1(c.get(y, (0, None))[1])}</td>" for y in years)
                 + f"<td class=num>{num(c[base][0])}</td><td class=num>{num(c.get(years[-1], (0,))[0])}</td></tr>")
    return (f"<h3>Companies sorted in {base}</h3><table><tr><th>Group</th>{''.join(f'<th>{y}</th>' for y in years)}"
            f"<th>Companies {base}</th><th>Companies {years[-1]}</th></tr>{rows}</table>")


ind_rows = "".join(f"<tr><td>{s}</td><td>{esc(d)}</td><td class=num>{num(n)}</td><td class=num>{cap:,.0f}</td>"
                   f"<td class=num>{f1(a)}</td><td class=num>{f1(b)}</td><td class=num>{f1(e)}</td>"
                   f"<td class=num>{'' if a is None or e is None else f'{e - a:+.1f}'}</td></tr>"
                   for s, d, n, cap, a, b, e in industries)
top_rows = "".join(f"<tr><td>{s or ''}</td><td>{esc(d or 'no SIC code')}</td><td>{esc(big)}</td><td class=num>{num(n)}</td>"
                   f"<td class=num>{cap:,.0f}</td><td class=num>{sh:.1f}%</td><td class=num>{f1(ro)}</td>"
                   f"<td class=num>{num(n24)}</td><td class=num>{f1(ro24)}</td></tr>"
                   for s, d, big, n, cap, sh, ro, n24, ro24 in top)
abs_rows = "".join(f"<tr><td>{esc(ABSENT[c])}</td><td class=num>{oi}</td><td class=num>{a}</td><td class=num>{lc}</td>"
                   f"<td class=num>{yrs}</td></tr>" for c, oi, a, lc, yrs in sorted(absent, key=lambda r: list(ABSENT).index(r[0])))

yr = [r[0] for r in by_year]
base_layout = {"margin": {"l": 50, "r": 20, "t": 10, "b": 40}, "hovermode": "x unified",
               "font": {"family": "system-ui, Segoe UI, sans-serif", "size": 13},
               "yaxis": {"ticksuffix": "%", "zeroline": True, "zerolinecolor": "#6b7280"}, "xaxis": {"dtick": 1},
               "legend": {"orientation": "h", "y": 1.12}, "plot_bgcolor": "#ffffff", "paper_bgcolor": "#ffffff"}


def line(x, y, name, color):
    return {"x": x, "y": y, "name": name, "type": "scatter", "mode": "lines+markers",
            "line": {"color": color, "width": 1.8}, "marker": {"size": 5}, "hovertemplate": "%{y:.1f}%"}


COLORS = ["#6b7280", "#0f766e", "#2563eb", "#c2410c", "#b91c1c"]
charts = [("c1", [line(yr, [r[i] for r in by_year], name, color) for i, name, color in
                  ((8, "90th percentile", "#b91c1c"), (7, "75th", "#c2410c"), (6, "Median", "#2563eb"),
                   (5, "25th", "#0f766e"), (4, "10th", "#6b7280"))])]
for base in BASES:
    years = list(range(base, base + TRACK + 1))
    charts.append((f"c{base}", [line([y for y in years if y in cohort[base][grp]],
                                     [cohort[base][grp][y][1] for y in years if y in cohort[base][grp]],
                                     {1: "1 (lowest return)", 5: "5 (highest return)"}.get(grp, str(grp)), COLORS[grp - 1])
                                for grp in range(1, 6)]))
script = "".join(f"Plotly.newPlot('{cid}', {json.dumps(traces)}, {json.dumps(base_layout)}, {{responsive:true}});"
                 for cid, traces in charts)
div = "<div id={} style='height:340px'></div>"

body = f"""<div class="head"><h1>Returns on capital</h1>
<div class="stamps"><span>Page built {now_et()}</span><span>Served version {version}</span></div></div>

<table><tr><th>Step</th><th>Company-years, {FIRST} to {LAST}</th></tr>
<tr><td>Annual operating income reported (calendar-year frame)</td><td class=num>{num(n_income)}</td></tr>
<tr><td>... with total assets and current liabilities at the start of the year</td><td class=num>{num(n_matched)}</td></tr>
<tr><td>... with capital above zero</td><td class=num>{num(n_matched - n_nocap)}</td></tr>
<tr><td>... after removing the same business filed twice</td><td class=num>{num(n_kept)}</td></tr></table>
<p class=sub>Return on capital = operating income / (total assets less current liabilities at the start of the year).</p>

<h2>By year</h2>
{div.format('c1')}
<table><tr><th>Year</th><th>Companies</th><th>Capital, $ trillion</th><th>All companies, %</th><th>10th percentile</th>
<th>25th</th><th>Median</th><th>75th</th><th>90th</th></tr>{year_rows}</table>
<p class=sub>Percentiles are weighted by capital: half of all capital earns less than the median.</p>

<h2>Five fixed groups, followed {TRACK} years</h2>
<p class=sub>Return on capital, %. Each group held a fifth of capital in its first year.</p>
{''.join(cohort_table(b) + div.format(f'c{b}') for b in BASES)}

<h2>By industry</h2>
<h3>The 25 industries with the most capital, 2021 to {LAST}</h3>
<table><tr><th>SIC</th><th>Industry</th><th>Companies 2021-{LAST}</th><th>Capital a year, $ billion</th>
<th>2011-15, %</th><th>2016-20, %</th><th>2021-{str(LAST)[2:]}, %</th><th>Change, points</th></tr>{ind_rows}</table>
<h3>Where the highest-return group of 2018 sits</h3>
<table><tr><th>SIC</th><th>Industry</th><th>Largest company</th><th>Companies 2018</th><th>Capital 2018, $ billion</th>
<th>Share of the group</th><th>Return 2018, %</th><th>Companies 2024</th><th>Same companies 2024, %</th></tr>{top_rows}</table>

<h2>What the measure cannot see</h2>
<table><tr><th>Company</th><th>Annual operating income facts</th><th>Total assets facts</th>
<th>Current liabilities facts</th><th>Years on this page</th></tr>{abs_rows}</table>
<p class=sub>A company with no operating income line, or no current liabilities line, has no return here. Research
and advertising are expensed, so capital leaves them out; goodwill from acquisitions is in capital; share buybacks
shrink it.</p>"""

out = HERE / "returns.html"
out.write_text(page("Returns on capital", "Catalog", body, crumb="Returns on capital",
                    parent=("SEC Company Facts", "company-facts.html"), head=f'<script src="{PLOTLY}"></script>')
               .replace("</main></body>", f"</main><script>{script}</script></body>"), encoding="utf-8")
print(f"wrote {out}: {n_kept:,} company-years {FIRST} to {LAST}, {len(industries)} industries listed")

"""Temporary exploration page: H&R Block, the largest US tax preparation chain (James, 2026-11-16).

Two inputs, nothing stored:
  served SEC Company Facts (CIK 12659)   revenue, costs, net income by fiscal year and quarter
  <data root>/tmp/scaffold/hrb/*.htm     the main document of each annual report (10-K), downloaded from SEC EDGAR;
                                         read as text for what the tagged data lacks: returns prepared, net average
                                         charge, offices, peak headcount, field wages
Each year's figure is the one in that year's own report (first column); later restatements are counted, not used.
Every figure is computed here. Writes tax-hrblock.html.  Run: uv run python scaffold/tax_hrblock.py"""
import html
import json
import re
from pathlib import Path

import duckdb
from common import esc, now_et, page, served_obs

from pharos.paths import data_root

HERE = Path(__file__).parent
DATA = data_root()
DOCS = DATA / "tmp" / "scaffold" / "hrb"
version = (DATA / "serving" / "CURRENT").read_text(encoding="utf-8").strip()
S = DATA / "serving" / version
CF = f"read_parquet('{(S / 'company_facts.parquet').as_posix()}/**/*.parquet', hive_partitioning=false)"
OBS = (S / "observations.parquet").as_posix()
PLOTLY = "https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.35.2/plotly.min.js"
CIK = 12659
AS = "assisted"
con = duckdb.connect()


def q(sql):
    return con.execute(sql).fetchall()


# ---------- tagged financial data ----------
con.execute(f"CREATE TABLE h AS SELECT * FROM {CF} WHERE cik = {CIK}")
n_facts, n_concepts, d0, d1, last_filed = q("SELECT count(*), count(DISTINCT concept), min(end_date), max(end_date), max(filed) FROM h")[0]
example = q("""SELECT concept, start_date, end_date, val, form, filed FROM h
               WHERE concept = 'RevenueFromContractWithCustomerExcludingAssessedTax' AND end_date - start_date > 350
               ORDER BY end_date DESC, filed DESC LIMIT 1""")[0]
con.execute("""CREATE TABLE a AS SELECT concept, year(end_date) AS fy, end_date, arg_max(val, filed) AS val
               FROM h WHERE unit = 'USD' AND end_date - start_date BETWEEN 350 AND 380 GROUP BY ALL""")
if q("SELECT count(*) FROM (SELECT concept, fy FROM a GROUP BY ALL HAVING count(*) > 1)")[0][0]:
    raise SystemExit("two annual periods share a fiscal-year label")


def annual(*concepts):
    out = {}
    for c in reversed(concepts):   # the first concept named wins where both report
        out.update({fy: v for fy, v in q(f"SELECT fy, val FROM a WHERE concept = '{c}'")})
    return out


rev = annual("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues")
cost = annual("CostsAndExpenses")
ni = annual("NetIncomeLoss")
mkt = annual("MarketingAndAdvertisingExpense")
fy_end = dict(q("SELECT DISTINCT fy, end_date FROM a WHERE concept = 'NetIncomeLoss'"))
FYS = sorted(fy for fy in rev if fy in cost and fy in ni)
LAST = FYS[-1]

# quarters of the latest fiscal year: three are tagged, the fourth is the year less the three
qtr = {}
for c, key in (("RevenueFromContractWithCustomerExcludingAssessedTax", "rev"), ("NetIncomeLoss", "ni")):
    rows = q(f"""SELECT end_date, arg_min(val, filed) FROM h WHERE concept = '{c}' AND end_date - start_date BETWEEN 80 AND 100
                 AND end_date > DATE '{fy_end[LAST - 1]}' AND end_date <= DATE '{fy_end[LAST]}' GROUP BY ALL ORDER BY 1""")
    if len(rows) != 3:
        raise SystemExit(f"expected three tagged quarters of {c} in fiscal {LAST}, found {len(rows)}")
    qtr[key] = [v for _, v in rows] + [(rev if key == "rev" else ni)[LAST] - sum(v for _, v in rows)]
# periods of the latest fiscal year that the filings tag with two different values
clash = q(f"""SELECT concept, start_date, end_date, arg_min(val, filed), arg_max(val, filed), max(filed), arg_max(form, filed)
              FROM h WHERE unit = 'USD' AND end_date > DATE '{fy_end[LAST - 1]}' AND end_date <= DATE '{fy_end[LAST]}'
              GROUP BY ALL HAVING count(DISTINCT abs(val)) > 1 ORDER BY concept""")
clash_ni = [r for r in clash if r[0] == "NetIncomeLoss"]
QLAB = ["July to September", "October to December", "January to March", "April to June"]

# ---------- annual report text ----------
NUM = r"((?:\s+[\d,]+(?:\.\d+)?){1,3})"
PATS = {"co_ret": r"Company-owned operations" + NUM, "fr_ret": r"Franchise operations" + NUM, "online": r"\bOnline" + NUM,
        "desktop": r"(?:Software|Desktop)" + NUM}


def text_of(path):
    t = path.read_bytes().decode("utf-8", "replace")
    t = re.sub(r"(?is)<(script|style|ix:header).*?</\1>", " ", t)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t)).replace("|", " ").replace("$", " ").replace("\xa0", " ")
    t = re.sub(r"\((\d)\)", " ", t)   # footnote markers
    return re.sub(r"\s+", " ", t)


def nums(s):
    return [float(x.replace(",", "")) for x in s.split()]


docs = sorted(p for p in DOCS.glob("20*_10-K_*.htm"))
if not docs:
    raise SystemExit(f"no annual reports in {DOCS}")
own, later, people, headline = {}, {}, {}, {}
for p in docs:
    fy, t = int(p.name[:4]), text_of(p)
    pk = re.search(r"highest number of persons we employed.{0,120}?approximately ([\d,]+)", t)
    ft = re.search(r"approximately ([\d,]+) regular full-time", t)
    fw = re.search(r"Field wages((?:\s+[\d,]{6,}){1,3})", t)
    people[fy] = {"peak": pk and nums(pk.group(1))[0], "regular": ft and nums(ft.group(1))[0], "fw": fw and nums(fw.group(1))[0] * 1e3}
    starts = [m.start() for m in re.finditer(r"TAX RETURNS PREPARED", t)
              if re.match(r".{0,80}Company-owned operations\s+\d", t[m.start():m.start() + 200])]
    if not starts:
        a = re.search(r"([\d.]+) million U\.S\. assisted tax returns", t)
        d = re.search(r"([\d.]+) million DIY online paid tax returns", t)
        c = re.search(r"due to a ([\d.]+)% increase in net average charge", t)
        if a or d or c:
            headline[fy] = {"assisted": a and float(a.group(1)), "diy_paid": d and float(d.group(1)), "nac_pct": c and float(c.group(1))}
        continue
    seg = t[starts[0]:starts[0] + 4500]
    hdr = re.findall(r"(?i:year ended) (?:April|June) 30,((?:\s+20\d\d){2,3})", t[max(0, starts[0] - 300):starts[0] + 50])
    years = [int(y) for y in hdr[-1].split()]
    if years[0] != fy:
        raise SystemExit(f"{p.name}: table header starts at {years[0]}, expected {fy}")
    row = {}
    for k, pat in PATS.items():
        m = re.search(pat, seg)
        row[k] = nums(m.group(1)) if m else None
    for k, pat in (("co_off", r"Total company-owned offices" + NUM), ("fr_off", r"Total franchise offices" + NUM)):
        m = re.search(pat, seg) or re.search(pat.replace("Total company", "Company").replace("Total franchise", "Franchise"), seg)
        row[k] = nums(m.group(1)) if m else None
    n = re.search(r"NET AVERAGE (?:CHARGE|FEE).{0,120}?Company-owned operations" + NUM + r".{0,40}?Franchise operations" + NUM, seg)
    row["co_nac"], row["fr_nac"] = (nums(n.group(1)), nums(n.group(2))) if n else (None, None)
    for k, v in row.items():
        if v is None:
            continue
        own[(k, fy)] = v[0]
        for y, x in zip(years[1:], v[1:]):
            later.setdefault((k, y), []).append((fy, x))

nac_late, filled, first_table = {}, set(), min(fy for _, fy in own)
for k in ("co_nac", "fr_nac"):
    for (kk, y), lv in later.items():
        if kk == k and (k, y) not in own and y >= first_table:
            filled.add((k, y))
            own[(k, y)] = max(lv)[1]   # the latest report that prints it
            nac_late.setdefault(y, max(lv)[0])
STAT = sorted({fy for _, fy in own})
restated = sorted((k, y, own[(k, y)], [(f, x) for f, x in lv if x != own[(k, y)]]) for (k, y), lv in later.items()
                  if (k, y) in own and (k, y) not in filled and any(x != own[(k, y)] for _, x in lv))


def o(k, fy):
    return own.get((k, fy))


# net average charge after the table stops: chained from the last reported dollar figure by each report's stated % change
nac_chain, lastnac = {}, max(fy for k, fy in own if k == "co_nac")
v = own[("co_nac", lastnac)]
for fy in range(lastnac + 1, LAST + 1):
    if fy not in headline or headline[fy]["nac_pct"] is None:
        raise SystemExit(f"no stated change in net average charge for fiscal {fy}")
    v *= 1 + headline[fy]["nac_pct"] / 100
    nac_chain[fy] = v

# consumer prices, March of each fiscal year, to restate the charge in the latest year's dollars
cpi = {(d[:4], d[5:7]): val for d, val in served_obs(con, OBS, "CPIAUCSL")}
march = {fy: cpi[(str(fy), "03")] for fy in range(STAT[0], LAST + 1)}
nac_all = {**{fy: o("co_nac", fy) for fy in STAT if o("co_nac", fy)}, **nac_chain}
nac_real = {fy: v * march[LAST] / march[fy] for fy, v in nac_all.items()}
nac0 = min(nac_all)
NACX = list(range(nac0, lastnac + 1))

PF = sorted(fy for fy in people if people[fy]["peak"])
FW = sorted(fy for fy in people if people[fy]["fw"] and people[fy]["peak"])
pk_max = max(PF, key=lambda fy: people[fy]["peak"])


def per_head(fy):
    return people[fy]["fw"] / people[fy]["peak"]


# ---------- charts ----------
font = {"family": "system-ui, sans-serif", "size": 13, "color": "#1d1d1b"}
base = {"margin": {"l": 60, "r": 20, "t": 10, "b": 40}, "font": font, "plot_bgcolor": "#ffffff", "paper_bgcolor": "#ffffff",
        "legend": {"orientation": "h", "y": 1.12}}
grid = {"gridcolor": "#e3e1da", "rangemode": "tozero"}
BLUE, GREY, ORANGE, GREEN = "#2563eb", "#9ca3af", "#d97706", "#059669"
ret_parts = (("co_ret", "Company-owned offices", BLUE), ("fr_ret", "Franchise offices", "#93c5fd"), ("online", "Online", ORANGE),
             ("desktop", "Desktop software", "#fcd34d"))
charts = [
    ("c1", [{"x": FYS, "y": [rev[f] / 1e6 for f in FYS], "type": "bar", "name": "Revenue", "marker": {"color": "#bfdbfe"},
             "hovertemplate": "%{x}: $%{y:,.0f} million<extra>Revenue</extra>"},
            {"x": FYS, "y": [ni[f] / 1e6 for f in FYS], "type": "scatter", "mode": "lines+markers", "name": "Net income",
             "line": {"color": BLUE, "width": 2}, "hovertemplate": "%{x}: $%{y:,.0f} million<extra>Net income</extra>"}],
     {**base, "yaxis": {**grid, "title": {"text": "$ million"}}, "xaxis": {"dtick": 1}}),
    ("c2", [{"x": STAT, "y": [o(k, f) and o(k, f) / 1e3 for f in STAT], "type": "bar", "name": lab, "marker": {"color": col},
             "hovertemplate": "%{x}: %{y:.2f} million<extra>" + lab + "</extra>"} for k, lab, col in ret_parts],
     {**base, "barmode": "stack", "yaxis": {**grid, "title": {"text": "US returns, millions"}}, "xaxis": {"dtick": 1}}),
    ("c3", [{"x": NACX, "y": [o("co_nac", f) for f in NACX], "type": "scatter", "mode": "markers+lines",
             "name": "Company-owned offices, as reported", "line": {"color": BLUE, "width": 2}, "connectgaps": False, "hovertemplate": "%{x}: $%{y:.2f}"},
            {"x": list(nac_chain), "y": list(nac_chain.values()), "type": "scatter", "mode": "markers+lines",
             "name": "Company-owned, from the stated % change", "line": {"color": BLUE, "width": 2, "dash": "dot"}, "hovertemplate": "%{x}: $%{y:.2f}"},
            {"x": NACX, "y": [o("fr_nac", f) for f in NACX], "type": "scatter", "mode": "markers+lines",
             "name": "Franchise offices, as reported", "line": {"color": GREY, "width": 2}, "hovertemplate": "%{x}: $%{y:.2f}"},
            {"x": list(nac_real), "y": list(nac_real.values()), "type": "scatter", "mode": "markers",
             "name": f"Company-owned, in {LAST} dollars", "marker": {"color": ORANGE, "size": 7}, "hovertemplate": "%{x}: $%{y:.2f}"}],
     {**base, "yaxis": {**grid, "tickprefix": "$", "title": {"text": "Charge per return"}}, "xaxis": {"dtick": 1}}),
    ("c4", [{"x": PF, "y": [people[f]["peak"] / 1e3 for f in PF], "type": "bar", "name": "Peak headcount, thousands (left)", "marker": {"color": "#bfdbfe"},
             "hovertemplate": "%{x}: %{y:.1f} thousand<extra></extra>"},
            {"x": FW, "y": [per_head(f) for f in FW], "type": "scatter", "mode": "lines+markers", "yaxis": "y2",
             "name": "Field wages per person at peak, $ (right)", "line": {"color": ORANGE, "width": 2}, "hovertemplate": "%{x}: $%{y:,.0f}<extra></extra>"}],
     {**base, "yaxis": {**grid, "title": {"text": "People, thousands"}}, "xaxis": {"dtick": 1},
      "yaxis2": {"overlaying": "y", "side": "right", "rangemode": "tozero", "tickprefix": "$", "showgrid": False},
      "margin": {"l": 60, "r": 70, "t": 10, "b": 40}}),
    ("c5", [{"x": QLAB, "y": [v / 1e6 for v in qtr["rev"]], "type": "bar", "name": "Revenue", "marker": {"color": "#bfdbfe"},
             "hovertemplate": "%{x}: $%{y:,.0f} million<extra>Revenue</extra>"},
            {"x": QLAB, "y": [v / 1e6 for v in qtr["ni"]], "type": "bar", "name": "Net income", "marker": {"color": BLUE},
             "hovertemplate": "%{x}: $%{y:,.0f} million<extra>Net income</extra>"}],
     {**base, "barmode": "group", "yaxis": {"gridcolor": "#e3e1da", "title": {"text": "$ million"}, "zerolinecolor": "#1d1d1b"}}),
]
script = "".join(f"Plotly.newPlot('{cid}', {json.dumps(tr)}, {json.dumps(lay)}, {{responsive:true}});" for cid, tr, lay in charts)
div = "<div id={} style='height:{}px'></div>"


# ---------- tables ----------
def f0(x, pre="", scale=1.0, dec=0):
    return "" if x is None else f"{pre}{x / scale:,.{dec}f}"


def td(x):
    return f"<td class=num>{x}</td>"


year_rows = ""
for fy in range(min(STAT[0], FYS[0]), LAST + 1):
    pe, hd = people.get(fy, {}), headline.get(fy, {})
    assisted = (o("co_ret", fy) + o("fr_ret", fy)) / 1e3 if o("co_ret", fy) else hd.get("assisted")
    nac = f0(o("co_nac", fy), "$", dec=2) if o("co_nac", fy) else (f"about ${nac_chain[fy]:,.0f}" if fy in nac_chain else "")
    year_rows += (f"<tr><td>{fy}</td>" + td(f0(rev.get(fy), "$", 1e6)) + td(f0(ni.get(fy), "$", 1e6))
                  + td("" if fy not in rev or fy not in cost else f"{(rev[fy] - cost[fy]) / rev[fy] * 100:.1f}%")
                  + td(f0(o("co_ret", fy), scale=1e3, dec=2)) + td(f0(o("fr_ret", fy), scale=1e3, dec=2))
                  + td("" if assisted is None else f"{assisted:.2f}" if o("co_ret", fy) else f"{assisted:.1f}")
                  + td(nac) + td(f0(o("co_off", fy))) + td(f0(o("fr_off", fy)))
                  + td(f0(pe.get("peak"))) + td(f0(pe.get("regular"))) + td(f0(pe.get("fw"), "$", 1e6))
                  + td("" if not pe.get("fw") or fy not in rev else f"{pe['fw'] / rev[fy] * 100:.1f}%")
                  + td("" if not pe.get("fw") or not pe.get("peak") else f"${per_head(fy):,.0f}") + "</tr>")

qrows = "".join(f"<tr><td>{lab}</td>{td(f0(r, '$', 1e6))}{td(f'{r / rev[LAST] * 100:.1f}%')}{td(f0(n, '$', 1e6))}</tr>"
                for lab, r, n in zip(QLAB, qtr["rev"], qtr["ni"]))

s0, s1 = STAT[0], STAT[-1]
off0 = min(fy for fy in STAT if o("co_off", fy))
ex = next((r for r in restated if r[0] == "co_ret"), restated[0] if restated else None)
nac_gap = [fy for fy in range(nac0, lastnac + 1) if not o("co_nac", fy)]
short = 2020
h_years = [fy for fy in sorted(headline) if headline[fy]["assisted"]]
odd = [
    ("The fiscal year moved, and two months fall in no year shown.",
     f"Fiscal {LAST - 5} and earlier end on April 30; fiscal 2022 on ends on June 30 (the year-end dates in the tagged data: "
     f"{fy_end[2021]}, then {fy_end[2022]}). May and June 2021 are in neither year."),
    (f"Fiscal {short} is not a normal year.",
     f"Assisted returns (company-owned plus franchise) were {f0(o('co_ret', short) + o('fr_ret', short), scale=1e3, dec=2)} million in fiscal {short}, "
     f"against {f0(o('co_ret', short - 1) + o('fr_ret', short - 1), scale=1e3, dec=2)} million the year before and "
     f"{f0(o('co_ret', short + 1) + o('fr_ret', short + 1), scale=1e3, dec=2)} million the year after. "
     f"The fiscal 2022 report says the 2019 filing season was extended to July 15, 2020, past the April 30 year end."),
    (f"The table of returns, charges and offices stops after fiscal {s1}.",
     f"Reports for fiscal {s1 + 1} on give only rounded headline counts ({', '.join(f'{headline[fy][AS]} million assisted returns in {fy}' for fy in h_years)}) "
     f"and a percentage change in the net average charge. The charge after {lastnac} on this page is the {lastnac} figure "
     f"(${own[('co_nac', lastnac)]:.2f}) grown by those percentages: about ${nac_chain[LAST]:,.0f} in {LAST}. It is an estimate."),
    ("The dollar charge is missing in the middle of the series.",
     f"No report prints a net average charge for fiscal {', '.join(map(str, nac_gap))}. "
     f"For fiscal {', '.join(map(str, sorted(nac_late)))} the figure comes from a later report, because that year's own report prints none."),
    ("The newest quarterly filing tags some numbers with the wrong dates.",
     f"{len(clash)} dollar figures for periods inside fiscal {LAST} carry two different values in the tagged data. Net income for "
     f"{clash_ni[0][1]} to {clash_ni[0][2]} is ${clash_ni[0][3] / 1e6:,.1f} million in the filing that first reported it and "
     f"${clash_ni[0][4] / 1e6:,.1f} million in the {clash_ni[0][6]} filed {clash_ni[0][5]}. The second number equals that filing's loss for its own "
     f"quarter, so it is a mislabelled date, not a restatement. The quarter table below uses the first-filed value."),
    ("Fiscal 2008 and 2009 revenue is not comparable with later years.",
     f"Revenue is ${rev[2009] / 1e6:,.0f} million in fiscal 2009 and ${rev[2010] / 1e6:,.0f} million in fiscal 2010. Not checked on this page; "
     f"from memory, the company later sold its business-accounting arm and restated only the years it still had to present."),
    ("Later reports change earlier years.",
     f"{len(restated)} of {len([1 for k in later if k in own and k not in filled])} year-and-measure pairs were printed with a different number in a later report. "
     + (f"Example: company-owned returns for fiscal {ex[1]} were {ex[2]:,.0f} thousand in that year's report and "
        f"{', then '.join(f'{x:,.0f} thousand in the fiscal {fy} report' for fy, x in sorted(ex[3]))}." if ex else "")
     + " This page uses each year's own report."),
    ("Peak headcount fell by almost half while field wages rose.",
     f"The most people employed at once was {people[pk_max]['peak']:,.0f} in fiscal {pk_max} and {people[LAST]['peak']:,.0f} in fiscal {LAST}. "
     f"Field wages were ${people[FW[0]]['fw'] / 1e6:,.0f} million in fiscal {FW[0]} and ${people[LAST]['fw'] / 1e6:,.0f} million in fiscal {LAST}: "
     f"${per_head(FW[0]):,.0f} per person at peak then, ${per_head(LAST):,.0f} now."),
    ("Franchise offices are being absorbed.",
     f"Franchise offices went from {o('fr_off', off0):,.0f} in fiscal {off0} to {o('fr_off', s1):,.0f} in fiscal {s1}; "
     f"company-owned offices from {o('co_off', off0):,.0f} to {o('co_off', s1):,.0f}. The fiscal {LAST} report says the company bought franchise offices during the year."),
    ("Field wages are not only US tax preparers.",
     "The reports print one field wages line for the whole company. It includes Canada and Australia and other office staff, and the peak headcount "
     "is everyone employed, so dollars per person at peak is a rough size, not a preparer's pay."),
]
odd_rows = "".join(f"<tr><td>{esc(a)}</td><td>{esc(b)}</td></tr>" for a, b in odd)

body = f"""<h1>H&amp;R Block</h1>
<p class=sub>Temporary exploration page, built {now_et()}. The largest US tax preparation chain, from its own filings with the
Securities and Exchange Commission (SEC). Fiscal years; fiscal {LAST} ended {fy_end[LAST]}.</p>

<h2>What was pulled</h2>
<table><tr><th>Source</th><th>What it holds</th><th>Covers</th><th>Size</th></tr>
<tr><td>SEC Company Facts, already stored in Pharos (version {version})</td><td>Every tagged number in the company's financial statements</td>
<td>Periods ending {d0} to {d1}; latest filing {last_filed}</td><td class=num>{n_facts:,} facts, {n_concepts} concepts</td></tr>
<tr><td>Annual reports (form 10-K), main document, downloaded today from SEC EDGAR</td>
<td>Text and tables the tagged data lacks: returns prepared, net average charge, offices, headcount, field wages</td>
<td>Reports filed {docs[0].name[:4]} to {docs[-1].name[:4]}</td>
<td class=num>{len(docs)} reports; {len(STAT)} carry the returns table (fiscal {s0} to {s1}); {len(PF)} state peak headcount</td></tr></table>
<p class=sub>One tagged fact, as stored: {esc(example[0])}, {example[1]} to {example[2]}, {example[3]:,.0f} dollars, form {example[4]}, filed {example[5]}.</p>

<h2>What looks odd</h2>
<table><tr><th>Finding</th><th>Evidence</th></tr>{odd_rows}</table>

<h2>The business</h2>
<h3>Revenue and net income by fiscal year</h3>
{div.format('c1', 340)}
<h3>Fiscal {LAST} by quarter</h3>
<div class=cols><div>{div.format('c5', 300)}</div>
<div><table><tr><th>Quarter</th><th>Revenue, $ million</th><th>Share of the year</th><th>Net income, $ million</th></tr>{qrows}</table>
<p class=sub>The first three quarters are tagged in the filings; April to June is the year less those three.</p></div></div>

<h2>Returns and price</h2>
<h3>US returns prepared, fiscal {s0} to {s1}</h3>
{div.format('c2', 360)}
<p class=sub>Online and desktop are returns clients prepared themselves with H&amp;R Block software. The table stops after fiscal {s1}.</p>
<h3>Net average charge per return prepared in an office</h3>
{div.format('c3', 380)}
<p class=sub>Net average charge is tax preparation fees divided by returns prepared (the company's definition in its fiscal {s1} report).
The dotted line is an estimate: the fiscal {lastnac} figure grown by each later report's stated percentage change.
The orange points restate the company-owned charge in fiscal {LAST} dollars using consumer prices (FRED series CPIAUCSL) in March of each year:
${nac_real[nac0]:,.0f} in fiscal {nac0}, ${nac_real[LAST]:,.0f} in fiscal {LAST}.</p>

<h2>People</h2>
<h3>Most people employed at once, and field wages per person at that peak</h3>
{div.format('c4', 360)}

<h2>Year by year</h2>
<div class=wrap><table><tr><th>Fiscal year</th><th>Revenue, $ million</th><th>Net income, $ million</th><th>Revenue less all costs, share of revenue</th>
<th>Returns, company-owned offices, millions</th><th>Returns, franchise offices, millions</th><th>Assisted returns, millions</th>
<th>Net average charge, company-owned</th><th>Company-owned offices</th><th>Franchise offices</th>
<th>Peak headcount</th><th>Regular full-time staff</th><th>Field wages, $ million</th><th>Field wages, share of revenue</th>
<th>Field wages per person at peak</th></tr>{year_rows}</table></div>
<p class=sub>Assisted returns are company-owned plus franchise through fiscal {s1}; after that they are the rounded headline in each report.
A charge shown as "about" is the estimate described under the charge chart. Blank cells are years the reports do not give the figure.
Offices are US offices, with shared locations (counters inside another store) included in the years the reports list them.</p>"""

out = HERE / "tax-hrblock.html"
out.write_text(page("H&R Block", "Ideas", body, crumb="H&R Block", head=f'<script src="{PLOTLY}"></script>')
               .replace("</main></body>", f"</main><script>{script}</script></body>"), encoding="utf-8")
print(f"wrote {out}: fiscal {FYS[0]}-{LAST}, returns table {s0}-{s1}, {len(restated)} restated pairs, "
      f"charge {own[('co_nac', lastnac)]:.2f} ({lastnac}) -> about {nac_chain[LAST]:.0f} ({LAST}), real {nac_real[nac0]:.0f} -> {nac_real[LAST]:.0f}")

"""Scaffold: merger enforcement, the FTC and DOJ statistics stored by D175 (2026-11-15).

Five served tables, landed by `uv run pharos antitrust` from files placed by hand in <data root>/manual/antitrust/
(ftc.gov refuses scripted downloads): Hart-Scott-Rodino filings and second requests by fiscal year, FTC merger
enforcement actions by type, DOJ Antitrust Division merger outcomes, and the FTC's Horizontal Merger Investigation Data
(markets investigated, by concentration and by number of competitors, and whether the FTC sought relief). Everything
is read from the served version at its newest vintage; nothing is fetched. A fiscal year ends September 30.
The two-period comparison subtracts the report covering fiscal years 1996-2003 from the one covering 1996-2011 and
leaves out oil markets; a negative difference stops the build.
Writes antitrust.html. Run: uv run python scaffold/antitrust.py"""
import json
from pathlib import Path

import duckdb
import imf
from common import esc, now_et, num, page

HERE = Path(__file__).parent
PLOTLY = "https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.35.2/plotly.min.js"
TABLES = {
    "hsr_fiscal_year": ("Hart-Scott-Rodino filings and second requests", "FTC and DOJ annual data file", "fiscal_year"),
    "ftc_merger_actions": ("FTC merger enforcement actions by type", "FTC competition enforcement database", "fiscal_year"),
    "doj_merger_outcomes": ("DOJ Antitrust Division merger outcomes", "DOJ workload statistics, FY2015-2024", "fiscal_year"),
    "ftc_hmid_hhi": ("Markets investigated, by post-merger HHI and change in HHI", "FTC Horizontal Merger Investigation Data, four reports", "edition_end_fy"),
    "ftc_hmid_competitors": ("Markets investigated, by significant competitors", "the same four reports", "edition_end_fy"),
}
HHI = ["0 - 1,799", "1,800 - 1,999", "2,000 - 2,399", "2,400 - 2,999", "3,000 - 3,999", "4,000 - 4,999",
       "5,000 - 6,999", "7,000 +"]
DELTA = ["0 - 99", "100 - 199", "200 - 299", "300 - 499", "500 - 799", "800 - 1,199", "1,200 - 2,499", "2,500 +"]
COMP = ["2 to 1", "3 to 2", "4 to 3", "5 to 4", "6 to 5", "7 to 6", "8 to 7", "9 to 8", "10 to 9", "10 +"]

con = duckdb.connect()
for t in TABLES:   # each table at its newest vintage
    con.execute(f"CREATE VIEW {t} AS SELECT * FROM {imf.served(t, 'antitrust')} "
                f"WHERE vintage = (SELECT max(vintage) FROM {imf.served(t, 'antitrust')})")


def q(sql):
    return con.execute(sql).fetchall()


def pct(a, b):
    return "" if not b else f"{100 * a / b:.1f}%"


# ---------------------------------------------------------------- what is stored
pop = ""
for t, (what, source, year) in TABLES.items():
    n, lo, hi, vintage = q(f"SELECT count(*), min({year}), max({year}), max(vintage) FROM {t}")[0]
    span = f"FY{lo} to FY{hi}" if year == "fiscal_year" else \
        "reports through " + ", ".join(f"FY{r[0]}" for r in q(f"SELECT DISTINCT {year} FROM {t} ORDER BY 1"))
    pop += (f"<tr><td>{t}</td><td>{esc(what)}</td><td class=num>{num(n)}</td><td>{span}</td>"
            f"<td>{esc(source)}</td><td class=nw>{vintage}</td></tr>")

# ---------------------------------------------------------------- filings and second requests
hsr = q("""SELECT fiscal_year, transactions_reported, adjusted_transactions, second_requests, second_requests_ftc,
                  second_requests_doj, early_termination_requested, early_termination_granted,
                  early_termination_not_granted FROM hsr_fiscal_year ORDER BY 1""")
if not hsr:
    raise SystemExit("hsr_fiscal_year is served with no rows")


def hsr_row(r):
    fy, rep, adj, sr, ftc, doj, req, gr, ng = r
    off = sr != ftc + doj or req != gr + ng   # a row the source file itself does not add up on
    return (f"<tr{' class=flag' if off else ''}><td>{fy}</td><td class=num>{num(rep)}</td><td class=num>{num(adj)}</td>"
            f"<td class=num>{num(sr)}</td><td class=num>{num(ftc)}</td><td class=num>{num(doj)}</td>"
            f"<td class=num>{pct(sr, adj)}</td><td class=num>{num(req)}</td><td class=num>{num(gr)}</td>"
            f"<td class=num>{num(ng)}</td><td class=num>{pct(gr, req)}</td></tr>")


hsr_rows = "".join(hsr_row(r) for r in reversed(hsr))
off_years = [str(r[0]) for r in hsr if r[3] != r[4] + r[5] or r[6] != r[7] + r[8]]

# ---------------------------------------------------------------- outcomes
ftc = q("SELECT fiscal_year, consents, injunctions, part3, abandoned, total FROM ftc_merger_actions ORDER BY 1")
doj = q("""SELECT fiscal_year, complaints, litigation_complaints, simultaneous_settlement, abandoned, resolved_concerns,
                  merger_outcomes FROM doj_merger_outcomes ORDER BY 1""")
ftc_rows = "".join(f"<tr><td>{r[0]}</td>" + "".join(f"<td class=num>{num(v)}</td>" for v in r[1:])
                   + f"<td class=num>{pct(r[1], r[5])}</td></tr>" for r in reversed(ftc))
doj_rows = "".join(f"<tr><td>{r[0]}</td>" + "".join(f"<td class=num>{num(v)}</td>" for v in r[1:]) + "</tr>"
                   for r in reversed(doj))


# ---------------------------------------------------------------- concentration at challenge
def periods(table, col, order, prefix):
    """{band: (enforced, closed)} for markets excluding oil in FY1996-2003 and in FY2004-2011."""
    got = {}
    for ed in (2003, 2011):
        rows = q(f"""SELECT {col}, sum(CASE WHEN markets = 'All Markets' THEN enforced ELSE -enforced END),
                            sum(CASE WHEN markets = 'All Markets' THEN closed ELSE -closed END)
                     FROM {table} WHERE edition_end_fy = {ed} AND table_no LIKE '{prefix}.%'
                       AND markets IN ('All Markets', 'Oil Markets') GROUP BY 1""")
        got[ed] = {r[0]: (r[1], r[2]) for r in rows}
        if set(got[ed]) != set(order):
            raise SystemExit(f"{table}: the FY{ed} report has bands {sorted(got[ed])}, expected {order}")
    late = {b: (got[2011][b][0] - got[2003][b][0], got[2011][b][1] - got[2003][b][1]) for b in order}
    if any(v < 0 for b in order for v in late[b] + got[2003][b]):
        raise SystemExit(f"{table}: a count is negative after subtracting oil markets or the FY2003 report")
    return got[2003], late


def period_rows(order, early, late):
    out = ""
    for b in order:
        (e1, c1), (e2, c2) = early[b], late[b]
        out += (f"<tr><td class=nw>{b}</td><td class=num>{e1}</td><td class=num>{e1 + c1}</td><td class=num>{pct(e1, e1 + c1)}</td>"
                f"<td class=num>{e2}</td><td class=num>{e2 + c2}</td><td class=num>{pct(e2, e2 + c2)}</td></tr>")
    e1, c1 = (sum(early[b][i] for b in order) for i in (0, 1))
    e2, c2 = (sum(late[b][i] for b in order) for i in (0, 1))
    return out + (f"<tr><th>All</th><td class=num>{e1}</td><td class=num>{e1 + c1}</td><td class=num>{pct(e1, e1 + c1)}</td>"
                  f"<td class=num>{e2}</td><td class=num>{e2 + c2}</td><td class=num>{pct(e2, e2 + c2)}</td></tr>")


hhi_early, hhi_late = periods("ftc_hmid_hhi", "post_merger_hhi", HHI, "3")
comp_early, comp_late = periods("ftc_hmid_competitors", "significant_competitors", COMP, "4")

cells = {(r[0], r[1]): (r[2], r[3]) for r in q(
    "SELECT post_merger_hhi, delta_hhi, enforced, closed FROM ftc_hmid_hhi "
    "WHERE edition_end_fy = 2011 AND table_no = '3.1'")}
if len(cells) != 64:
    raise SystemExit(f"ftc_hmid_hhi: table 3.1 of the FY2011 report has {len(cells)} cells, expected 64")
grid = "".join(
    f"<tr><th>{h}</th>" + "".join(
        f"<td class=num>{'' if sum(cells[h, d]) == 0 else f'{cells[h, d][0]} / {sum(cells[h, d])}'}</td>" for d in DELTA)
    + f"<td class=num>{sum(cells[h, d][0] for d in DELTA)} / {sum(sum(cells[h, d]) for d in DELTA)}</td></tr>" for h in HHI)
tot_e, tot_n = sum(v[0] for v in cells.values()), sum(sum(v) for v in cells.values())

industries = q("""SELECT markets, sum(enforced), sum(enforced + closed) FROM ftc_hmid_hhi
                  WHERE edition_end_fy = 2011 AND table_no LIKE '3.%' AND markets <> 'All Markets'
                  GROUP BY 1 ORDER BY sum(enforced) * 1.0 / sum(enforced + closed) DESC""")
ind_rows = "".join(f"<tr><td>{esc(m.replace(' Markets', '').replace(chr(34), ''))}</td><td class=num>{e}</td>"
                   f"<td class=num>{n}</td><td class=num>{pct(e, n)}</td></tr>" for m, e, n in industries)

# ---------------------------------------------------------------- charts
BLUE, ORANGE, RED, PURPLE, GRAY, GREEN = "#2563eb", "#c2410c", "#b91c1c", "#6d28d9", "#9ca3af", "#15803d"
base = {"margin": {"l": 55, "r": 20, "t": 10, "b": 40}, "hovermode": "x unified", "barmode": "stack",
        "font": {"family": "system-ui, Segoe UI, sans-serif", "size": 13},
        "legend": {"orientation": "h", "y": 1.12}, "plot_bgcolor": "#ffffff", "paper_bgcolor": "#ffffff"}
fy = [r[0] for r in hsr]


def bar(x, y, name, color):
    return {"x": x, "y": y, "name": name, "type": "bar", "marker": {"color": color}}


def line(x, y, name, color):
    return {"x": x, "y": y, "name": name, "type": "scatter", "mode": "lines+markers",
            "line": {"color": color, "width": 1.8}, "marker": {"size": 5}, "hovertemplate": "%{y:.1f}%<extra></extra>"}


pct_axis = dict(base, yaxis={"ticksuffix": "%", "rangemode": "tozero"}, showlegend=False)
charts = [
    ("c1", "Transactions reported, by fiscal year", [bar(fy, [r[1] for r in hsr], "Transactions reported", BLUE)],
     dict(base, showlegend=False)),
    ("c2", "Second requests issued, by agency",
     [bar(fy, [r[4] for r in hsr], "FTC", BLUE), bar(fy, [r[5] for r in hsr], "DOJ", ORANGE)], base),
    ("c3", "Second requests as a share of adjusted transactions",
     [line(fy, [100 * r[3] / r[2] for r in hsr], "Second request rate", RED)], pct_axis),
    ("c4", "Early termination granted, as a share of transactions that requested it",
     [line(fy, [100 * r[7] / r[6] for r in hsr], "Granted", GREEN)], pct_axis),
    ("c5", "FTC merger enforcement actions, by type",
     [bar([r[0] for r in ftc], [r[1] for r in ftc], "Consent order", BLUE),
      bar([r[0] for r in ftc], [r[2] for r in ftc], "Federal court injunction sought", RED),
      bar([r[0] for r in ftc], [r[3] for r in ftc], "Administrative complaint", PURPLE),
      bar([r[0] for r in ftc], [r[4] for r in ftc], "Abandoned or restructured", GRAY)], base),
    ("c6", "Share of investigated markets where the FTC sought relief, by post-merger HHI, markets excluding oil",
     [dict(bar(HHI, [100 * e / (e + c) if e + c else None for e, c in (p[b] for b in HHI)], name, color),
           hovertemplate="%{y:.0f}%<extra></extra>")
      for p, name, color in ((hhi_early, "FY1996-2003", GRAY), (hhi_late, "FY2004-2011", BLUE))],
     dict(base, barmode="group", yaxis={"ticksuffix": "%", "range": [0, 100]}, xaxis={"type": "category"})),
]
divs = {cid: f"<h3>{esc(title)}</h3><div id={cid} style='height:330px'></div>" for cid, title, _, _ in charts}
script = "".join(f"Plotly.newPlot('{cid}', {json.dumps(traces)}, {json.dumps(layout)}, {{responsive:true}});"
                 for cid, _, traces, layout in charts)

P_HEAD = ("<tr><th></th><th colspan=3>FY1996-2003</th><th colspan=3>FY2004-2011</th></tr>"
          "<tr><th>{}</th><th>Relief sought</th><th>Markets</th><th>Share</th>"
          "<th>Relief sought</th><th>Markets</th><th>Share</th></tr>")
last = hsr[-1]
body = f"""<div class="head"><h1>Merger enforcement: FTC and DOJ statistics</h1>
<div class="stamps"><span>Page built {now_et()}</span><span>Figures retrieved {q("SELECT max(vintage) FROM hsr_fiscal_year")[0][0]}</span></div></div>

<table><tr><th>Served table</th><th>Contents</th><th>Rows</th><th>Covers</th><th>Source</th><th>Retrieved</th></tr>{pop}</table>
<p class=sub>One row of hsr_fiscal_year: fiscal year {last[0]} (ended {last[0]}-09-30), {num(last[1])} transactions reported,
{num(last[3])} second requests. The files are placed by hand; `uv run pharos antitrust` lands them.</p>

<h2>Filings and second requests</h2>
{divs['c1']}{divs['c2']}{divs['c3']}{divs['c4']}
<div class="wrap"><table>
<tr><th>Fiscal year</th><th>Transactions reported</th><th>Adjusted transactions</th><th>Second requests</th><th>FTC</th>
<th>DOJ</th><th>Second requests / adjusted</th><th>Early termination requested</th><th>Granted</th><th>Not granted</th>
<th>Granted / requested</th></tr>{hsr_rows}</table></div>
<p class=sub>Adjusted transactions are those in which a second request could have been issued. Shaded: the source
file's own figures do not add up in fiscal years {' and '.join(off_years)} (FTC plus DOJ against the total, or granted
plus not granted against requested); they are stored as published.</p>

<h2>Enforcement outcomes</h2>
{divs['c5']}
<div class="cols"><div><h3>FTC, by fiscal year</h3><table>
<tr><th>Fiscal year</th><th>Consent orders</th><th>Injunctions sought</th><th>Administrative complaints</th>
<th>Abandoned or restructured</th><th>Total</th><th>Consent share</th></tr>{ftc_rows}</table></div>
<div><h3>DOJ Antitrust Division, by fiscal year</h3><table>
<tr><th>Fiscal year</th><th>Complaints filed</th><th>For litigation</th><th>With a settlement</th>
<th>Transactions abandoned</th><th>Concerns resolved, no complaint</th><th>Ended without a complaint</th></tr>{doj_rows}</table></div></div>

<h2>Concentration at challenge</h2>
<p class=sub>From the FTC's Horizontal Merger Investigation Data: each market the FTC investigated after a second
request, by how concentrated the merger would leave it, and whether the FTC sought relief. The reports end with
fiscal year 2011.</p>
{divs['c6']}
<div class="cols"><div><h3>By post-merger HHI, markets excluding oil</h3>
<table>{P_HEAD.format('Post-merger HHI')}{period_rows(HHI, hhi_early, hhi_late)}</table></div>
<div><h3>By significant competitors before and after, markets excluding oil</h3>
<table>{P_HEAD.format('Competitors')}{period_rows(COMP, comp_early, comp_late)}</table></div></div>
<p class=sub>FY2004-2011 is the report covering fiscal years 1996-2011 less the report covering 1996-2003.</p>

<h3>Relief sought / markets investigated, fiscal years 1996-2011, all markets</h3>
<div class="wrap"><table><tr><th>Post-merger HHI</th>{''.join(f'<th>Change in HHI {d}</th>' for d in DELTA)}<th>All</th></tr>
{grid}<tr><th>All</th><td colspan=8></td><td class=num>{num(tot_e)} / {num(tot_n)}</td></tr></table></div>

<h3>By industry group, fiscal years 1996-2011</h3>
<table><tr><th>Markets</th><th>Relief sought</th><th>Markets investigated</th><th>Share</th></tr>{ind_rows}</table>"""

out = HERE / "antitrust.html"
out.write_text(page("Merger enforcement", "Catalog", body, crumb="Merger enforcement", head=f'<script src="{PLOTLY}"></script>')
               .replace("</main></body>", f"</main><script>{script}</script></body>"), encoding="utf-8")
print(f"wrote {out}: {len(hsr)} fiscal years of filings, {len(ftc)} of FTC actions, {len(doj)} of DOJ outcomes, "
      f"{tot_n:,} markets investigated")

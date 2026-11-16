"""Temporary exploration page: registered US tax return preparers by credential, place and business (James, 2026-11-16).

Reads <data root>/tmp/scaffold/ptin/current/slim.csv: the IRS list of preparer tax identification number (PTIN) holders
released under the Freedom of Information Act, with names, street addresses, phone numbers and websites dropped when it
was downloaded. Also reads the IRS state file for tax year 2023 in tmp/scaffold/irs/ for paid returns per state.
Quick lane, nothing stored. Every figure is computed here except the IRS_PAGE block. Writes tax-preparers.html.
Run: uv run python scaffold/tax_preparers.py"""
import json
from pathlib import Path

import duckdb
from common import esc, now_et, num, page

from pharos.paths import data_root

HERE = Path(__file__).parent
TMP = data_root() / "tmp" / "scaffold"
PLOTLY = "https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.35.2/plotly.min.js"
EXTRACT_DATE = "2026-08-14"   # date of foia-extract.csv inside the IRS ZIP
# Read off irs.gov/tax-professionals/return-preparer-office-federal-tax-return-preparer-statistics on 2026-11-16.
IRS_PAGE = {"as of": "2026-11-01", "Current PTIN holders": 902514, "Certified public accountants": 210487, "Enrolled agents": 72945,
            "Attorneys": 26696, "Annual Filing Season Program records of completion": 14492}
CODES = (("CPA", "Certified public accountant"), ("EA", "Enrolled agent"), ("ATTY", "Attorney"), ("SRTP", "State-regulated tax preparer"),
         ("CAA", "Certifying acceptance agent"), ("ERPA", "Enrolled retirement plan agent"), ("EAC", "Enrolled actuary"))
FIRMS = (("H&R Block", r"^(HR|HANDR)BLOCK|BLOCKADVISORS"), ("Jackson Hewitt", "JACKSONHEWITT"), ("Liberty Tax", "LIBERTYTAX"),
         ("Intuit (TurboTax)", "^INTUIT|TURBOTAX"), ("Deloitte", "^DELOITTE"), ("Ernst & Young", "^(EY|EYLLP|EYUS.*)$|^ERNST(AND)?YOUNG"),
         ("KPMG", "^KPMG"), ("PwC", "^PWC|^PRICEWATERHOUSECOOPERS"))

con = duckdb.connect()
q = lambda s: con.execute(s).fetchall()
con.execute(f"""CREATE TABLE p AS SELECT upper(trim(coalesce(DBA, ''))) AS dba, regexp_replace(upper(coalesce(DBA, '')), '[^A-Z0-9]', '', 'g') AS dkey,
    trim(coalesce(BUS_ST_CODE, '')) AS st, trim(coalesce(BUS_CNTRY_CDE, '')) AS country,
    string_split(coalesce(PROFESSION, ''), ',') AS prof, coalesce(AFSP_Indicator, '') AS afsp
    FROM read_csv('{(TMP / 'ptin' / 'current' / 'slim.csv').as_posix()}', header=true, all_varchar=true)""")
unknown = q(f"SELECT DISTINCT x FROM (SELECT unnest(prof) AS x FROM p) WHERE x <> '' AND x NOT IN ({', '.join(repr(c) for c, _ in CODES)})")
if unknown:
    raise SystemExit(f"credential codes not in CODES: {unknown}")
con.execute("ALTER TABLE p ADD COLUMN cred BOOLEAN")
con.execute("UPDATE p SET cred = list_has_any(prof, ['CPA', 'EA', 'ATTY'])")
n_all, n_us, n_none, n_afsp, n_afsp_only, n_cred, n_multi, n_blank, n_names = q("""SELECT count(*), count(*) FILTER (WHERE country = ''),
    count(*) FILTER (WHERE prof = ['']), count(*) FILTER (WHERE afsp = 'Y'), count(*) FILTER (WHERE afsp = 'Y' AND prof = ['']),
    count(*) FILTER (WHERE cred), count(*) FILTER (WHERE len(prof) > 1), count(*) FILTER (WHERE dba = ''), count(DISTINCT dba) FROM p""")[0]
cred = [(lab, code, q(f"SELECT count(*) FROM p WHERE list_contains(prof, '{code}')")[0][0]) for code, lab in CODES]
cred_d = {lab: n for lab, _, n in cred}
cred_rows = "".join(f"<tr><td>{esc(lab)}</td><td>{code}</td><td class=num>{num(n)}</td><td class=num>{100 * n / n_all:.1f}%</td></tr>" for lab, code, n in cred)
cred_rows += (f"<tr><td>No credential listed</td><td></td><td class=num>{num(n_none)}</td><td class=num>{100 * n_none / n_all:.1f}%</td></tr>"
              f"<tr><td>... of those, completed the IRS Annual Filing Season Program</td><td></td><td class=num>{num(n_afsp_only)}</td><td class=num>{100 * n_afsp_only / n_all:.1f}%</td></tr>")
cmp_rows = "".join(f"<tr><td>{esc(lab)}</td><td class=num>{num(IRS_PAGE[lab])}</td><td class=num>{num(mine)}</td><td class=num>{mine - IRS_PAGE[lab]:+,}</td></tr>"
                   for lab, mine in (("Current PTIN holders", n_all), ("Certified public accountants", cred_d["Certified public accountant"]),
                                     ("Enrolled agents", cred_d["Enrolled agent"]), ("Attorneys", cred_d["Attorney"]),
                                     ("Annual Filing Season Program records of completion", n_afsp)))

abroad = q("SELECT country, count(*), count(*) FILTER (WHERE cred) FROM p WHERE country <> '' GROUP BY 1 ORDER BY 2 DESC LIMIT 8")
abroad_rows = "".join(f"<tr><td>{c}</td><td class=num>{num(n)}</td><td class=num>{num(k)}</td></tr>" for c, n, k in abroad)

con.execute(f"""CREATE TABLE r AS SELECT STATE AS st, replace(N1, ',', '')::DOUBLE AS returns, replace(PREP, ',', '')::DOUBLE AS paid
    FROM read_csv('{(TMP / 'irs' / '23in55cmcsv.csv').as_posix()}', header=true, all_varchar=true) WHERE AGI_STUB = '0'""")
states = q("""SELECT r.st, count(*), count(*) FILTER (WHERE list_contains(prof, 'CPA')), count(*) FILTER (WHERE list_contains(prof, 'EA')),
    count(*) FILTER (WHERE prof = ['']), any_value(r.paid), any_value(r.returns) FROM p JOIN r ON r.st = p.st
    WHERE p.country = '' AND r.st NOT IN ('US', 'OA') GROUP BY 1 ORDER BY any_value(r.paid) / count(*) DESC""")
us_paid, = q("SELECT paid FROM r WHERE st = 'US'")[0]
state_rows = "".join(f"<tr><td>{s}</td><td class=num>{num(n)}</td><td class=num>{num(c)}</td><td class=num>{num(e)}</td><td class=num>{100 * no / n:.0f}%</td>"
                     f"<td class=num>{paid / 1e6:.2f}</td><td class=num>{paid / n:.0f}</td></tr>" for s, n, c, e, no, paid, ret in states)

firm_case = "CASE " + " ".join(f"WHEN regexp_matches(dkey, '{rx}') THEN '{lab}'" for lab, rx in FIRMS) + " ELSE NULL END"
firms = q(f"""SELECT {firm_case} AS firm, count(*), count(*) FILTER (WHERE cred), count(*) FILTER (WHERE country <> ''),
    count(DISTINCT dba) FROM p GROUP BY 1 HAVING firm IS NOT NULL ORDER BY 2 DESC""")
firm_rows = "".join(f"<tr><td>{esc(f)}</td><td class=num>{num(n)}</td><td class=num>{100 * n / n_all:.1f}%</td><td class=num>{100 * c / n:.0f}%</td>"
                    f"<td class=num>{num(ab)}</td><td class=num>{num(sp)}</td></tr>" for f, n, c, ab, sp in firms)
n_firms = sum(f[1] for f in firms)
top_names = q("SELECT dba, count(*) FROM p WHERE dba <> '' GROUP BY 1 ORDER BY 2 DESC LIMIT 25")
name_rows = "".join(f"<tr><td>{esc(d)}</td><td class=num>{num(n)}</td></tr>" for d, n in top_names)
size = q("""SELECT CASE WHEN n = 1 THEN '1' WHEN n <= 5 THEN '2 to 5' WHEN n <= 25 THEN '6 to 25' WHEN n <= 100 THEN '26 to 100' ELSE 'Over 100' END AS b,
    count(*), sum(n), min(n) FROM (SELECT dba, count(*) AS n FROM p WHERE dba <> '' GROUP BY 1) GROUP BY 1 ORDER BY 4""")
size_rows = "".join(f"<tr><td>{b}</td><td class=num>{num(k)}</td><td class=num>{num(s)}</td><td class=num>{100 * s / n_all:.1f}%</td></tr>" for b, k, s, _ in size)

odd = (
    ("The list is smaller than the IRS's own count", f"The list (dated {EXTRACT_DATE}) has {num(n_all)} preparers. The IRS statistics page says "
     f"{num(IRS_PAGE['Current PTIN holders'])} hold a current number as of {IRS_PAGE['as of']}. The gap of {num(IRS_PAGE['Current PTIN holders'] - n_all)} is not explained by either page."),
    ("The two Annual Filing Season Program counts are not the same thing",
     f"The IRS page counts records of completion issued so far for the 2027 season ({num(IRS_PAGE['Annual Filing Season Program records of completion'])}). "
     f"The list flags {num(n_afsp)} preparers as of {EXTRACT_DATE}, which is an earlier season. The difference in the table above is not an error in either."),
    ("Most registered preparers list no credential", f"{num(n_none)} of {num(n_all)} ({100 * n_none / n_all:.0f}%). Registration is required to sign a return for pay; a credential is not."),
    ("A registration is not a job", "The list does not say whether a preparer signed any returns this year, works one season or all year, or is an employee or self-employed."),
    ("Many are abroad", f"{num(n_all - n_us)} list a country other than the United States, {num(abroad[0][1])} of them under the code {abroad[0][0]}. "
     "The country codes are the IRS's own and are not decoded here."),
    ("Business names are typed by hand", f"{num(n_names)} different spellings; {num(n_blank)} preparers ({100 * n_blank / n_all:.0f}%) give none. "
     "The firm table matches spellings by pattern, so each count is a minimum. Franchise offices that use their own name are missed."),
    ("Credential codes are decoded from memory", "CPA, EA and ATTY are certain. SRTP, CAA, ERPA and EAC are labelled from memory and were not checked against an IRS key."),
)
odd_rows = "".join(f"<tr><td>{esc(a)}</td><td>{esc(b)}</td></tr>" for a, b in odd)

font = {"family": "system-ui, Segoe UI, sans-serif", "size": 13}
base = {"margin": {"l": 230, "r": 30, "t": 10, "b": 40}, "font": font, "plot_bgcolor": "#ffffff", "paper_bgcolor": "#ffffff", "showlegend": False}
bars = [("No credential listed", n_none, "#9ca3af")] + [(lab, n, "#2563eb") for lab, _, n in cred]
bars.sort(key=lambda b: b[1])
charts = [
    ("c1", [{"y": [b[0] for b in bars], "x": [b[1] for b in bars], "type": "bar", "orientation": "h", "marker": {"color": [b[2] for b in bars]},
             "hovertemplate": "%{y}: %{x:,}<extra></extra>"}], {**base, "xaxis": {"gridcolor": "#e3e1da"}}),
    ("c2", [{"y": [f[0] for f in reversed(firms)], "x": [f[1] for f in reversed(firms)], "type": "bar", "orientation": "h",
             "marker": {"color": "#2563eb"}, "hovertemplate": "%{y}: %{x:,}<extra></extra>"}], {**base, "xaxis": {"gridcolor": "#e3e1da"}}),
]
script = "".join(f"Plotly.newPlot('{cid}', {json.dumps(tr)}, {json.dumps(lay)}, {{responsive:true}});" for cid, tr, lay in charts)
div = "<div id={} style='height:{}px'></div>"

body = f"""<div class="head"><h1>Registered tax return preparers</h1>
<div class="stamps"><span>Page built {now_et()}</span><span>IRS list dated {EXTRACT_DATE}, not stored</span></div></div>
<p class=sub>Anyone paid to prepare a federal return must hold a preparer tax identification number (PTIN) from the IRS.
The IRS publishes the list of holders. This page counts it; no person is named.</p>

<h2>What was pulled</h2>
<table><tr><th>File</th><th>Rows</th><th>In the United States</th><th>Abroad</th><th>Columns used</th></tr>
<tr><td>foia-extract.csv, from foia-extract-consolidated.zip</td><td class=num>{num(n_all)}</td><td class=num>{num(n_us)}</td><td class=num>{num(n_all - n_us)}</td>
<td>business name, state, country, profession, Annual Filing Season Program flag</td></tr></table>
<h3>Against the IRS statistics page ({IRS_PAGE['as of']})</h3>
<table><tr><th>Count</th><th>IRS page</th><th>This list</th><th>Difference</th></tr>{cmp_rows}</table>

<h2>What looks odd</h2>
<table><tr><th>Finding</th><th>Evidence</th></tr>{odd_rows}</table>

<h2>By credential</h2>
<p class=sub>{num(n_cred)} preparers ({100 * n_cred / n_all:.0f}%) are a CPA, an enrolled agent or an attorney. {num(n_multi)} list more than one credential, so the rows below add to more than the total.</p>
{div.format('c1', 330)}
<table><tr><th>Credential</th><th>Code in the file</th><th>Preparers</th><th>Share of all</th></tr>{cred_rows}</table>

<h2>By business name</h2>
<p class=sub>The eight names below account for {num(n_firms)} preparers, {100 * n_firms / n_all:.1f}% of the list. Counts are minimums.</p>
{div.format('c2', 330)}
<div class=cols>
<div><table><tr><th>Business</th><th>Preparers</th><th>Share of all</th><th>CPA, enrolled agent or attorney</th><th>Abroad</th><th>Spellings matched</th></tr>{firm_rows}</table>
<h3>Preparers by size of business name</h3>
<table><tr><th>Preparers under one spelling</th><th>Spellings</th><th>Preparers</th><th>Share of all</th></tr>{size_rows}</table></div>
<div><h3>The 25 most common spellings, as typed</h3><table><tr><th>Business name</th><th>Preparers</th></tr>{name_rows}</table></div>
</div>

<h2>By state</h2>
<p class=sub>Preparers with a US address, against returns signed by a paid preparer in tax year 2023 (IRS state file). Sorted by paid returns per preparer.
Nationally: {us_paid / 1e6:.1f} million paid returns over {num(n_us)} US preparers is {us_paid / n_us:.0f} each.</p>
<div class=wrap><table><tr><th>State</th><th>Preparers</th><th>CPAs</th><th>Enrolled agents</th><th>No credential listed</th>
<th>Paid returns 2023, millions</th><th>Paid returns per preparer</th></tr>{state_rows}</table></div>
<h3>Abroad, by country code</h3>
<table><tr><th>Country code</th><th>Preparers</th><th>CPA, enrolled agent or attorney</th></tr>{abroad_rows}</table>"""

out = HERE / "tax-preparers.html"
out.write_text(page("Registered tax return preparers", "Ideas", body, crumb="Registered tax return preparers",
                    head=f'<script src="{PLOTLY}"></script>')
               .replace("</main></body>", f"</main><script>{script}</script></body>"), encoding="utf-8")
print(f"wrote {out}: {num(n_all)} preparers, {num(n_none)} with no credential, {len(states)} states, firms {[(f[0], f[1]) for f in firms]}")

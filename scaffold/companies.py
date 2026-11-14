"""Companies page: every company in SEC Company Facts, by industry (James, 2026-11-14). Hangs off the Series page,
where Company Facts is a single row.

Industries are the SIC codes SEC assigns (submission_details.sic, with SEC's own description), listed under the SIC
division their first two digits fall in. Clicking an industry lists its companies. Measured from the served version
at build time (one pass over company_facts, about ten seconds): facts per company, the years it first and last filed
them, and whether a 10-K for the latest complete fiscal year carries any. A company with no SIC code, or SEC's
placeholder 0000, is in the last group; a code outside every division stops the build. Read-only. Writes companies.html.
Run: uv run python scaffold/companies.py"""
import json
from pathlib import Path

import duckdb
from common import esc, now_et, num, page

from pharos.paths import data_root

HERE = Path(__file__).parent
DATA = data_root()
version = (DATA / "serving" / "CURRENT").read_text(encoding="utf-8").strip()
S = DATA / "serving" / version

# SIC divisions (1987 SIC manual): first and last 2-digit major group, name. Same table as the archived industry-code
# guide (archive/explore-2026-11-12/lib/base.py).
DIVISIONS = ((1, 9, "Agriculture, forestry, fishing"), (10, 14, "Mining"), (15, 17, "Construction"),
             (20, 39, "Manufacturing"), (40, 49, "Transportation, communications, utilities"),
             (50, 51, "Wholesale trade"), (52, 59, "Retail trade"), (60, 67, "Finance, insurance, real estate"),
             (70, 89, "Services"), (91, 97, "Public administration"), (99, 99, "Nonclassifiable"))
NO_CODE = "No industry code"

con = duckdb.connect()
con.execute("SET memory_limit='6GB'")
CF = f"read_parquet('{(S / 'company_facts.parquet').as_posix()}/**/*.parquet', hive_partitioning=false)"
SD = f"read_parquet('{(S / 'submission_details.parquet').as_posix()}')"
# The latest complete fiscal year: the year before the newest filing date (as on the Concepts page).
fy = con.execute(f"SELECT year(max(filed)) - 1 FROM {CF}").fetchone()[0]
rows = con.execute(f"""
    WITH co AS (SELECT cik, count(*) facts, min(year(filed)) y0, max(year(filed)) y1,
                       count(*) FILTER (fy = {fy} AND form = '10-K') > 0 AS k
                FROM {CF} GROUP BY 1)
    SELECT coalesce(d.sic, ''), coalesce(d.sic_description, ''), d.name, array_to_string(d.tickers, ' '),
           d.fiscal_year_end, co.y0, co.y1, co.facts, co.k, d.cik IS NULL
    FROM co LEFT JOIN {SD} d USING (cik) ORDER BY 1, lower(d.name)""").fetchall()
if not rows:
    raise SystemExit(f"no facts in {S / 'company_facts.parquet'}")
if any(r[9] for r in rows):
    raise SystemExit(f"{sum(1 for r in rows if r[9])} companies in company_facts have no row in submission_details")


def division(sic):
    if sic in ("", "0000"):   # blank, or SEC's placeholder 0000 (no description; 6 companies on 2026-11-14)
        return len(DIVISIONS)
    for i, (lo, hi, _) in enumerate(DIVISIONS):
        if len(sic) == 4 and sic.isdigit() and lo <= int(sic[:2]) <= hi:
            return i
    raise SystemExit(f"companies.py: SIC code {sic!r} falls in no SIC division")


industries, members = {}, {}   # sic -> [division, sic, description, companies, companies with the 10-K]; sic -> companies
for sic, desc, name, tickers, fye, y0, y1, facts, k, _ in rows:
    ind = industries.setdefault(sic, [division(sic), sic, desc, 0, 0])
    if ind[2] != desc:
        raise SystemExit(f"submission_details: SIC {sic} carries two descriptions: {ind[2]!r} and {desc!r}")
    ind[3] += 1
    ind[4] += int(k)
    fye = f"{fye[:2]}-{fye[2:]}" if fye and len(fye) == 4 else (fye or "")
    members.setdefault(sic, []).append([name, tickers, fye, y0, y1, facts, int(k)])
n_fy = sum(i[4] for i in industries.values())

SCRIPT = """
const IND = __IND__, CO = __CO__, DIV = __DIV__, open = new Set();
const E = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const F = n => n.toLocaleString('en-US');
function inside(sic) {
  return '<tr class="in"><td></td><td colspan="3"><table><thead><tr><th>Company</th><th>Ticker</th><th>Fiscal year end</th>'
    + '<th>First filed</th><th>Last filed</th><th>Facts</th><th>FY __FY__ 10-K</th></tr></thead><tbody>'
    + CO[sic].map(c => `<tr><td>${E(c[0])}</td><td class="nw">${E(c[1])}</td><td class="nw">${E(c[2])}</td><td class="num">${c[3]}</td>`
      + `<td class="num">${c[4]}</td><td class="num">${F(c[5])}</td><td>${c[6] ? 'yes' : ''}</td></tr>`).join('')
    + '</tbody></table></td></tr>';
}
function draw() {
  let h = '';
  DIV.forEach((d, i) => {
    const rs = IND.filter(r => r[0] === i);
    if (!rs.length) return;
    h += `<tr class="g1"><td colspan="2">${E(d)} <span class="cnt">${F(rs.length)} industries</span></td>`
      + `<td class="num">${F(rs.reduce((s, r) => s + r[3], 0))}</td><td class="num">${F(rs.reduce((s, r) => s + r[4], 0))}</td></tr>`;
    for (const r of rs) {
      h += `<tr class="ind" data-sic="${E(r[1])}"><td class="nw">${open.has(r[1]) ? '&#9662;' : '&#9656;'} ${E(r[1])}</td>`
        + `<td>${E(r[2] || d)}</td><td class="num">${F(r[3])}</td><td class="num">${F(r[4])}</td></tr>`;
      if (open.has(r[1])) h += inside(r[1]);
    }
  });
  document.getElementById('rows').innerHTML = h;
}
document.getElementById('rows').onclick = e => {
  const tr = e.target.closest('tr.ind');
  if (!tr) return;
  const sic = tr.dataset.sic;
  open.has(sic) ? open.delete(sic) : open.add(sic);
  draw();
};
draw();
"""

STYLE = """<style>
tr.g1 td { background:var(--box); font-weight:700; border-bottom:2px solid var(--fg); padding-top:10px; }
.cnt { color:var(--muted); font-weight:400; margin-left:6px; }
#rows td { font-size:15px; padding:3px 10px; }
tr.ind { cursor:pointer; } tr.ind:hover td { background:var(--box); }
tr.in > td { padding:4px 10px 12px; border-bottom:2px solid var(--line); }
tr.in table { margin:0; }
</style>"""

body = f"""<div class="head"><h1>Company Facts companies</h1>
<div class="stamps"><span>Page built {now_et()}</span></div></div>
<p class=sub>Every company with facts in SEC Company Facts, by the industry code (SIC) SEC assigns it: {num(len(rows))}
companies in {num(sum(1 for i in industries.values() if i[0] < len(DIVISIONS)))} industries, read from served version {esc(version)}.
{num(n_fy)} of them have facts from a 10-K for fiscal year {fy}. Click an industry to list its companies.</p>
<div class="wrap"><table>
<thead><tr><th>SIC</th><th>Industry</th><th>Companies</th><th>Companies, FY {fy} 10-K</th></tr></thead>
<tbody id="rows"></tbody></table></div>
<script>{SCRIPT.replace("__IND__", json.dumps(sorted(industries.values(), key=lambda i: (i[0], i[1])), separators=(",", ":")))
               .replace("__CO__", json.dumps(members, separators=(",", ":")).replace("</", "<\\/"))
               .replace("__DIV__", json.dumps([d[2] for d in DIVISIONS] + [NO_CODE])).replace("__FY__", str(fy))}</script>"""

(HERE / "companies.html").write_text(
    page("Company Facts companies", "Series", body, crumb="Company Facts companies", head=STYLE), encoding="utf-8")
print(f"wrote {HERE / 'companies.html'}: {len(rows)} companies, {len(industries)} industry groups, {n_fy} with a FY {fy} 10-K")

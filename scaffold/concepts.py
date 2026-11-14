"""Concepts page: every us-gaap concept in SEC Company Facts, one row each, grouped by financial statement
(James, 2026-11-14). Hangs off the Series page, where Company Facts is a single row.

Measured from the served version at build time (one pass over company_facts, about ten seconds): companies that ever
reported the concept, companies whose 10-K for the latest complete fiscal year carries it, the years it was first and
last filed, its usual unit, and whether its facts are point-in-time or cover a period.

Pharos stores nothing that says which statement a concept belongs to. That comes from SEC's Financial Statement Data
Sets, quick lane: one quarter's zip (QUARTER below) is downloaded once to <data root>/tmp/scaffold/fsds/ and its
pre.txt read. A concept's statement is the one on which the most filings in that quarter presented it; a tie goes to
the earlier statement in STMT. A concept no filing in that quarter put on a statement lands in the last group.
Read-only apart from that download. Writes concepts.html.
Run: uv run python scaffold/concepts.py"""
import json
import zipfile
from pathlib import Path

import duckdb
from common import esc, now_et, num, page

from pharos.loaders import sec
from pharos.paths import data_root

HERE = Path(__file__).parent
DATA = data_root()
version = (DATA / "serving" / "CURRENT").read_text(encoding="utf-8").strip()
S = DATA / "serving" / version

QUARTER = "2026q3"
URL = f"https://www.sec.gov/files/dera/data/financial-statement-data-sets/{QUARTER}.zip"
# pre.txt stmt codes, as the data set's readme.htm names them; the order breaks ties.
STMT = (("BS", "Balance sheet"), ("IS", "Income statement"), ("CF", "Cash flow"), ("EQ", "Equity"),
        ("CI", "Comprehensive income"), ("SI", "Schedule of investments"), ("UN", "Unclassifiable statement"))
NONE = f"Not on a statement in {QUARTER[:4]} Q{QUARTER[-1]} filings (notes, or a concept no longer used)"
SHOWN = 30   # rows shown per statement until "Show all" is clicked

folder = DATA / "tmp" / "scaffold" / "fsds"
folder.mkdir(parents=True, exist_ok=True)
zip_path = folder / f"{QUARTER}.zip"
pre = folder / f"{QUARTER}-pre.txt"
if not pre.exists():
    if not zip_path.exists():
        sec.http_fetch(URL, to=zip_path)
    with zipfile.ZipFile(zip_path) as z, z.open("pre.txt") as src, open(pre, "wb") as out:
        for block in iter(lambda: src.read(1 << 20), b""):
            out.write(block)

con = duckdb.connect()
con.execute("SET memory_limit='6GB'")
PRE = f"read_csv('{pre.as_posix()}', delim='\t', header=true, quote='', all_varchar=true)"
codes = {r[0] for r in con.execute(f"SELECT DISTINCT stmt FROM {PRE} WHERE stmt IS NOT NULL").fetchall()}
if codes - {c for c, _ in STMT}:
    raise SystemExit(f"{pre}: statement codes this page does not know: {sorted(codes - {c for c, _ in STMT})}")
n_filings = con.execute(f"SELECT count(DISTINCT adsh) FROM {PRE}").fetchone()[0]
order = " ".join(f"WHEN '{c}' THEN {i}" for i, (c, _) in enumerate(STMT))
con.execute(f"""CREATE TABLE placed AS
    SELECT tag, CASE stmt {order} END AS s FROM (
        SELECT tag, stmt, count(DISTINCT adsh) n FROM {PRE}
        WHERE version LIKE 'us-gaap/%' AND stmt IS NOT NULL GROUP BY ALL)
    QUALIFY row_number() OVER (PARTITION BY tag ORDER BY n DESC, CASE stmt {order} END) = 1""")

CF = f"read_parquet('{(S / 'company_facts.parquet').as_posix()}/**/*.parquet', hive_partitioning=false)"
CC = f"read_parquet('{(S / 'company_concepts.parquet').as_posix()}/**/*.parquet', hive_partitioning=false)"
# The latest complete fiscal year: the year before the newest filing date.
fy = con.execute(f"SELECT year(max(filed)) - 1 FROM {CF}").fetchone()[0]
rows = con.execute(f"""
    WITH f AS (SELECT concept, count(*) facts, count(*) FILTER (start_date IS NULL) instant, mode(unit) unit,
                      count(DISTINCT cik) n, count(DISTINCT cik) FILTER (fy = {fy} AND form = '10-K') n_fy,
                      min(year(filed)) y0, max(year(filed)) y1
               FROM {CF} WHERE taxonomy = 'us-gaap' GROUP BY 1),
         l AS (SELECT concept, any_value("label") AS lbl, count(DISTINCT "label") AS labels
               FROM {CC} WHERE taxonomy = 'us-gaap' GROUP BY 1)
    SELECT coalesce(l.lbl, f.concept), f.concept, coalesce(p.s, {len(STMT)}),
           -- by majority: a flow concept carries a few facts filed with no start date (NetIncomeLoss: 115 of 1.58M)
           CASE WHEN instant * 2 > facts THEN 'point in time' ELSE 'over a period' END,
           unit, n, n_fy, y0, y1, l.labels
    FROM f LEFT JOIN l USING (concept) LEFT JOIN placed p ON p.tag = f.concept
    ORDER BY n DESC, f.concept""").fetchall()
if not rows:
    raise SystemExit(f"no us-gaap facts in {S / 'company_facts.parquet'}")
if any(r[9] is not None and r[9] > 1 for r in rows):
    raise SystemExit("company_concepts: a us-gaap concept carries more than one label; this page shows one")
rows = [r[:9] for r in rows]
n_placed = sum(1 for r in rows if r[2] < len(STMT))

SCRIPT = """
const ROWS = __ROWS__, GROUPS = __GROUPS__, SHOWN = __SHOWN__, open = new Set();
const E = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const F = n => n.toLocaleString('en-US');
function draw() {
  let h = '';
  GROUPS.forEach((g, i) => {
    const rs = ROWS.filter(r => r[2] === i);
    if (!rs.length) return;
    h += `<tr class="g1"><td colspan="8">${E(g)} <span class="cnt">${F(rs.length)}</span></td></tr>`;
    h += (open.has(i) ? rs : rs.slice(0, SHOWN)).map(r =>
      `<tr><td>${E(r[0])}</td><td class="tag">${E(r[1])}</td><td class="nw">${E(r[3])}</td><td class="nw">${E(r[4])}</td>`
      + `<td class="num">${F(r[5])}</td><td class="num">${F(r[6])}</td><td class="num">${r[7]}</td><td class="num">${r[8]}</td></tr>`).join('');
    if (rs.length > SHOWN) h += `<tr><td colspan="8"><button data-g="${i}">${open.has(i) ? 'Show the first ' + SHOWN : 'Show all ' + F(rs.length)}</button></td></tr>`;
  });
  document.getElementById('rows').innerHTML = h;
}
document.getElementById('rows').onclick = e => {
  const g = e.target.dataset.g;
  if (g === undefined) return;
  open.has(+g) ? open.delete(+g) : open.add(+g);
  draw();
};
draw();
"""

STYLE = """<style>
main, nav { max-width:none; }
tr.g1 td { background:var(--box); font-weight:700; border-bottom:2px solid var(--fg); padding-top:10px; }
.cnt { color:var(--muted); font-weight:400; margin-left:6px; }
#rows td { font-size:15px; padding:3px 10px; }
#rows td.tag { color:var(--muted); word-break:break-all; max-width:420px; }
#rows td:first-child { max-width:520px; }
#rows button { font:inherit; padding:3px 12px; border:1px solid var(--line); background:var(--bg); border-radius:4px; cursor:pointer; }
</style>"""

body = f"""<div class="head"><h1>Company Facts concepts</h1>
<div class="stamps"><span>Page built {now_et()}</span></div></div>
<p class=sub>Every us-gaap concept in SEC Company Facts, one row each: {num(len(rows))} concepts, read from served
version {esc(version)}. Within a statement, the concepts most companies have reported come first.</p>
<p class=sub>Statement: the one on which the most filings presented the concept in SEC's Financial Statement Data Set
for {QUARTER[:4]} Q{QUARTER[-1]} ({num(n_filings)} filings, mostly 10-Qs). {num(n_placed)} concepts were on a statement in those
filings; the other {num(len(rows) - n_placed)} are in the last group.</p>
<div class="wrap"><table>
<thead><tr><th>Concept</th><th>Tag</th><th>Period</th><th>Usual unit</th><th>Companies, ever</th>
<th>Companies, FY {fy} 10-K</th><th>First filed</th><th>Last filed</th></tr></thead>
<tbody id="rows"></tbody></table></div>
<script>{SCRIPT.replace("__ROWS__", json.dumps(rows, separators=(",", ":")).replace("</", "<\\/"))
               .replace("__GROUPS__", json.dumps([n for _, n in STMT] + [NONE])).replace("__SHOWN__", str(SHOWN))}</script>"""

(HERE / "concepts.html").write_text(
    page("Company Facts concepts", "Series", body, crumb="Company Facts concepts", head=STYLE), encoding="utf-8")
print(f"wrote {HERE / 'concepts.html'}: {len(rows)} concepts, {n_placed} on a statement, FY {fy}")

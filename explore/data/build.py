"""Data page: an entity-relationship diagram (Mermaid) of the eight served tables in catalog.duckdb, with every column
and its DuckDB type, read from the catalog at build time. Also writes the same diagram as data-er.pdf (headless Chrome).

Keys are not declared in the database: catalog.duckdb holds views over Parquet, which carry no constraints. PK marks
the key each table is asserted unique on when it is staged (SEC: stage_sec.py's Dataset.key; observations: the
dedup key in transform/*_observations.sql; series_catalog: checked here). FK marks a join column. Each relationship's
orphan count (child rows with no parent) is measured at build and printed; any key that is not unique stops the build.
Writes data.html, data-er.mmd and data-er.pdf next to this file."""
import html as h
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import duckdb

from pharos import stage_sec
from pharos.paths import data_root

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "lib"))
import nav  # noqa: E402

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
MERMAID = "https://cdn.jsdelivr.net/npm/mermaid@11.9.0/dist/mermaid.min.js"   # 11.4.1 rejects classDef in ER; 11.6.0 leaves one row of each table unboxed

KEYS = {ds.table: ds.key for ds in (*stage_sec.FILING_DATASETS, stage_sec.FACTS, stage_sec.CONCEPTS)}
KEYS["observations"] = ("series_id", "obs_date", "vintage")   # dedup key (transform/fred_observations.sql, C07)
KEYS["series_catalog"] = ("series_id",)

# (parent, child, join columns, cardinality, label). Solid line: the join columns are part of the child's key.
RELS = (
    ("series_catalog", "observations", ("series_id",), "||--o{", "series_id"),
    ("submission_details", "submission_filings", ("cik",), "||--o{", "cik"),
    ("submission_details", "filings", ("cik",), "|o..o{", "cik"),
    ("submission_details", "tickers", ("cik",), "||..o{", "cik"),
    ("submission_details", "company_concepts", ("cik",), "||--o{", "cik"),
    ("company_concepts", "company_facts", ("cik", "taxonomy", "concept"), "||--o{", "cik, taxonomy, concept"),
    ("submission_filings", "company_facts", ("cik", "accession"), "|o..o{", "cik, accession"),
    ("submission_filings", "filings", ("cik", "accession", "form"), "|o..o|", "same filing: cik, accession, form"),
)
FACT = ("observations", "company_facts", "filings", "submission_filings")       # one row per event: blue
DIM = ("series_catalog", "submission_details", "tickers", "company_concepts")   # one row per thing: green

con = duckdb.connect((data_root() / "catalog.duckdb").as_posix(), read_only=True)
tables = [t for (t,) in con.sql("select table_name from information_schema.tables where table_name like 'serving_%' "
                                "order by table_name").fetchall()]
names = [t.removeprefix("serving_") for t in tables]
if sorted(names) != sorted(KEYS):
    raise SystemExit(f"data: served tables {sorted(names)} differ from the keys known here {sorted(KEYS)}")

fk_cols = {}
for parent, child, cols, _, _ in RELS:
    fk_cols.setdefault(child, set()).update(cols)


def mtype(t: str) -> str:
    return t.replace("TIMESTAMP WITH TIME ZONE", "TIMESTAMPTZ").replace(" ", "_")


rows = {}
entities = []
for t, n in zip(tables, names):
    rows[n] = con.sql(f"select count(*) from {t}").fetchone()[0]
    key = ", ".join(KEYS[n])
    dup = con.sql(f"select count(*) - count(distinct ({key})) from {t}").fetchone()[0] if n in (
        "observations", "series_catalog") else 0   # SEC keys are asserted unique at staging (_require_unique)
    if dup:
        raise SystemExit(f"data: {n} key ({key}) repeats {dup} times")
    cols = con.sql(f"select column_name, data_type from information_schema.columns where table_name = '{t}' "
                   "order by ordinal_position").fetchall()
    lines = []
    for c, ty in cols:
        marks = [m for m, on in (("PK", c in KEYS[n]), ("FK", c in fk_cols.get(n, ()))) if on]
        lines.append(f"        {mtype(ty)} {c}{' ' + ', '.join(marks) if marks else ''}")
    entities.append(f'    {n}["{n} ({rows[n]:,} rows)"] {{\n' + "\n".join(lines) + "\n    }")

orphans = []
for parent, child, cols, card, label in RELS:
    on = " and ".join(f"c.{x} = p.{x}" for x in cols)
    n = con.sql(f"select count(*) from serving_{child} c anti join serving_{parent} p on {on}").fetchone()[0]
    orphans.append((parent, child, label, n))
    print(f"{child} -> {parent} on {label}: {n:,} of {rows[child]:,} child rows have no parent")

# --- Contents: what the data covers, measured from the served tables at build; descriptions and questions are prose.
def one(sql):
    return con.sql(sql).fetchone()


def table(head, body):
    th = "".join(f"<th>{c}</th>" for c in head)
    tr = "".join("<tr>" + "".join(f"<td>{h.escape(str(c))}</td>" for c in r) + "</tr>" for r in body)
    return f'<div class="tw"><table><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table></div>'


def qs(items):
    return "<p class=q>Questions it can answer:</p><ul>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>"


FREQ = {"D": "daily", "M": "monthly", "Q": "quarterly", "A": "annual"}
macro = con.sql("""select c.series_id, c.name, c.units, c.frequency, c.adjustment,
                          min(o.obs_date), max(o.obs_date), count(distinct o.vintage)
                   from serving_series_catalog c join serving_observations o using (series_id)
                   where c.source = 'fred' group by all order by c.series_id""").fetchall()
prices = con.sql("""select c.entity_id, min(o.obs_date), max(o.obs_date), count(*), c.active_to
                    from serving_series_catalog c left join serving_observations o using (series_id)
                    where c.source = 'yf' group by all order by c.entity_id""").fetchall()
if not macro or not prices:
    raise SystemExit("data: no FRED or no price series in the catalog")
ops = dict(con.sql("select entity_type, count(*) from serving_submission_details group by 1").fetchall())
sf = one("select count(*), count(distinct cik), min(filing_date), max(filing_date) from serving_submission_filings")
fl = one("select count(*), min(date_filed), max(date_filed), count(distinct form) from serving_filings")
cc = dict(con.sql("select taxonomy, count(distinct concept) from serving_company_concepts group by 1").fetchall())
cf = one("select count(*), count(distinct cik), min(filed), max(filed) from serving_company_facts")
tk = one("select count(*), count(distinct cik) from serving_tickers")

macro_rows = [(s.removeprefix("fred:"), n, u, FREQ.get(f, f), a, lo, hi, f"{v:,}") for s, n, u, f, a, lo, hi, v in macro]
price_rows = [(t, lo or "none", hi or "none", f"{n:,}" if lo else "0",
               f"delisted {to}; Yahoo has no history" if to and not lo else (f"ended {to}" if to else ""))
              for t, lo, hi, n, to in prices]
sec_rows = (
    ("submission_details", "one company or other filer: name, industry code (SIC), state, tickers, fiscal year end",
     f"{rows['submission_details']:,} filers, of which {ops.get('operating', 0):,} operating companies"),
    ("submission_filings", "one filing in a filer's own history: form, filing date, period it reports on",
     f"{sf[0]:,} filings by {sf[1]:,} filers, filed {sf[2]} to {sf[3]}"),
    ("filings", "one filing in EDGAR's quarterly index of everything filed",
     f"{fl[0]:,} filings, {fl[3]:,} form types, filed {fl[1]} to {fl[2]}"),
    ("company_concepts", "one line item a company has reported, with its label and definition",
     f"{rows['company_concepts']:,}; {cc.get('us-gaap', 0):,} US GAAP and {cc.get('ifrs-full', 0):,} IFRS line items"),
    ("company_facts", "one reported number: a line item, for one period, as stated in one filing",
     f"{cf[0]:,} numbers from {cf[1]:,} companies, filed {cf[2]} to {cf[3]}"),
    ("tickers", "a ticker in use today and the company it belongs to",
     f"{tk[0]:,} tickers for {tk[1]:,} companies"),
)

contents = f"""
<h2>Economy: US macro series</h2>
<p>{len(macro)} US series from the St. Louis Fed (FRED), each with every past release (ALFRED vintages), so you can see
what a figure was first reported as and how it was revised. Table: <code>serving_observations</code>, described in
<code>serving_series_catalog</code>.</p>
{table(("Series", "Name", "Units (latest release)", "Frequency", "Adjustment", "First", "Last", "Releases"), macro_rows)}
{qs(("How much has real GDP per person grown since 1960? (GDPC1 over POPTHM)",
     "What share of GDP is paid to workers, and how has it moved? (COE over GDP)",
     "What share of adults is working? (CE16OV over CNP16OV)",
     "How far did the first GDP estimate miss the figure we have today, and in which quarters?"))}

<h2>Markets: daily stock prices</h2>
<p>Daily closing prices for {len(prices)} US stocks from Yahoo Finance, adjusted for splits but not for dividends.
Table: <code>serving_observations</code> (series <code>yf:close:TICKER</code>).</p>
{table(("Ticker", "First", "Last", "Days", "Note"), price_rows)}
{qs(("How did each stock do through the 2008 and 2020 recessions?",
     "What was a company's price relative to the earnings it had reported by that day?",
     "Which of these stocks move together, and which don't?"))}

<h2>Companies: SEC filings and reported financials</h2>
<p>Every company that files with the SEC: who they are, what they filed and when, and every number tagged in their
financial statements (XBRL). A number keeps the filing it came from and its filing date, so you can see what was
known on any given day, including restatements.</p>
{table(("Table", "One row is", "What's in it"), sec_rows)}
{qs(("Which industries have the most public companies, and how concentrated are they?",
     "How has a company's revenue grown, and did it later restate any year?",
     "How many days after year end do companies file their annual report (10-K)?",
     "Where are companies incorporated today, by industry?"))}

<h2>Not here yet</h2>
<ul>
<li>Other economies: 56 FRED series (GDP, unemployment, interest rates, exchange rates) for 12 countries and the euro
area are planned next.</li>
<li>Not on FRED, so a new source would be needed: monthly inflation outside the euro area; unemployment for China,
India, Brazil and the euro area; 10-year bond yields for China, Brazil and the euro area.</li>
<li>Prices for any stock beyond the {len(prices)} above, and dividend-adjusted (total return) prices.</li>
<li>US national accounts beyond GDP and pay (BEA): spending by category, industry output.</li>
</ul>
<p class="muted">Ideas for what to add, or questions you'd like answered, go to James.</p>
"""

mmd = "erDiagram\n" + "\n".join(f'    {p} {card} {c} : "{label}"' for p, c, _, card, label in RELS) + "\n\n" \
      + "\n\n".join(entities) + "\n\n" \
      + "    classDef fact fill:#F3F7FF,stroke:#000000,stroke-width:2px,color:#000000\n" \
      + "    classDef dim fill:#EFFFF2,stroke:#000000,stroke-width:2px,color:#000000\n" \
      + f"    class {','.join(FACT)} fact\n    class {','.join(DIM)} dim\n"
(HERE / "data-er.mmd").write_text(mmd, encoding="utf-8")

built = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
INIT_T = "mermaid.initialize({startOnLoad:false, theme:'base', er:{useMaxWidth:%s}, " \
       "themeVariables:{lineColor:'#000000', textColor:'#000000', rowOdd:'#ffffff', rowEven:'#ffffff'}});"
INIT = INIT_T % 'false'   # the PDF: full size
INIT_PAGE = INIT_T % 'true'   # the page: fit to the width

# Print copy for the PDF: the page is sized to the drawn diagram, so nothing is scaled or cut.
pdf_html = HERE / "_print.html"
pdf_html.write_text(f"""<!doctype html><html><head><meta charset="utf-8"><style>
body {{ margin:0; background:#fff; }} pre.mermaid {{ margin:0; padding:24px; }}</style>
<script src="{MERMAID}"></script></head><body><pre class="mermaid">{h.escape(mmd)}</pre>
<script>{INIT}
mermaid.run().then(() => {{ const d = document.documentElement;
  const st = document.createElement('style');
  st.textContent = '@page {{ size: ' + (d.scrollWidth + 4) + 'px ' + (d.scrollHeight + 4) + 'px; margin:0 }}';
  document.head.appendChild(st); document.title = 'done'; }});</script></body></html>""", encoding="utf-8")
dom = subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--virtual-time-budget=15000", "--dump-dom",
                      pdf_html.as_uri()], check=True, capture_output=True, text=True, encoding="utf-8").stdout
if "<svg" not in dom or "Syntax error" in dom or "<title>done</title>" not in dom:
    raise SystemExit("data: Mermaid did not render the diagram (syntax error or the script did not load)")
pdf = HERE / "data-er.pdf"
subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", "--virtual-time-budget=15000",
                f"--print-to-pdf={pdf}", pdf_html.as_uri()], check=True, capture_output=True)
pdf_html.unlink()
if not pdf.is_file() or pdf.stat().st_size < 10_000:
    raise SystemExit("data: the PDF did not render")

page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pharos data</title>
<style>
:root {{ --bg:#ffffff; --fg:#1d1d1b; --muted:#5f5e58; --line:#e3e1da; }}
body {{ background:var(--bg); color:var(--fg); font:15px/1.5 system-ui, "Segoe UI", sans-serif; margin:0;
        padding:24px 16px 60px; }}
main {{ max-width:1250px; margin:0 auto; }}
h1 {{ font-size:24px; margin:0 0 6px; }}
.muted {{ color:var(--muted); }}
.wrap {{ overflow:auto; border:1px solid var(--line); background:#fff; margin-top:14px; }}
pre.mermaid {{ margin:0; padding:16px; }}
h2 {{ font-size:18px; margin:30px 0 6px; }}
.tw {{ overflow-x:auto; }}
table {{ border-collapse:collapse; font-size:13.5px; margin:8px 0; }}
th, td {{ text-align:left; padding:4px 10px; border-bottom:1px solid var(--line); vertical-align:top; }}
th {{ color:var(--muted); font-weight:600; }}
p.q {{ margin:10px 0 2px; font-weight:600; }}
ul {{ margin:2px 0 0; }}
code {{ font-size:13px; }}
{nav.CSS}
</style>
<script src="{MERMAID}"></script>
</head><body><main>
{nav.html("Data")}
<h1>Data</h1>
<p class="muted">What Pharos holds today, measured from the served data at {built}, with questions each part can
answer. The diagram at the bottom shows how the tables connect.</p>
{contents}
<h2>How the tables connect</h2>
<p class="muted">The eight served tables in catalog.duckdb, every column with its type, read from the catalog at
{built}. PK is the key each table is checked unique on when it is staged; FK is a join column. The database itself
declares no keys: the tables are views over Parquet files. Solid line: the join columns are part of the child's key.
<a href="data-er.pdf">PDF</a></p>
<div class="wrap"><pre class="mermaid">{h.escape(mmd)}</pre></div>
</main>
<script>{INIT_PAGE} mermaid.run();</script>
</body></html>"""
(HERE / "data.html").write_text(page, encoding="utf-8")
print(f"wrote {HERE / 'data.html'}, {pdf} ({pdf.stat().st_size:,} bytes)")

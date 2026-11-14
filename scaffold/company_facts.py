"""Company Facts sheet: the SEC Company Facts source for the Catalog (James, 2026-11-11).

Sections: open questions, population by form, then Data: schema diagram, tables, columns, joins, shape over time.
Oddities, one company by hand and Pipeline were dropped (James, 2026-11-11: not needed for the catalog). Everything is measured from the served version at build time. The heavy
measurements (several minutes the first time) are cached in <data root>/tmp/scaffold/ under the SEC run the
served tables came from (groups.parquet, source_run), so a rebuild takes seconds until `pharos sec` lands new data;
a prices and FRED refresh changes the served version but not the SEC tables, and does not re-measure. Read-only. Writes company-facts.html.
Run: uv run python scaffold/company_facts.py"""
import json
import re
import time
from pathlib import Path

import duckdb

from pharos import stage_sec
from pharos.paths import data_root
from common import MERMAID, esc, num, now_et, page

HERE = Path(__file__).parent
DATA = data_root()
version = (DATA / "serving" / "CURRENT").read_text(encoding="utf-8").strip()
S = DATA / "serving" / version
con = duckdb.connect()
con.execute("SET memory_limit='6GB'")
sec_run = con.execute(f"SELECT source_run FROM '{(S / 'groups.parquet').as_posix()}' WHERE \"group\" = 'sec'").fetchall()
if len(sec_run) != 1 or not sec_run[0][0]:
    raise SystemExit(f"served version {version}: groups.parquet has no single source_run for group sec: {sec_run}")
sec_run = sec_run[0][0]
CACHE = DATA / "tmp" / "scaffold" / f"company_facts-sec-{sec_run}.json"

TABLES = {  # table -> (declared key, role)
    "company_facts": (stage_sec.FACTS.key, "one fact as one filing reported it"),
    "company_concepts": (stage_sec.CONCEPTS.key, "one concept a company uses, with its label"),
    "submission_details": (("cik",), "one company (joins on cik)"),
    "filings": (("cik", "accession", "form", "name"), "one filing per party in the EDGAR index (joins on cik, accession)"),
}
MAIN = ("company_facts", "company_concepts")


def src(t):
    p = S / f"{t}.parquet"
    return f"read_parquet('{p.as_posix()}/**/*.parquet', hive_partitioning=false)" if p.is_dir() \
        else f"read_parquet('{p.as_posix()}')"


for t in TABLES:
    con.execute(f"CREATE VIEW {t} AS SELECT * FROM {src(t)}")

timings = {}


def timed(label, fn):
    t = time.perf_counter()
    out = fn()
    timings[label] = round(time.perf_counter() - t, 1)
    return out


def measure():
    m = {"version": version, "sec_run": sec_run}
    # tables: rows, columns, types, size, key duplicates
    m["tables"] = {}
    for t, (key, _) in TABLES.items():
        cols = [(c[0], c[1]) for c in con.execute(f"DESCRIBE {t}").fetchall()]
        n = con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        p = S / f"{t}.parquet"
        size = sum(f.stat().st_size for f in p.rglob("*.parquet")) if p.is_dir() else p.stat().st_size
        files = len(list(p.rglob("*.parquet"))) if p.is_dir() else 1
        k = ", ".join(key)
        dup = timed(f"key check {t}", lambda: con.execute(
            f"SELECT count(*) - count(DISTINCT ({k})) FROM {t}").fetchone()[0])
        m["tables"][t] = {"rows": n, "cols": cols, "bytes": size, "files": files, "key": list(key), "dup": dup}
    # columns: filled, distinct, min, max, top values when few distinct
    m["columns"] = {}
    for t in MAIN:
        cols = m["tables"][t]["cols"]
        expr = ", ".join(f'count("{c}"), approx_count_distinct("{c}"), min("{c}")::VARCHAR, max("{c}")::VARCHAR'
                         for c, _ in cols)
        r = timed(f"profile {t}", lambda: con.execute(f"SELECT {expr} FROM {t}").fetchone())
        prof = []
        for i, (c, ty) in enumerate(cols):
            filled, distinct, lo, hi = r[4 * i: 4 * i + 4]
            top = None
            if distinct <= 50 and ty.startswith("VARCHAR"):
                top = con.execute(f'SELECT coalesce("{c}", \'(null)\'), count(*) n FROM {t} GROUP BY 1 '
                                  f"ORDER BY n DESC LIMIT 6").fetchall()
            prof.append({"col": c, "type": ty, "filled": filled, "distinct": distinct, "min": lo, "max": hi, "top": top})
        m["columns"][t] = prof
    # joins: child rows with no parent
    m["joins"] = timed("joins", lambda: [
        ("company_facts.cik", "submission_details.cik", con.execute(
            "SELECT count(*) FROM (SELECT DISTINCT cik FROM company_facts) ANTI JOIN submission_details USING (cik)"
        ).fetchone()[0], "companies"),
        ("company_facts.accession", "filings.accession", con.execute(
            "SELECT count(*) FROM (SELECT DISTINCT accession FROM company_facts) f "
            "ANTI JOIN (SELECT DISTINCT accession FROM filings) i USING (accession)").fetchone()[0], "filings"),
        ("company_facts (cik, taxonomy, concept)", "company_concepts", con.execute(
            "SELECT count(*) FROM (SELECT DISTINCT cik, taxonomy, concept FROM company_facts) "
            "ANTI JOIN company_concepts USING (cik, taxonomy, concept)").fetchone()[0], "concepts"),
        ("company_concepts (cik, taxonomy, concept)", "company_facts", con.execute(
            "SELECT count(*) FROM company_concepts ANTI JOIN (SELECT DISTINCT cik, taxonomy, concept "
            "FROM company_facts) USING (cik, taxonomy, concept)").fetchone()[0], "concepts"),
    ])
    m["unmatched_accessions"] = con.execute(
        "SELECT f.accession, any_value(f.form), min(f.filed)::VARCHAR, count(*) FROM company_facts f "
        "ANTI JOIN (SELECT DISTINCT accession FROM filings) i USING (accession) "
        "GROUP BY 1 ORDER BY 4 DESC LIMIT 5").fetchall()
    m["unmatched_accession_years"] = con.execute(
        "SELECT year(f.filed) y, count(DISTINCT f.accession) FROM company_facts f "
        "ANTI JOIN (SELECT DISTINCT accession FROM filings) i USING (accession) GROUP BY 1 ORDER BY 1").fetchall()
    # shape over time, by the year the fact was filed
    m["by_year"] = timed("by year", lambda: con.execute(
        "SELECT year(filed) y, count(*), count(DISTINCT cik), count(DISTINCT accession) FROM company_facts "
        "GROUP BY 1 ORDER BY 1").fetchall())
    # oddities: (label, where, why it matters)
    odd = [
        ("Period ends after the year 2100", "end_date > DATE '2100-01-01'",
         "a typo'd year in the filing (6016 for 2016); picks wrongly as a company's latest period"),
        ("Period ends more than a year after the filing", "end_date > filed + INTERVAL 366 DAY AND end_date <= DATE '2100-01-01'",
         "a fact about the future: forecasts, commitment schedules, or a typo"),
        ("Period ends before 1950", "end_date < DATE '1950-01-01'", "very old dates: real history or a typo"),
        ("Start date after end date", "start_date > end_date", "impossible duration"),
        ("Duration over 400 days", "date_diff('day', start_date, end_date) > 400",
         "longer than a fiscal year: cumulative-since-inception facts, or a typo"),
        ("Filed after its available date", "filed > available_date", "would let a fact be used before it was public"),
        ("Assets below zero", "concept = 'Assets' AND val < 0", "total assets cannot be negative"),
        ("Value is NaN or infinite", "isnan(val) OR isinf(val)", "not a number"),
    ]
    m["odd"] = []
    for label, where, why in odd:
        n = con.execute(f"SELECT count(*) FROM company_facts WHERE {where}").fetchone()[0]
        ex = con.execute(f"SELECT cik, concept, unit, start_date::VARCHAR, end_date::VARCHAR, val, form, "
                         f"accession, filed::VARCHAR FROM company_facts WHERE {where} "
                         f"ORDER BY end_date DESC LIMIT 5").fetchall() if n else []
        m["odd"].append((label, where, why, n, ex))
    timings["oddities"] = None
    m["timings"] = timings
    return m


if CACHE.exists():
    M = json.loads(CACHE.read_text(encoding="utf-8"))
    cached = True
else:
    t0 = time.perf_counter()
    M = measure()
    M["measure_s"] = round(time.perf_counter() - t0, 1)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(M, default=str), encoding="utf-8")
    M = json.loads(CACHE.read_text(encoding="utf-8"))
    cached = False

# population: facts and filings by form, all years (cached with the other measurements)
if "by_form_all" not in M:
    M["by_form_all"] = con.execute("SELECT form, count(*), count(DISTINCT accession) FROM company_facts "
                                   "GROUP BY 1 ORDER BY 2 DESC").fetchall()
    CACHE.write_text(json.dumps(M, default=str), encoding="utf-8")
POP_TOP = 10
pop = M["by_form_all"]
pop_facts = sum(r[1] for r in pop)
pop_rows = "".join(f"<tr><td>{esc(f)}</td><td class=num>{num(n)}</td><td class=num>{n / pop_facts:.1%}</td>"
                   f"<td class=num>{num(a)}</td></tr>" for f, n, a in pop[:POP_TOP])
rest = pop[POP_TOP:]
pop_rows += (f"<tr><td>{len(rest)} other forms</td><td class=num>{num(sum(r[1] for r in rest))}</td>"
             f"<td class=num>{sum(r[1] for r in rest) / pop_facts:.1%}</td><td class=num>{num(sum(r[2] for r in rest))}</td></tr>"
             f"<tr><td><b>Total</b></td><td class=num><b>{num(pop_facts)}</b></td><td></td>"
             f"<td class=num><b>{num(sum(r[2] for r in pop))}</b></td></tr>")

T_cols = {t: M["tables"][t]["cols"] for t in MAIN}
# examples per column: up to three distinct non-null values from the first 20,000 rows of each table, cached
if "examples" not in M:
    M["examples"] = {}
    for t in MAIN:
        cols = [c for c, _ in T_cols[t]]
        rows = con.execute(f"SELECT * FROM {t} LIMIT 20000").fetchall()
        ex = {}
        for i, c in enumerate(cols):
            seen = []
            for r in rows:
                v = r[i]
                if v is not None and str(v) not in seen:
                    seen.append(str(v))
                if len(seen) == 3:
                    break
            ex[c] = seen
        M["examples"][t] = ex
    CACHE.write_text(json.dumps(M, default=str), encoding="utf-8")

# open questions: one line each in company-facts-questions.md, shown first on the page
questions = [ln[2:] for ln in (HERE / "company-facts-questions.md").read_text(encoding="utf-8").splitlines()
             if ln.startswith("- ")]
q_html = "".join(f"<li>{esc(q)}</li>" for q in questions)

# filings and facts by form, by year filed: the forms that carry XBRL change over time (424B2, DEF 14A in 2026)
FORM_YEARS = list(range(2019, 2027))
by_form = con.execute(f"""
    WITH t AS (SELECT form, year(filed) y, count(DISTINCT accession) filings, count(*) facts FROM company_facts
               WHERE year(filed) BETWEEN {FORM_YEARS[0]} AND {FORM_YEARS[-1]} GROUP BY 1, 2),
         top AS (SELECT form FROM t GROUP BY 1 ORDER BY sum(filings) DESC LIMIT 12)
    SELECT CASE WHEN form IN (SELECT form FROM top) THEN form ELSE '(other)' END f, y, sum(filings), sum(facts)
    FROM t GROUP BY 1, 2""").fetchall()

# ---------------------------------------------------------------- page
T = M["tables"]


def mtype(ty):
    return {"TIMESTAMP WITH TIME ZONE": "TIMESTAMPTZ", "VARCHAR[]": "VARCHAR_LIST"}.get(ty, ty).replace(" ", "_")


def entity(t):
    key = set(T[t]["key"])
    cols = T[t]["cols"] if t in MAIN else [c for c in T[t]["cols"] if c[0] in key or c[0] == "accession"]
    lines = "".join(f"    {mtype(ty)} {c}{' PK' if c in key else ''}\n" for c, ty in cols)
    return f'  {t}["{t} ({KIND[t][0]})"] {{\n{lines}  }}\n'


# star-schema role and diagram color per table: one fact table (the measured values) and three dimensions that
# describe it (the company, the filing, the concept)
KIND = {"company_facts": ("fact", "#fef9c3"), "submission_details": ("dimension: company", "#dbeafe"),
        "filings": ("dimension: filing", "#dcfce7"), "company_concepts": ("dimension: concept", "#fce7f3")}
mmd = ("erDiagram\n" + "".join(entity(t) for t in TABLES)
       + '  submission_details ||--o{ company_facts : "cik"\n'
       + '  filings ||--o{ company_facts : "cik, accession"\n'
       + '  company_concepts ||--o{ company_facts : "cik, taxonomy, concept"\n'
       + "".join(f"  classDef c_{t} fill:{KIND[t][1]},stroke:#9ca3af,color:#1d1d1b\n  class {t} c_{t}\n"
                 for t in TABLES))

table_rows = "".join(
    f"<tr{' class=flag' if T[t]['dup'] else ''}><td><b>{t}</b><div class=sub>{esc(role)}</div></td>"
    f"<td><span style='background:{KIND[t][1]}; padding:0 4px'>{esc(KIND[t][0])}</span></td>"
    f"<td class=num>{num(T[t]['rows'])}</td><td class=num>{len(T[t]['cols'])}</td>"
    f"<td class=num>{num(T[t]['files'])}</td><td class=num>{T[t]['bytes'] / 1e9:,.2f} GB</td>"
    f"<td>{esc(', '.join(T[t]['key']))}</td><td class=num>{num(T[t]['dup'])}</td></tr>"
    for t, (_, role) in TABLES.items())


def col_table(t):
    rows = []
    n = T[t]["rows"]
    for c in M["columns"][t]:
        ex = "<br>".join(esc(v if len(v) <= 40 else v[:39] + "...") for v in M["examples"][t].get(c["col"], []))
        rng = "" if c["min"] is None else f"{esc(str(c['min'])[:32])}<br>{esc(str(c['max'])[:32])}"
        rows.append(f"<tr><td>{esc(c['col'])}</td><td>{esc(c['type'])}</td>"
                    f"<td class=num>{c['filled'] / n:.1%}</td><td class=num>{num(c['distinct'])}</td>"
                    f"<td>{rng}</td><td>{ex}</td></tr>")
    return (f"<h3>{t}: {num(n)} rows</h3><div class=wrap><table><tr><th>Column</th><th>Type</th><th>Filled</th>"
            f"<th>Distinct (approx)</th><th>Min / max</th><th>Examples</th></tr>{''.join(rows)}</table></div>")


join_rows = "".join(f"<tr{' class=flag' if n else ''}><td class=mono>{esc(a)}</td><td class=mono>{esc(b)}</td>"
                    f"<td class=num>{num(n)}</td><td>{esc(unit)}</td></tr>" for a, b, n, unit in M["joins"])
unmatched = "".join(f"<tr><td class=mono>{esc(a)}</td><td>{esc(f)}</td><td>{esc(d)}</td><td class=num>{num(n)}</td></tr>"
                    for a, f, d, n in M["unmatched_accessions"])
unmatched_years = ", ".join(f"{y}: {num(n)}" for y, n in M["unmatched_accession_years"])


def bars(rows, idx, label, color):
    """Inline SVG bar chart: one bar per year."""
    vals = [(r[0], r[idx]) for r in rows if r[0] is not None]
    w, h, pad = 720, 170, 30
    top = max(v for _, v in vals) or 1
    bw = (w - pad) / len(vals)
    out = []
    for i, (y, v) in enumerate(vals):
        bh = (h - 30) * v / top
        x = pad + i * bw
        out.append(f'<rect x="{x:.1f}" y="{h - 18 - bh:.1f}" width="{bw * 0.8:.1f}" height="{bh:.1f}" fill="{color}">'
                   f"<title>{y}: {v:,}</title></rect>")
        if y % 5 == 0:
            out.append(f'<text x="{x + bw * 0.4:.1f}" y="{h - 4}" font-size="13" text-anchor="middle" fill="#5f5e58">{y}</text>')
    out.append(f'<text x="0" y="12" font-size="13" fill="#5f5e58">{top:,}</text>')
    return f'<div><h3>{label}</h3><svg width="{w}" height="{h}" role="img">{"".join(out)}</svg></div>'


forms = {}
for f, y, nf, nx in by_form:
    forms.setdefault(f, {})[y] = (nf, nx)
order = sorted(forms, key=lambda f: (f == "(other)", -sum(v[0] for v in forms[f].values())))
form_rows = "".join(
    f"<tr><td class=mono>{esc(f)}</td>" + "".join(
        f'<td class=num title="{num(forms[f].get(y, (0, 0))[1])} facts">{num(forms[f].get(y, (0, 0))[0]) if y in forms[f] else ""}</td>'
        for y in FORM_YEARS) + "</tr>" for f in order)

body = f"""
<div class="head"><h1>SEC Company Facts</h1>
<div class="stamps"><span>Built {now_et()}</span></div></div>
<h3>Open questions ({len(questions)})</h3>
<ul>{q_html}</ul>

<h3>Population: every filing that carries XBRL, by form</h3>
<table><tr><th>Form</th><th>Facts</th><th>Share</th><th>Filings</th></tr>{pop_rows}</table>



<h2>Data</h2>
<h3>1. Schema</h3>
<p class=sub>Company Facts' two tables in full; the two tables they join to, key columns only. PK: the key each table
is checked unique on when staged.</p>
<pre class="mermaid">{esc(mmd)}</pre>

<h3>2. Tables</h3>
<table class="t2"><tr><th>Table</th><th>Type</th><th>Rows</th><th>Cols</th><th>Files</th><th>On disk</th><th>Key</th><th>Duplicates on key</th></tr>
{table_rows}</table>

<h3>3. Columns</h3>
{''.join(col_table(t) for t in MAIN)}

<h3>4. Joins</h3>
<table><tr><th>From</th><th>To</th><th>No match</th><th>Counted in</th></tr>{join_rows}</table>
<details><summary>Filings in Company Facts that the filing index doesn't have: by year filed, and the five largest</summary>
<p class=mono>{unmatched_years}</p>
<table><tr><th>accession</th><th>form</th><th>filed</th><th>facts</th></tr>{unmatched}</table></details>

<h3>5. Shape over time (by year filed)</h3>
<div class="cols">
{bars(M['by_year'], 1, 'Facts', '#2563eb')}
{bars(M['by_year'], 2, 'Companies', '#15803d')}
{bars(M['by_year'], 3, 'Filings', '#b45309')}
</div>

<h3>5b. Filings by form, by year filed</h3>
<p class=sub>The 12 forms with the most filings 2019-2026; the rest in (other). Hover a cell for its fact count.</p>
<div class=wrap><table><tr><th>Form</th>{''.join(f'<th class=num>{y}</th>' for y in FORM_YEARS)}</tr>{form_rows}</table></div>

"""
head = (f'<script src="{MERMAID}"></script><script>document.addEventListener("DOMContentLoaded", () => '
        f'mermaid.initialize({{startOnLoad:true, theme:"base", er:{{useMaxWidth:false}}, themeVariables:{{fontFamily:"system-ui, Segoe UI, sans-serif", fontSize:"14px"}}}}));</script>'
        # Mermaid tints only every other row with a classDef fill; paint every row so each table reads as one color
        + "<style>" + "".join(f"g.c_{t} .row-rect-odd > path:first-child, g.c_{t} .row-rect-even > path:first-child "
                              f"{{ fill:{KIND[t][1]} !important; }}" for t in TABLES) + "</style>")
(HERE / "company-facts.html").write_text(page("Company Facts", "Catalog", body, crumb="SEC Company Facts", head=head),
                                         encoding="utf-8")
print(f"wrote {HERE / 'company-facts.html'} ({'cached' if cached else 'measured'}); timings {M['timings']}")

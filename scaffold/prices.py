"""Prices sheet: Yahoo (trusted, served) with Tiingo (trusted, checks only, never served) for the Catalog (James, 2026-11-11).
Same order as the other sheets: open questions, population by ticker with a rows-per-year timeline, then Data:
schema, tables, columns, joins, and 5. Yahoo against Tiingo, using the pipeline's own transform/reconcile_all.sql
(the comparison check C12 samples from) over the served observations and the Tiingo snapshot the served version
keeps in inputs/. 5b: splits and dividends, from raw Yahoo (newest run per date) against the Tiingo snapshot.
Read-only; seconds. Writes prices.html.
Run: uv run python scaffold/prices.py"""
import csv
from pathlib import Path

import duckdb

from pharos.paths import data_root
from common import MERMAID, esc, num, now_et, page

HERE = Path(__file__).parent
ROOT = HERE.parent
DATA = data_root()
version = (DATA / "serving" / "CURRENT").read_text(encoding="utf-8").strip()
S = DATA / "serving" / version
YF_RAW = DATA / "raw" / "yf" / "prices"
TI_RAW = DATA / "raw" / "tiingo" / "prices"
for p in (S / "inputs" / "raw_tiingo_prices.parquet", S / "inputs" / "reclassified.parquet", YF_RAW, TI_RAW):
    if not p.exists():
        raise SystemExit(f"not on disk: {p}")

TABLES = {  # table -> (key, role, star-schema role, diagram color)
    "observations": (("series_id", "obs_date", "vintage"), "one Yahoo split-adjusted close (yf:close:<ticker>)",
                     "fact", "#fef9c3"),
    "series_catalog": (("series_id",), "one series per ticker (joins on series_id)", "dimension: series", "#dbeafe"),
    "tiingo_snapshot": (("ticker", "obs_date"), "Tiingo's full history as of the served run; checks only, never served",
                        "second source", "#e5e7eb"),
}

con = duckdb.connect()
con.execute(f"CREATE VIEW observations AS SELECT * FROM read_parquet('{(S / 'observations.parquet').as_posix()}') "
            "WHERE series_id LIKE 'yf:%'")
con.execute(f"CREATE VIEW series_catalog AS SELECT * FROM read_parquet('{(S / 'series_catalog.parquet').as_posix()}') "
            "WHERE series_id LIKE 'yf:%'")
con.execute(f"CREATE VIEW tiingo_snapshot AS SELECT * FROM "
            f"read_parquet('{(S / 'inputs' / 'raw_tiingo_prices.parquet').as_posix()}')")
# the names reconcile_all.sql reads
con.execute("CREATE VIEW raw_tiingo_prices AS SELECT * FROM tiingo_snapshot")
con.execute(f"CREATE VIEW reclassified AS SELECT * FROM read_parquet('{(S / 'inputs' / 'reclassified.parquet').as_posix()}')")
con.execute("CREATE VIEW reconcile_all AS "
            + (ROOT / "src" / "pharos" / "transform" / "reconcile_all.sql").read_text(encoding="utf-8"))
# raw Yahoo, newest run per (ticker, date): the splits and dividends serving doesn't carry
con.execute(f"""CREATE VIEW yf_raw AS SELECT * FROM read_parquet('{YF_RAW.as_posix()}/*.parquet', union_by_name=true)
                QUALIFY run_id = max(run_id) OVER (PARTITION BY ticker, obs_date)""")


def q(sql):
    return con.execute(sql).fetchall()


# ---------------------------------------------------------------- measure
T = {}
for t, (key, *_r) in TABLES.items():
    cols = [(c[0], c[1]) for c in q(f"DESCRIBE {t}")]
    n, dup = q(f"SELECT count(*), count(*) - count(DISTINCT ({', '.join(key)})) FROM {t}")[0]
    T[t] = {"rows": n, "cols": cols, "key": key, "dup": dup}

raw = {}
for name, folder in (("Yahoo", YF_RAW), ("Tiingo", TI_RAW)):
    files = sorted(folder.glob("*.parquet"))
    rows = q(f"SELECT count(*) FROM read_parquet('{folder.as_posix()}/*.parquet', union_by_name=true)")[0][0]
    newest = q(f"SELECT count(*) FROM read_parquet('{files[-1].as_posix()}')")[0][0]
    raw[name] = (len(files), rows, newest, sum(f.stat().st_size for f in files))

PROFILE, EXAMPLES = {}, {}
for t in TABLES:
    cols = T[t]["cols"]
    expr = ", ".join(f'count("{c}"), count(DISTINCT "{c}"), min("{c}")::VARCHAR, max("{c}")::VARCHAR' for c, _ in cols)
    r = q(f"SELECT {expr} FROM {t}")[0]
    PROFILE[t] = [dict(col=c, type=ty, filled=r[4 * i], distinct=r[4 * i + 1], min=r[4 * i + 2], max=r[4 * i + 3])
                  for i, (c, ty) in enumerate(cols)]
    sample = q(f"SELECT * FROM {t} USING SAMPLE 2000 ROWS (reservoir, 1)")
    EXAMPLES[t] = {}
    for i, (c, _) in enumerate(cols):
        seen = []
        for row in sample:
            if row[i] is not None and str(row[i]) not in seen:
                seen.append(str(row[i]))
            if len(seen) == 3:
                break
        EXAMPLES[t][c] = seen

watch = list(csv.DictReader((ROOT / "config" / "watchlist.csv").open(encoding="utf-8")))
con.execute("CREATE TABLE watch (ticker VARCHAR, trap VARCHAR, active_from VARCHAR, active_to VARCHAR)")
con.executemany("INSERT INTO watch VALUES (?, ?, ?, ?)",
                [(w["ticker"], w["trap"], w["active_from"], w["active_to"]) for w in watch])

POP = q("""
    WITH y AS (SELECT split_part(series_id, ':', 3) ticker, count(*) n, min(obs_date)::VARCHAR lo,
                      max(obs_date)::VARCHAR hi FROM observations GROUP BY 1),
         t AS (SELECT ticker, count(*) n, min(obs_date)::VARCHAR lo, max(obs_date)::VARCHAR hi
               FROM tiingo_snapshot GROUP BY 1),
         ev AS (SELECT ticker, count(*) FILTER (WHERE stock_splits > 0) splits,
                       count(*) FILTER (WHERE dividends > 0) divs FROM yf_raw GROUP BY 1)
    SELECT w.ticker, w.trap, y.n, y.lo, y.hi, t.n, t.lo, t.hi, ev.splits, ev.divs
    FROM watch w LEFT JOIN y USING (ticker) LEFT JOIN t USING (ticker) LEFT JOIN ev USING (ticker)
    ORDER BY w.ticker""")

# Yahoo rows per ticker per year: the timeline under the population
RY = q("SELECT split_part(series_id, ':', 3), year(obs_date), count(*) FROM observations GROUP BY 1, 2")

JOINS = [
    ("observations.series_id", "series_catalog", q(
        "SELECT count(*) FROM (SELECT DISTINCT series_id FROM observations) ANTI JOIN series_catalog USING (series_id)")[0][0],
     "series"),
    ("series_catalog.series_id", "observations", q(
        "SELECT count(*) FROM series_catalog ANTI JOIN (SELECT DISTINCT series_id FROM observations) USING (series_id)")[0][0],
     "series"),
    ("watchlist.csv ticker", "observations", q(
        "SELECT count(*) FROM watch ANTI JOIN (SELECT DISTINCT split_part(series_id, ':', 3) ticker FROM observations) "
        "USING (ticker)")[0][0], "tickers"),
    ("observations (ticker, obs_date)", "tiingo_snapshot", q(
        "SELECT count(*) FROM (SELECT split_part(series_id, ':', 3) ticker, obs_date FROM observations) "
        "ANTI JOIN tiingo_snapshot USING (ticker, obs_date)")[0][0], "dates"),
    ("tiingo_snapshot (ticker, obs_date), inside Yahoo's range", "observations", q(
        "WITH y AS (SELECT split_part(series_id, ':', 3) ticker, obs_date FROM observations), "
        "r AS (SELECT ticker, min(obs_date) lo, max(obs_date) hi FROM y GROUP BY 1) "
        "SELECT count(*) FROM (SELECT t.ticker, t.obs_date FROM tiingo_snapshot t JOIN r ON t.ticker = r.ticker "
        "AND t.obs_date BETWEEN r.lo AND r.hi) ANTI JOIN y USING (ticker, obs_date)")[0][0], "dates"),
]
MISSING = [r[0] for r in q("SELECT ticker FROM watch ANTI JOIN (SELECT DISTINCT split_part(series_id, ':', 3) ticker "
                           "FROM observations) USING (ticker) ORDER BY 1")]

TOL = 0.005  # C12's tolerance (D38)
REC = q(f"""
    SELECT ticker, count(*), count(*) FILTER (WHERE tiingo_close IS NULL),
           median(abs(rel_diff)), max(abs(rel_diff)), arg_max(obs_date::VARCHAR, abs(rel_diff)),
           arg_max(yahoo_close, abs(rel_diff)), arg_max(tiingo_close, abs(rel_diff)),
           count(*) FILTER (WHERE abs(rel_diff) > {TOL})
    FROM reconcile_all GROUP BY 1 ORDER BY 1""")

# over-tolerance dates by D41's shape rule: isolated = the common dates either side are both within tolerance
SHAPE = q(f"""
    WITH r AS (SELECT ticker, obs_date, abs(rel_diff) > {TOL} AS over,
                      lag(abs(rel_diff) > {TOL}) OVER w AS prev_over, lead(abs(rel_diff) > {TOL}) OVER w AS next_over
               FROM reconcile_all WINDOW w AS (PARTITION BY ticker ORDER BY obs_date))
    SELECT count(*), count(*) FILTER (WHERE prev_over IS FALSE AND next_over IS FALSE),
           min(year(obs_date)), max(year(obs_date)) FROM r WHERE over""")[0]

EVENTS = q("""
    WITH y AS (SELECT ticker, obs_date, stock_splits FROM yf_raw WHERE stock_splits > 0),
         t AS (SELECT ticker, obs_date, split_factor FROM tiingo_snapshot WHERE split_factor <> 1)
    SELECT ticker, obs_date::VARCHAR, y.stock_splits, t.split_factor
    FROM y FULL JOIN t USING (ticker, obs_date) ORDER BY ticker, obs_date""")
DIVS = q("""
    WITH y AS (SELECT ticker, obs_date, dividends FROM yf_raw WHERE dividends > 0),
         t AS (SELECT ticker, obs_date, div_cash FROM tiingo_snapshot WHERE div_cash > 0)
    SELECT ticker, count(y.dividends), count(t.div_cash), count(*) FILTER (WHERE y.dividends IS NOT NULL
           AND t.div_cash IS NOT NULL), count(*) FILTER (WHERE y.dividends IS NULL OR t.div_cash IS NULL)
    FROM y FULL JOIN t USING (ticker, obs_date) GROUP BY 1 ORDER BY 1""")

# ---------------------------------------------------------------- page
qfile = HERE / "prices-questions.md"
questions = [ln[2:] for ln in qfile.read_text(encoding="utf-8").splitlines() if ln.startswith("- ")]
q_html = "".join(f"<li>{esc(x)}</li>" for x in questions)


def span(lo, hi):
    return f"{lo} to {hi}" if lo else ""


pop_rows = "".join(
    f"<tr{' class=flag' if yn is None else ''}><td>{esc(tk)}</td><td class=sub>{esc(trap)}</td>"
    f"<td class=num>{num(yn)}</td><td>{span(ylo, yhi)}</td><td class=num>{num(tn)}</td><td>{span(tlo, thi)}</td>"
    f"<td class=num>{num(sp)}</td><td class=num>{num(dv)}</td></tr>"
    for tk, trap, yn, ylo, yhi, tn, tlo, thi, sp, dv in POP)
pop_rows += (f"<tr><td><b>Total</b></td><td></td><td class=num><b>{num(sum(r[2] or 0 for r in POP))}</b></td><td></td>"
             f"<td class=num><b>{num(sum(r[5] or 0 for r in POP))}</b></td><td></td>"
             f"<td class=num><b>{num(sum(r[8] or 0 for r in POP))}</b></td>"
             f"<td class=num><b>{num(sum(r[9] or 0 for r in POP))}</b></td></tr>")


def timeline(rows, unit):
    """One row per series on a shared year axis; bar height = count that year."""
    by = {}
    for s, y, n in rows:
        by.setdefault(s, {})[y] = n
    y0, y1 = min(r[1] for r in rows), max(r[1] for r in rows)
    top = max(r[2] for r in rows)
    lab, w_year, row_h, bar_h = 90, 30, 34, 26
    width = lab + (y1 - y0 + 1) * w_year
    out = []
    for i, s in enumerate(sorted(by)):
        base = i * row_h + bar_h
        out.append(f'<text x="0" y="{base - 6}" font-size="14" fill="#1d1d1b">{esc(s)}</text>')
        out.append(f'<line x1="{lab}" x2="{width}" y1="{base}" y2="{base}" stroke="#e3e1da"/>')
        for y, n in by[s].items():
            h = bar_h * n / top
            out.append(f'<rect x="{lab + (y - y0) * w_year + 2}" y="{base - h:.1f}" width="{w_year - 4}" height="{h:.1f}" '
                       f'fill="#2563eb"><title>{esc(s)} {y}: {n} {unit}</title></rect>')
    axis_y = len(by) * row_h + 6
    for y in range(y0, y1 + 1):
        if y % 5 == 0:
            out.append(f'<text x="{lab + (y - y0) * w_year + w_year / 2}" y="{axis_y + 10}" font-size="13" '
                       f'text-anchor="middle" fill="#5f5e58">{y}</text>')
    return (f'<p class=sub>Bar height: {unit} that year (tallest: {top}). Hover a bar for its count.</p>'
            f'<div class=wrap><svg width="{width}" height="{axis_y + 16}" role="img">{"".join(out)}</svg></div>')


def mtype(ty):
    return {"TIMESTAMP WITH TIME ZONE": "TIMESTAMPTZ"}.get(ty, ty).replace(" ", "_")


def entity(t):
    key = set(TABLES[t][0])
    lines = "".join(f"    {mtype(ty)} {c}{' PK' if c in key else ''}\n" for c, ty in T[t]["cols"])
    return f'  {t}["{t} ({TABLES[t][2]})"] {{\n{lines}  }}\n'


mmd = ("erDiagram\n" + "".join(entity(t) for t in TABLES)
       + '  series_catalog ||--o{ observations : "series_id"\n'
       + '  tiingo_snapshot |o--o| observations : "ticker, obs_date (checks only)"\n'
       + "".join(f"  classDef c_{t} fill:{TABLES[t][3]},stroke:#9ca3af,color:#1d1d1b\n  class {t} c_{t}\n" for t in TABLES))

table_rows = "".join(
    f"<tr{' class=flag' if T[t]['dup'] else ''}><td><b>{t}</b><div class=sub>{esc(role)}</div></td>"
    f"<td><span style='background:{color}; padding:0 4px'>{esc(kind)}</span></td>"
    f"<td class=num>{num(T[t]['rows'])}</td><td class=num>{len(T[t]['cols'])}</td>"
    f"<td>{esc(', '.join(key))}</td><td class=num>{num(T[t]['dup'])}</td></tr>"
    for t, (key, role, kind, color) in TABLES.items())
raw_line = "; ".join(f"{name}: {num(f)} runs, {num(n)} rows in all, {num(nw)} in the newest run, {b / 1e6:,.1f} MB"
                     for name, (f, n, nw, b) in raw.items())


def col_table(t):
    n = T[t]["rows"]
    rows = []
    for c in PROFILE[t]:
        ex = "<br>".join(esc(v if len(v) <= 40 else v[:39] + "...") for v in EXAMPLES[t].get(c["col"], []))
        rng = "" if c["min"] is None else f"{esc(str(c['min'])[:32])}<br>{esc(str(c['max'])[:32])}"
        rows.append(f"<tr><td>{esc(c['col'])}</td><td>{esc(c['type'])}</td><td class=num>{c['filled'] / n:.1%}</td>"
                    f"<td class=num>{num(c['distinct'])}</td><td>{rng}</td><td>{ex}</td></tr>")
    return (f"<h3>{t}: {num(n)} rows</h3><div class=wrap><table><tr><th>Column</th><th>Type</th><th>Filled</th>"
            f"<th>Distinct</th><th>Min / max</th><th>Examples</th></tr>{''.join(rows)}</table></div>")


join_rows = "".join(f"<tr{' class=flag' if n else ''}><td>{esc(a)}</td><td>{esc(b)}</td>"
                    f"<td class=num>{num(n)}</td><td>{esc(unit)}</td></tr>" for a, b, n, unit in JOINS)


def pct(x, d=4):
    if x is None:
        return ""
    return "under 0.0001%" if x < 1e-6 else f"{100 * x:.{d}f}%"


def f2(x):
    return "" if x is None else f"{x:,.2f}"


rec_rows = "".join(
    f"<tr{' class=flag' if over else ''}><td>{esc(tk)}</td><td class=num>{num(n)}</td><td class=num>{num(nul)}</td>"
    f"<td class=num>{pct(med)}</td><td class=num>{pct(mx)}</td><td>{esc(when or '')}</td>"
    f"<td class=num>{f2(yc)}</td><td class=num>{f2(tc)}</td><td class=num>{num(over)}</td></tr>"
    for tk, n, nul, med, mx, when, yc, tc, over in REC)
ev_rows = "".join(
    f"<tr{' class=flag' if (ys is None) != (tf is None) or (ys and tf and abs(ys / tf - 1) > 1e-4) else ''}>"
    f"<td>{esc(tk)}</td><td>{d}</td><td class=num>{'' if ys is None else f'{ys:g}'}</td>"
    f"<td class=num>{'' if tf is None else f'{tf:g}'}</td></tr>" for tk, d, ys, tf in EVENTS)
div_rows = "".join(
    f"<tr{' class=flag' if odd else ''}><td>{esc(tk)}</td><td class=num>{num(y)}</td><td class=num>{num(t)}</td>"
    f"<td class=num>{num(both)}</td><td class=num>{num(odd)}</td></tr>" for tk, y, t, both, odd in DIVS)

body = f"""
<div class="head"><h1>Prices: Yahoo and Tiingo</h1>
<div class="stamps"><span>Built {now_et()}</span></div></div>
<h3>Open questions ({len(questions)})</h3>
<ul>{q_html}</ul>

<h3>Population: {len(POP)} watchlist tickers</h3>
<div class=wrap><table><tr><th>Ticker</th><th>Why it's on the watchlist</th><th>Yahoo closes (served)</th><th>Yahoo span</th>
<th>Tiingo rows</th><th>Tiingo span</th><th>Splits (Yahoo)</th><th>Dividends (Yahoo)</th></tr>{pop_rows}</table></div>

<h3>Yahoo closes by year</h3>
{timeline(RY, 'closes')}

<h2>Data</h2>
<h3>1. Schema</h3>
<p class=sub>Served: Yahoo's split-adjusted close only (yf rows of observations and series_catalog). Tiingo is the
snapshot the served version keeps for its checks. PK: the key each table is unique on.</p>
<pre class="mermaid">{esc(mmd)}</pre>

<h3>2. Tables</h3>
<table><tr><th>Table</th><th>Type</th><th>Rows</th><th>Cols</th><th>Key</th><th>Duplicates on key</th></tr>
{table_rows}</table>
<p class=sub>Raw. {esc(raw_line)}.</p>

<h3>3. Columns</h3>
{''.join(col_table(t) for t in TABLES)}

<h3>4. Joins</h3>
<table><tr><th>From</th><th>To</th><th>No match</th><th>Counted in</th></tr>{join_rows}</table>
<p class=sub>Watchlist tickers with no Yahoo closes: {esc(', '.join(MISSING)) or 'none'}.</p>

<h3>5. Yahoo against Tiingo, every common date</h3>
<p class=sub>Split-adjusted close, Yahoo's served value against Tiingo's close adjusted by Tiingo's own split
factors: the pipeline's reconcile_all.sql, which check C12 samples from. Over tolerance: more than 0.5%:
{SHAPE[0]} dates, {SHAPE[2]}-{SHAPE[3]}, of which {SHAPE[1]} isolated (both neighboring dates within 0.5%), the kind
D41 reports but doesn't block. "Under 0.0001%" is float rounding: the two sources agree.
Highlighted: a ticker with any date over.</p>
<div class=wrap><table><tr><th>Ticker</th><th>Common dates</th><th>No Tiingo adjusted close</th><th>Difference (median, absolute)</th>
<th>Largest</th><th>On</th><th>Yahoo</th><th>Tiingo</th><th>Over 0.5%</th></tr>{rec_rows}</table></div>

<h3>5b. Splits, both sources</h3>
<p class=sub>Highlighted: one source only, or ratios that differ by more than 0.01%.</p>
<table><tr><th>Ticker</th><th>Date</th><th>Yahoo ratio</th><th>Tiingo factor</th></tr>{ev_rows}</table>

<h3>5c. Dividends, both sources</h3>
<p class=sub>Ex-dates with a dividend. Only one source: a date one source has and the other doesn't.</p>
<table><tr><th>Ticker</th><th>Yahoo</th><th>Tiingo</th><th>Both</th><th>Only one source</th></tr>{div_rows}</table>
"""
head = (f'<script src="{MERMAID}"></script><script>document.addEventListener("DOMContentLoaded", () => '
        f'mermaid.initialize({{startOnLoad:true, theme:"base", er:{{useMaxWidth:false}}, themeVariables:{{fontFamily:"system-ui, Segoe UI, sans-serif", fontSize:"14px"}}}}));</script>'
        + "<style>" + "".join(f"g.c_{t} .row-rect-odd > path:first-child, g.c_{t} .row-rect-even > path:first-child "
                              f"{{ fill:{TABLES[t][3]} !important; }}" for t in TABLES) + "</style>")
(HERE / "prices.html").write_text(page("Prices", "Catalog", body, crumb="Prices: Yahoo and Tiingo", head=head),
                                  encoding="utf-8")
print(f"wrote {HERE / 'prices.html'}: {len(POP)} tickers; missing from Yahoo: {MISSING}; over tolerance {SHAPE}")

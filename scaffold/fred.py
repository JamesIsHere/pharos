"""FRED / ALFRED sheet: the FRED source for the Catalog (James, 2026-11-11). Same sections as the Company Facts sheet:
open questions, population by series, then Data: schema diagram, tables, columns, joins, and 5. Revisions (FRED's
own question: how much values move after they first print). Everything is measured from the served version and the
newest raw run at build time; 32k rows, so no cache. Read-only. Writes fred.html.
Run: uv run python scaffold/fred.py"""
from pathlib import Path

import duckdb

from pharos.paths import data_root
from common import MERMAID, esc, num, now_et, page

HERE = Path(__file__).parent
DATA = data_root()
version = (DATA / "serving" / "CURRENT").read_text(encoding="utf-8").strip()
S = DATA / "serving" / version
RAW = sorted((DATA / "raw" / "fred" / "observations").glob("*.parquet"))
RAW_SERIES = sorted((DATA / "raw" / "fred" / "series").glob("*.parquet"))
if not RAW or not RAW_SERIES:
    raise SystemExit(f"no raw FRED files under {DATA / 'raw' / 'fred'}")

TABLES = {  # table -> (key, role, star-schema role, diagram color)
    "observations": (("series_id", "obs_date", "vintage"), "one value of one period, as it stood from one vintage",
                     "fact", "#fef9c3"),
    "series_catalog": (("series_id",), "one series: name, units, frequency (joins on series_id)",
                       "dimension: series", "#dbeafe"),
}

con = duckdb.connect()
con.execute(f"CREATE VIEW observations AS SELECT * FROM read_parquet('{(S / 'observations.parquet').as_posix()}') "
            "WHERE series_id LIKE 'fred:%'")
con.execute(f"CREATE VIEW series_catalog AS SELECT * FROM read_parquet('{(S / 'series_catalog.parquet').as_posix()}') "
            "WHERE series_id LIKE 'fred:%'")
con.execute(f"CREATE VIEW raw_latest AS SELECT * FROM read_parquet('{RAW[-1].as_posix()}')")


def q(sql):
    return con.execute(sql).fetchall()


# ---------------------------------------------------------------- measure
T = {}
for t, (key, *_rest) in TABLES.items():
    cols = [(c[0], c[1]) for c in q(f"DESCRIBE {t}")]
    k = ", ".join(key)
    n, dup = q(f"SELECT count(*), count(*) - count(DISTINCT ({k})) FROM {t}")[0]
    T[t] = {"rows": n, "cols": cols, "key": key, "dup": dup}

raw_files = len(RAW)
raw_rows_all = q(f"SELECT count(*) FROM read_parquet('{(DATA / 'raw' / 'fred' / 'observations').as_posix()}/*.parquet')")[0][0]
raw_rows_latest = q("SELECT count(*) FROM raw_latest")[0][0]
raw_bytes = sum(p.stat().st_size for p in RAW) + sum(p.stat().st_size for p in RAW_SERIES)

PROFILE = {}
EXAMPLES = {}
for t in TABLES:
    cols = T[t]["cols"]
    expr = ", ".join(f'count("{c}"), count(DISTINCT "{c}"), min("{c}")::VARCHAR, max("{c}")::VARCHAR'
                     for c, _ in cols)
    r = q(f"SELECT {expr} FROM {t}")[0]
    PROFILE[t] = [dict(col=c, type=ty, filled=r[4 * i], distinct=r[4 * i + 1], min=r[4 * i + 2], max=r[4 * i + 3])
                  for i, (c, ty) in enumerate(cols)]
    rows = q(f"SELECT * FROM {t} USING SAMPLE 2000 ROWS (reservoir, 1)")
    EXAMPLES[t] = {}
    for i, (c, _) in enumerate(cols):
        seen = []
        for row in rows:
            v = row[i]
            if v is not None and str(v) not in seen:
                seen.append(str(v))
            if len(seen) == 3:
                break
        EXAMPLES[t][c] = seen

POP = q("""
    SELECT o.series_id, c.name, c.frequency, c.adjustment, c.units, count(*) n, count(DISTINCT o.obs_date),
           min(o.obs_date)::VARCHAR, max(o.obs_date)::VARCHAR, count(DISTINCT o.vintage),
           min(o.vintage)::VARCHAR, max(o.vintage)::VARCHAR, count(*) FILTER (WHERE o.value IS NULL)
    FROM observations o LEFT JOIN series_catalog c USING (series_id)
    GROUP BY ALL ORDER BY o.series_id""")

JOINS = [
    ("observations.series_id", "series_catalog", q(
        "SELECT count(*) FROM (SELECT DISTINCT series_id FROM observations) ANTI JOIN series_catalog USING (series_id)"
    )[0][0], "series"),
    ("series_catalog.series_id", "observations", q(
        "SELECT count(*) FROM series_catalog ANTI JOIN (SELECT DISTINCT series_id FROM observations) USING (series_id)"
    )[0][0], "series"),
    (f"raw run {RAW[-1].stem} (fred_id, obs_date, realtime_start)", "observations", q(
        "SELECT count(*) FROM (SELECT DISTINCT 'fred:' || fred_id series_id, obs_date, realtime_start vintage FROM raw_latest) "
        "ANTI JOIN observations USING (series_id, obs_date, vintage)")[0][0], "rows"),
    ("observations", f"raw run {RAW[-1].stem}", q(
        "SELECT count(*) FROM observations ANTI JOIN (SELECT DISTINCT 'fred:' || fred_id series_id, obs_date, "
        "realtime_start vintage FROM raw_latest) USING (series_id, obs_date, vintage)")[0][0], "rows"),
]

# Revisions, per period: every non-null print in vintage order. A period first printed on the series' earliest vintage
# was loaded when ALFRED's record began, not printed live, so its "first print" is not one: those periods are left out.
# A period whose first and latest prints carry different units was rebased (chained-dollar base year), not revised.
REV = q("""
    WITH per AS (
        SELECT series_id, obs_date, count(*) prints,
               arg_min(value, vintage) first_val, arg_min(units, vintage) first_units, min(vintage) first_vintage,
               arg_max(value, vintage) last_val, arg_max(units, vintage) last_units
        FROM observations WHERE value IS NOT NULL GROUP BY 1, 2),
    floor AS (SELECT series_id, min(vintage) v0 FROM observations GROUP BY 1),
    live AS (SELECT per.*, first_units <> last_units rebased,
                    CASE WHEN first_units = last_units AND first_val <> 0
                         THEN 100 * (last_val / first_val - 1) END pct
             FROM per JOIN floor USING (series_id) WHERE first_vintage > v0)
    SELECT series_id, count(*) periods, count(*) FILTER (WHERE rebased) rebased,
           median(prints) FILTER (WHERE NOT rebased), max(prints) FILTER (WHERE NOT rebased),
           avg((prints > 1)::INT) FILTER (WHERE NOT rebased),
           median(abs(pct)), max(abs(pct)),
           arg_max(obs_date::VARCHAR, abs(pct)), arg_max(first_val, abs(pct)), arg_max(last_val, abs(pct)),
           arg_max(pct, abs(pct))
    FROM live GROUP BY 1 ORDER BY 1""")

# one period's prints, the example in section 5: GDP's latest period with more than one print
EXAMPLE_PERIOD = q("""
    SELECT obs_date FROM observations WHERE series_id = 'fred:GDP' AND value IS NOT NULL
    GROUP BY 1 HAVING count(*) > 1 ORDER BY 1 DESC LIMIT 1""")
EXAMPLE = q(f"""
    SELECT obs_date::VARCHAR, vintage::VARCHAR, value, units FROM observations
    WHERE series_id = 'fred:GDP' AND obs_date = DATE '{EXAMPLE_PERIOD[0][0]}' ORDER BY vintage""") if EXAMPLE_PERIOD else []

# ---------------------------------------------------------------- page
questions = [ln[2:] for ln in (HERE / "fred-questions.md").read_text(encoding="utf-8").splitlines() if ln.startswith("- ")]
q_html = "".join(f"<li>{esc(x)}</li>" for x in questions)

pop_rows = "".join(
    f"<tr><td>{esc(s[5:])}</td><td>{esc(name)}</td><td>{esc(f)}</td><td>{esc(a)}</td><td>{esc(u)}</td>"
    f"<td class=num>{num(n)}</td><td class=num>{num(d)}</td><td>{lo} to {hi}</td><td class=num>{num(nv)}</td>"
    f"<td>{v0} to {v1}</td><td class=num>{num(nul)}</td></tr>"
    for s, name, f, a, u, n, d, lo, hi, nv, v0, v1, nul in POP)
pop_rows += (f"<tr><td><b>Total</b></td><td></td><td></td><td></td><td></td><td class=num><b>{num(sum(r[5] for r in POP))}</b></td>"
             f"<td></td><td></td><td></td><td></td><td class=num><b>{num(sum(r[12] for r in POP))}</b></td></tr>")


def mtype(ty):
    return {"TIMESTAMP WITH TIME ZONE": "TIMESTAMPTZ"}.get(ty, ty).replace(" ", "_")


def entity(t):
    key = set(T[t]["key"])
    lines = "".join(f"    {mtype(ty)} {c}{' PK' if c in key else ''}\n" for c, ty in T[t]["cols"])
    return f'  {t}["{t} ({TABLES[t][2]})"] {{\n{lines}  }}\n'


mmd = ("erDiagram\n" + "".join(entity(t) for t in TABLES)
       + '  series_catalog ||--o{ observations : "series_id"\n'
       + "".join(f"  classDef c_{t} fill:{TABLES[t][3]},stroke:#9ca3af,color:#1d1d1b\n  class {t} c_{t}\n" for t in TABLES))

table_rows = "".join(
    f"<tr{' class=flag' if T[t]['dup'] else ''}><td><b>{t}</b><div class=sub>{esc(role)}</div></td>"
    f"<td><span style='background:{color}; padding:0 4px'>{esc(kind)}</span></td>"
    f"<td class=num>{num(T[t]['rows'])}</td><td class=num>{len(T[t]['cols'])}</td>"
    f"<td>{esc(', '.join(T[t]['key']))}</td><td class=num>{num(T[t]['dup'])}</td></tr>"
    for t, (_, role, kind, color) in TABLES.items())


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


def f2(x):
    return "" if x is None else f"{x:,.2f}"


rev_rows = "".join(
    f"<tr><td>{esc(s[5:])}</td><td class=num>{num(per)}</td><td class=num>{num(reb)}</td>"
    f"<td class=num>{'' if med_p is None else f'{med_p:g}'}</td><td class=num>{num(max_p)}</td>"
    f"<td class=num>{'' if share is None else f'{share:.0%}'}</td>"
    f"<td class=num>{'' if med is None else f'{med:.2f}%'}</td>"
    f"<td class=num>{'' if mx is None else f'{sgn:+.2f}%'}</td><td>{esc(when or '')}</td>"
    f"<td class=num>{f2(fv)}</td><td class=num>{f2(lv)}</td></tr>"
    for s, per, reb, med_p, max_p, share, med, mx, when, fv, lv, sgn in REV)
ex_rows = "".join(f"<tr><td>{d}</td><td>{v}</td><td class=num>{f2(x)}</td><td>{esc(u)}</td></tr>" for d, v, x, u in EXAMPLE)

# vintages timeline: one row per series on a shared year axis, a bar per year = vintages released that year
VY = q("SELECT series_id, year(vintage) y, count(DISTINCT vintage) FROM observations GROUP BY 1, 2")
vy = {}
for s, y, n in VY:
    vy.setdefault(s, {})[y] = n


def vintage_timeline():
    y0, y1 = min(y for _, y, _ in VY), max(y for _, y, _ in VY)
    top = max(n for _, _, n in VY)
    lab, w_year, row_h, bar_h = 90, 14, 40, 30
    width = lab + (y1 - y0 + 1) * w_year
    out = []
    for i, s in enumerate(sorted(vy)):
        base = i * row_h + bar_h
        out.append(f'<text x="0" y="{base - 8}" font-size="14" fill="#1d1d1b">{esc(s[5:])}</text>')
        out.append(f'<line x1="{lab}" x2="{width}" y1="{base}" y2="{base}" stroke="#e3e1da"/>')
        for y, n in vy[s].items():
            h = bar_h * n / top
            out.append(f'<rect x="{lab + (y - y0) * w_year + 1}" y="{base - h:.1f}" width="{w_year - 2}" height="{h:.1f}" '
                       f'fill="#2563eb"><title>{esc(s[5:])} {y}: {n} vintages</title></rect>')
    axis_y = len(vy) * row_h + 6
    for y in range(y0 - y0 % 5 + 5, y1 + 1, 5):
        out.append(f'<text x="{lab + (y - y0) * w_year + w_year / 2}" y="{axis_y + 10}" font-size="13" '
                   f'text-anchor="middle" fill="#5f5e58">{y}</text>')
    return (f'<p class=sub>Bar height: vintages released that year (tallest: {top}). Hover a bar for its count.</p>'
            f'<div class=wrap><svg width="{width}" height="{axis_y + 16}" role="img">{"".join(out)}</svg></div>')

body = f"""
<div class="head"><h1>FRED / ALFRED</h1>
<div class="stamps"><span>Built {now_et()}</span></div></div>
<h3>Open questions ({len(questions)})</h3>
<ul>{q_html}</ul>

<h3>Population: {len(POP)} series, every vintage</h3>
<p class=sub>Pages built on these series: <a href="m3.html">Manufacturing (M3)</a>, <a href="qfr.html">Census QFR</a>, <a href="housing.html">Housing pipeline</a>, <a href="housing-need.html">Housing supply vs need</a>.</p>
<div class=wrap><table><tr><th>Series</th><th>Name</th><th>Freq</th><th>Adj</th><th>Units (latest)</th><th>Rows</th>
<th>Periods</th><th>Periods span</th><th>Vintages</th><th>Vintages span</th><th>Null values</th></tr>{pop_rows}</table></div>

<h3>Vintages by year released</h3>
{vintage_timeline()}

<h2>Data</h2>
<h3>1. Schema</h3>
<p class=sub>The two served tables, FRED rows only (series_id starts with fred:). PK: the key each table is unique on.</p>
<pre class="mermaid">{esc(mmd)}</pre>

<h3>2. Tables</h3>
<table><tr><th>Table</th><th>Type</th><th>Rows</th><th>Cols</th><th>Key</th><th>Duplicates on key</th></tr>
{table_rows}</table>
<p class=sub>Raw: {num(raw_files)} runs, {num(raw_rows_all)} observation rows in all, {raw_bytes / 1e6:,.1f} MB.
Each run lands the full vintage history again ({num(raw_rows_latest)} rows in the newest run); staging keeps one row
per key.</p>

<h3>3. Columns</h3>
{''.join(col_table(t) for t in TABLES)}

<h3>4. Joins</h3>
<table><tr><th>From</th><th>To</th><th>No match</th><th>Counted in</th></tr>{join_rows}</table>

<h3>5. Revisions: how far values move after their first print</h3>
<p class=sub>Per period: its first print against its latest. Left out: periods first printed on the series' earliest
vintage (loaded when ALFRED's record began, not printed live). Rebased: first and latest prints in different units
(a new base year); counted, not compared. Prints include the first.</p>
<div class=wrap><table><tr><th>Series</th><th>Periods printed live</th><th>Rebased</th><th>Prints per period (median)</th>
<th>Most prints</th><th>Share revised</th><th>Change first to latest (median, absolute)</th><th>Largest change</th>
<th>Period</th><th>First print</th><th>Latest</th></tr>{rev_rows}</table></div>

<h3>Example: GDP, period {esc(EXAMPLE[0][0]) if EXAMPLE else ''}, every print</h3>
<table><tr><th>Period</th><th>Vintage</th><th>Value</th><th>Units</th></tr>{ex_rows}</table>
"""
head = (f'<script src="{MERMAID}"></script><script>document.addEventListener("DOMContentLoaded", () => '
        f'mermaid.initialize({{startOnLoad:true, theme:"base", er:{{useMaxWidth:false}}, themeVariables:{{fontFamily:"system-ui, Segoe UI, sans-serif", fontSize:"14px"}}}}));</script>'
        + "<style>" + "".join(f"g.c_{t} .row-rect-odd > path:first-child, g.c_{t} .row-rect-even > path:first-child "
                              f"{{ fill:{TABLES[t][3]} !important; }}" for t in TABLES) + "</style>")
(HERE / "fred.html").write_text(page("FRED / ALFRED", "Catalog", body, crumb="FRED / ALFRED", head=head), encoding="utf-8")
print(f"wrote {HERE / 'fred.html'}: {len(POP)} series, {num(T['observations']['rows'])} rows")

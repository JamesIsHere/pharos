"""Scaffold: UN World Population Prospects, population of every country by year (quick lane, 2026-11-14).

One file from the UN Population Division: total population by country, one value a year as of July 1, in thousands.
It is downloaded once to <data root>/tmp/scaffold/wpp/ (a revision comes out every two years; REVISION below names
the one read) and nothing is stored in Pharos. The file carries estimates and, from the year the projection variants
start, projections; this page shows the Medium variant, which is the only one that runs the whole span, and shades
the projected years. Countries are the file's Country/Area rows, matched to config/countries.csv by ISO3 code; one
that does not match stops the build. Regions, income groups and the world total are counted and left out.
Run: uv run python scaffold/population.py"""
import urllib.request
from pathlib import Path

import duckdb
from common import esc, now_et, num, page

from pharos.paths import data_root

HERE = Path(__file__).parent
REVISION = "WPP2024"
NAME = f"{REVISION}_TotalPopulationBySex.csv.gz"
URL = f"https://population.un.org/wpp/assets/Excel%20Files/1_Indicator%20(Standard)/CSV_FILES/{NAME}"
COUNTRIES = HERE.parent / "config" / "countries.csv"

folder = data_root() / "tmp" / "scaffold" / "wpp"
folder.mkdir(parents=True, exist_ok=True)
gz = folder / NAME
if not gz.exists():
    part = gz.with_name(gz.name + ".part")
    with urllib.request.urlopen(urllib.request.Request(URL, headers={"User-Agent": "pharos-scaffold"}), timeout=300) as r, \
            open(part, "wb") as out:
        for block in iter(lambda: r.read(1 << 20), b""):
            out.write(block)
    part.replace(gz)

con = duckdb.connect()
con.execute(f"CREATE TABLE f AS SELECT * FROM read_csv('{gz.as_posix()}')")
need = {"ISO3_code", "LocTypeName", "Location", "Variant", "Time", "MidPeriod", "PopTotal"}
have = {r[0] for r in con.execute("DESCRIBE f").fetchall()}
if need - have:
    raise SystemExit(f"{gz}: columns missing: {sorted(need - have)}")
con.execute("CREATE TABLE w AS SELECT ISO3_code AS code, Time AS y, PopTotal AS v, MidPeriod AS mid FROM f "
            "WHERE LocTypeName = 'Country/Area' AND Variant = 'Medium'")
con.execute(f"CREATE TABLE k AS SELECT * FROM read_csv('{COUNTRIES.as_posix()}', all_varchar=true)")

n, n_null, n_dup = con.execute("SELECT count(*), count(*) FILTER (v IS NULL OR code IS NULL), "
                               "count(*) - count(DISTINCT (code, y)) FROM w").fetchone()
if not n or n_null or n_dup:
    raise SystemExit(f"{gz}: {n} country rows, {n_null} with no code or value, {n_dup} repeated country-years")
unknown = con.execute("SELECT DISTINCT code FROM w WHERE code NOT IN (SELECT code FROM k) ORDER BY 1").fetchall()
if unknown:
    raise SystemExit(f"{gz}: country codes not in config/countries.csv: {[u[0] for u in unknown]}")
if con.execute("SELECT count(*) FROM w WHERE mid <> y + 0.5").fetchone()[0]:
    raise SystemExit(f"{gz}: a value is not dated mid-year (July 1)")
# Projections start in the first year the other variants cover; every year before it is an estimate.
first_proj = con.execute("SELECT min(Time) FROM f WHERE Variant <> 'Medium'").fetchone()[0]
variants = con.execute("SELECT count(DISTINCT Variant) FROM f").fetchone()[0]
others = con.execute("SELECT count(DISTINCT LocID) FROM f WHERE LocTypeName IS DISTINCT FROM 'Country/Area'").fetchone()[0]
LO, HI = con.execute("SELECT min(y), max(y) FROM w").fetchone()
EST = first_proj - 1
if not LO < EST < HI:
    raise SystemExit(f"{gz}: projections start in {first_proj}, outside the file's years {LO} to {HI}")
years = list(range(LO, HI + 1))

data = {}
for code, y, v in con.execute("SELECT code, y, v FROM w").fetchall():
    data.setdefault(code, {})[y] = v
names = dict(con.execute("SELECT code, name FROM k").fetchall())
order = sorted(data, key=lambda c: -data[c][EST])   # largest first in the last estimate year


def cell(c, y):
    v = data[c].get(y)
    return f'<td class="num{" est" if y > EST else ""}">{"" if v is None else f"{v:,.0f}"}</td>'


head = "".join(f'<th{" id=last" if y == EST else ""}>{y}</th>' for y in years)
rows = "".join(f"<tr><td class=cty>{esc(names[c])}</td><td>{c}</td>{''.join(cell(c, y) for y in years)}</tr>"
               for c in order)

CSS = """<style>
.panel { font-size:14px; }
.panel td, .panel th { padding:3px 7px; }
.panel .cty { position:sticky; left:0; background:var(--bg); white-space:nowrap; }
td.est { background:var(--box); color:var(--muted); }
</style>"""

body = f"""<div class="head"><h1>UN World Population Prospects</h1>
<div class="stamps"><span>Page built {now_et()}</span><span>Revision {REVISION[3:]}</span></div></div>

<table><tr><th>Series</th><th>Countries</th><th>Other locations in the file</th><th>Values</th><th>Years</th>
<th>Estimates through</th><th>Projections</th><th>Dated</th><th>Unit</th></tr>
<tr><td>Total population</td><td class=num>{len(data)}</td><td class=num>{others}</td><td class=num>{num(n)}</td>
<td>{LO} to {HI}</td><td>{EST}</td><td>{first_proj} to {HI}, Medium variant (1 of {variants} in the file)</td>
<td>July 1 of each year</td><td>Thousands of people</td></tr></table>
<p class=sub>One value: country, year, population in thousands.</p>
<table><tr><th>Country</th><th>Year</th><th>Value</th></tr>
<tr><td>USA</td><td>{EST}</td><td class=num>{data['USA'][EST]:,.3f}</td></tr></table>

<h2>Population, thousands</h2>
<p class=sub>{len(data)} countries, largest first by {EST} population. Shaded years are projections ({first_proj} on).
The table opens at {EST}; scroll left for earlier years, back to {LO}, and right for projections to {HI}.</p>
<div class="wrap"><table class="panel"><tr><th class=cty>Country</th><th>Code</th>{head}</tr>{rows}</table></div>
<script>document.querySelectorAll(".wrap").forEach(w => {{
  const t = w.querySelector("#last");
  if (t) w.scrollLeft = t.offsetLeft + t.offsetWidth - w.clientWidth;
}});</script>"""

(HERE / "population.html").write_text(page("World Population", "Catalog", body, crumb="UN World Population Prospects",
                                           head=CSS), encoding="utf-8")
print(f"wrote {HERE / 'population.html'}: {len(data)} countries, years {LO}-{HI}, estimates through {EST}")

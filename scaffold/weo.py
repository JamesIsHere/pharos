"""Scaffold: IMF World Economic Outlook, GDP and unemployment for every country (2026-11-12; stored since D174).

Read from the served tables weo_observations and weo_series, the newest edition served (`uv run pharos imf` lands
it); nothing is fetched here. Annual: NGDPD (GDP, current prices, US dollars) and LUR (unemployment rate, percent).
Values are in units (US GDP 2024 = 29,381,025,000,000): SCALE is a display hint, not a multiplier. Each country and
series carries LATEST_ACTUAL_ANNUAL_DATA; years after it are IMF estimates or forecasts and are shaded. Countries are
the IMF codes in config/countries.csv (imf_code), shown by that table's code and name; every other code must be an IMF
country group (G followed by digits or X), counted in the population table and left out of the country tables.
GDP per person divides NGDPD by the UN's population for the same country and year (World Population Prospects, the
served table wpp_population; `uv run pharos un` lands it). Population is as of July 1, in thousands; from the UN's
first projection year on it is the Medium projection.
Run: uv run python scaffold/weo.py"""
import csv
import re
from pathlib import Path

import duckdb
import imf
from common import esc, now_et, page

HERE = Path(__file__).parent
SERIES = {"NGDPD": "GDP, current prices, US$ billions", "LUR": "Unemployment rate, percent"}
COUNTRIES = HERE.parent / "config" / "countries.csv"

# Countries: config/countries.csv, by IMF code.
with open(COUNTRIES, encoding="utf-8") as f:
    country = {r["imf_code"]: r for r in csv.DictReader(f) if r["imf_code"]}
names = {k: r["name"] for k, r in country.items()}

# Data: one series per country and indicator; the notes carry the latest actual year and the labor definition.
data, meta = imf.fetch(list(SERIES))
updated = imf.updates(list(SERIES))
EDITION = imf.edition()

for ind, m in data.items():
    unknown = sorted(c for c in m if c not in country and not re.fullmatch("G[0-9X]+", c))
    if unknown:
        raise SystemExit(f"weo: {ind} codes neither in config/countries.csv nor IMF groups: {unknown}")
if len([c for c in data["NGDPD"] if c in country]) < 190:
    raise SystemExit("weo: fewer than 190 countries have GDP")


def is_country(c):
    return c in country


def la_label(ind, c):
    return meta[ind].get(c, {}).get("LATEST_ACTUAL_ANNUAL_DATA") or ""


def latest_actual(ind, c):
    return imf.latest_actual(meta, ind, c)


def la_cell(ind, c):
    v, y = la_label(ind, c), latest_actual(ind, c)
    if y is None:
        return f"{esc(v)} (no calendar rule)" if v else ""
    return str(y) if v.isdigit() else f"{y} ({esc(v)})"


years = sorted({y for m in data.values() for obs in m.values() for y in obs})
LO, HI = years[0], years[-1]

# Population: what came back.
pop = []
for ind, label in SERIES.items():
    m = data[ind]
    cs = [c for c in m if is_country(c)]
    no_la = [c for c in cs if latest_actual(ind, c) is None]
    fy = [c for c in cs if la_label(ind, c) and not la_label(ind, c).isdigit()]
    las = sorted(latest_actual(ind, c) for c in cs if latest_actual(ind, c) is not None)
    pop.append(f"<tr><td>{ind}</td><td>{esc(label)}</td><td class=num>{len(cs)}</td>"
               f"<td class=num>{len(m) - len(cs)}</td><td class=num>{sum(len(m[c]) for c in cs):,}</td>"
               f"<td>{min(y for c in cs for y in m[c])} to {max(y for c in cs for y in m[c])}</td>"
               f"<td>{las[0]} to {las[-1]}</td><td class=num>{len(fy)}</td><td class=num>{len(no_la)}</td></tr>")

us_la = latest_actual("NGDPD", "USA")
example = (f"<tr><td>USA</td><td>NGDPD</td><td>{us_la}</td><td class=num>{data['NGDPD']['USA'][us_la]:,.0f}</td>"
           f"<td>{us_la}</td></tr>")

# Countries ordered by GDP in the last year every large economy has as an actual (the US's latest actual year).
ORDER = sorted((c for c in data["NGDPD"] if is_country(c)),
               key=lambda c: -data["NGDPD"][c].get(us_la, 0.0))
ORDER += sorted(c for c in data["LUR"] if is_country(c) and c not in data["NGDPD"])

# GDP per person: NGDPD over the UN's population (thousands, July 1) for the same country and year.
con = duckdb.connect()
imf.population(con)
people = {}
for code, y, v in con.execute("SELECT iso3_code, year, pop_total FROM wpp "
                              "WHERE loc_type_name = 'Country/Area' AND variant = 'Medium'").fetchall():
    people.setdefault(code, {})[y] = v
pop_first_proj = con.execute("SELECT min(year) FROM wpp WHERE variant <> 'Medium'").fetchone()[0]
gdp_countries = [c for c in data["NGDPD"] if is_country(c)]
no_people = sorted(names[c] for c in gdp_countries if country[c]["code"] not in people)
data["PC"] = {}
for c in gdp_countries:
    ppl = people.get(country[c]["code"])
    if ppl is None:
        continue
    missing = [y for y in data["NGDPD"][c] if y not in ppl]
    if missing:
        raise SystemExit(f"weo: {c} has GDP for {missing} but the UN file has no population for those years")
    data["PC"][c] = {y: v / (ppl[y] * 1000) for y, v in data["NGDPD"][c].items()}
PC_ORDER = sorted(data["PC"], key=lambda c: -data["PC"][c].get(us_la, float("-inf")))
RANK = {c: i for i, c in enumerate((c for c in PC_ORDER if us_la in data["PC"][c]), 1)}


def table(ind, fmt, extra_head="", extra=lambda c: "", la_ind=None, order=None):
    """la_ind: the IMF series whose latest actual year shades this table (a derived table has none of its own).
    order: the countries, in row order; the GDP table's order when not given."""
    la_ind, order = la_ind or ind, order or ORDER
    head = "".join(f"<th>{y}</th>" for y in years)
    rows = []
    for c in order:
        obs = data[ind].get(c)
        if obs is None:
            continue
        la = latest_actual(la_ind, c)
        cells = "".join(
            f'<td class="num{" est" if la is not None and y > la else ""}">{fmt(obs[y]) if y in obs else ""}</td>'
            for y in years)
        rows.append(f"<tr><td class=cty>{esc(names[c])}</td><td>{country[c]['code']}</td>{extra(c)}"
                    f"<td class=num>{la_cell(la_ind, c)}</td>{cells}</tr>")
    return (f'<div class="wrap"><table class="panel"><tr><th class=cty>Country</th><th>Code</th>{extra_head}'
            f"<th>Latest actual</th>{head}</tr>{''.join(rows)}</table></div>"), len(rows)


def gdp_fmt(v):
    b = v / 1e9
    return f"{b:,.0f}" if b >= 100 else f"{b:,.1f}"


gdp_html, n_gdp = table("NGDPD", gdp_fmt)
lur_html, n_lur = table("LUR", lambda v: f"{v:.1f}", "<th>Definition</th>",
                        lambda c: f"<td>{esc(meta['LUR'].get(c, {}).get('LABOR_SECTOR_EMPLOYMENT_TYPE', ''))}</td>")
pc_html, n_pc = table("PC", lambda v: f"{v:,.0f}", f"<th>Rank, {us_la}</th>",
                      lambda c: f"<td class=num>{RANK.get(c, '')}</td>", la_ind="NGDPD", order=PC_ORDER)

upd = sorted(d.isoformat() for _, d in updated if d)
no_upd = sorted({c for c, d in updated if not d})
print("series without an update date:", no_upd)
CSS = """<style>
.panel { font-size:14px; }
.panel td, .panel th { padding:3px 7px; }
.panel .cty { position:sticky; left:0; background:var(--bg); white-space:nowrap; }
td.est { background:var(--box); color:var(--muted); }
</style>"""

body = f"""<div class="head"><h1>IMF World Economic Outlook</h1>
<div class="stamps"><span>Page built {now_et()}</span><span>Edition published {EDITION}</span><span>Newest country update {esc(upd[-1])}</span></div></div>

<table><tr><th>Code</th><th>Series</th><th>Countries</th><th>Country groups</th><th>Values</th><th>Years</th>
<th>Latest actual year</th><th>Fiscal-year countries</th><th>No latest actual</th></tr>{"".join(pop)}</table>
<p class=sub>One value: country, series, year, value in US dollars, latest actual year.</p>
<table><tr><th>Country</th><th>Series</th><th>Year</th><th>Value</th><th>Latest actual</th></tr>{example}</table>

<h2>GDP, current prices, US$ billions</h2>
<p class=sub>{n_gdp} countries, largest first by {us_la} GDP. Shaded years come after the country's latest actual
year: IMF estimates and forecasts. A fiscal-year country shows its fiscal year beside the calendar column the IMF files
it under. Both tables open at the newest years; scroll left for earlier ones, back to {LO}.</p>
{gdp_html}

<h2>Unemployment rate, percent</h2>
<p class=sub>{n_lur} countries, in the GDP table's order. Definition is the IMF's label for each country: its own
national definition, or the ILO's harmonized one. Shaded years: estimates and forecasts, as above.</p>
{lur_html}

<h2 id="per-person">GDP per person, US$</h2>
<p class=sub>{n_pc} countries, ranked by {us_la} GDP per person, highest first. Each value is the GDP above divided by
the UN's population for that country and year (<a href="population.html">UN World Population Prospects</a>, as of
July 1). Shaded years are IMF GDP estimates and forecasts, as above; population from {pop_first_proj} on is the UN's
Medium projection.{(" No UN population, so not in this table: " + esc(", ".join(no_people)) + ".") if no_people else ""}</p>
{pc_html}
<script>document.querySelectorAll(".wrap").forEach(w => {{ if (w.querySelector(".panel")) w.scrollLeft = w.scrollWidth; }});</script>"""

(HERE / "weo.html").write_text(page("World Economic Outlook", "Catalog", body, crumb="IMF World Economic Outlook",
                                    head=CSS), encoding="utf-8")
print(f"wrote {HERE / 'weo.html'}: GDP {n_gdp} countries, unemployment {n_lur}, per person {n_pc}, years {LO}-{HI}")

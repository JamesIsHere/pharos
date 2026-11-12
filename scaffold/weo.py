"""Scaffold: IMF World Economic Outlook, GDP and unemployment for every country (quick lane, 2026-11-12).

Fetched from the IMF's SDMX API (api.imf.org, no key) when the page is built; nothing is stored. The live WEO
dataflow, annual: NGDPD (GDP, current prices, US dollars) and LUR (unemployment rate, percent). Values arrive in
units (US GDP 2024 = 29,381,025,000,000): SCALE is a display hint, not a multiplier. Each country and series carries
LATEST_ACTUAL_ANNUAL_DATA; years after it are IMF estimates or forecasts and are shaded. Countries are the IMF codes
in config/countries.csv (imf_code), shown by that table's code and name; every other code must be an IMF country
group (G followed by digits or X), counted in the population table and left out of the country tables.
Run: uv run python scaffold/weo.py"""
import csv
import re
import urllib.request
import xml.etree.ElementTree as XT
from datetime import datetime
from pathlib import Path

from common import esc, now_et, page

HERE = Path(__file__).parent
API = "https://api.imf.org/external/sdmx/2.1/"
UA = {"User-Agent": "pharos-scaffold"}
SERIES = {"NGDPD": "GDP, current prices, US$ billions", "LUR": "Unemployment rate, percent"}
COUNTRIES = HERE.parent / "config" / "countries.csv"


def get(path):
    with urllib.request.urlopen(urllib.request.Request(API + path, headers=UA), timeout=300) as r:
        return r.read()


def local(tag):
    return tag.rsplit("}", 1)[-1]


# Countries: config/countries.csv, by IMF code.
with open(COUNTRIES, encoding="utf-8") as f:
    country = {r["imf_code"]: r for r in csv.DictReader(f) if r["imf_code"]}
names = {k: r["name"] for k, r in country.items()}

# Data: one series per country and indicator; group blocks carry the latest actual year and the labor definition.
root = XT.fromstring(get("data/IMF.RES,WEO/." + "+".join(SERIES) + ".A"))
data = {k: {} for k in SERIES}
meta = {k: {} for k in SERIES}
updated = set()
for e in root.iter():
    t = local(e.tag)
    if t == "Series":
        ind, c = e.get("INDICATOR"), e.get("COUNTRY")
        obs = {int(o.get("TIME_PERIOD")): float(o.get("OBS_VALUE")) for o in e if local(o.tag) == "Obs"}
        data[ind][c] = obs
        updated.add((c, e.get("COUNTRY_UPDATE_DATE")))
    elif t == "Group" and e.get("COUNTRY") and e.get("INDICATOR") in SERIES:
        meta[e.get("INDICATOR")][e.get("COUNTRY")] = e.attrib

for ind, m in data.items():
    if not m:
        raise SystemExit(f"weo: no series came back for {ind}")
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
    """The calendar-year column of the latest actual. A fiscal or solar year label (FY2024/25) maps by the rule the
    series' own notes state: FY(t-1/t) = CY(t) puts FY2024/25 in 2025; FY(t/t+1) = CY(t) puts it in 2024. No stated
    rule: None, and the row is not shaded. A label of any other shape stops the build."""
    v = la_label(ind, c)
    if not v:
        return None
    if v.isdigit():
        return int(v)
    m = re.fullmatch(r"[FS]Y(\d{4})/(\d{2})", v)
    if not m:
        raise SystemExit(f"weo: {ind} {c} latest actual {v!r} is neither a year nor FYyyyy/yy")
    rules = set(re.findall(r"[FS]Y\s*\((t-1/t|t/t\+1)\)\s*=\s*CY\s*\(t\)", meta[ind][c].get("METHODOLOGY_NOTES") or ""))
    if len(rules) > 1:
        raise SystemExit(f"weo: {ind} {c} notes state two fiscal-year rules: {rules}")
    if not rules:
        return None
    return int(m.group(1)) + (1 if rules.pop() == "t-1/t" else 0)


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
order = sorted((c for c in data["NGDPD"] if is_country(c)),
               key=lambda c: -data["NGDPD"][c].get(us_la, 0.0))
order += sorted(c for c in data["LUR"] if is_country(c) and c not in data["NGDPD"])


def table(ind, fmt, extra_head="", extra=lambda c: ""):
    head = "".join(f"<th>{y}</th>" for y in years)
    rows = []
    for c in order:
        obs = data[ind].get(c)
        if obs is None:
            continue
        la = latest_actual(ind, c)
        cells = "".join(
            f'<td class="num{" est" if la is not None and y > la else ""}">{fmt(obs[y]) if y in obs else ""}</td>'
            for y in years)
        rows.append(f"<tr><td class=cty>{esc(names[c])}</td><td>{country[c]['code']}</td>{extra(c)}"
                    f"<td class=num>{la_cell(ind, c)}</td>{cells}</tr>")
    return (f'<div class="wrap"><table class="panel"><tr><th class=cty>Country</th><th>Code</th>{extra_head}'
            f"<th>Latest actual</th>{head}</tr>{''.join(rows)}</table></div>"), len(rows)


def gdp_fmt(v):
    b = v / 1e9
    return f"{b:,.0f}" if b >= 100 else f"{b:,.1f}"


gdp_html, n_gdp = table("NGDPD", gdp_fmt)
lur_html, n_lur = table("LUR", lambda v: f"{v:.1f}", "<th>Definition</th>",
                        lambda c: f"<td>{esc(meta['LUR'].get(c, {}).get('LABOR_SECTOR_EMPLOYMENT_TYPE', ''))}</td>")

upd = sorted(datetime.strptime(d, "%m/%d/%Y").date().isoformat() for _, d in updated if d)
no_upd = sorted({c for c, d in updated if not d})
print("series without an update date:", no_upd)
CSS = """<style>
.panel { font-size:14px; }
.panel td, .panel th { padding:3px 7px; }
.panel .cty { position:sticky; left:0; background:var(--bg); white-space:nowrap; }
td.est { background:var(--box); color:var(--muted); }
</style>"""

body = f"""<div class="head"><h1>IMF World Economic Outlook</h1>
<div class="stamps"><span>Fetched {now_et()}</span><span>Newest country update {esc(upd[-1])}</span></div></div>

<table><tr><th>Code</th><th>Series</th><th>Countries</th><th>Country groups</th><th>Values</th><th>Years</th>
<th>Latest actual year</th><th>Fiscal-year countries</th><th>No latest actual</th></tr>{"".join(pop)}</table>
<p class=sub>One value: country, series, year, value in US dollars, latest actual year.</p>
<table><tr><th>Country</th><th>Series</th><th>Year</th><th>Value</th><th>Latest actual</th></tr>{example}</table>

<h2>GDP, current prices, US$ billions</h2>
<p class=sub>{n_gdp} countries, largest first by {us_la} GDP. Shaded years come after the country's latest actual
year: IMF estimates and forecasts. A fiscal-year country shows its fiscal year beside the calendar column the IMF files
it under.</p>
{gdp_html}

<h2>Unemployment rate, percent</h2>
<p class=sub>{n_lur} countries, in the GDP table's order. Definition is the IMF's label for each country: its own
national definition, or the ILO's harmonized one. Shaded years: estimates and forecasts, as above.</p>
{lur_html}"""

(HERE / "weo.html").write_text(page("World Economic Outlook", "Catalog", body, crumb="IMF World Economic Outlook",
                                    head=CSS), encoding="utf-8")
print(f"wrote {HERE / 'weo.html'}: GDP {n_gdp} countries, unemployment {n_lur}, years {LO}-{HI}")

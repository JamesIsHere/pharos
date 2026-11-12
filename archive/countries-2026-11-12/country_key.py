"""Archived page: how the country key is built and used. Reads the two committed CSVs and the served SEC data.
Run from the pharos root: uv run python archive/countries-2026-11-12/country_key.py"""
import csv
import sys
from collections import Counter
from pathlib import Path

import duckdb

ROOT = Path("C:/Users/james/Desktop/workshop/pharos")
sys.path.insert(0, str(ROOT / "scaffold"))
from common import CSS, MERMAID, esc, now_et  # noqa: E402

OUT = Path(__file__).parent / "country_key.html"
cty = list(csv.DictReader(open(ROOT / "config/countries.csv", encoding="utf-8")))
sec = list(csv.DictReader(open(ROOT / "config/sec_country_codes.csv", encoding="utf-8")))
by_code = {r["code"]: r for r in cty}

# ---- Reconciliation: each source's codes, from the files the build downloaded (same scratch folder) ----
import json  # noqa: E402
import re  # noqa: E402
import xml.etree.ElementTree as XT  # noqa: E402

HERE = Path(__file__).parent
L = "{http://www.w3.org/XML/1998/namespace}lang"
imf = {}
for cl in XT.parse(HERE / "inputs" / "weo_dsd.xml").getroot().iter():
    if cl.tag.endswith("Codelist") and cl.get("id") == "CL_WEO_COUNTRY":
        for c in cl:
            if c.tag.endswith("Code"):
                imf[c.get("id")] = [n.text for n in c if n.tag.endswith("Name") and n.get(L) == "en"][0]
IMF_REGIONS = {"AFR", "APD", "EUR", "MCD", "SDS", "WHD"}
imf_groups = sorted(k for k in imf if not re.fullmatch("[A-Z]{3}", k))
imf_g = [k for k in imf_groups if k.startswith("G")]
imf_u = [k for k in imf_groups if k.startswith("U")]
assert len(imf_g) + len(imf_u) == len(imf_groups), [k for k in imf_groups if k[0] not in "GU"]
imf_regions = sorted(k for k in imf if k in IMF_REGIONS)
imf_cty = [k for k in imf if re.fullmatch("[A-Z]{3}", k) and k not in IMF_REGIONS]
wb_all = json.load(open(HERE / "inputs" / "wb_countries.json", encoding="utf-8"))
wb_agg = [x for x in wb_all if x["region"]["value"] == "Aggregates"]
wb_cty = [x for x in wb_all if x["region"]["value"] != "Aggregates"]
seccodes = json.load(open(HERE / "inputs" / "sec_codes.json", encoding="utf-8"))
sec_terr = [c for c in seccodes if c in ("GU", "PR", "VI")]
sec_unk = [c for c in seccodes if c == "XX"]
sec_states = [c for c in seccodes if c.isalpha() and c not in sec_terr + sec_unk]
sec_can = [c for c, n in seccodes.items() if ", CANADA" in n or n.startswith("CANADA")]
sec_cty = [c for c in seccodes if c not in sec_terr + sec_unk + sec_states + sec_can]
sec_retired = [r for r in sec if r["sec_name"].startswith("(retired)")]

# Ties: every bridge must land on what the CSVs hold.
n_imf_rows = sum(1 for r in cty if r["imf_code"])
n_wb_rows = sum(1 for r in cty if r["wb_code"])
assert len(imf) == len(imf_groups) + len(imf_regions) + len(imf_cty) and len(imf_cty) == n_imf_rows, "IMF bridge"
assert len(wb_all) == len(wb_agg) + len(wb_cty) and len(wb_cty) == n_wb_rows, "World Bank bridge"
assert len(seccodes) == len(sec_states) + len(sec_terr) + len(sec_can) + len(sec_unk) + len(sec_cty), "SEC bridge"
assert len(seccodes) + len(sec_retired) == len(sec), "SEC rows"

in_imf = {r["code"] for r in cty if r["imf_code"]}
in_wb = {r["code"] for r in cty if r["wb_code"]}
sec_to = {}
for r in sec:
    if r["country"]:
        sec_to.setdefault(r["country"], []).append(r["sec_code"])
sets = {"IMF and World Bank": sorted(in_imf & in_wb), "IMF only": sorted(in_imf - in_wb),
        "World Bank only": sorted(in_wb - in_imf), "neither (added for the SEC)": sorted(set(by_code) - in_imf - in_wb)}
assert sum(len(v) for v in sets.values()) == len(cty), "union"
sec_country_targets = {r["country"] for r in sec if r["country"]}
no_sec = sorted(set(by_code) - sec_country_targets)


def bridge(title, start, steps, end_label, end):
    rows = f"<tr><td>{esc(title)}</td><td class=num>{start}</td><td></td></tr>"
    rows += "".join(f"<tr><td>&minus; {esc(lb)}</td><td class=num>&minus;{n}</td><td class=sub>{esc(ex)}</td></tr>"
                    for lb, n, ex in steps)
    rows += f"<tr class=tot><td>= {esc(end_label)}</td><td class=num>{end}</td><td></td></tr>"
    return f"<table class=bridge><tr><th></th><th>Codes</th><th>Examples</th></tr>{rows}</table>"


def ex(names, n=4):
    return ", ".join(names[:n]) + (" ..." if len(names) > n else "")


former_imf = sum(1 for k in imf_cty if by_code.get({"KOS": "XKX", "WBG": "PSE"}.get(k, k), {}).get("status") == "former")
b_imf = bridge("IMF country list", len(imf), [
    ("IMF groups (G codes)", len(imf_g), ex([imf[k] for k in ("G001", "G119", "G110")])),
    ("UN geographic regions (U codes)", len(imf_u), ex([imf[k] for k in ("U019", "U015", "U202")])),
    ("regions with three-letter codes", len(imf_regions), ", ".join(imf_regions))],
    f"countries and territories ({former_imf} former)", len(imf_cty))
b_wb = bridge("World Bank country list", len(wb_all), [
    ("aggregates", len(wb_agg), ex([x["name"].strip() for x in wb_agg if x["id"] in ("SSF", "HIC", "EMU", "WLD")]))],
    "countries and territories", len(wb_cty))
b_sec = bridge("SEC code page", len(seccodes), [
    ("US states and DC", len(sec_states), ex(sec_states, 5)),
    ("Canadian provinces and federal", len(sec_can), "Alberta, British Columbia, Ontario ... and Canada (federal)"),
    ("unknown", len(sec_unk), "XX")],
    "country and territory codes (incl. GU, PR, VI)", len(sec_cty) + len(sec_terr))

union_rows = "".join(
    f"<tr><td>{esc(k)}</td><td class=num>{len(v)}</td><td class=sub>"
    f"{esc(ex([by_code[c]['name'] for c in v], 6))}</td></tr>" for k, v in sets.items())
union = (f"<table class=bridge><tr><th>countries.csv rows, by which list has them</th><th>Rows</th><th>Examples</th></tr>"
         f"{union_rows}<tr class=tot><td>= countries.csv</td><td class=num>{len(cty)}</td><td></td></tr></table>")
sec_rows_tbl = (f"<table class=bridge><tr><th>sec_country_codes.csv rows</th><th>Rows</th><th>Examples</th></tr>"
                f"<tr><td>codes on the SEC page</td><td class=num>{len(seccodes)}</td><td></td></tr>"
                f"<tr><td>+ retired codes still in the data</td><td class=num>+{len(sec_retired)}</td>"
                f"<td class=sub>{esc(ex([r['sec_code'] for r in sec_retired], 6))}</td></tr>"
                f"<tr class=tot><td>= sec_country_codes.csv</td><td class=num>{len(sec)}</td><td></td></tr>"
                f"<tr><td class=sub>countries.csv rows at least one SEC code points to</td>"
                f"<td class=num>{len(sec_country_targets)}</td><td></td></tr>"
                f"<tr><td class=sub>countries.csv rows no SEC code points to</td><td class=num>{len(no_sec)}</td>"
                f"<td class=sub>{esc(ex([by_code[c]['name'] for c in no_sec], 5))}</td></tr></table>")

RECON = f"""flowchart LR
  i["IMF<br/><b>{len(imf)}</b> codes"]:::data --> ig["{len(imf_groups) + len(imf_regions)} groups and regions<br/>left out"]:::gate
  i --> ic["<b>{len(imf_cty)}</b> countries"]:::work
  w["World Bank<br/><b>{len(wb_all)}</b> codes"]:::data --> wg["{len(wb_agg)} aggregates<br/>left out"]:::gate
  w --> wc["<b>{len(wb_cty)}</b> countries"]:::work
  s["SEC<br/><b>{len(seccodes)}</b> codes on its list<br/>+ {len(sec_retired)} retired codes<br/>still in company data"]:::data --> ss["{len(sec_states)} US states and DC<br/>{len(sec_can)} Canadian provinces<br/>point at USA / CAN"]:::work
  s --> sc["{len(sec_cty) + len(sec_terr)} country codes"]:::work
  s --> su["{len(sec_unk)} unknown (XX)"]:::gate
  s --> sr["{len(sec_retired)} retired codes<br/>{sum(1 for r in sec_retired if r['country'])} placed by filers' addresses<br/>{sum(1 for r in sec_retired if not r['country'])} unknown"]:::work
  ic --> u["merge on ISO code<br/>{len(sets['IMF and World Bank'])} in both<br/>{len(sets['IMF only'])} IMF only<br/>{len(sets['World Bank only'])} World Bank only<br/>{len(sets['neither (added for the SEC)'])} added for SEC"]:::work
  wc --> u
  u --> c["countries.csv<br/><b>{len(cty)}</b> rows"]:::good
  ss --> k["sec_country_codes.csv<br/><b>{len(sec)}</b> rows"]:::good
  sc --> k
  su --> k
  sr --> k
  k -- "every row points<br/>at one country (or none)" --> c
  classDef work fill:#F3F7FF,stroke:#000,stroke-width:2px,color:#000
  classDef data fill:#FFFEE5,stroke:#000,stroke-width:2px,color:#000
  classDef gate fill:#FCE9E6,stroke:#000,stroke-width:2px,color:#000
  classDef good fill:#EFFFF2,stroke:#000,stroke-width:2px,color:#000"""

def lst(rows):
    return "".join(f"<tr><td>{esc(k)}</td><td>{esc(n)}</td></tr>" for k, n in rows)


imf_left = (f'<tr><th colspan=2>IMF groups: G codes ({len(imf_g)})</th></tr>' + lst((k, imf[k]) for k in imf_g)
            + f'<tr><th colspan=2>UN geographic regions: U codes ({len(imf_u)})</th></tr>' + lst((k, imf[k]) for k in imf_u)
            + f'<tr><th colspan=2>Regions with three-letter codes ({len(imf_regions)})</th></tr>'
            + lst((k, imf[k]) for k in imf_regions))
wb_left = lst(sorted(((x["id"], x["name"].strip()) for x in wb_agg), key=lambda t: t[1]))
LEFT_OUT = f"""<h3>Left out: what the red boxes hold</h3>
<div class="cols lo">
<div><b>IMF: {len(imf_groups) + len(imf_regions)} groups and regions</b>
<div class=roster><table><tr><th>code</th><th>name</th></tr>{imf_left}</table></div></div>
<div><b>World Bank: {len(wb_agg)} aggregates</b>, by name
<div class=roster><table><tr><th>code</th><th>name</th></tr>{wb_left}</table></div></div>
</div>"""

roster = "".join(
    f"<tr><td>{r['code']}</td><td>{esc(r['name'])}</td><td>{r['status']}</td><td>{r['imf_code']}</td>"
    f"<td>{r['wb_code']}</td><td>{esc(' '.join(sorted(sec_to.get(r['code'], []))[:8]))}"
    f"{' ...' if len(sec_to.get(r['code'], [])) > 8 else ''}</td></tr>" for r in cty)

# The SEC rules, in the order build_countries.py applies them; counted from the CSV it wrote.
HAND = {"B8", "C5", "E8", "G0", "Y3", "H2", "I6", "K3", "1P", "1N", "N1", "N5", "1U", "P5", "1X", "R8", "U8", "U7",
        "U9", "V0", "V1", "2B", "L9", "V6", "F5", "W8", "X5", "D8", "X8", "T7"}


def rule(r):
    c, n = r["sec_code"], r["sec_name"]
    if r["basis"] == "inferred":
        return "6 retired code: country from filers' addresses"
    if r["basis"] == "unknown":
        return "7 no country (unknown)"
    if c in ("GU", "PR", "VI"):
        return "1 US territory: its own ISO code"
    if c.isalpha():
        return "2 US state or DC: USA + state code"
    if ", CANADA" in n or n.startswith("CANADA"):
        return "3 Canadian province or federal: CAN"
    if c in HAND:
        return "4 by hand (name differs)"
    return "5 name match against IMF / World Bank names"


rules = Counter(rule(r) for r in sec)
rule_rows = "".join(f"<tr><td>{esc(k[2:])}</td><td class=num>{v}</td></tr>" for k, v in sorted(rules.items()))

status = Counter((r["status"], r["iso"]) for r in cty)
both = sum(1 for r in cty if r["imf_code"] and r["wb_code"])
imf_only = sum(1 for r in cty if r["imf_code"] and not r["wb_code"])
wb_only = sum(1 for r in cty if r["wb_code"] and not r["imf_code"])
neither = sum(1 for r in cty if not r["wb_code"] and not r["imf_code"])


def rows_of(table, keys, cols):
    look = {r[cols[0]]: r for r in table}
    return "".join("<tr>" + "".join(f"<td>{esc(look[k][c])}</td>" for c in cols) + "</tr>" for k in keys)


cty_cols = ["code", "name", "iso", "status", "ended", "imf_code", "wb_code"]
cty_sample = rows_of(cty, ["USA", "CYM", "DEU", "PSE", "XKX", "TWN", "CHI", "SUN", "YUG"], cty_cols)
sec_cols = ["sec_code", "sec_name", "country", "subdivision", "basis"]
sec_sample = rows_of(sec, ["DE", "NV", "PR", "A1", "Z4", "E9", "X0", "W8", "I8", "LO", "XX"], sec_cols)

SQL = """WITH s AS (
  SELECT cik, name, upper(state_of_incorporation) AS sec_code
  FROM serving_submission_details
  WHERE exchanges IS NOT NULL AND exchanges <> '[]'          -- listed on an exchange
)
SELECT c.code, c.name AS country, c.imf_code,
       count(*)                               AS companies,
       count(*) FILTER (WHERE k.subdivision = 'DE') AS delaware
FROM s
JOIN read_csv('config/sec_country_codes.csv', all_varchar=true) k
  ON k.sec_code = s.sec_code                                -- SEC code  -> country key
JOIN read_csv('config/countries.csv', all_varchar=true) c
  ON c.code = k.country                                     -- country key -> name, IMF, World Bank
GROUP BY ALL
ORDER BY companies DESC
LIMIT 12"""

con = duckdb.connect(str(ROOT / "data/catalog.duckdb"), read_only=True)
con.execute(f"SET file_search_path='{ROOT.as_posix()}'")
res = con.execute(SQL).fetchall()
res_rows = "".join(f"<tr><td>{a}</td><td>{esc(b)}</td><td>{c}</td><td class=num>{d:,}</td><td class=num>{e:,}</td></tr>"
                   for a, b, c, d, e in res)
cover = con.execute("""
  WITH s AS (SELECT upper(state_of_incorporation) sec_code FROM serving_submission_details
             WHERE exchanges IS NOT NULL AND exchanges <> '[]')
  SELECT count(*) total,
         count(*) FILTER (WHERE sec_code = '') blank,
         count(*) FILTER (WHERE k.country IS NOT NULL AND k.country <> '') mapped,
         count(*) FILTER (WHERE k.sec_code IS NOT NULL AND coalesce(k.country,'') = '') no_country,
         count(*) FILTER (WHERE sec_code <> '' AND k.sec_code IS NULL) unmatched
  FROM s LEFT JOIN read_csv('config/sec_country_codes.csv', all_varchar=true) k USING (sec_code)""").fetchone()

DIAGRAM = """flowchart LR
  imf["IMF<br/>WEO country list<br/>344 codes"]:::data
  wb["World Bank<br/>country list<br/>296 entries"]:::data
  sec["SEC<br/>EDGAR state and<br/>country codes<br/>309 codes"]:::data
  addr["SEC submissions<br/>filers' addresses<br/>(retired codes)"]:::data
  b["scripts/build_countries.py<br/>drop regions and aggregates<br/>fix IMF codes (KOS, WBG)<br/>apply SEC rules 1-7<br/>stop on anything unplaced"]:::work
  c["config/countries.csv<br/>261 rows<br/>key: code"]:::good
  k["config/sec_country_codes.csv<br/>323 rows<br/>key: sec_code"]:::good
  weo["WEO page<br/>joins on imf_code"]:::cmd
  q["SQL over SEC data<br/>joins on sec_code"]:::cmd
  imf --> b
  wb --> b
  sec --> b
  addr -. "probe, written into<br/>the script by hand" .-> b
  b --> c
  b --> k
  k -- "country = code" --> c
  c --> weo
  k --> q
  c --> q
  classDef cmd fill:#F6EFFF,stroke:#000,stroke-width:2px,color:#000
  classDef work fill:#F3F7FF,stroke:#000,stroke-width:2px,color:#000
  classDef data fill:#FFFEE5,stroke:#000,stroke-width:2px,color:#000
  classDef good fill:#EFFFF2,stroke:#000,stroke-width:2px,color:#000"""

PY = '''for c, n in sorted(sec.items()):                 # every code on the SEC page
    if c == "XX":                   country = ""                        # 7 unknown
    elif c in SEC_US_TERRITORIES:   country = SEC_US_TERRITORIES[c]     # 1 GU PR VI
    elif c.isalpha():               country, sub = "USA", c             # 2 states + DC
    elif ", CANADA" in n:           country, sub = "CAN", c             # 3 provinces
    elif c in SEC_BY_HAND:          country = SEC_BY_HAND[c]            # 4 e.g. W8 TURKEY -> TUR
    else:                                                              # 5 name match
        m = by_name.get(norm(n), set())        # norm("KOREA, REPUBLIC OF") = "korearepublicof"
        if len(m) != 1:
            raise SystemExit(f"SEC {c} {n!r} matches {m}: add it to SEC_BY_HAND")
        country = m.pop()
# 6 retired codes come from SEC_RETIRED, e.g. "I8": ("DEU", "", "94 filers: Dusseldorf, Essen ...")'''

body = f"""<div class="head"><h1>Country key</h1>
<div class="stamps"><span>Page built {now_et()}</span><span>Archived page</span></div></div>

<h2>From codes to countries</h2>
<p class=sub>Each list counts codes, not countries. Red boxes are codes that are not countries.</p>
<pre class="mermaid">{esc(RECON)}</pre>
{LEFT_OUT}

<h3>Step 1: each list down to its countries</h3>
<div class=cols>{b_imf}{b_wb}{b_sec}</div>

<h3>Step 2: the three country sets merged</h3>
<div class=cols>{union}{sec_rows_tbl}</div>

<h3>All {len(cty)} countries</h3>
<div class=roster><table><tr><th>code</th><th>name</th><th>status</th><th>IMF code</th><th>World Bank code</th>
<th>SEC codes pointing here</th></tr>{roster}</table></div>

<table><tr><th>File</th><th>Rows</th><th>Key</th><th>One row is</th></tr>
<tr><td>config/countries.csv</td><td class=num>{len(cty)}</td><td>code</td><td>a country or territory, current or former</td></tr>
<tr><td>config/sec_country_codes.csv</td><td class=num>{len(sec)}</td><td>sec_code</td><td>one EDGAR state or country code</td></tr></table>

<h2>Pipeline</h2>
<pre class="mermaid">{esc(DIAGRAM)}</pre>

<h2>countries.csv</h2>
<div class=cols><table><tr><th>Rows</th><th>Count</th></tr>
<tr><td>current, ISO code</td><td class=num>{status[('current', 'yes')]}</td></tr>
<tr><td>current, no ISO code</td><td class=num>{status[('current', 'no')]}</td></tr>
<tr><td>former</td><td class=num>{status[('former', 'yes')] + status[('former', 'no')]}</td></tr>
<tr><td>IMF and World Bank code</td><td class=num>{both}</td></tr>
<tr><td>IMF code only</td><td class=num>{imf_only}</td></tr>
<tr><td>World Bank code only</td><td class=num>{wb_only}</td></tr>
<tr><td>neither</td><td class=num>{neither}</td></tr></table>
<table><tr>{''.join(f'<th>{c}</th>' for c in cty_cols)}</tr>{cty_sample}</table></div>

<h2>sec_country_codes.csv</h2>
<div class=cols><table><tr><th>Rule, in order</th><th>Codes</th></tr>{rule_rows}
<tr><td><b>total</b></td><td class=num><b>{len(sec)}</b></td></tr></table>
<table><tr>{''.join(f'<th>{c}</th>' for c in sec_cols)}</tr>{sec_sample}</table></div>

<h3>The rules in code (scripts/build_countries.py, condensed)</h3>
<pre class=code>{esc(PY)}</pre>

<h2>Using the keys: SQL</h2>
<p class=sub>Listed companies by country of incorporation: the SEC code joins to sec_country_codes, whose country
joins to countries. imf_code is the column that joins on to IMF data such as the WEO page.</p>
<pre class=code>{esc(SQL)}</pre>
<div class=cols><table><tr><th>code</th><th>country</th><th>imf_code</th><th>companies</th><th>of which Delaware</th></tr>{res_rows}</table>
<table><tr><th>Listed companies</th><th>Count</th></tr>
<tr><td>all</td><td class=num>{cover[0]:,}</td></tr>
<tr><td>incorporation code maps to a country</td><td class=num>{cover[2]:,}</td></tr>
<tr><td>code blank in SEC data</td><td class=num>{cover[1]:,}</td></tr>
<tr><td>code is XX, E7 or L1 (no country)</td><td class=num>{cover[3]:,}</td></tr>
<tr><td>code not in sec_country_codes</td><td class=num>{cover[4]:,}</td></tr></table></div>"""

head = (f'<script src="{MERMAID}"></script><script>document.addEventListener("DOMContentLoaded", () => '
        f'mermaid.initialize({{startOnLoad:true, theme:"base", flowchart:{{useMaxWidth:true, htmlLabels:true}}, '
        f'themeVariables:{{fontFamily:"system-ui, Segoe UI, sans-serif", fontSize:"14px"}}}}));</script>'
        '<style>pre.code { background:var(--box); border:1px solid var(--line); padding:10px 12px; font:13px/1.45 '
        'Consolas, monospace; overflow-x:auto; } .cols table { align-self:flex-start; } table.bridge tr.tot td { border-top:2px solid var(--fg); font-weight:700; } .lo > div { flex:1 1 560px; min-width:0; } .lo .roster { display:block; margin-top:6px; } .roster { max-height:640px; overflow:auto; border:1px solid var(--line); display:inline-block; }</style>')
OUT.write_text(f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Country key</title>
<style>{CSS}</style>{head}</head><body><nav><b>COUNTRIES</b><a href="../../scaffold/archive.html">Scaffold</a><a class=on href="country_key.html">Data</a><a href="charts.html">Charts</a><a href="trusted.html">Trusted</a></nav><main>{body}</main></body></html>""", encoding="utf-8")
print(OUT, "| sum check:", cover[0] == cover[1] + cover[2] + cover[3] + cover[4])

"""Builds the country tables in config/ (2026-11-12). Run: uv run python scripts/build_countries.py

config/countries.csv: one row per country or territory, current or former. Key `code` is ISO 3166-1 alpha-3
where ISO has one (iso = yes), else the World Bank's code (Kosovo XKX) or the IMF's (iso = no). Columns: code, name,
iso, status (current|former), ended (year a former entity ceased; from prior knowledge, not a fetched source),
imf_code, wb_code. A country group or region is never a row.

config/sec_country_codes.csv: every EDGAR state/country code, current or retired-but-in-use, mapped to a country
code. Columns: sec_code, sec_name, country, subdivision (US state or Canadian province code, else blank), basis:
  sec list       on the SEC's current code page; country by name or by the US/Canada rule
  inferred       retired code, not on the SEC page; country inferred from the filers' own addresses (2026-11-12)
  unknown        no country (SEC's XX, or a retired code whose filers are spread over several countries)

Sources, fetched when this runs: the IMF WEO country codelist (api.imf.org, no key), the World Bank country list
(api.worldbank.org, no key), the SEC's EDGAR state and country code page (Pharos's own SEC fetcher). Anything the
rules below cannot place stops the build. The CSVs are the output; edit the rules here, never the CSVs by hand."""
import csv
import json
import re
import sys
import unicodedata
import urllib.request
import xml.etree.ElementTree as XT
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from pharos.loaders.sec import http_fetch  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
UA = {"User-Agent": "pharos-config"}
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"

# IMF three-letter codes that are regions, not countries ("Asia and Pacific (APD)").
IMF_REGIONS = {"AFR", "APD", "EUR", "MCD", "SDS", "WHD"}
# IMF codes that differ from ISO, mapped to the key used here.
IMF_TO_CODE = {"KOS": "XKX",   # Kosovo: no ISO code; World Bank's XKX
               "WBG": "PSE"}   # West Bank and Gaza: ISO PSE (State of Palestine)
# Codes with no ISO 3166-1 alpha-3: the World Bank's Kosovo, the IMF's combined Curacao and Sint Maarten.
NOT_ISO = {"XKX", "CWX", "CHI"}
# Former entities and the year they ceased (prior knowledge, not fetched).
FORMER = {"CSK": 1992, "DDR": 1990, "SUN": 1991, "YUG": 1992, "SCG": 2006, "ANT": 2010, "YAR": 1990, "YMD": 1990,
          "VDR": 1976}
# Countries the SEC needs that neither the IMF nor the World Bank lists.
EXTRA = {"ATA": "Antarctica"}

# SEC names that do not match an IMF or World Bank name, by hand.
SEC_BY_HAND = {"B8": "ATA", "C5": "BHS", "E8": "CPV", "G0": "COG", "Y3": "COD", "H2": "EGY", "I6": "GMB", "K3": "HKG",
               "1P": "KAZ", "1N": "KGZ", "N1": "LBY", "N5": "MAC", "1U": "MKD", "P5": "NRU", "1X": "PSE", "R8": "PCN",
               "U8": "SHN", "U7": "KNA", "U9": "LCA", "V0": "SPM", "V1": "VCT", "2B": "SVK", "L9": "SJM", "V6": "SWZ",
               "F5": "TWN", "W8": "TUR", "X5": "VEN", "D8": "VGB", "X8": "WLF", "T7": "YEM"}
SEC_US_TERRITORIES = {"GU": "GUM", "PR": "PRI", "VI": "VIR"}
# Retired codes still in submissions, by where their filers' business and mailing addresses are (probe 2026-11-12).
SEC_RETIRED = {
    "E6": ("CAN", "QC", "64 filers, all Montreal or Laval"),
    "O6": ("CAN", "QC", "2 filers, Montreal"),
    "I8": ("DEU", "", "94 filers: Dusseldorf, Essen, Wiesbaden, Neuss ..."),
    "I9": ("DEU", "", "29 filers: Munich, Berlin, Mainz, Cologne; 1 Tel Aviv, 1 Russia"),
    "X9": ("DEU", "", "18 filers: Berlin, Germany; 1 Sao Paulo"),
    "U2": ("RUS", "", "39 filers: Moscow, Kazan, Samara, St Petersburg"),
    "Y2": ("YUG", "", "8 filers: Belgrade, Novi Beograd; strays in Nevis, Ukraine, Moscow, Whitehorse"),
    "G5": ("CSK", "", "3 filers: Czech Republic, Slovak Republic"),
    "L4": ("ISR", "", "4 filers: Yokneam, Tel Aviv, Beer Sheva"),
    "L5": ("ISR", "", "4 filers: Yokneam, Ramat Gan, Tel Aviv"),
    "Q9": ("MNP", "", "1 filer: Northern Mariana Islands"),
    "LO": ("USA", "LA", "11 filers: Baton Rouge, Metairie, Monroe; a typo for LA"),
    "E7": ("", "", "9 filers: Ontario, Cayman, Amsterdam, Jersey, Guernsey"),
    "L1": ("", "", "4 filers: Tokyo, Mumbai, Liechtenstein, Wiesbaden"),
}


def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=300) as r:
        return r.read()


def norm(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\(.*?\)|,? united kingdom-british overseas territory", "", s)
    return re.sub(r"[^a-z]", "", s)


def clean_imf_name(n):
    return re.sub(r",? United Kingdom-British Overseas Territory$", "", n)


# IMF: the WEO country codelist.
imf = {}
for cl in XT.fromstring(get("https://api.imf.org/external/sdmx/2.1/dataflow/IMF.RES/WEO/latest?references=all&detail=full")).iter():
    if cl.tag.endswith("Codelist") and cl.get("id") == "CL_WEO_COUNTRY":
        for c in cl:
            if c.tag.endswith("Code"):
                imf[c.get("id")] = [n.text for n in c if n.tag.endswith("Name") and n.get(XML_LANG) == "en"][0]
if len(imf) < 300:
    raise SystemExit(f"countries: IMF CL_WEO_COUNTRY has {len(imf)} codes, expected 300+")
regions_by_name = {k for k, n in imf.items() if re.fullmatch("[A-Z]{3}", k) and re.search(rf"\({k}\)$", n)}
if regions_by_name != IMF_REGIONS:
    raise SystemExit(f"countries: IMF region codes changed: {sorted(regions_by_name ^ IMF_REGIONS)}")

# World Bank: economies only (aggregates carry region "Aggregates").
meta, wb_all = json.loads(get("https://api.worldbank.org/v2/country?format=json&per_page=500"))
if meta["pages"] != 1:
    raise SystemExit(f"countries: World Bank country list came in {meta['pages']} pages, expected 1")
wb = {x["id"]: x["name"] for x in wb_all if x["region"]["value"] != "Aggregates"}
wb_agg = {x["id"] for x in wb_all if x["region"]["value"] == "Aggregates"}

# Countries: every World Bank economy, every IMF three-letter code that is neither a region nor a World Bank
# aggregate, and the extras.
rows = {}
for k, n in wb.items():
    rows[k] = {"code": k, "name": n, "imf_code": "", "wb_code": k}
for k, n in imf.items():
    if not re.fullmatch("[A-Z]{3}", k) or k in IMF_REGIONS or k in wb_agg:
        continue
    code = IMF_TO_CODE.get(k, k)
    r = rows.setdefault(code, {"code": code, "name": clean_imf_name(n), "imf_code": "", "wb_code": ""})
    r["imf_code"] = k
for k, n in EXTRA.items():
    if k in rows:
        raise SystemExit(f"countries: extra {k} is already listed; drop it from EXTRA")
    rows[k] = {"code": k, "name": n, "imf_code": "", "wb_code": ""}
for k in FORMER:
    if k not in rows:
        raise SystemExit(f"countries: former entity {k} is not in the IMF codelist")
for r in rows.values():
    r["iso"] = "no" if r["code"] in NOT_ISO else "yes"
    r["status"] = "former" if r["code"] in FORMER else "current"
    r["ended"] = FORMER.get(r["code"], "")

# SEC: the current code page, then the retired codes still in use.
page = http_fetch("https://www.sec.gov/submit-filings/filer-support-resources/edgar-state-country-codes").content
sec = {c: re.sub(r"<[^>]+>|\s+", " ", n).strip()
       for c, n in re.findall(r"<tr[^>]*>\s*<td[^>]*>\s*([A-Za-z0-9]{2})\s*</td>\s*<td[^>]*>(.*?)</td>",
                              page.decode("utf-8"), re.S)}
if len(sec) < 300:
    raise SystemExit(f"countries: SEC code page parsed to {len(sec)} codes, expected 300+")
overlap = set(sec) & set(SEC_RETIRED)
if overlap:
    raise SystemExit(f"countries: codes listed as retired are on the SEC page again: {sorted(overlap)}")
by_name = {}
for r in rows.values():
    by_name.setdefault(norm(r["name"]), set()).add(r["code"])
for k, n in imf.items():
    if IMF_TO_CODE.get(k, k) in rows:
        by_name.setdefault(norm(n), set()).add(IMF_TO_CODE.get(k, k))

sec_rows = []
for c, n in sorted(sec.items()):
    sub, basis = "", "sec list"
    if c == "XX":
        country, basis = "", "unknown"
    elif c.isalpha() and c in SEC_US_TERRITORIES:
        country = SEC_US_TERRITORIES[c]
    elif c.isalpha():
        country, sub = "USA", c              # the 50 states and DC: every other all-letter code
    elif ", CANADA" in n:
        country, sub = "CAN", c
    elif n.startswith("CANADA"):
        country = "CAN"
    elif c in SEC_BY_HAND:
        country = SEC_BY_HAND[c]
    else:
        m = by_name.get(norm(n), set())
        if len(m) != 1:
            raise SystemExit(f"countries: SEC {c} {n!r} matches {sorted(m) or 'nothing'}; add it to SEC_BY_HAND")
        country = m.pop()
    sec_rows.append({"sec_code": c, "sec_name": n, "country": country, "subdivision": sub, "basis": basis})
for c, (country, sub, why) in sorted(SEC_RETIRED.items()):
    sec_rows.append({"sec_code": c, "sec_name": f"(retired) {why}", "country": country, "subdivision": sub,
                     "basis": "inferred" if country else "unknown"})
bad = sorted({r["country"] for r in sec_rows if r["country"] and r["country"] not in rows})
if bad:
    raise SystemExit(f"countries: SEC rows point at codes not in countries.csv: {bad}")

cols = ["code", "name", "iso", "status", "ended", "imf_code", "wb_code"]
with open(ROOT / "config" / "countries.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, cols)
    w.writeheader()
    w.writerows(sorted(rows.values(), key=lambda r: r["code"]))
with open(ROOT / "config" / "sec_country_codes.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, ["sec_code", "sec_name", "country", "subdivision", "basis"])
    w.writeheader()
    w.writerows(sec_rows)
n_imf = sum(1 for r in rows.values() if r["imf_code"])
n_wb = sum(1 for r in rows.values() if r["wb_code"])
print(f"config/countries.csv: {len(rows)} rows ({sum(r['status'] == 'former' for r in rows.values())} former; "
      f"{n_imf} with an IMF code, {n_wb} with a World Bank code)")
print(f"config/sec_country_codes.csv: {len(sec_rows)} rows "
      + ", ".join(f"{b} {sum(r['basis'] == b for r in sec_rows)}" for b in ("sec list", "inferred", "unknown")))

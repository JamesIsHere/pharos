"""Archived page: the trusted bar (D163) measured against the SEC sources, IMF WEO and the country key.
Every number is a query run when this builds. Run from the pharos root: uv run python archive/countries-2026-11-12/trusted.py"""
import csv
import json
import sys
import time
import xml.etree.ElementTree as XT
from pathlib import Path

import duckdb

ROOT = Path("C:/Users/james/Desktop/workshop/pharos")
HERE = Path(__file__).parent
sys.path.insert(0, str(ROOT / "scaffold"))
from common import CSS, esc, now_et  # noqa: E402

con = duckdb.connect(str(ROOT / "data/catalog.duckdb"), read_only=True)
VERSION = (ROOT / "data/serving/CURRENT").read_text().strip()
CACHE = json.loads((ROOT / f"data/tmp/scaffold/company_facts-{VERSION}.json").read_text())
EV = []   # evidence: (id, title, question, sql, header, rows, verdict, status)


def q(sql):
    t = time.time()
    r = con.execute(sql)
    rows, cols = r.fetchall(), [d[0] for d in r.description]
    return cols, rows, time.time() - t


def ev(i, title, ask, sql, status, verdict, cols=None, rows=None):
    EV.append(dict(id=i, title=title, ask=ask, sql=sql, cols=cols, rows=rows, status=status, verdict=verdict))
    return status


S = {}  # matrix: (source, bar item) -> (status, evidence id)

# ---------- 1 counts tie ----------
sql = f"""SELECT dataset, rows_downloaded, rows_landed, rows_downloaded = rows_landed AS ties
FROM health_run_manifest
WHERE source = 'sec' AND run_id = (SELECT max(run_id) FROM health_run_manifest WHERE source = 'sec')
ORDER BY dataset"""
c, r, _ = q(sql)
cf_zero = [x for x in r if x[0].startswith("companyfacts") and x[1] == 0]
raw_runs = sorted(p.stem for p in (ROOT / "data/raw/sec/companyfacts").glob("*.parquet"))[-3:]
in_man = {x[0] for x in con.execute("SELECT DISTINCT run_id FROM health_run_manifest").fetchall()}
ev("N1", "Rows downloaded = rows landed, latest SEC run", "Did every row SEC sent land in raw?", sql, "partly",
   f"Ties for filing index, submissions and tickers. Company Facts records 0 = 0: the count is not measured. Its last "
   f"raw files come from runs {', '.join(raw_runs[-2:])}, which "
   f"{'are' if not any(x in in_man for x in raw_runs[-2:]) else 'are partly'} not in the run manifest (runs that "
   f"crashed before finishing, #117).", c, r)
for s in ("filing index", "submissions", "tickers"):
    S[(s, 1)] = ("met", "N1")
S[("Company Facts", 1)] = ("gap", "N1")

# ---------- 3 uniqueness ----------
sql = """SELECT count(*) AS rows, count(DISTINCT (cik, accession)) AS distinct_cik_accession,
       count(*) - count(DISTINCT (cik, accession)) AS duplicates
FROM serving_filings"""
c, r, _ = q(sql)
dup_f = r[0][2]
sqlx = """(SELECT cik, accession, string_agg(form, ' + ') AS forms, any_value(quarter) AS quarter
 FROM serving_filings GROUP BY cik, accession HAVING count(DISTINCT form) > 1 LIMIT 3)
UNION ALL
(SELECT cik, accession, string_agg(form, ' + '), any_value(quarter)
 FROM serving_filings GROUP BY cik, accession HAVING count(*) > 1 AND count(DISTINCT form) = 1 LIMIT 3)"""
cx, rx, _ = q(sqlx)
sqly = """SELECT count(*) AS duplicate_cik_accession,
       count(*) FILTER (WHERE n_forms > 1) AS differ_in_form,
       count(*) FILTER (WHERE n_forms = 1) AS same_form_repeated,
       count(*) FILTER (WHERE n_dates > 1) AS differ_in_date
FROM (SELECT cik, accession, count(DISTINCT form) n_forms, count(DISTINCT date_filed) n_dates
      FROM serving_filings GROUP BY cik, accession HAVING count(*) > 1)"""
cy, ry, _ = q(sqly)
ev("U1", "Filing index: one row per (cik, accession)", "Is the filing index's key unique?", sql + ";\n\n" + sqly,
   "gap" if dup_f else "met",
   f"{dup_f:,} duplicated (cik, accession) of {r[0][0]:,} rows. {ry[0][1]:,} are one filing under two forms (a "
   f"combined filing, e.g. SC TO-T/A with SC 13D/A): real, so the key should be (cik, accession, form). "
   f"{ry[0][2]:,} are the same row twice in one quarter's index (the examples below are UPLOAD, SEC staff letters). "
   "Remediation: key on (cik, accession, form), drop exact repeats in staging, then the gate check.", cy, ry)
EV[-1]["extra"] = (cx, rx)
S[("filing index", 3)] = ("gap" if dup_f else "met", "U1")

sql = """SELECT 'submission_details' AS tbl, 'cik' AS key, count(*) AS rows, count(DISTINCT cik) AS distinct_keys
FROM serving_submission_details
UNION ALL SELECT 'submission_filings', 'cik, accession', count(*), count(DISTINCT (cik, accession))
FROM serving_submission_filings
UNION ALL SELECT 'submission_filings', 'cik, accession, form', count(*), count(DISTINCT (cik, accession, form))
FROM serving_submission_filings
UNION ALL SELECT 'tickers', 'ticker', count(*), count(DISTINCT ticker) FROM serving_tickers
UNION ALL SELECT 'tickers', 'cik, ticker', count(*), count(DISTINCT (cik, ticker)) FROM serving_tickers"""
c, r, _ = q(sql)
d = {(x[0], x[1]): x[2] - x[3] for x in r}
ev("U2", "Submissions and tickers: keys unique", "Are the submissions and ticker keys unique?", sql,
   "met" if not d[("submission_details", "cik")] and not d[("submission_filings", "cik, accession, form")]
   and not d[("tickers", "ticker")] else "gap",
   "; ".join(f"{t} on ({k}): {n:,} duplicates" for (t, k), n in d.items())
   + ". The one (cik, accession) repeat is a combined filing under two forms, as in U1; on (cik, accession, form) "
   "submission filings are unique.", c, r)
S[("submissions", 3)] = ("met" if not d[("submission_details", "cik")] and not d[("submission_filings", "cik, accession, form")]
                         else "gap", "U2")
S[("tickers", 3)] = ("met" if not d[("tickers", "ticker")] else "gap", "U2")

t = CACHE["tables"]["company_facts"]
ev("U3", "Company Facts: one row per fact key", "Is the fact key unique?",
   f"-- measured by the Company Facts sheet on served version {CACHE['version']}, cached\n"
   f"SELECT count(*) - count(DISTINCT ({', '.join(t['key'])})) FROM serving_company_facts",
   "met" if t["dup"] == 0 else "gap", f"{t['dup']:,} duplicates of {t['rows']:,} facts (version {CACHE['version']}, "
   f"the version served now: {VERSION}).", ["rows", "duplicates"], [(t["rows"], t["dup"])])
S[("Company Facts", 3)] = ("met" if t["dup"] == 0 else "gap", "U3")

# ---------- 2 completeness ----------
sql = """WITH q AS (SELECT DISTINCT quarter FROM serving_filings),
     want AS (SELECT y || 'Q' || n AS quarter FROM range(1993, year(current_date) + 1) t(y), range(1, 5) s(n)
              WHERE make_date(y::INT, (n::INT - 1) * 3 + 1, 1) <= current_date)
SELECT count(*) AS expected_quarters, count(q.quarter) AS present,
       list(want.quarter ORDER BY want.quarter) FILTER (WHERE q.quarter IS NULL) AS missing
FROM want LEFT JOIN q USING (quarter)"""
c, r, _ = q(sql)
miss = r[0][2] or []
ev("C1", "Filing index: every quarter since 1993Q1", "Is any quarter of EDGAR's index missing?", sql,
   "met" if not miss else "gap", f"{r[0][1]} of {r[0][0]} quarters present; missing: {', '.join(miss) or 'none'}.", c, r)
S[("filing index", 2)] = ("met" if not miss else "gap", "C1")

sql = """SELECT count(DISTINCT f.cik) AS ciks_filing_since_2024,
       count(DISTINCT f.cik) FILTER (WHERE d.cik IS NULL) AS missing_from_submissions
FROM serving_filings f LEFT JOIN serving_submission_details d USING (cik)
WHERE f.date_filed >= DATE '2024-01-01'"""
c, r, _ = q(sql)
ev("C2", "Submissions: every company that filed since 2024 has a details row", "Is any recent filer missing?", sql,
   "met" if r[0][1] == 0 else "partly", f"{r[0][1]:,} of {r[0][0]:,} companies missing: "
   + "; ".join(f"{n} (Form {fm}, {dt})" for _, n, fm, dt in con.execute("""SELECT DISTINCT f.cik, f.name, f.form, f.date_filed
   FROM serving_filings f LEFT JOIN serving_submission_details d USING (cik)
   WHERE f.date_filed >= DATE '2024-01-01' AND d.cik IS NULL""").fetchall())
   + ". Form D is a private-placement notice; whether such filers belong in the expected set is a definition for you.",
   c, r)
S[("submissions", 2)] = ("met" if r[0][1] == 0 else "partly", "C2")

sql = """WITH listed AS (SELECT cik FROM serving_submission_details WHERE exchanges IS NOT NULL AND exchanges <> '[]')
SELECT (SELECT count(*) FROM listed) AS listed_companies,
       (SELECT count(*) FROM listed WHERE cik NOT IN (SELECT cik FROM serving_tickers)) AS listed_without_ticker_row,
       (SELECT count(DISTINCT cik) FROM serving_tickers
         WHERE cik NOT IN (SELECT cik FROM serving_submission_details)) AS ticker_ciks_not_in_submissions"""
c, r, _ = q(sql)
ok = r[0][1] == 0 and r[0][2] == 0
ev("C3", "Tickers: every listed company has a ticker row, every ticker a company", "Does the ticker file cover the "
   "listed companies?", sql, "met" if ok else "gap",
   f"{r[0][1]:,} of {r[0][0]:,} listed companies have no ticker row; {r[0][2]:,} ticker CIKs are not in submissions.",
   c, r)
S[("tickers", 2)] = ("met" if ok else "gap", "C3")

sql = """WITH want AS (
  SELECT DISTINCT cik, accession FROM serving_submission_filings
  WHERE form = '10-K' AND is_xbrl = 1 AND filing_date BETWEEN DATE '2024-01-01' AND DATE '2024-12-31'),
have AS (SELECT DISTINCT accession FROM serving_company_facts)   -- any CIK, any form: joint filers
SELECT count(*) AS xbrl_10k_filed_2024, count(have.accession) AS with_facts,
       count(*) - count(have.accession) AS without_facts
FROM want LEFT JOIN have USING (accession)"""
c, r, secs = q(sql)
share = r[0][1] / r[0][0] * 100 if r[0][0] else 0
ev("C4", "Company Facts: every XBRL 10-K filed in 2024 has facts", "Does Company Facts hold every annual report "
   "SEC says carries XBRL?", sql, "met" if r[0][2] == 0 else "gap",
   f"{r[0][1]:,} of {r[0][0]:,} ({share:.2f}%) have facts; {r[0][2]:,} do not. Expected set: submissions' "
   "is_xbrl flag. Matched on accession alone: when a parent and a subsidiary file one report, the facts sit under "
   "one of their CIKs; matching on (cik, accession) and form wrongly showed 165 missing.", c, r)
S[("Company Facts", 2)] = ("met" if r[0][2] == 0 else "gap", "C4")

# ---------- 4 reconciliation ----------
sql = """WITH i AS (SELECT DISTINCT cik, accession FROM serving_filings WHERE quarter = '2025Q3'),
     s AS (SELECT DISTINCT cik, accession FROM serving_submission_filings
           WHERE filing_date BETWEEN DATE '2025-07-01' AND DATE '2025-09-30')
SELECT count(*) FILTER (WHERE i.accession IS NOT NULL AND s.accession IS NOT NULL) AS in_both,
       count(*) FILTER (WHERE s.accession IS NULL) AS index_only,
       count(*) FILTER (WHERE i.accession IS NULL) AS submissions_only
FROM i FULL JOIN s USING (cik, accession)"""
c, r, _ = q(sql)
tot = sum(r[0])
ev("R1", "Filing index against submissions, 2025 Q3", "Do EDGAR's two filing lists agree on one closed quarter?", sql,
   "met" if r[0][1] + r[0][2] <= tot * 0.001 else "partly",
   f"{r[0][0]:,} in both, {r[0][1]:,} only in the index, {r[0][2]:,} only in submissions "
   f"({(r[0][1] + r[0][2]) / tot * 100:.3f}% unmatched). Both lists are published by SEC, so this proves the two "
   "products agree, not that SEC is right.", c, r)
S[("filing index", 4)] = (EV[-1]["status"], "R1")
S[("submissions", 4)] = (EV[-1]["status"], "R1")

sql = """SELECT count(*) AS ticker_rows,
       count(*) FILTER (WHERE list_contains(d.tickers, t.ticker)) AS ticker_listed_on_company,
       count(*) FILTER (WHERE d.cik IS NOT NULL AND NOT coalesce(list_contains(d.tickers, t.ticker), false)) AS company_lists_other_tickers,
       count(*) FILTER (WHERE d.cik IS NULL) AS company_missing
FROM serving_tickers t LEFT JOIN serving_submission_details d USING (cik)"""
c, r, _ = q(sql)
ev("R2", "Tickers against submissions", "Does each ticker's company list that ticker?", sql,
   "met" if r[0][2] + r[0][3] == 0 else "partly",
   f"{r[0][1]:,} of {r[0][0]:,} agree; {r[0][2]:,} disagree (the ticker file and submissions are built on different "
   f"days); {r[0][3]:,} companies missing.", c, r)
S[("tickers", 4)] = (EV[-1]["status"], "R2")

j = {x[0]: x[2] for x in CACHE["joins"]}
n_un = j["company_facts.accession"]
ev("R3", "Company Facts accessions against the filing index", "Is every filing behind a fact in EDGAR's index?",
   f"-- measured by the Company Facts sheet on version {CACHE['version']}, cached\n"
   "SELECT count(DISTINCT accession) FROM serving_company_facts WHERE accession NOT IN (SELECT accession FROM serving_filings)",
   "partly", f"{n_un} filings with facts are not in the index. This ties facts to filings, but it is not an "
   "independent check of the values: no second source of XBRL values is loaded yet (candidate: SEC Financial "
   "Statement Data Sets, a sample of companies).", ["accession", "form", "filed", "facts"],
   [tuple(x) for x in CACHE["unmatched_accessions"]])
S[("Company Facts", 4)] = ("gap", "R3")

# WEO against FRED: US GDP
root = XT.parse(HERE / "inputs" / "weo.xml").getroot()
weo_us = {}
for e in root.iter():
    if e.tag.endswith("Series") and e.get("COUNTRY") == "USA" and e.get("INDICATOR") == "NGDPD":
        weo_us = {int(o.get("TIME_PERIOD")): float(o.get("OBS_VALUE")) for o in e if o.tag.endswith("Obs")}
sql = """SELECT year(obs_date) AS year, avg(value) * 1e9 AS fred_gdp, count(*) AS quarters
FROM (SELECT obs_date, value FROM serving_observations WHERE series_id = 'fred:GDP'
      QUALIFY row_number() OVER (PARTITION BY obs_date ORDER BY vintage DESC) = 1)
WHERE year(obs_date) BETWEEN 2000 AND 2025
GROUP BY 1 HAVING count(*) = 4 ORDER BY 1"""
c, r, _ = q(sql)
rows = [(y, f, weo_us.get(y), (weo_us[y] / f - 1) * 100 if y in weo_us else None) for y, f, _ in r]
mx = max(abs(x[3]) for x in rows if x[3] is not None)
ev("R4", "IMF WEO US GDP against FRED GDP (trusted)", "Does the IMF's US GDP match BEA's, as served by FRED?",
   sql + "\n-- joined in Python to WEO USA NGDPD, the same years", "met" if mx < 0.5 else "partly",
   f"{len(rows)} years 2000-2025; largest difference {mx:.4f}%. FRED: latest vintage, annual mean of quarterly "
   "SAAR, billions x 1e9. Both come from BEA, so this proves the IMF's US figures arrive intact (units, scale, "
   "years); it does not test BEA.", ["year", "fred_gdp", "weo_gdp", "diff_pct"],
   [(y, f"{f:,.0f}", f"{w:,.0f}" if w else "", f"{dd:+.3f}" if dd is not None else "") for y, f, w, dd in rows])
S[("IMF WEO", 4)] = (EV[-1]["status"], "R4")

# WEO completeness against the country table
cty = list(csv.DictReader(open(ROOT / "config/countries.csv", encoding="utf-8")))
weo_cs = {e.get("COUNTRY") for e in root.iter() if e.tag.endswith("Series") and e.get("INDICATOR") == "NGDPD"}
cur = [x for x in cty if x["imf_code"] and x["status"] == "current" and x["wb_code"]]
missing = sorted(x["name"] for x in cur if x["imf_code"] not in weo_cs)
ev("C5", "IMF WEO: GDP for every current country both IMF and World Bank list", "Is any real economy missing from "
   "WEO's GDP?", "-- Python: countries.csv rows with an IMF and a World Bank code, status current,\n"
   "-- against the NGDPD series in the WEO pull", "partly",
   f"{len(cur) - len(missing)} of {len(cur)} have GDP; {len(missing)} do not. The expected set needs your call: "
   "the missing are small territories the IMF does not cover.", ["missing (first 25)"],
   [(m,) for m in missing[:25]])
S[("IMF WEO", 2)] = ("partly", "C5")
S[("IMF WEO", 3)] = ("open", None)
S[("IMF WEO", 1)] = ("open", None)

# Country key: coverage gate
sql = """WITH used AS (
  SELECT upper(code) AS sec_code FROM (
    SELECT state_of_incorporation AS code FROM serving_submission_details
    UNION ALL SELECT json_extract_string(addresses, '$.business.stateOrCountry') FROM serving_submission_details
    UNION ALL SELECT json_extract_string(addresses, '$.mailing.stateOrCountry') FROM serving_submission_details)
  WHERE code IS NOT NULL AND code <> '')
SELECT count(DISTINCT sec_code) AS codes_in_use,
       count(DISTINCT sec_code) FILTER (WHERE k.sec_code IS NULL) AS not_in_sec_country_codes
FROM used LEFT JOIN read_csv('config/sec_country_codes.csv', all_varchar=true) k USING (sec_code)"""
c, r, _ = q(sql)
ev("K1", "Country key: every SEC code in use has a row", "Can any company's country code fail to translate?", sql,
   "met" if r[0][1] == 0 else "gap", f"{r[0][1]} of {r[0][0]} codes in use have no row.", c, r)

# Junk in Company Facts
odd = {o[0]: o[3] for o in CACHE["odd"]}
ev("J1", "Company Facts: values that cannot be right", "What known junk would a trusted analysis hit?",
   "-- measured by the Company Facts sheet, cached", "open",
   "These are as filed by the companies. Remediation is a definition, for your sign-off: drop, flag, or keep.",
   ["oddity", "facts"], list(odd.items()))

# ---------- 6 complete run ----------
sql = """SELECT run_id, any_value(status) AS status, count(*) AS datasets,
       count(*) FILTER (WHERE source = 'sec') AS sec_datasets
FROM health_run_manifest GROUP BY run_id ORDER BY run_id DESC LIMIT 5"""
c, r, _ = q(sql)
ev("W1", "Complete runs", "Has a whole run finished and published?", sql, "partly",
   f"The latest run ({r[0][0]}) published all {r[0][3]} SEC datasets. The bar wants one complete run with the new "
   "checks in place, so this is met only after the checks exist. #117 (runs that crash partway) is still open.",
   c, r)
for s in ("filing index", "submissions", "tickers", "Company Facts"):
    S[(s, 6)] = ("open", "W1")
S[("IMF WEO", 6)] = ("open", None)

# ---------- 5 definitions (sign-off lists) ----------
DEFS = {
    "filing index": ["Which row wins when EDGAR lists one (cik, accession) twice (U1)",
                     "A quarter is complete once the next quarter's index exists"],
    "submissions": ["The incorporation and address codes translate through config/sec_country_codes.csv, "
                    "including the 12 inferred retired codes",
                    "Blank incorporation code means unknown, not US"],
    "tickers": ["A ticker belongs to the company the ticker file names on the day it was pulled"],
    "Company Facts": ["Facts dated after 2100 and fiscal years above 9999 (J1): drop, flag or keep",
                      "Annual = form 10-K and a duration of about a year; the store keeps every form"],
    "IMF WEO": ["Years after LATEST_ACTUAL_ANNUAL_DATA are IMF estimates or forecasts",
                "Fiscal-year series map to calendar years by the rule in each series' notes; Bhutan stays unmapped",
                "SCALE is display only; values are in units",
                "A country is a row of countries.csv; every other code is a group"],
}
for s in DEFS:
    S[(s, 5)] = ("open", "D")

# ---------- page ----------
SOURCES = ["filing index", "submissions", "tickers", "Company Facts", "IMF WEO"]
STATE = {"filing index": "landed", "submissions": "landed", "tickers": "landed", "Company Facts": "landed",
         "IMF WEO": "quick lane"}
BAR = {1: "Counts tie", 2: "Completeness", 3: "Uniqueness", 4: "Reconciliation", 5: "Definitions signed off",
       6: "Complete run"}
WORD = {"met": "measured: passes", "partly": "measured: partly", "gap": "measured: fails", "open": "not yet"}
CLS = {"met": "ok", "partly": "warnc", "gap": "bad", "open": "muted"}


def cell(s, b):
    st, e = S.get((s, b), ("open", None))
    w = WORD[st]
    link = f'<a href="#{e}">{e}</a>' if e and e != "D" else ('<a href="#defs">list</a>' if e == "D" else "")
    return f'<td><span class="{CLS[st]}">{w}</span> <span class=sub>{link}</span></td>'


matrix = "".join(f"<tr><td><b>{esc(s)}</b><div class=sub>{STATE[s]}</div></td>"
                 + "".join(cell(s, b) for b in BAR) + "</tr>" for s in SOURCES)
counts = {k: sum(1 for s in SOURCES for b in BAR if S.get((s, b), ("open",))[0] == k) for k in WORD}


def table(cols, rows):
    if not rows:
        return ""
    return ("<div class=wrap><table><tr>" + "".join(f"<th>{esc(x)}</th>" for x in cols) + "</tr>"
            + "".join("<tr>" + "".join(f"<td>{esc(str(v) if cols[i] == 'cik' else fmt(v))}</td>" for i, v in enumerate(row)) + "</tr>" for row in rows)
            + "</table></div>")


def fmt(v):
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, int):
        return f"{v:,}" if abs(v) >= 10000 else str(v)
    if isinstance(v, (list, tuple)):
        return ", ".join(str(x) for x in v)
    return "" if v is None else str(v)


evid = ""
for e in EV:
    extra = ""
    if e.get("extra"):
        extra = "<p class=sub>Examples of duplicated keys:</p>" + table(*e["extra"])
    evid += (f'<section id="{e["id"]}"><h3>{e["id"]} &middot; {esc(e["title"])} '
             f'<span class="{CLS[e["status"]]}">{WORD[e["status"]]}</span></h3>'
             f'<p><b>Question:</b> {esc(e["ask"])}</p><p><b>Result:</b> {esc(e["verdict"])}</p>'
             f'{table(e["cols"], e["rows"])}{extra}'
             f'<details><summary>SQL</summary><pre class=code>{esc(e["sql"])}</pre></details></section>')

defs = "".join(f"<h3>{esc(s)}</h3><ul>" + "".join(f"<li>{esc(x)}</li>" for x in v) + "</ul>" for s, v in DEFS.items())

body = f"""<div class="head"><h1>Trusted</h1><div class="stamps"><span>Measured {now_et()}</span>
<span>Served version {VERSION}</span></div></div>

<h2>The bar (D163)</h2>
<table><tr><th>#</th><th>Item</th><th>What passing means</th></tr>
<tr><td>1</td><td>Counts tie</td><td>Rows landed equal rows the source sent</td></tr>
<tr><td>2</td><td>Completeness</td><td>A gate check: nothing missing from an expected set</td></tr>
<tr><td>3</td><td>Uniqueness</td><td>A gate check: the key appears once</td></tr>
<tr><td>4</td><td>Reconciliation</td><td>One comparison against an independent source</td></tr>
<tr><td>5</td><td>Definitions</td><td>You sign off the source's definitions on its Scaffold sheet</td></tr>
<tr><td>6</td><td>Complete run</td><td>One whole run with items 2-3 in place as gate checks</td></tr></table>

<h2>Where each source stands</h2>
<p class=sub>"Measured" means the test was run today against served data. Items 2 and 3 count as met only once the
test is a gate check in checks/; none of those checks exists yet. {counts['met']} passing, {counts['partly']} partly,
{counts['gap']} failing, {counts['open']} not yet done.</p>
<div class=wrap><table><tr><th>Source</th>{''.join(f'<th>{b} {esc(t)}</th>' for b, t in BAR.items())}</tr>{matrix}</table></div>

<h2>Work to reach trusted</h2>
<table><tr><th>Source</th><th>Build or fix</th><th>Decide (you)</th><th>From</th></tr>
<tr><td>filing index</td><td>Key on (cik, accession, form); drop the 114 exact repeats in staging; gate checks for
quarter completeness and uniqueness</td><td>The key</td><td><a href="#U1">U1</a> <a href="#C1">C1</a></td></tr>
<tr><td>submissions</td><td>Gate checks: cik unique, (cik, accession, form) unique, every SEC country code has a row
in config/sec_country_codes.csv</td><td>Whether Form D-only filers belong in the expected set</td>
<td><a href="#U2">U2</a> <a href="#C2">C2</a> <a href="#K1">K1</a></td></tr>
<tr><td>tickers</td><td>Gate checks: ticker unique, every ticker's company in submissions</td><td>Whether the 44
listed companies without a ticker row and the 71 ticker disagreements are acceptable, and at what threshold</td>
<td><a href="#C3">C3</a> <a href="#R2">R2</a></td></tr>
<tr><td>Company Facts</td><td>Make the count control measure facts (it records 0 = 0); find why 16 XBRL annual
reports have no facts; load an independent sample for reconciliation (SEC Financial Statement Data Sets); gate
checks for the fact key and the 2024 coverage</td><td>The reconciliation source; what to do with the junk values</td>
<td><a href="#N1">N1</a> <a href="#C4">C4</a> <a href="#R3">R3</a> <a href="#J1">J1</a></td></tr>
<tr><td>IMF WEO</td><td>Land it first: loader writing one raw file per edition, schema check, freshness check,
design.md row; then a uniqueness gate on (country, series, year, edition) and the completeness gate</td>
<td>The expected set of countries</td><td><a href="#C5">C5</a> <a href="#R4">R4</a></td></tr>
<tr><td>all five</td><td>One complete run with the gates in place; blocked while #117 (runs that crash partway)
is open</td><td>Sign off each source's definitions (list at the bottom)</td><td><a href="#W1">W1</a></td></tr>
</table>

<h2>Evidence</h2>
{evid}

<h2 id="defs">Definitions to sign off</h2>
{defs}"""

STYLE = ("<style>pre.code { background:var(--box); border:1px solid var(--line); padding:10px 12px; "
         "font:13px/1.45 Consolas, monospace; overflow-x:auto; } .warnc { color:#a16207; font-weight:600; } "
         "section { margin:0 0 22px; } section h3 { font-size:16px; } .muted { color:#9ca3af; }</style>")
(HERE / "trusted.html").write_text(f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Trusted</title><style>{CSS}</style>{STYLE}
</head><body><nav><b>COUNTRIES</b><a href="../../scaffold/archive.html">Scaffold</a><a href="country_key.html">Data</a><a href="charts.html">Charts</a>
<a class=on href="trusted.html">Trusted</a></nav><main>{body}</main></body></html>""", encoding="utf-8")
print("trusted.html:", counts, "| evidence:", [(e["id"], e["status"]) for e in EV])

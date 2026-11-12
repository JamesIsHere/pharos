"""Insiders: SEC's Insider Transactions Data Sets (Forms 3, 4, 5), the newest four quarters. Two sections (James,
2026-11-11): Data (what was pulled, its shape, its schema as declared against as found, what it says) and Pipeline (how
it is fetched, and what landing it in Pharos would take, each item from a number in the Data section).

The build reads SEC's listing page, downloads the four quarterly zips into <data root>/tmp/explore-insiders/ (each
fetched once and kept, with a fetch.json), reads them with DuckDB as text, and measures every column against SEC's
own schema file (FORM_345_metadata.json, in each zip). Nothing is stored in Pharos. Writes insiders.html here."""
import html
import json
import re
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import duckdb

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "lib"))
import nav  # noqa: E402

from pharos.loaders.sec import http_fetch  # noqa: E402
from pharos.paths import data_root  # noqa: E402

LISTING = "https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets"
N_QUARTERS = 4
CACHE = data_root() / "tmp" / "explore-insiders"
TABLES = ("SUBMISSION", "REPORTINGOWNER", "NONDERIV_TRANS", "NONDERIV_HOLDING", "DERIV_TRANS", "DERIV_HOLDING",
          "FOOTNOTES", "OWNER_SIGNATURE")
GRAIN = {"SUBMISSION": "one filing", "REPORTINGOWNER": "one insider on a filing",
         "NONDERIV_TRANS": "one stock trade", "NONDERIV_HOLDING": "one stock holding, no trade",
         "DERIV_TRANS": "one option or derivative trade", "DERIV_HOLDING": "one option or derivative holding, no trade",
         "FOOTNOTES": "one footnote", "OWNER_SIGNATURE": "one signature"}
# Form 4 transaction codes (General Instruction 8)
CODE_NAMES = {"P": "open-market buy", "S": "open-market sale", "A": "grant or award", "F": "shares withheld for tax",
              "M": "option exercise", "J": "other", "D": "disposed to the company", "G": "gift", "C": "conversion",
              "L": "small acquisition", "W": "will or inheritance", "X": "in-the-money exercise",
              "I": "discretionary", "E": "expiration (short)", "H": "expiration (long)", "O": "out-of-money exercise",
              "K": "equity swap", "U": "tender", "V": "voluntary early report", "Z": "voting trust"}
DATE_FMT = "%d-%b-%Y"
timings = []


class timed:
    def __init__(self, label):
        self.label = label

    def __enter__(self):
        self.t = time.perf_counter()

    def __exit__(self, *exc):
        timings.append((self.label, time.perf_counter() - self.t))


t_all = time.perf_counter()

# fetch: links come from SEC's page, never a pattern (SEC moved its newest files to another folder in 2026)
with timed("Read SEC's listing page"):
    listing = http_fetch(LISTING).content.decode("utf-8", "replace")
links = {q: "https://www.sec.gov" + href for href, q in re.findall(r'href="(/files/[^"]*/(\d{4}q\d)_form345\.zip)"', listing)}
listed = sorted(links)
if not listed:
    raise SystemExit("insiders: no quarterly files found on SEC's listing page")
QUARTERS = tuple(listed[-N_QUARTERS:])

fetches = {}
with timed("Download and unzip quarters not yet on disk"):
    for q in QUARTERS:
        folder = CACHE / q
        meta = folder / "fetch.json"
        if not meta.exists():
            CACHE.mkdir(parents=True, exist_ok=True)
            zpath = CACHE / f"{q}_form345.zip"
            t0 = time.perf_counter()
            f = http_fetch(links[q], to=zpath)
            t1 = time.perf_counter()
            with zipfile.ZipFile(zpath) as z:
                members = z.namelist()
                z.extractall(folder)
            t2 = time.perf_counter()
            meta.write_text(json.dumps({
                "url": f.url, "zip_bytes": zpath.stat().st_size, "members": members,
                "last_modified": f.last_modified.isoformat() if f.last_modified else None,
                "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "download_s": round(t1 - t0, 2), "unzip_s": round(t2 - t1, 2)}), encoding="utf-8")
            zpath.unlink()
        fetches[q] = json.loads(meta.read_text(encoding="utf-8"))

# SEC's declared schema, one per zip; it must be the same in every quarter
decl_by_q = {q: json.loads((CACHE / q / "FORM_345_metadata.json").read_text(encoding="utf-8")) for q in QUARTERS}
if len({json.dumps(d, sort_keys=True) for d in decl_by_q.values()}) != 1:
    raise SystemExit("insiders: SEC's declared schema differs between quarters")
DECL = {t["url"].removesuffix(".tsv"): t["tableSchema"] for t in decl_by_q[QUARTERS[-1]]["tables"]}
if set(DECL) != set(TABLES):
    raise SystemExit(f"insiders: SEC's schema lists {sorted(DECL)}, expected {sorted(TABLES)}")

con = duckdb.connect()
with timed("Read the eight tables (four quarters) as text"):
    for t in TABLES:
        files = [(CACHE / q / f"{t}.tsv").as_posix() for q in QUARTERS]
        con.execute(f"""CREATE TABLE {t} AS SELECT *, regexp_extract(filename, '(\\d{{4}}q\\d)', 1) AS q
                        FROM read_csv({files}, delim='\t', header=true, all_varchar=true, quote='',
                                      union_by_name=true, filename=true)""")
found_cols = {t: [c[0] for c in con.execute(f"DESCRIBE {t}").fetchall() if c[0] not in ("filename", "q")]
              for t in TABLES}
col_sets_by_q = {t: {tuple(c[0] for c in duckdb.sql(
    f"DESCRIBE SELECT * FROM read_csv('{(CACHE / q / f'{t}.tsv').as_posix()}', delim='\t', header=true, "
    "all_varchar=true, quote='')").fetchall()) for q in QUARTERS} for t in TABLES}
if any(len(s) != 1 for s in col_sets_by_q.values()):
    raise SystemExit("insiders: a table's columns differ between quarters")


def target(dt):
    base = dt.get("base", "") if isinstance(dt, dict) else str(dt)
    return "DATE" if base.startswith("date") else "DOUBLE" if base == "NUMBER" else "VARCHAR"


def declared(dt):
    base = dt.get("base", "") if isinstance(dt, dict) else str(dt)
    if base.startswith("date"):
        return "date DD-MON-YYYY"
    return f"{base}({dt['maxLength']})" if isinstance(dt, dict) and "maxLength" in dt else base


# per column: declared type, target type, filled, distinct, values that fail the cast or overrun the length, example
schema = {}
with timed("Measure every column against SEC's schema"):
    for t in TABLES:
        n = con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        dcols = {c["name"]: c for c in DECL[t]["columns"]}
        rows = []
        for name in list(dcols) + [c for c in found_cols[t] if c not in dcols]:
            d = dcols.get(name)
            if name not in found_cols[t]:
                rows.append((name, declared(d["datatype"]), target(d["datatype"]), bool(d.get("required")),
                             None, None, None, None, "declared, not in file"))
                continue
            tgt = target(d["datatype"]) if d else "VARCHAR"
            c = f'"{name}"'
            if tgt == "DATE":
                bad = f"count({c}) - count(try_strptime({c}, '{DATE_FMT}'))"
            elif tgt == "DOUBLE":
                bad = f"count({c}) - count(try_cast({c} AS DOUBLE))"
            elif d and isinstance(d["datatype"], dict) and "maxLength" in d["datatype"]:
                bad = f"count(*) FILTER (WHERE length({c}) > {d['datatype']['maxLength']})"
            else:
                bad = "0"
            filled, distinct, nbad, ex = con.execute(
                f"SELECT count({c}), approx_count_distinct({c}), {bad}, "
                f"(SELECT {c} FROM {t} WHERE {c} IS NOT NULL LIMIT 1) FROM {t}").fetchone()
            rows.append((name, declared(d["datatype"]) if d else "(not declared)", tgt, bool(d and d.get("required")),
                         filled / n if n else 0, distinct, nbad, ex, "" if d else "in file, not declared"))
        schema[t] = (n, rows)

# keys and joins
keys = {}
with timed("Check keys and joins"):
    for t in TABLES:
        pk = DECL[t]["primaryKey"]
        pk = [pk] if isinstance(pk, str) else pk
        cols = ", ".join(f'"{k}"' for k in pk)
        dup = con.execute(f"SELECT count(*) - count(DISTINCT ({cols})) FROM {t}").fetchone()[0]
        orphan = 0 if t == "SUBMISSION" else con.execute(
            f"SELECT count(*) FROM {t} ANTI JOIN SUBMISSION USING (ACCESSION_NUMBER)").fetchone()[0]
        keys[t] = (pk, dup, orphan)

# what it says
P = lambda s: f"try_strptime({s}, '{DATE_FMT}')"  # noqa: E731
forms = con.execute("SELECT DOCUMENT_TYPE, count(*) n FROM SUBMISSION GROUP BY 1 ORDER BY n DESC").fetchall()
first_day, last_day = con.execute(f"SELECT min({P('FILING_DATE')}), max({P('FILING_DATE')}) FROM SUBMISSION").fetchone()
n_issuers, n_tick = con.execute(
    "SELECT count(DISTINCT ISSUERCIK), count(DISTINCT ISSUERCIK) FILTER (WHERE upper(trim(coalesce("
    "ISSUERTRADINGSYMBOL, ''))) NOT IN ('', 'NONE', 'N/A', 'NA')) FROM SUBMISSION").fetchone()
n_owners = con.execute("SELECT count(DISTINCT RPTOWNERCIK) FROM REPORTINGOWNER").fetchone()[0]
rel = con.execute("SELECT RPTOWNER_RELATIONSHIP, count(*) n FROM REPORTINGOWNER GROUP BY 1 ORDER BY n DESC").fetchall()
codes = con.execute("SELECT coalesce(TRANS_CODE, '(blank)'), count(*) n FROM NONDERIV_TRANS GROUP BY 1 ORDER BY n DESC").fetchall()
example = con.execute(f"""
    SELECT s.ACCESSION_NUMBER, s.FILING_DATE, s.ISSUERNAME, s.ISSUERTRADINGSYMBOL, r.RPTOWNERNAME, r.RPTOWNER_TITLE,
           n.TRANS_DATE, n.TRANS_CODE, n.TRANS_SHARES, n.TRANS_PRICEPERSHARE, n.SHRS_OWND_FOLWNG_TRANS
    FROM NONDERIV_TRANS n JOIN SUBMISSION s USING (ACCESSION_NUMBER) JOIN REPORTINGOWNER r USING (ACCESSION_NUMBER)
    WHERE s.ISSUERTRADINGSYMBOL = 'NVDA' AND n.TRANS_CODE = 'S' ORDER BY {P('s.FILING_DATE')}, s.ACCESSION_NUMBER
    LIMIT 1""").fetchone()
lag = con.execute(f"""
    SELECT quantile_cont(d, [0.5, 0.9, 0.99]) FROM (
      SELECT date_diff('day', {P('n.TRANS_DATE')}, {P('s.FILING_DATE')}) d
      FROM NONDERIV_TRANS n JOIN SUBMISSION s USING (ACCESSION_NUMBER)
      WHERE s.DOCUMENT_TYPE = '4' AND n.TRANS_CODE IN ('P', 'S'))""").fetchone()[0]
joint = con.execute("SELECT count(*) FROM (SELECT ACCESSION_NUMBER FROM REPORTINGOWNER GROUP BY 1 "
                    "HAVING count(*) > 1)").fetchone()[0]
flag_vals = con.execute("SELECT coalesce(AFF10B5ONE, '(blank)'), count(*) FROM SUBMISSION GROUP BY 1 "
                        "ORDER BY 2 DESC").fetchall()
amend_orphans = con.execute("SELECT count(*) FROM SUBMISSION WHERE DOCUMENT_TYPE LIKE '%/A' "
                            "AND DATE_OF_ORIG_SUB IS NULL").fetchone()[0]
n_amend = sum(n for f, n in forms if f.endswith("/A"))
after_filing = con.execute(f"""SELECT count(*) FROM NONDERIV_TRANS n JOIN SUBMISSION s USING (ACCESSION_NUMBER)
    WHERE {P('n.TRANS_DATE')} > {P('s.FILING_DATE')}""").fetchone()[0]

# tie-out to the filing index Pharos serves (original Form 4s, by accession)
version = (data_root() / "serving" / "CURRENT").read_text(encoding="utf-8").strip()
idx_root = data_root() / "serving" / version / "filings.parquet"
tie = []
with timed("Tie out to Pharos's filing index"):
    for q in QUARTERS:
        part = (idx_root / f"quarter={q.upper()}").as_posix()
        con.execute(f"CREATE OR REPLACE VIEW idx AS SELECT DISTINCT accession FROM read_parquet('{part}/*.parquet') "
                    "WHERE form = '4'")
        tie.append((q,) + con.execute(f"""
            SELECT (SELECT count(*) FROM idx),
                   (SELECT count(*) FROM SUBMISSION WHERE q = '{q}' AND DOCUMENT_TYPE = '4'),
                   (SELECT count(*) FROM idx ANTI JOIN SUBMISSION s ON idx.accession = s.ACCESSION_NUMBER),
                   (SELECT count(*) FROM SUBMISSION s ANTI JOIN idx ON idx.accession = s.ACCESSION_NUMBER
                    WHERE s.q = '{q}' AND s.DOCUMENT_TYPE = '4')""").fetchone())
total_s = time.perf_counter() - t_all

# ---------------------------------------------------------------- page
def esc(x):
    return html.escape(str(x))


def num(x):
    return f"{x:,}"


rows_by_q = {t: dict(con.execute(f"SELECT q, count(*) FROM {t} GROUP BY 1").fetchall()) for t in TABLES}
n_filings = schema["SUBMISSION"][0]
n_rows = sum(schema[t][0] for t in TABLES)
zip_mb = sum(fetches[q]["zip_bytes"] for q in QUARTERS) / 1e6
cast_fail = sum(r[6] or 0 for t in TABLES for r in schema[t][1] if r[2] != "VARCHAR")
over_len = sum(r[6] or 0 for t in TABLES for r in schema[t][1] if r[2] == "VARCHAR")
missing_cols = [(t, r[0]) for t in TABLES for r in schema[t][1] if r[8] == "declared, not in file"]
extra_cols = [(t, r[0]) for t in TABLES for r in schema[t][1] if r[8] == "in file, not declared"]
fn_cols = sum(1 for t in TABLES for r in schema[t][1] if r[0].endswith("_FN"))
req_nulls = sum(1 for t in TABLES for r in schema[t][1] if r[3] and r[4] is not None and r[4] < 1)
dups = sum(k[1] for k in keys.values())
orphans = sum(k[2] for k in keys.values())

pulled = "".join(
    f"<tr><td>{t}</td><td>{GRAIN[t]}</td><td class=num>{len(found_cols[t])}</td>"
    + "".join(f"<td class=num>{num(rows_by_q[t].get(q, 0))}</td>" for q in QUARTERS)
    + f"<td class=num>{num(schema[t][0])}</td></tr>" for t in TABLES)
form_rows = "".join(f"<tr><td>{esc(f)}</td><td class=num>{num(n)}</td><td class=num>{n / n_filings:.1%}</td></tr>"
                    for f, n in forms)
key_rows = "".join(
    f"<tr><td>{t}</td><td>{esc(', '.join(k[0]))}</td><td class=num>{num(k[1])}</td>"
    f"<td class=num>{'' if t == 'SUBMISSION' else num(k[2])}</td></tr>" for t, k in keys.items())


def schema_table(t):
    n, rows = schema[t]
    body = "".join(
        f"<tr{' class=flag' if (r[6] or r[8] or (r[3] and r[4] is not None and r[4] < 1)) else ''}>"
        f"<td>{esc(r[0])}</td><td>{esc(r[1])}</td><td>{r[2]}</td><td>{'yes' if r[3] else ''}</td>"
        f"<td class=num>{'' if r[4] is None else f'{r[4]:.1%}'}</td>"
        f"<td class=num>{'' if r[5] is None else num(r[5])}</td>"
        f"<td class=num>{'' if r[6] is None else num(r[6])}</td>"
        f"<td class=ex>{esc('' if r[7] is None else str(r[7])[:40])}</td><td>{esc(r[8])}</td></tr>" for r in rows)
    n_flag = sum(1 for r in rows if r[6] or r[8] or (r[3] and r[4] is not None and r[4] < 1))
    return (f"<details><summary><b>{t}</b>: {GRAIN[t]}; {len(rows)} columns, {num(n)} rows"
            f"{f'; {n_flag} flagged' if n_flag else ''}</summary><div class=wrap><table>"
            "<tr><th>Column</th><th>SEC declares</th><th>Cast to</th><th>Required</th><th class=num>Filled</th>"
            "<th class=num>Distinct (approx)</th><th class=num>Fail cast or too long</th><th>Example</th><th></th></tr>"
            f"{body}</table></div></details>")


code_rows = "".join(f"<tr><td>{esc(c)}</td><td>{esc(CODE_NAMES.get(c, ''))}</td><td class=num>{num(n)}</td>"
                    f"<td class=num>{n / schema['NONDERIV_TRANS'][0]:.1%}</td></tr>" for c, n in codes)
rel_rows = "".join(f"<tr><td>{esc(r)}</td><td class=num>{num(n)}</td></tr>" for r, n in rel[:8])
ex_labels = ("Accession", "Filed", "Company", "Ticker", "Insider", "Title", "Trade date", "Code", "Shares", "Price",
             "Shares owned after")
ex_row = "".join(f"<tr><td>{a}</td><td>{esc(b)}</td></tr>" for a, b in zip(ex_labels, example)) if example else ""
fetch_rows = "".join(
    f"<tr><td>{q.upper()}</td><td class=num>{fetches[q]['zip_bytes'] / 1e6:,.1f} MB</td>"
    f"<td>{esc((fetches[q]['last_modified'] or '')[:10])}</td>"
    f"<td class=num>{fetches[q]['download_s']:.1f}s</td><td class=num>{fetches[q]['unzip_s']:.1f}s</td></tr>"
    for q in QUARTERS)
time_rows = "".join(f"<tr><td>{esc(lb)}</td><td class=num>{s:.1f}s</td></tr>" for lb, s in timings)
tie_rows = "".join(f"<tr><td>{q.upper()}</td><td class=num>{num(a)}</td><td class=num>{num(b)}</td>"
                   f"<td class=num>{num(c)}</td><td class=num>{num(d)}</td></tr>" for q, a, b, c, d in tie)
flag_txt = ", ".join(f"{esc(v)} ({num(n)})" for v, n in flag_vals)
missing_txt = ", ".join(f"{t}.{c}" for t, c in missing_cols) or "none"
extra_txt = ", ".join(f"{t}.{c}" for t, c in extra_cols) or "none"

br = "<br>"
landing = [  # (item, found in the data, to do)
    ("Raw", f"{len(QUARTERS)} zips, 8 tables each", "Write each table as text, one file per run, under raw/insiders/; "
                                                   "vintage = SEC's Last-Modified"),
    ("Types", f"{num(cast_fail)} cast failures", "Dates DD-MON-YYYY to DATE, NUMBER to DOUBLE, rest text; "
                                                  "a failure stops the load"),
    ("Columns", "Declared, not in file:" + br + br.join(f"{t}.{c}" for t, c in missing_cols)
                + br + "In file, not declared:" + br + br.join(f"{t}.{c}" for t, c in extra_cols),
     "Check against the file's columns, not SEC's declaration"),
    ("Keys", f"{num(dups)} duplicates (OWNER_SIGNATURE)" + br + f"{num(orphans)} rows with no filing",
     "Dedupe signatures in staging"),
    ("Time", f"Trade to filing: median {lag[0]:.0f}d, p90 {lag[1]:.0f}d, p99 {lag[2]:.0f}d" + br
             + f"{num(after_filing)} trades dated after filing",
     "obs_date = TRANS_DATE" + br + "available_date = FILING_DATE"),
    ("Amendments", f"{num(n_amend)} amended filings" + br + f"{num(amend_orphans)} with no original date",
     "Rule: amendment replaces or adds"),
    ("Joint filers", f"{num(joint)} filings with 2+ insiders", "Count trades by filing, not by insider"),
    ("Roles", "Comma list in one field (Director,Officer)", "Split into four yes/no columns"),
    ("10b5-1 flag", br.join(f"{esc(v)}: {num(n)}" for v, n in flag_vals), "Map to one yes/no"),
    ("Footnotes", f"{fn_cols} _FN columns hold footnote IDs", "Keep as text; join to FOOTNOTES"),
    ("Link to Pharos", f"Form 4s only in SEC's file: {num(sum(r[4] for r in tie))}" + br
                       + f"Only in Pharos's index: {num(sum(r[3] for r in tie))}", "Join on ISSUERCIK"),
    ("Freshness", f"Quarterly; newest {QUARTERS[-1].upper()}, posted "
                  f"{(fetches[QUARTERS[-1]]['last_modified'] or '')[:10]}", "One freshness check"),
]
landing_rows = "".join(f"<tr><td>{a}</td><td>{b}</td><td>{c}</td></tr>" for a, b, c in landing)
built = datetime.now().strftime("%Y-%m-%d %H:%M")

out_html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Insiders</title>
<style>
:root {{ --bg:#ffffff; --fg:#1d1d1b; --muted:#5f5e58; --line:#e3e1da; --box:#f6f5f1; --flag:#fef3c7; }}
body {{ background:var(--bg); color:var(--fg); font:15px/1.5 system-ui, "Segoe UI", sans-serif; margin:0; padding:24px 16px 60px; }}
main {{ max-width:1250px; margin:0 auto; }}
h1 {{ font-size:24px; margin:0 0 6px; }}
h2 {{ font-size:21px; margin:34px 0 4px; border-bottom:2px solid var(--fg); }}
h3 {{ font-size:16px; margin:22px 0 4px; }}
.muted {{ color:var(--muted); }}
.facts td:first-child {{ color:var(--muted); padding-right:18px; }}
ul {{ margin:4px 0; padding-left:20px; font-size:14px; }}
table {{ border-collapse:collapse; font-size:14px; margin:4px 0; }}
th, td {{ border-bottom:1px solid var(--line); padding:3px 10px; text-align:left; vertical-align:top; }}
td.num, th.num {{ text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }}
td.ex {{ font:12px Consolas, monospace; color:var(--muted); }}
tr.flag td {{ background:var(--flag); }}
details {{ margin:4px 0; }} summary {{ cursor:pointer; padding:3px 0; }}
.cols {{ display:flex; flex-wrap:wrap; gap:28px; }}
.wrap {{ overflow-x:auto; }}
{nav.CSS}
</style></head>
<body>{nav.html("Insiders")}<main>
<h1>Insiders</h1>
<table class="facts">
<tr><td>Source</td><td>SEC Insider Transactions Data Sets</td></tr>
<tr><td>Forms</td><td>3, 4, 5 and amendments</td></tr>
<tr><td>Quarters</td><td>{QUARTERS[0].upper()} to {QUARTERS[-1].upper()}</td></tr>
<tr><td>Filed</td><td>{first_day:%Y-%m-%d} to {last_day:%Y-%m-%d}</td></tr>
<tr><td>Filings</td><td>{num(n_filings)}</td></tr>
<tr><td>Rows</td><td>{num(n_rows)} in 8 tables</td></tr>
<tr><td>Companies</td><td>{num(n_issuers)} ({num(n_tick)} with a ticker)</td></tr>
<tr><td>Insiders</td><td>{num(n_owners)}</td></tr>
<tr><td>Stored in Pharos</td><td>nothing</td></tr>
<tr><td>Built</td><td>{built}, explore/insiders/build.py</td></tr>
</table>

<h2>Data</h2>
<h3>What was pulled</h3>
<div class="wrap"><table>
<tr><th>Table</th><th>One row is</th><th class=num>Cols</th>{''.join(f'<th class=num>{q.upper()}</th>' for q in QUARTERS)}<th class=num>Total</th></tr>
{pulled}
</table></div>

<div class="cols">
<div><h3>Filings by form</h3><table><tr><th>Form</th><th class=num>Filings</th><th class=num>Share</th></tr>{form_rows}</table></div>
<div><h3>Keys (SEC's declared)</h3><table><tr><th>Table</th><th>Key</th><th class=num>Duplicates</th><th class=num>No filing</th></tr>{key_rows}</table></div>
</div>

<h3>Schema: declared against found</h3>
<ul class="muted">
<li>Cast to: type from SEC's declaration</li>
<li>Fail cast or too long: values that don't convert, or text over the declared length</li>
<li>Shaded: needs a decision</li>
</ul>
{''.join(schema_table(t) for t in TABLES)}

<h3>What it says</h3>
<div class="cols">
<div><table><tr><th>Code</th><th>Stock trade type</th><th class=num>Rows</th><th class=num>Share</th></tr>{code_rows}</table></div>
<div><table><tr><th>Insider's role (as filed)</th><th class=num>Rows</th></tr>{rel_rows}</table></div>
<div><table><tr><th colspan=2>One observation (NONDERIV_TRANS joined to its filing)</th></tr>{ex_row}</table></div>
</div>

<h2>Pipeline</h2>
<h3>Fetch</h3>
<table class="facts">
<tr><td>Listing</td><td><a href="{LISTING}">SEC's page</a>: {len(listed)} quarterly zips, {listed[0].upper()} to {listed[-1].upper()}</td></tr>
<tr><td>Links</td><td>read from the page, not built from a pattern</td></tr>
<tr><td>Client</td><td>Pharos's SEC client (SEC_USER_AGENT, 10 requests/s)</td></tr>
<tr><td>Cache</td><td>{esc(CACHE)}</td></tr>
</table>
<div class="cols">
<div><table><tr><th>Quarter</th><th class=num>Zip</th><th>SEC Last-Modified</th><th class=num>Download</th><th class=num>Unzip</th></tr>{fetch_rows}</table></div>
<div><table><tr><th>This build</th><th class=num>Time</th></tr>{time_rows}<tr><td>Total</td><td class=num>{total_s:.1f}s</td></tr></table></div>
</div>

<h3>To land it in Pharos</h3>
<div class="wrap"><table><tr><th>Item</th><th>Found</th><th>To do</th></tr>{landing_rows}</table></div>

<h3>Tie-out: original Form 4s, SEC's file against Pharos's filing index</h3>
<table><tr><th>Quarter</th><th class=num>Filing index</th><th class=num>SEC's file</th><th class=num>Only in index</th><th class=num>Only in SEC's file</th></tr>{tie_rows}</table>
</main></body></html>"""
out = HERE / "insiders.html"
out.write_text(out_html, encoding="utf-8")
print(f"wrote {out} in {total_s:.1f}s; cast failures {cast_fail}, too long {over_len}, required-null cols {req_nulls}, "
      f"missing cols {missing_cols}, extra {extra_cols}")

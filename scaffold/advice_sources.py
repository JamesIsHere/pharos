"""Temporary exploration page: who households turn to for saving and investment decisions (James, 2026-11-16).

Reads the Federal Reserve's 2025 Survey of Consumer Finances, downloaded to <data root>/tmp/scaffold/scf/ (quick lane,
nothing stored):
  extract/SCFP2025.csv   the Fed's summary file: one row per household per implicate (5 each), weight, income, net worth
  full/p25i6.dta         the full public file; only the 15 answer slots of the information-sources question are read,
                         exported once to full/advice_sources.csv (pandas reads the Stata file; everything else is DuckDB)
and the SEC's monthly file of registered investment advisers, downloaded to <data root>/tmp/scaffold/adv/:
  registered/*.CSV       one row per adviser firm, 448 columns from its Form ADV; 27 are kept in slim.csv (clients,
                         assets, how the firm charges). Column meanings follow the form's item numbers, from memory.
Every figure is computed here. Writes advice-sources.html.  Run: uv run python scaffold/advice_sources.py"""
import csv
import json
from pathlib import Path

import duckdb
from common import esc, now_et, page

from pharos.paths import data_root

HERE = Path(__file__).parent
SCF = data_root() / "tmp" / "scaffold" / "scf"
PLOTLY = "https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.35.2/plotly.min.js"
SLOTS = [f"x{i}" for i in list(range(7112, 7122)) + list(range(6865, 6870))]
# answer codes of the question, from the Fed's 2025 codebook
SRC = (("planner", 12, "Financial planner", "#2563eb"), ("accountant", 9, "Accountant", "#059669"), ("broker", 11, "Broker", "#7c3aed"),
       ("banker", 10, "Banker", "#d97706"), ("lawyer", 8, "Lawyer", "#9ca3af"), ("friend", 7, "Friend or relative", "#dc2626"),
       ("internet", 5, "Internet", "#6b7280"))
CUTS = ((.10, "Bottom 10%"), (.25, "10 to 25"), (.50, "25 to 50"), (.75, "50 to 75"), (.90, "75 to 90"), (.95, "90 to 95"), (.99, "95 to 99"),
        (1.0001, "Top 1%"))

src_csv = SCF / "full" / "advice_sources.csv"
if not src_csv.exists():
    import pandas as pd
    dta = SCF / "full" / "p25i6.dta"
    names = {n.lower(): n for n in pd.read_stata(dta, iterator=True).variable_labels()}
    df = pd.read_stata(dta, columns=[names[c] for c in ["y1", "yy1", "x42001"] + SLOTS], convert_categoricals=False)
    df.columns = [c.lower() for c in df.columns]
    df.to_csv(src_csv, index=False)

con = duckdb.connect()


def q(sql):
    return con.execute(sql).fetchall()


con.execute(f"CREATE TABLE e AS SELECT * FROM read_csv('{(SCF / 'extract' / 'SCFP2025.csv').as_posix()}', header=true)")
con.execute(f"CREATE TABLE a AS SELECT * FROM read_csv('{src_csv.as_posix()}', header=true)")
n_rows, n_hh, hh_m, n_cols = q("SELECT count(*), count(DISTINCT YY1), sum(WGT) / 1e6, (SELECT count(*) FROM (DESCRIBE e)) FROM e")[0]
flags = ", ".join("(" + " OR ".join(f"{s} = {code}" for s in SLOTS) + f")::INT AS s_{k}" for k, code, *_ in SRC)
con.execute(f"""CREATE TABLE j AS SELECT e.*, a.x42001, {flags},
                sum(WGT) OVER (ORDER BY NETWORTH, e.Y1) / sum(WGT) OVER () AS pn,
                sum(WGT) OVER (ORDER BY INCOME, e.Y1) / sum(WGT) OVER () AS pi FROM e JOIN a ON a.y1 = e.Y1""")
n_join, wdiff, mism, per_hh = q("""SELECT count(*), max(abs(WGT - x42001 / 5)), sum((IFINPLAN <> greatest(s_planner, s_accountant, s_lawyer))::INT),
                                   (SELECT max(c) FROM (SELECT count(*) c FROM e GROUP BY YY1)) FROM j""")[0]
if n_join != n_rows or mism:
    raise SystemExit(f"join or definition check failed: {n_join} of {n_rows} rows joined, {mism} rows disagree with the Fed's own flag")
ex = q("SELECT YY1, Y1, WGT, INCOME, NETWORTH, s_planner, s_accountant, s_friend FROM j ORDER BY Y1 LIMIT 1")[0]
overall = dict(zip([k for k, *_ in SRC], q("SELECT " + ", ".join(f"sum(WGT * s_{k}) / sum(WGT) * 100" for k, *_ in SRC) + " FROM j")[0]))
nosave = q("SELECT sum(WGT * IDONT) / sum(WGT) * 100 FROM j")[0][0]


def groups(p, val):
    case = "CASE " + " ".join(f"WHEN {p} <= {c} THEN {i}" for i, (c, _) in enumerate(CUTS)) + " END"
    sel = ", ".join(f"sum(WGT * s_{k}) / sum(WGT) * 100" for k, *_ in SRC)
    return q(f"""SELECT {case} g, count(DISTINCT YY1), min({val}), max({val}), sum(WGT * HOTHMA) / sum(WGT) * 100,
                 sum(WGT * HSTOCKS) / sum(WGT) * 100, sum(WGT * HRETQLIQ) / sum(WGT) * 100, {sel} FROM j GROUP BY 1 ORDER BY 1""")


nw, inc = groups("pn", "NETWORTH"), groups("pi", "INCOME")
# twenty equal slices of households by net worth, to see whether there is a step
vent = q("""SELECT least(floor(pn * 20), 19)::INT v, min(NETWORTH), max(NETWORTH), sum(WGT * s_planner) / sum(WGT) * 100,
            sum(WGT * s_friend) / sum(WGT) * 100, sum(WGT * HOTHMA) / sum(WGT) * 100, count(DISTINCT YY1) FROM j GROUP BY 1 ORDER BY 1""")
cross = next((r for r in vent if r[3] > r[4]), None)
jump = max(range(1, len(vent)), key=lambda i: vent[i][3] - vent[i - 1][3])
age = q("""SELECT AGECL, sum(WGT * s_planner) / sum(WGT) * 100, sum(WGT * s_friend) / sum(WGT) * 100, sum(WGT * s_internet) / sum(WGT) * 100
           FROM j GROUP BY 1 ORDER BY 1""")
AGES = ["Under 35", "35 to 44", "45 to 54", "55 to 64", "65 to 74", "75 and over"]


def money(x):
    a = abs(x)
    s = f"${a / 1e12:,.1f} trillion" if a >= 1e12 else f"${a / 1e9:,.1f} billion" if a >= 1e9 else f"${a / 1e6:,.1f} million" if a >= 1e6 else f"${a:,.0f}"
    return ("-" if x < 0 else "") + s


# ---------- advisers (SEC Form ADV) ----------
ADV = data_root() / "tmp" / "scaffold" / "adv"
KEEP = {"Organization CRD#": "crd", "Primary Business Name": "name", "Main Office State": "st", "Main Office Country": "country",
        "Latest ADV Filing Date": "filed", "5A": "employees", "5B(1)": "advisory_staff", "5D(a)(1)": "a_n", "5D(a)(2)": "a_few",
        "5D(a)(3)": "a_aum", "5D(b)(1)": "b_n", "5D(b)(2)": "b_few", "5D(b)(3)": "b_aum", "5E(1)": "pct_aum", "5E(2)": "hourly",
        "5E(3)": "subscription", "5E(4)": "fixed", "5E(5)": "commission", "5E(6)": "performance", "5E(7)": "other_comp",
        "5F(2)(c)": "aum", "5F(2)(f)": "accounts", "5G(1)": "planning", "5G(2)": "pm_individuals", "5H": "plan_clients",
        "5H-If more than 500, how many": "plan_more", "5I(1)": "wrap"}
adv_raw = sorted((ADV / "registered").glob("*.CSV"))[-1]
adv_month = "2 November 2026"   # the SEC's zip is named ia110226-registered.zip
with open(adv_raw, encoding="cp1252", newline="") as fh, open(ADV / "slim.csv", "w", encoding="utf-8", newline="") as fo:
    rd = csv.reader(fh)
    hdr = next(rd)
    idx, wr, adv_cols, adv_bad = [hdr.index(k) for k in KEEP], csv.writer(fo), len(hdr), 0
    wr.writerow(KEEP.values())
    for row in rd:
        adv_bad += len(row) != len(hdr)
        wr.writerow([row[i].strip() for i in idx])
con.execute(f"CREATE TABLE s AS SELECT * FROM read_csv('{(ADV / 'slim.csv').as_posix()}', header=true, all_varchar=true)")
con.execute("""CREATE TABLE t AS SELECT * EXCLUDE (a_n, a_aum, b_n, b_aum, aum),
               try_cast(replace(a_n, ',', '') AS DOUBLE) an, try_cast(replace(a_aum, ',', '') AS DOUBLE) aa,
               try_cast(replace(b_n, ',', '') AS DOUBLE) bn, try_cast(replace(b_aum, ',', '') AS DOUBLE) ba,
               try_cast(replace(aum, ',', '') AS DOUBLE) tot, strptime(filed, '%m/%d/%Y')::DATE AS fdate FROM s""")
f_n, f_crd, f_last, f_us = q("SELECT count(*), count(DISTINCT crd), max(fdate), count(*) FILTER (country = 'United States') FROM t")[0]
if f_n != f_crd or adv_bad:
    raise SystemExit(f"adviser file: {f_n} rows, {f_crd} firm numbers, {adv_bad} malformed rows")
(n_ord, n_hnw, n_ind, n_hnw_only, c_ord, a_ord, c_hnw, a_hnw, a_all) = q("""SELECT count(*) FILTER (an > 0), count(*) FILTER (bn > 0),
    count(*) FILTER (an > 0 OR bn > 0), count(*) FILTER (bn > 0 AND coalesce(an, 0) = 0 AND a_few IS NULL),
    sum(an), sum(aa), sum(bn), sum(ba), sum(tot) FROM t""")[0]
noasset_f, noasset_c = q("SELECT count(*), sum(an) FROM t WHERE an > 0 AND coalesce(aa, 0) = 0")[0]
QS = [.1, .25, .5, .75, .9]
fo_n, fo_q = q(f"SELECT count(*), quantile_cont(aa / an, {QS}) FROM t WHERE an >= 5 AND aa > 0")[0]
fh_n, fh_q = q(f"SELECT count(*), quantile_cont(ba / bn, {QS}) FROM t WHERE bn >= 5 AND ba > 0")[0]
BK = ((0, "Under $10,000"), (1e4, "$10,000 to $50,000"), (5e4, "$50,000 to $100,000"), (1e5, "$100,000 to $250,000"),
      (2.5e5, "$250,000 to $500,000"), (5e5, "$500,000 to $1 million"), (1e6, "$1 million and over"))
bcase = "CASE " + " ".join(f"WHEN aa / an >= {lo} THEN {i}" for i, (lo, _) in reversed(list(enumerate(BK)))) + " END"
buckets = q(f"SELECT {bcase} b, count(*), sum(an), sum(aa) FROM t WHERE an >= 5 AND aa > 0 GROUP BY 1 ORDER BY 1")
bk_firms, bk_clients = sum(r[1] for r in buckets), sum(r[2] for r in buckets)
small_share = sum(r[2] for r in buckets if r[0] <= 1) / bk_clients * 100
hist = {k: dict(q(f"SELECT floor(log10({a} / {n}) * 5)::INT, count(*) FROM t WHERE {n} >= 5 AND {a} > 0 GROUP BY 1"))
        for k, a, n in (("ord", "aa", "an"), ("hnw", "ba", "bn"))}
hb = list(range(min(min(h) for h in hist.values()), max(max(h) for h in hist.values()) + 1))
COMP = (("pct_aum", "A percentage of assets managed"), ("fixed", "Fixed fees"), ("hourly", "Hourly charges"),
        ("performance", "Performance-based fees"), ("commission", "Commissions"), ("subscription", "Subscription fees"), ("other_comp", "Other"))
comp = q("SELECT " + ", ".join(f"count(*) FILTER ({k} = 'Y') * 100.0 / count(*)" for k, _ in COMP) + ", count(*) FROM t WHERE an > 0 OR bn > 0")[0]
only_pct = q("""SELECT count(*) FILTER (pct_aum = 'Y' AND fixed <> 'Y' AND hourly <> 'Y' AND commission <> 'Y' AND subscription <> 'Y'
                AND performance <> 'Y' AND other_comp <> 'Y') * 100.0 / count(*) FROM t WHERE an > 0 OR bn > 0""")[0][0]
plan_y = q("SELECT count(*) FILTER (planning = 'Y') * 100.0 / count(*) FROM t WHERE an > 0 OR bn > 0")[0][0]
top_firms = q("SELECT name, an, aa, aa / an, bn, ba FROM t WHERE an > 0 ORDER BY an DESC LIMIT 12")
top_share = sum(r[1] for r in top_firms) / c_ord * 100

# ---------- charts ----------
font = {"family": "system-ui, sans-serif", "size": 13, "color": "#1d1d1b"}
base = {"margin": {"l": 55, "r": 20, "t": 10, "b": 50}, "font": font, "plot_bgcolor": "#ffffff", "paper_bgcolor": "#ffffff",
        "legend": {"orientation": "h", "y": 1.14}, "hovermode": "x unified"}
ypct = {"gridcolor": "#e3e1da", "rangemode": "tozero", "ticksuffix": "%", "title": {"text": "Share of households in the group"}}
LAB = [lab for _, lab in CUTS]


def lines(rows):
    return [{"x": LAB, "y": [r[7 + i] for r in rows], "name": lab, "type": "scatter", "mode": "lines+markers",
             "line": {"color": col, "width": 3 if k == "planner" else 1.8}, "hovertemplate": "%{y:.0f}%"}
            for i, (k, _, lab, col) in enumerate(SRC)]


vx = [f"{v * 5} to {v * 5 + 5}" for v, *_ in vent]
charts = [
    ("c1", lines(nw), {**base, "yaxis": ypct, "xaxis": {"title": {"text": "Households ranked by net worth"}}}),
    ("c2", [{"x": vx, "y": [r[3] for r in vent], "name": "Financial planner", "type": "scatter", "mode": "lines+markers",
             "line": {"color": "#2563eb", "width": 3}, "hovertemplate": "%{y:.0f}%"},
            {"x": vx, "y": [r[4] for r in vent], "name": "Friend or relative", "type": "scatter", "mode": "lines+markers",
             "line": {"color": "#dc2626", "width": 1.8}, "hovertemplate": "%{y:.0f}%"},
            {"x": vx, "y": [r[5] for r in vent], "name": "Has a managed account, trust or annuity", "type": "scatter", "mode": "lines+markers",
             "line": {"color": "#111827", "width": 1.8, "dash": "dot"}, "hovertemplate": "%{y:.0f}%"}],
     {**base, "yaxis": ypct, "xaxis": {"title": {"text": "Households ranked by net worth, twenty equal slices (percent of households)"}, "tickangle": -45},
      "margin": {"l": 55, "r": 20, "t": 10, "b": 90}}),
    ("c3", lines(inc), {**base, "yaxis": ypct, "xaxis": {"title": {"text": "Households ranked by income"}}}),
]
charts += [
    ("c4", [{"x": [b + .5 for b in hb], "y": [hist[k].get(b, 0) for b in hb], "name": lab, "type": "bar", "marker": {"color": col},
             "customdata": [10 ** ((b + .5) / 5) for b in hb],
             "opacity": .75, "hovertemplate": "about $%{customdata:,.0f} per client: %{y} firms<extra>" + lab + "</extra>"}
            for k, lab, col in (("ord", "Ordinary individual clients", "#2563eb"), ("hnw", "High net worth clients", "#d97706"))],
     {**base, "barmode": "overlay", "hovermode": "closest", "yaxis": {"gridcolor": "#e3e1da", "title": {"text": "Adviser firms"}},
      "bargap": 0.05, "xaxis": {"title": {"text": "Assets managed per client at the firm"}, "tickvals": [15, 20, 25, 30, 35, 40, 45],
                "ticktext": ["$1,000", "$10,000", "$100,000", "$1 million", "$10 million", "$100 million", "$1 billion"]}}),
    ("c5", [{"y": [lab for _, lab in COMP][::-1], "x": list(comp[:len(COMP)])[::-1], "type": "bar", "orientation": "h", "marker": {"color": "#2563eb"},
             "hovertemplate": "%{y}: %{x:.0f}%<extra></extra>"}],
     {**base, "showlegend": False, "hovermode": "closest", "xaxis": {"gridcolor": "#e3e1da", "ticksuffix": "%", "range": [0, 100]},
      "yaxis": {"automargin": True}, "margin": {"l": 230, "r": 20, "t": 10, "b": 40}}),
]
script = "".join(f"Plotly.newPlot('{cid}', {json.dumps(tr)}, {json.dumps(lay)}, {{responsive:true}});" for cid, tr, lay in charts)
div = "<div id={} style='height:{}px'></div>"


def table(rows, what):
    head = (f"<tr><th>Group</th><th>Households surveyed</th><th>{what}, from</th><th>to</th>"
            + "".join(f"<th>{lab}</th>" for _, _, lab, _ in SRC)
            + "<th>Has a managed account, trust or annuity</th><th>Owns stocks directly</th><th>Has a retirement account</th></tr>")
    body = "".join(f"<tr><td>{LAB[r[0]]}</td><td class=num>{r[1]:,}</td><td class=num>{money(r[2])}</td><td class=num>{money(r[3])}</td>"
                   + "".join(f"<td class=num>{x:.0f}%</td>" for x in r[7:]) + "".join(f"<td class=num>{x:.0f}%</td>" for x in r[4:7]) + "</tr>"
                   for r in rows)
    return f"<div class=wrap><table>{head}{body}</table></div>"


vent_rows = "".join(f"<tr><td>{vx[i]}</td><td class=num>{r[6]:,}</td><td class=num>{money(r[1])}</td><td class=num>{money(r[2])}</td>"
                    f"<td class=num>{r[3]:.0f}%</td><td class=num>{r[4]:.0f}%</td><td class=num>{r[5]:.0f}%</td></tr>" for i, r in enumerate(vent))
age_rows = "".join(f"<tr><td>{AGES[int(r[0]) - 1]}</td>" + "".join(f"<td class=num>{x:.0f}%</td>" for x in r[1:]) + "</tr>" for r in age)
top, bot, mid = nw[-1], nw[1], nw[4]
add = [
    ("Rows are households times five", f"{n_rows:,} rows, {n_hh:,} households, {per_hh} rows each",
     "The Fed fills in missing answers five ways, so each household appears five times; each row carries a fifth of the household's weight."),
    ("Weights add up to US households", f"{hh_m:,.1f} million", "Sum of the weight column in the summary file."),
    ("The two files describe the same rows", f"{n_join:,} of {n_rows:,} rows joined; largest weight difference {wdiff:.6f}",
     "Joined on the row number; the summary weight should be the full file's weight divided by five."),
    ("My reading of the answer codes matches the Fed's", f"{mism} rows disagree",
     "The Fed's summary flag for lawyer, accountant or financial planner, rebuilt here from the three separate codes."),
]
add_rows = "".join(f"<tr><td>{esc(a)}</td><td>{esc(b)}</td><td>{esc(c)}</td></tr>" for a, b, c in add)
odd = [
    ("The question is about information, not payment.",
     "The survey asks which sources of information the household uses to make decisions about saving and investments. A household that names a "
     "financial planner may pay a fee, hold an account with one, or have had one conversation. Nothing here measures what anyone pays."),
    ("The survey does not ask who prepared the tax return.",
     "The codebook mentions tax preparers only as an occupation code. Paid tax help by income is on the tax filers page, from IRS data."),
    ("Mostly a slope, with one visible jump.",
     f"Across twenty equal slices of households by net worth, the planner share rises from {vent[0][3]:.0f}% to {vent[-1][3]:.0f}%. "
     f"The largest rise between neighbouring slices is {vent[jump][3] - vent[jump - 1][3]:.0f} points (into the slice starting at {money(vent[jump][1])}); "
     "most slices hold 200 to 300 surveyed households, so a few points either way is noise."),
    ("Planners overtake friends and relatives at the same place.",
     (f"The first slice where more households name a financial planner than a friend or relative starts at net worth {money(cross[1])} "
      f"(households {vx[cross[0]]} percent from the bottom): {cross[3]:.0f}% against {cross[4]:.0f}%." if cross else "No slice has planners ahead.")),
    ("Wealthy households use more of every professional, and the planner is not replaced.",
     f"In the top 1% by net worth, {top[7]:.0f}% name a financial planner, {top[8]:.0f}% an accountant, {top[9]:.0f}% a broker and {top[11]:.0f}% a lawyer; "
     f"in the group from the 10th to the 25th percentile the same shares are {bot[7]:.0f}%, {bot[8]:.0f}%, {bot[9]:.0f}% and {bot[11]:.0f}%."),
    ("Bankers peak in the middle.",
     f"The banker share is highest in the group {LAB[max(nw, key=lambda r: r[10])[0]]} ({max(r[10] for r in nw):.0f}%) and lower at both ends "
     f"({nw[0][10]:.0f}% at the bottom, {top[10]:.0f}% at the top)."),
    ("The top groups are heavily sampled on purpose.",
     f"The top 1% by net worth is {top[1]:,} of the {n_hh:,} surveyed households. The survey over-samples wealthy households and the weights correct for it."),
]
odd_rows = "".join(f"<tr><td>{esc(a)}</td><td>{esc(b)}</td></tr>" for a, b in odd)

bucket_rows = "".join(f"<tr><td>{BK[int(r[0])][1]}</td><td class=num>{r[1]:,}</td><td class=num>{r[1] / bk_firms * 100:.0f}%</td>"
                      f"<td class=num>{r[2] / 1e6:,.2f}</td><td class=num>{r[2] / bk_clients * 100:.0f}%</td><td class=num>{money(r[3])}</td></tr>" for r in buckets)
top_rows = "".join(f"<tr><td>{esc(r[0])}</td><td class=num>{r[1] / 1e6:,.2f}</td><td class=num>{money(r[2] or 0)}</td>"
                   f"<td class=num>{'' if not r[2] else money(r[3])}</td><td class=num>{'' if not r[4] else f'{r[4]:,.0f}'}</td></tr>" for r in top_firms)


def qrow(lab, n, v):
    return f"<tr><td>{lab}</td><td class=num>{n:,}</td>" + "".join(f"<td class=num>{money(round(x, -3))}</td>" for x in v) + "</tr>"


adv_odd = [
    ("Clients are not households.",
     f"Firms report {c_ord / 1e6:,.1f} million ordinary individual clients and {c_hnw / 1e6:,.1f} million high net worth clients. The survey in part 1 counts "
     f"{hh_m:,.1f} million households. One household can be a client of several firms, and a firm that manages money on behalf of another firm reports the same people again."),
    ("The typical firm and the typical client are far apart.",
     f"The median firm manages {money(round(fo_q[2], -3))} per ordinary client. But {small_share:.0f}% of ordinary clients are at firms averaging under $50,000 each, "
     f"and the largest single firm by clients, {top_firms[0][0].title()}, reports {top_firms[0][1] / 1e6:,.1f} million clients at {money(top_firms[0][3])} each."),
    ("Few firms work with small accounts.",
     f"Of {fo_n:,} firms with at least five ordinary clients, nine in ten average more than {money(round(fo_q[0], -3))} per client. "
     f"In part 1, planner use jumps at about $600,000 of household net worth."),
    ("Some firms take only the wealthy.",
     f"{n_hnw_only:,} of the {n_ind:,} firms with individual clients report high net worth clients and no ordinary ones."),
    ("Almost every firm charges a percentage of assets.",
     f"{comp[0]:.0f}% of firms with individual clients charge a percentage of assets; {only_pct:.0f}% charge that way and no other. "
     f"{comp[2]:.0f}% offer hourly charges and {comp[1]:.0f}% fixed fees. {plan_y:.0f}% say they offer financial planning."),
    ("Minimum account sizes and fee rates are not in this file.",
     "Each firm states them in a brochure filed as a document, not as data. Assets per client is what clients turned out to have, not the firm's stated minimum."),
    ("Some firms report clients without assets.",
     f"{noasset_f} firms report ordinary clients ({noasset_c:,.0f} in total) and no assets for them. They are left out of the per-client figures."),
    ('"High net worth" is the form\'s term, not checked today.',
     "From memory, the form means a person with at least $1.1 million managed by the firm or net worth above $2.2 million. "
     f"The reported figures fit that reading: the median firm has {money(round(fh_q[2], -3))} per high net worth client."),
]
adv_odd_rows = "".join(f"<tr><td>{esc(a)}</td><td>{esc(b)}</td></tr>" for a, b in adv_odd)

body = f"""<h1>Who gets financial advice</h1>
<p class=sub>Temporary exploration page, built {now_et()}. Source: Federal Reserve, Survey of Consumer Finances 2025, public files.
One question: "What sources of information do you use to make decisions about saving and investments?" Households may name several.
Part 2 is the other side: the adviser firms, from the SEC's file of registered investment advisers.</p>

<h1 style="margin-top:28px">Part 1. Households</h1>

<h2>What was pulled</h2>
<table><tr><th>File</th><th>What it holds</th><th>Size</th></tr>
<tr><td>Summary file (SCFP2025.csv)</td><td>The Fed's own computed variables for each household: weight, income, net worth, what it owns</td>
<td class=num>{n_rows:,} rows, {n_cols} columns</td></tr>
<tr><td>Full public file (p25i6.dta)</td><td>Every survey answer. Read here: only the 15 answer slots of the information-sources question</td>
<td class=num>{n_rows:,} rows, {len(SLOTS)} columns read</td></tr></table>
<p class=sub>One row, as joined: household {ex[0]}, row {ex[1]}, weight {ex[2]:,.1f}, income {money(ex[3])}, net worth {money(ex[4])},
names a financial planner: {'yes' if ex[5] else 'no'}, an accountant: {'yes' if ex[6] else 'no'}, a friend or relative: {'yes' if ex[7] else 'no'}.</p>
<p class=sub>All households: financial planner {overall['planner']:.1f}%, accountant {overall['accountant']:.1f}%, broker {overall['broker']:.1f}%,
banker {overall['banker']:.1f}%, lawyer {overall['lawyer']:.1f}%, friend or relative {overall['friend']:.1f}%, internet {overall['internet']:.1f}%.
{nosave:.1f}% say they do not save or invest.</p>

<h2>Does it add up</h2>
<table><tr><th>Check</th><th>Result</th><th>What it compares</th></tr>{add_rows}</table>

<h2>What looks odd</h2>
<table><tr><th>Finding</th><th>Evidence</th></tr>{odd_rows}</table>

<h2>By net worth</h2>
<h3>Share of households naming each source, by net worth group</h3>
{div.format('c1', 400)}
{table(nw, 'Net worth')}
<h3>Is there a cutoff? Twenty equal slices of households by net worth</h3>
{div.format('c2', 400)}
<div class=wrap><table><tr><th>Slice, percent of households from the bottom</th><th>Households surveyed</th><th>Net worth, from</th><th>to</th>
<th>Financial planner</th><th>Friend or relative</th><th>Has a managed account, trust or annuity</th></tr>{vent_rows}</table></div>
<p class=sub>"Managed account, trust or annuity" is the Fed's summary category for assets someone else manages for the household.</p>

<h2>By income</h2>
<h3>Share of households naming each source, by income group</h3>
{div.format('c3', 400)}
{table(inc, 'Income')}

<h2>By age of the household head</h2>
<table><tr><th>Age</th><th>Financial planner</th><th>Friend or relative</th><th>Internet</th></tr>{age_rows}</table>

<h1 style="margin-top:36px">Part 2. Adviser firms</h1>
<p class=sub>Source: SEC, registered investment advisers, file of {adv_month} (latest filing in it: {f_last}). Each firm reports on Form ADV
how many clients of each kind it has, the assets it manages for them, and how it charges.</p>

<h2>What was pulled</h2>
<table><tr><th>File</th><th>What it holds</th><th>Size</th></tr>
<tr><td>{esc(adv_raw.name)}</td><td>One row per registered adviser firm, worldwide; {f_us:,} have their main office in the United States</td>
<td class=num>{f_n:,} firms, {adv_cols} columns, {len(KEEP)} read; {adv_bad} malformed rows</td></tr></table>
<table style="margin-top:10px"><tr><th>Kind of client</th><th>Firms that have them</th><th>Clients, millions</th><th>Assets managed</th><th>Assets per client</th></tr>
<tr><td>Individuals, not high net worth ("ordinary" on this page)</td><td class=num>{n_ord:,}</td><td class=num>{c_ord / 1e6:,.1f}</td>
<td class=num>{money(a_ord)}</td><td class=num>{money(a_ord / c_ord)}</td></tr>
<tr><td>High net worth individuals</td><td class=num>{n_hnw:,}</td><td class=num>{c_hnw / 1e6:,.1f}</td>
<td class=num>{money(a_hnw)}</td><td class=num>{money(a_hnw / c_hnw)}</td></tr></table>
<p class=sub>All clients of all kinds, including funds and institutions: {money(a_all)} managed.</p>

<h2>What looks odd</h2>
<table><tr><th>Finding</th><th>Evidence</th></tr>{adv_odd_rows}</table>

<h2>How big is a client</h2>
<h3>Adviser firms by the assets they manage per client</h3>
{div.format('c4', 380)}
<table><tr><th>Assets per client, across firms with at least five such clients</th><th>Firms</th><th>10% of firms are below</th><th>25% below</th><th>Median firm</th>
<th>75% below</th><th>90% below</th></tr>{qrow('Ordinary individual clients', fo_n, fo_q)}{qrow('High net worth clients', fh_n, fh_q)}</table>
<h3>Ordinary clients: where the firms are and where the clients are</h3>
<table><tr><th>Firm's assets per ordinary client</th><th>Firms</th><th>Share of firms</th><th>Ordinary clients, millions</th><th>Share of clients</th>
<th>Assets managed for them</th></tr>{bucket_rows}</table>
<h3>The twelve firms with the most ordinary clients ({top_share:.0f}% of all ordinary clients)</h3>
<table><tr><th>Firm, as named in the file</th><th>Ordinary clients, millions</th><th>Assets managed for them</th><th>Per client</th><th>High net worth clients</th></tr>{top_rows}</table>

<h2>How firms charge</h2>
<h3>Share of the {comp[len(COMP)]:,} firms with individual clients that use each kind of charge</h3>
{div.format('c5', 300)}
<p class=sub>A firm can tick several. The file says which kinds a firm uses, not the rate.</p>"""

out = HERE / "advice-sources.html"
out.write_text(page("Who households turn to for investment decisions", "Ideas", body, crumb="Advice sources",
                    head=f'<script src="{PLOTLY}"></script>')
               .replace("</main></body>", f"</main><script>{script}</script></body>"), encoding="utf-8")
print(f"wrote {out}: {n_hh:,} households, {hh_m:.1f}M weighted, planner {overall['planner']:.1f}%, "
      f"crossover at {money(cross[1]) if cross else 'none'}, biggest step {vent[jump][3] - vent[jump - 1][3]:.0f} pts at {money(vent[jump][1])}")

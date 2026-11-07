"""Reporting Consistency: does a company change annual net income it already reported? Every 10-K repeats prior years
as comparatives, and Company Facts keeps each filing's value, so a figure's first-reported and latest values can be
compared. Reads catalog.duckdb read-only; writes consistency.html next to this file."""
import csv
import html as h
import sys
from datetime import datetime
from pathlib import Path

import duckdb
import plotly.graph_objects as go
import plotly.io as pio
import polars as pl

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "lib"))
import base  # noqa: E402
import nav  # noqa: E402

WINDOW = (2014, 2023)      # ten fiscal years, each re-reported in at least two later 10-Ks for a filer that kept filing
SHOW = (2011, 2024)        # heatmap columns
TOP_N = 40                 # largest companies by 2024 revenue in the second heatmap
BUCKETS = ("under 1%", "1-5%", "5-20%", "over 20%")
BUCKET_COLORS = ("#bfdbfe", "#fcd34d", "#f97316", "#b91c1c")

con = duckdb.connect(base.CATALOG, read_only=True)
con.execute("SET memory_limit = '6GB'")

# One row per filing that reported a company's one-year net income, from 10-K and 10-K/A only, never before the
# period ended. A key is (cik, start_date, end_date): one fiscal year as the company defined it.
con.execute("""
create temp table versions as
select cik, start_date, end_date, year(end_date - interval 182 day) as fy, accession, form, filed, val
from serving_company_facts
where taxonomy = 'us-gaap' and unit = 'USD' and concept = 'NetIncomeLoss'
  and form in ('10-K', '10-K/A') and date_diff('day', start_date, end_date) between 350 and 380
  and end_date <= filed""")

# Per key: the first-reported value, the latest value, and the first filing that disagreed with the first value.
con.execute("""
create temp table keys as
with ordered as (
    select *, first_value(val) over w as first_v, row_number() over w as seq
    from versions window w as (partition by cik, start_date, end_date order by filed, accession)),
diff as (
    select cik, start_date, end_date, min(seq) as first_diff_seq from ordered where val <> first_v group by all),
agg as (
    select cik, start_date, end_date, any_value(fy) as fy, count(distinct accession) as n_filings,
           arg_min(val, (filed, accession)) as first_v, arg_max(val, (filed, accession)) as last_v,
           min(filed) as first_filed
    from versions group by all)
select a.*, (d.first_diff_seq is not null) as changed,
       (select o.form from ordered o where o.cik = a.cik and o.start_date = a.start_date and o.end_date = a.end_date
          and o.seq = d.first_diff_seq) as changed_in,
       (select o.filed from ordered o where o.cik = a.cik and o.start_date = a.start_date and o.end_date = a.end_date
          and o.seq = d.first_diff_seq) as changed_on
from agg a left join diff d using (cik, start_date, end_date)""")

k = con.sql("""
select *,
  case when not changed then null
       when first_v = 0 then 'over 20%'
       when abs(last_v - first_v) / abs(first_v) < 0.01 then 'under 1%'
       when abs(last_v - first_v) / abs(first_v) < 0.05 then '1-5%'
       when abs(last_v - first_v) / abs(first_v) < 0.20 then '5-20%'
       else 'over 20%' end as size,
  case when not changed then null
       when first_v <> 0 and last_v <> 0 and (abs(last_v / first_v) between 900 and 1100
                                             or abs(first_v / last_v) between 900 and 1100) then 'scale error'
       when changed_in = '10-K/A' then 'amended 10-K'
       else 'later 10-K' end as how,
  case when first_v = 0 then null else (last_v - first_v) / abs(first_v) end as pct
from keys""").pl()
# Scale errors (a value tagged in thousands as dollars, or the reverse) are tagging mistakes, not accounting changes.
k = k.with_columns(pl.when(pl.col("how") == "scale error").then(None).otherwise(pl.col("size")).alias("size"))

names = con.sql("select cik, name from serving_submission_details").pl()
tick = con.sql("select ticker, cik from serving_tickers").pl()

# ---------- population by fiscal year ----------
rep = k.filter((pl.col("n_filings") > 1) & pl.col("fy").is_between(2010, 2024))
by_fy = (rep.group_by("fy").agg(pl.len().alias("repeated"),
                                *[(pl.col("size") == b).sum().alias(b) for b in BUCKETS],
                                (pl.col("how") == "scale error").sum().alias("scale"),
                                (pl.col("how") == "amended 10-K").sum().alias("amended"),
                                (pl.col("how") == "later 10-K").sum().alias("later"))
         .sort("fy"))

fig_fy = go.Figure()
for b, c in zip(BUCKETS, BUCKET_COLORS):
    fig_fy.add_trace(go.Bar(x=by_fy["fy"], y=by_fy[b] / by_fy["repeated"], name=f"changed by {b}", marker_color=c,
                            customdata=by_fy[b], hovertemplate="%{x}: %{y:.1%} (%{customdata:,} figures)<extra></extra>"))
fig_fy.update_layout(barmode="stack", height=430, template="plotly_white", yaxis_tickformat=".0%",
                     yaxis_title="Share of re-reported figures", xaxis_title="Fiscal year of the figure",
                     title="Annual net income later changed, by size of the change",
                     legend=dict(orientation="h", y=-0.2), margin=dict(l=60, r=20, t=60, b=100))

fig_how = go.Figure()
for col, label, c in (("later", "changed in a later 10-K's comparative column", "#64748b"),
                      ("amended", "changed by an amended 10-K (10-K/A)", "#b91c1c"),
                      ("scale", "scale error (about 1,000x)", "#a3a3a3")):
    fig_how.add_trace(go.Bar(x=by_fy["fy"], y=by_fy[col], name=label, marker_color=c))
fig_how.update_layout(barmode="stack", height=400, template="plotly_white", yaxis_title="Figures changed",
                      xaxis_title="Fiscal year of the figure", title="Where each change first appeared",
                      legend=dict(orientation="h", y=-0.2), margin=dict(l=60, r=20, t=60, b=100))

# ---------- the rule: per company over the ten-year window ----------
w = k.filter(pl.col("fy").is_between(*WINDOW))
per = (w.group_by("cik").agg(pl.col("fy").n_unique().alias("years"),
                             (pl.col("n_filings") > 1).sum().alias("re_reported"),
                             pl.col("size").is_not_null().sum().alias("changed"),
                             pl.col("size").is_in(["5-20%", "over 20%"]).sum().alias("changed_5"),
                             (pl.col("how") == "amended 10-K").sum().alias("amended"),
                             (pl.col("how") == "scale error").sum().alias("scale"),
                             pl.col("pct").filter(pl.col("size").is_not_null()).abs().max().alias("max_abs"))
       .filter((pl.col("years") == 10) & (pl.col("re_reported") >= 10)))
dist = per.group_by("changed").agg(pl.len().alias("n")).sort("changed")
full = pl.DataFrame({"changed": list(range(0, 11))}).join(dist, on="changed", how="left").fill_null(0)
n_cos = per.height
never = int(full.filter(pl.col("changed") == 0)["n"][0])
once = int(full.filter(pl.col("changed") == 1)["n"][0])
some = int(full.filter(pl.col("changed").is_between(2, 3))["n"].sum())
often = int(full.filter(pl.col("changed") >= 4)["n"].sum())

fig_rule = go.Figure(go.Bar(
    x=full["changed"], y=full["n"], text=[f"{v:,}" for v in full["n"]], textposition="outside",
    marker_color=["#15803d"] + ["#fcd34d"] + ["#f97316"] * 2 + ["#b91c1c"] * 7))
fig_rule.update_layout(height=400, template="plotly_white", xaxis=dict(title="Fiscal years (of ten) whose net income "
                       "was later changed", dtick=1), yaxis_title="Companies",
                       title=f"Companies with net income for all ten fiscal years {WINDOW[0]}-{WINDOW[1]}",
                       margin=dict(l=60, r=20, t=60, b=60))


# ---------- heatmaps ----------
def heat(ciks: list[int], labels: list[str], title: str) -> go.Figure:
    years = list(range(SHOW[0], SHOW[1] + 1))
    sub = k.filter(pl.col("cik").is_in(ciks) & pl.col("fy").is_between(*SHOW))
    # a fiscal-year change can give two keys in one fy: keep the one with the larger change
    sub = sub.sort(pl.col("pct").abs(), nulls_last=True, descending=True).unique(["cik", "fy"], keep="first")
    lut = {(r["cik"], r["fy"]): r for r in sub.iter_rows(named=True)}
    z, text, hover = [], [], []
    level = {None: 0, "under 1%": 1, "1-5%": 2, "5-20%": 3, "over 20%": 4}
    for c, lab in zip(ciks, labels):
        zr, tr, hr = [], [], []
        for y in years:
            r = lut.get((c, y))
            if r is None:
                zr.append(None); tr.append(""); hr.append(f"{lab}, FY{y}: no annual net income filed")
                continue
            first = f"${r['first_v'] / 1e6:,.0f}m"
            if r["n_filings"] == 1:
                zr.append(-1); tr.append("")
                hr.append(f"{lab}, FY{y}: {first}, reported once (not yet repeated)")
            elif r["how"] == "scale error":
                zr.append(5); tr.append("x1000")
                hr.append(f"{lab}, FY{y}: {first} first; a later filing tagged it about 1,000x off (scale error)")
            elif not r["changed"]:
                zr.append(0); tr.append("")
                hr.append(f"{lab}, FY{y}: {first}, same in all {r['n_filings']} filings")
            else:
                zr.append(level[r["size"]]); tr.append("" if r["pct"] is None else f"{r['pct']:+.0%}"
                                                      if abs(r["pct"]) >= 0.01 else f"{r['pct']:+.1%}")
                hr.append(f"{lab}, FY{y}: {first} first, ${r['last_v'] / 1e6:,.0f}m latest; first changed by "
                          f"{'an amended 10-K' if r['how'] == 'amended 10-K' else 'a later 10-K'} filed "
                          f"{r['changed_on']}; {r['n_filings']} filings")
        z.append(zr); text.append(tr); hover.append(hr)
    # discrete scale: -1 once, 0 unchanged, 1-4 buckets, 5 scale error
    stops = [("#f1f5f9", -1), ("#dcfce7", 0)] + list(zip(BUCKET_COLORS, (1, 2, 3, 4))) + [("#a3a3a3", 5)]
    cs = []
    for i, (col, _) in enumerate(stops):
        cs += [[i / len(stops), col], [(i + 1) / len(stops), col]]
    fig = go.Figure(go.Heatmap(z=z, x=[str(y) for y in years], y=labels, text=text, texttemplate="%{text}",
                               textfont=dict(size=10), customdata=hover, hovertemplate="%{customdata}<extra></extra>",
                               colorscale=cs, zmin=-1.5, zmax=5.5, showscale=False, xgap=2, ygap=2))
    fig.update_layout(height=60 + 22 * len(ciks), template="plotly_white", title=title,
                      yaxis=dict(autorange="reversed", tickfont=dict(size=11)), xaxis=dict(side="top"),
                      margin=dict(l=230, r=20, t=90, b=20))
    return fig


def label_for(c: int, ticker: str | None = None) -> str:
    n = names.filter(pl.col("cik") == c)["name"]
    nm = (n[0] if n.len() else str(c))[:28]
    return f"{ticker}  {nm}" if ticker else nm


with open(base.ROOT + "/config/watchlist.csv", newline="", encoding="utf-8") as f:
    wl = [r["ticker"] for r in csv.DictReader(f)]
wl_rows = [(t, tick.filter(pl.col("ticker") == t.replace(".", "-"))["cik"]) for t in wl]   # SEC writes BRK-B
wl_found = [(t, int(c[0])) for t, c in wl_rows if c.len()]
wl_missing = [t for t, c in wl_rows if not c.len()]
fig_wl = heat([c for _, c in wl_found], [label_for(c, t) for t, c in wl_found], "Watchlist")

top = base.revenue_year(2024).sort("revenue", descending=True).head(TOP_N)
fig_top = heat(top["cik"].to_list(), [label_for(c) for c in top["cik"].to_list()],
               f"The {TOP_N} largest companies by 2024 revenue")

# ---------- worst offenders among large companies ----------
big = base.revenue_year(2024).filter(pl.col("revenue") >= 1e9).select("cik", "revenue")
off = (per.join(big, on="cik").join(names, on="cik", how="left")
       .sort(["changed_5", "changed", "max_abs"], descending=True).head(25))


def table(df: pl.DataFrame) -> str:
    rows = ""
    for r in df.iter_rows(named=True):
        largest = "" if r["max_abs"] is None else f"{r['max_abs']:.0%}"
        rows += (f"<tr><td>{h.escape(r['name'] or str(r['cik']))}</td><td class=num>{r['revenue'] / 1e9:,.1f}</td>"
                 f"<td class=num>{r['changed']}</td><td class=num>{r['changed_5']}</td>"
                 f"<td class=num>{r['amended']}</td><td class=num>{largest}</td></tr>")
    return ("<div class=wrap><table><thead><tr><th>Company</th><th class=num>2024 revenue $bn</th>"
            "<th class=num>Years changed</th><th class=num>Changed 5%+</th><th class=num>By 10-K/A</th>"
            "<th class=num>Largest change</th></tr></thead><tbody>" + rows + "</tbody></table></div>")


first = True


def div(fig: go.Figure) -> str:
    global first
    out = pio.to_html(fig, full_html=False, include_plotlyjs=first, config={"displayModeBar": False})
    first = False
    return out


all_rep = rep.height
all_chg = int(rep["size"].is_not_null().sum())
all_5 = int(rep["size"].is_in(["5-20%", "over 20%"]).sum())
all_scale = int((rep["how"] == "scale error").sum())
built = datetime.now().strftime("%Y-%m-%d %H:%M")
pc = lambda a, b: f"{a / b:.0%}"  # noqa: E731

page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Reporting Consistency</title>
<style>
:root {{ --bg:#ffffff; --fg:#1d1d1b; --muted:#5f5e58; --line:#e3e1da; --box:#f6f5f1; }}
body {{ background:var(--bg); color:var(--fg); font:15px/1.5 system-ui, "Segoe UI", sans-serif; margin:0; padding:24px 16px 60px; }}
main {{ max-width:1250px; margin:0 auto; }}
h1 {{ font-size:24px; margin:0 0 6px; }}
h2 {{ font-size:19px; margin:34px 0 6px; }}
.muted {{ color:var(--muted); }}
.lead {{ background:var(--box); padding:12px 16px; margin:12px 0 16px; }}
.wrap {{ overflow-x:auto; }}
table {{ border-collapse:collapse; width:100%; font-size:14px; }}
th, td {{ border-bottom:1px solid var(--line); padding:5px 10px; text-align:left; vertical-align:top; }}
td.num, th.num {{ text-align:right; white-space:nowrap; }}
.key span {{ display:inline-block; padding:1px 8px; margin:2px 4px 2px 0; font-size:13px; }}
{nav.CSS}
</style></head>
<body>{nav.html("Reporting Consistency")}<main>
<h1>Reporting Consistency</h1>
<div class="lead">The rule: does a company change annual net income it has already reported? Each 10-K repeats the
prior two years, and Pharos keeps every filing's value, so each year's first-reported figure can be compared with
the latest. Of {n_cos:,} companies with net income for all ten fiscal years {WINDOW[0]}-{WINDOW[1]}, each repeated
in a later 10-K, <b>{never:,} ({pc(never, n_cos)}) never changed one</b>; {once:,} changed one year, {some:,} two or
three, and {often:,} four or more.</div>
<p class="muted">What this measures: changes to already-reported results, not guidance or earnings forecasts, which
are not in SEC's tagged data. One figure is one company's net income for one fiscal year (us-gaap NetIncomeLoss, the
one-year figure, from 10-K and 10-K/A filings). Changed means any later filing reported a different value; the size
is latest against first, as a share of the first. A change of about 1,000x is a tagging scale error, shown apart and
not counted. Page built {built}.</p>

<h2>1. How often reported net income changes</h2>
<p>Across {all_rep:,} figures repeated in at least one later 10-K (fiscal years 2010-2024), {all_chg:,}
({pc(all_chg, all_rep)}) were later changed, and {all_5:,} ({pc(all_5, all_rep)}) by 5% or more. A further
{all_scale:,} changed only through a scale error. The latest years are lower because they have been repeated fewer
times: fiscal 2024 has had one chance, fiscal 2014 has had two plus any amendment.</p>
{div(fig_fy)}

<h2>2. Where the change first appeared</h2>
<p>Most changes surface quietly, in the comparative column of the next 10-K, not in an amended 10-K. Likely causes in the largest cases (read from
the companies' known histories, not yet checked against the filing text): real error corrections (BorgWarner 2016, by 10-K/A), new accounting rules applied to past years
(insurers under the 2023 long-duration insurance standard: Brighthouse, Equitable; revenue recognition in 2018),
mergers that rewrite history (T-Mobile's 2011-2012 figures became the pre-merger company's; Paramount after the 2019
CBS-Viacom merger), and scale errors (Day One Biopharmaceuticals, about 1,000x). Telling these apart needs the
filing text; the page does not try yet.</p>
{div(fig_how)}

<h2>3. The rule, applied: companies by years changed</h2>
<p>Green never changed a reported net income in ten years; amber changed one year; orange two or three; red four or
more.</p>
{div(fig_rule)}

<h2>4. Year by year: the watchlist</h2>
<p class="key"><span style="background:#dcfce7">same in every filing</span><span style="background:#bfdbfe">changed
under 1%</span><span style="background:#fcd34d">1-5%</span><span style="background:#f97316">5-20%</span><span
style="background:#b91c1c;color:#fff">over 20%</span><span style="background:#a3a3a3">scale error</span><span
style="background:#f1f5f9">reported once so far</span> Each cell is one fiscal year; the number is the change, latest
against first. Hover for the values and the filing that changed it.{' Not in SEC\'s ticker map: ' + ', '.join(wl_missing) + '.' if wl_missing else ''}</p>
{div(fig_wl)}

<h2>5. Year by year: the {TOP_N} largest companies</h2>
{div(fig_top)}

<h2>6. Large companies that changed the most</h2>
<p>Companies with 2024 revenue of $1bn or more and net income for all ten years, ranked by years changed by 5% or
more, then by years changed at all.</p>
{table(off)}
</main></body></html>"""

(HERE / "consistency.html").write_text(page, encoding="utf-8")
print("wrote", HERE / "consistency.html", f"companies={n_cos} never={never} once={once} some={some} often={often}")

"""Where GDP Goes: GDP split by who spends it (consumers, investment, government, exports minus imports), trade
against GDP, the monthly trade balance, and public companies' foreign share of pretax income by sector.

Quick lane (CLAUDE.md, two lanes): the six FRED series are fetched at build time, today's values only, nothing
stored. GDP comes from Pharos (today's vintage); the four spending parts must add up to it, or the build stops.
Company figures come from Company Facts in Pharos. Writes trade.html next to this file."""
import json
import os
import sys
import urllib.parse
import urllib.request
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

KEY = os.environ["FRED_API_KEY"]
PARTS = (("PCEC", "Consumers", "#2563eb"), ("GPDI", "Business and housing investment", "#f97316"),
         ("GCE", "Government", "#15803d"), ("NETEXP", "Exports minus imports", "#b91c1c"))


def fred(series_id: str, start: str = "1947-01-01") -> pl.DataFrame:
    obs = json.load(urllib.request.urlopen("https://api.stlouisfed.org/fred/series/observations?" + urllib.parse.urlencode(
        {"series_id": series_id, "api_key": KEY, "file_type": "json", "observation_start": start})))["observations"]
    return pl.DataFrame({"date": [datetime.strptime(o["date"], "%Y-%m-%d").date() for o in obs],
                         series_id: [float(o["value"]) if o["value"] != "." else None for o in obs]})


q = fred("PCEC")
for s in ("GPDI", "GCE", "NETEXP", "EXPGS", "IMPGS"):
    q = q.join(fred(s), on="date", how="inner")
con = duckdb.connect(base.CATALOG, read_only=True)
gdp = con.sql("""select obs_date as date, value as GDP from serving_observations where series_id = 'fred:GDP'
                 qualify row_number() over (partition by obs_date order by vintage desc) = 1""").pl().drop_nulls()
q = q.join(gdp, on="date", how="inner").drop_nulls().sort("date")

# The identity: consumers + investment + government + net exports = GDP. FRED rounds to $0.1bn.
gap = q.select(((pl.col("PCEC") + pl.col("GPDI") + pl.col("GCE") + pl.col("NETEXP") - pl.col("GDP")).abs()
                / pl.col("GDP")).max()).item()
if gap > 0.001:
    raise SystemExit(f"spending parts do not add up to Pharos's GDP: worst quarter off by {gap:.3%}")
q = q.with_columns(*[(pl.col(s) / pl.col("GDP")).alias(f"{s}_sh") for s in ("PCEC", "GPDI", "GCE", "NETEXP",
                                                                            "EXPGS", "IMPGS")])

# 1. who spends GDP
fig1 = go.Figure()
for s, name, c in PARTS:
    fig1.add_trace(go.Scatter(x=q["date"], y=q[f"{s}_sh"], name=name, mode="lines", line=dict(color=c, width=2)))
fig1.add_hline(y=0, line_color="#94a3b8")
fig1.update_layout(height=440, template="plotly_white", yaxis_tickformat=".0%", yaxis_title="Share of GDP",
                   title="Who spends GDP: the four parts as shares, quarterly since 1947",
                   legend=dict(orientation="h", y=-0.15), margin=dict(l=60, r=20, t=60, b=80))

# 2. trade against GDP
fig2 = go.Figure()
fig2.add_trace(go.Bar(x=q["date"], y=q["NETEXP_sh"], name="Trade balance: green surplus, red deficit",
                      marker_color=["#15803d" if v >= 0 else "#b91c1c" for v in q["NETEXP_sh"]]))
fig2.add_trace(go.Scatter(x=q["date"], y=q["EXPGS_sh"], name="Exports", mode="lines", line=dict(color="#2563eb", width=2)))
fig2.add_trace(go.Scatter(x=q["date"], y=q["IMPGS_sh"], name="Imports", mode="lines", line=dict(color="#f97316", width=2)))
fig2.update_layout(height=440, template="plotly_white", yaxis_tickformat=".0%", yaxis_title="Share of GDP",
                   title="Exports and imports as shares of GDP", bargap=0,
                   legend=dict(orientation="h", y=-0.15), margin=dict(l=60, r=20, t=60, b=80))

# 3. monthly trade balance
m = fred("BOPGSTB", "1992-01-01").drop_nulls()
fig3 = go.Figure(go.Bar(x=m["date"], y=m["BOPGSTB"] / 1e3, marker_color="#b91c1c", name="Trade balance",
                        hovertemplate="%{x|%b %Y}: $%{y:.1f}bn<extra></extra>"))
fig3.update_layout(height=400, template="plotly_white", yaxis_title="$ billions a month", bargap=0,
                   title="Monthly trade balance, goods and services, since 1992", margin=dict(l=60, r=20, t=60, b=40))

# 4. companies: foreign share of pretax income, by sector (10-K one-year figures, latest filed per company-year)
f = con.sql(f"""
    with v as (
        select cik, concept, end_date, val from serving_company_facts
        where taxonomy = 'us-gaap' and unit = 'USD' and form in ('10-K', '10-K/A')
          and concept in ('IncomeLossFromContinuingOperationsBeforeIncomeTaxesForeign',
                          'IncomeLossFromContinuingOperationsBeforeIncomeTaxesDomestic',
                          'IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest',
                          'IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments')
          and date_diff('day', start_date, end_date) between 350 and 380 and end_date <= filed
        qualify row_number() over (partition by cik, concept, end_date order by filed desc, accession desc) = 1),
    y as (
        select cik, year(end_date - interval 182 day) as yr,
               max(val) filter (where concept like '%Foreign') as foreign_inc,
               max(val) filter (where concept like '%Domestic') as domestic_inc,
               coalesce(max(val) filter (where concept like '%ExtraordinaryItemsNoncontrollingInterest'),
                        max(val) filter (where concept like '%EquityMethodInvestments')) as total_inc
        from v group by cik, end_date),
    d as (select cik, try_cast(sic as int) as sic from serving_submission_details)
    select y.*, {base.SECTOR_SQL} as sector from y left join d using (cik)
    where foreign_inc is not null and domestic_inc is not null and yr between 2010 and 2025
      and coalesce(sic, 0) <> 6770""").pl()
# The company's own arithmetic: foreign plus domestic must equal its total pretax income (to $1m or 0.5%). A
# company-year that does not add up, or has no total, is dropped: this removes scale errors such as a $188bn
# 'domestic loss' tagged by a company with under $1bn of revenue (OPKO Health, fiscal 2025).
n_before = f.height
f = f.filter(pl.col("total_inc").is_not_null()
             & ((pl.col("foreign_inc") + pl.col("domestic_inc") - pl.col("total_inc")).abs()
                <= pl.max_horizontal(pl.lit(1e6), pl.col("total_inc").abs() * 0.005)))
n_kept = f.height
tot = (f.group_by("yr").agg(pl.len().alias("n"), pl.col("foreign_inc").sum(), pl.col("domestic_inc").sum())
       .with_columns((pl.col("foreign_inc") / (pl.col("foreign_inc") + pl.col("domestic_inc"))).alias("share"))
       .sort("yr"))
latest = int(tot.filter(pl.col("n") >= 0.8 * tot["n"].max())["yr"].max())   # last year most companies have filed
sec = (f.filter((pl.col("yr") == latest) & ~pl.col("sector").is_in(["No SIC code", "Unclassified", "Government"]))
       .group_by("sector").agg(pl.len().alias("n"), pl.col("foreign_inc").sum(), pl.col("domestic_inc").sum())
       .filter(pl.col("n") >= 20)
       .with_columns((pl.col("foreign_inc") / (pl.col("foreign_inc") + pl.col("domestic_inc"))).alias("share"))
       .sort("share"))
fig4 = go.Figure(go.Bar(y=sec["sector"], x=sec["share"], orientation="h", marker_color="#2563eb",
                        text=[f"{v:.0%}  ({n} cos)" for v, n in zip(sec["share"], sec["n"])], textposition="outside",
                        hovertemplate="%{y}: %{x:.0%} of pretax income earned abroad<extra></extra>"))
fig4.update_layout(height=120 + 32 * sec.height, template="plotly_white", xaxis_tickformat=".0%",
                   xaxis_title="Foreign share of pretax income", margin=dict(l=300, r=60, t=60, b=50),
                   xaxis_range=[0, float(sec["share"].max()) * 1.25],
                   title=f"Public companies: pretax income earned abroad, by sector, fiscal {latest}")
fig4b = go.Figure(go.Scatter(x=tot["yr"].filter(tot["yr"] <= latest), y=tot["share"].filter(tot["yr"] <= latest),
                             mode="lines+markers", line=dict(color="#2563eb", width=2),
                             customdata=tot["n"].filter(tot["yr"] <= latest),
                             hovertemplate="%{x}: %{y:.0%} (%{customdata:,} companies)<extra></extra>"))
fig4b.update_layout(height=340, template="plotly_white", yaxis_tickformat=".0%", yaxis_title="Foreign share",
                    title="All reporting companies: foreign share of pretax income", margin=dict(l=60, r=20, t=60, b=40))

first = True


def div(fig) -> str:
    global first
    out = pio.to_html(fig, full_html=False, include_plotlyjs=first, config={"displayModeBar": False})
    first = False
    return out


L = q.tail(1)
lq = f"{L['date'][0].year} Q{(L['date'][0].month - 1) // 3 + 1}"
lm = m.tail(1)
t_l = tot.filter(pl.col("yr") == latest)
built = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Where GDP Goes</title>
<style>
:root {{ --bg:#ffffff; --fg:#1d1d1b; --muted:#5f5e58; --line:#e3e1da; --box:#f6f5f1; }}
body {{ background:var(--bg); color:var(--fg); font:15px/1.5 system-ui, "Segoe UI", sans-serif; margin:0; padding:24px 16px 60px; }}
main {{ max-width:1250px; margin:0 auto; }}
h1 {{ font-size:24px; margin:0 0 6px; }}
h2 {{ font-size:19px; margin:34px 0 6px; }}
.muted {{ color:var(--muted); }}
.lead {{ background:var(--box); padding:12px 16px; margin:12px 0 16px; }}
{nav.CSS}
</style></head>
<body>{nav.html("Where GDP Goes")}<main>
<h1>Where GDP Goes</h1>
<div class="lead">GDP measured by who spends it. In {lq}, consumers spent {L['PCEC_sh'][0]:.0%} of GDP, business and
housing investment {L['GPDI_sh'][0]:.0%}, government {L['GCE_sh'][0]:.0%}, and trade subtracted
{-L['NETEXP_sh'][0]:.1%}: exports were {L['EXPGS_sh'][0]:.1%} of GDP and imports {L['IMPGS_sh'][0]:.1%}. The monthly
trade balance for {lm['date'][0]:%B %Y} was a {'deficit' if lm['BOPGSTB'][0] < 0 else 'surplus'} of ${abs(lm['BOPGSTB'][0]) / 1e3:,.1f}bn. Public companies that report the split
earned {t_l['share'][0]:.0%} of their pretax income abroad in fiscal {latest}.</div>
<p class="muted">FRED series, fetched when the page is built and not stored: consumer spending (PCEC), investment
(GPDI), government (GCE), net exports (NETEXP), exports (EXPGS), imports (IMPGS), all quarterly at annual rates, and
the monthly trade balance (BOPGSTB). The four parts add up to Pharos's own GDP in every quarter (checked at build).
Company figures: 10-K pretax income from continuing operations, foreign and domestic, as each company reports it,
latest filed value; only company-years whose foreign and domestic parts add up to the total they report ({n_kept:,} of {n_before:,}; the rest include tagging scale errors); SPACs excluded. Page built {built}.</p>

<h2>1. Who spends GDP</h2>
<p>Consumers are about two-thirds and rising; the trade line sits below zero because the US imports more than it
exports, and imports are subtracted.</p>
{div(fig1)}

<h2>2. Trade against GDP</h2>
<p>Exports and imports each as a share of GDP; the bars are the gap between them.</p>
{div(fig2)}

<h2>3. The monthly trade balance</h2>
<p>The fastest-moving trade number, in billions of dollars a month (balance of payments basis).</p>
{div(fig3)}

<h2>4. Companies abroad</h2>
<p>The bridge down to companies: the share of pretax income public companies earn outside the US, as they report
it in their tax note. Sectors with at least 20 reporting companies.</p>
{div(fig4)}
{div(fig4b)}
</main></body></html>"""

(HERE / "trade.html").write_text(page, encoding="utf-8")
print("wrote", HERE / "trade.html", f"identity_gap={gap:.5%} latest_fy={latest} quarter={lq} kept={n_kept}/{n_before}")
print(sec.select("sector", "n", "share"))
print(tot)

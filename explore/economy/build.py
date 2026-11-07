"""Companies vs the Economy: public companies' revenue growth against GDP growth, nominal and real, overall, by
sector and company by company. Company figures come from the shared explore base (10-K one-year figures, latest
filed value, SPACs out); GDP and CPI from Pharos's FRED series, today's vintage. Reads catalog.duckdb read-only;
writes economy.html next to this file."""
import html as h
import sys
from datetime import datetime
from pathlib import Path

import duckdb
import plotly.graph_objects as go
import plotly.io as pio
import polars as pl
from plotly.subplots import make_subplots

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "lib"))
import base  # noqa: E402
import nav  # noqa: E402

YEARS = (2012, 2025)          # growth years (each needs the year before)
STREAK = (2015, 2024)         # ten growth years for the company scorecard
BIG = 1e9                     # scorecard: 2024 revenue at least $1bn
DROP_SECTORS = ("No SIC code", "Unclassified", "Government")

con = duckdb.connect(base.CATALOG, read_only=True)

# ---------- macro: annual averages of today's vintage ----------
macro = con.sql("""
    with o as (
        select series_id, obs_date, value from serving_observations
        where series_id in ('fred:GDP', 'fred:GDPC1', 'fred:CPIAUCSL')
        qualify row_number() over (partition by series_id, obs_date order by vintage desc) = 1)
    select year(obs_date) as yr,
           avg(value) filter (where series_id = 'fred:GDP') as gdp,
           avg(value) filter (where series_id = 'fred:GDPC1') as rgdp,
           avg(value) filter (where series_id = 'fred:CPIAUCSL') as cpi,
           count(value) filter (where series_id = 'fred:GDP') as gdp_q,
           count(value) filter (where series_id = 'fred:CPIAUCSL') as cpi_m
    from o where value is not null group by 1 order by 1""").pl()
# full years only; CPI may miss one month (FRED never published October 2025: health check C16)
macro = macro.filter((pl.col("gdp_q") == 4) & (pl.col("cpi_m") >= 11))
short_cpi = macro.filter(pl.col("cpi_m") == 11)["yr"].to_list()
macro = macro.with_columns((pl.col("gdp") / pl.col("gdp").shift(1) - 1).alias("gdp_g"),
                           (pl.col("rgdp") / pl.col("rgdp").shift(1) - 1).alias("rgdp_g"),
                           (pl.col("cpi") / pl.col("cpi").shift(1) - 1).alias("infl"))
last_macro_year = int(macro["yr"].max())
YEARS = (YEARS[0], min(YEARS[1], last_macro_year))

# ---------- companies: matched pairs (same company, this year and last, positive revenue both) ----------
c = (base.companies().filter(pl.col("revenue") > 0)
     .select("cik", "yr", "revenue", "name", "sector").sort(["cik", "yr"]))
prev = c.select("cik", (pl.col("yr") + 1).alias("yr"), pl.col("revenue").alias("rev_prev"))
pairs = (c.join(prev, on=["cik", "yr"]).filter(pl.col("yr").is_between(*YEARS))
         .join(macro.select("yr", "gdp", "gdp_g", "rgdp_g", "infl"), on="yr")
         .with_columns((pl.col("revenue") / pl.col("rev_prev") - 1).alias("g")))
pairs = pairs.with_columns(((1 + pl.col("g")) / (1 + pl.col("infl")) - 1).alias("rg"),
                           (pl.col("g") > pl.col("gdp_g")).alias("beat"))

yearly = (pairs.group_by("yr").agg(
    pl.len().alias("n"), pl.col("beat").mean().alias("beat_share"),
    pl.col("g").quantile(0.25).alias("p25"), pl.col("g").median().alias("p50"), pl.col("g").quantile(0.75).alias("p75"),
    (pl.col("revenue").sum() / pl.col("rev_prev").sum() - 1).alias("agg_g"),
    pl.col("rg").median().alias("real_p50"), (pl.col("rg") > 0).mean().alias("real_up"),
    pl.col("gdp_g").first(), pl.col("rgdp_g").first(), pl.col("infl").first())
    .sort("yr").with_columns(((1 + pl.col("agg_g")) / (1 + pl.col("infl")) - 1).alias("real_agg")))

X = yearly["yr"].to_list()

# 1. share beating the economy
fig1 = make_subplots(specs=[[{"secondary_y": True}]])
fig1.add_trace(go.Bar(x=X, y=yearly["beat_share"], name="Companies whose revenue grew faster than nominal GDP",
                      marker_color="#2563eb", text=[f"{v:.0%}" for v in yearly["beat_share"]], textposition="outside",
                      customdata=yearly["n"], hovertemplate="%{x}: %{y:.0%} of %{customdata:,} companies<extra></extra>"))
fig1.add_trace(go.Scatter(x=X, y=yearly["gdp_g"], name="Nominal GDP growth (right axis)", mode="lines+markers",
                          line=dict(color="#111827", width=2)), secondary_y=True)
fig1.update_yaxes(tickformat=".0%", range=[0, 0.85], title_text="Share of companies", secondary_y=False)
fig1.update_yaxes(tickformat=".0%", title_text="Nominal GDP growth", showgrid=False, secondary_y=True)
fig1.update_layout(height=440, template="plotly_white", title="Who outgrew the economy, each year",
                   legend=dict(orientation="h", y=-0.15), margin=dict(l=60, r=60, t=60, b=80))

# 2. the spread of company growth vs GDP
fig2 = go.Figure()
fig2.add_trace(go.Scatter(x=X, y=yearly["p75"], mode="lines", line=dict(width=0), showlegend=False, hoverinfo="skip"))
fig2.add_trace(go.Scatter(x=X, y=yearly["p25"], mode="lines", line=dict(width=0), fill="tonexty",
                          fillcolor="rgba(37,99,235,0.15)", name="Middle half of companies (25th-75th percentile)"))
fig2.add_trace(go.Scatter(x=X, y=yearly["p50"], mode="lines+markers", name="Median company",
                          line=dict(color="#2563eb", width=2)))
fig2.add_trace(go.Scatter(x=X, y=yearly["agg_g"], mode="lines+markers", name="All companies' revenue added together",
                          line=dict(color="#f97316", width=2)))
fig2.add_trace(go.Scatter(x=X, y=yearly["gdp_g"], mode="lines+markers", name="Nominal GDP",
                          line=dict(color="#111827", width=2, dash="dot")))
fig2.update_layout(height=440, template="plotly_white", yaxis_tickformat=".0%", yaxis_title="Revenue growth",
                   title="Revenue growth: the typical company, the total, and the economy",
                   legend=dict(orientation="h", y=-0.15), margin=dict(l=60, r=20, t=60, b=80))

# 3. real growth
fig3 = make_subplots(specs=[[{"secondary_y": True}]])
fig3.add_trace(go.Bar(x=X, y=yearly["real_up"], name="Companies that grew faster than inflation (right axis)",
                      marker_color="#cbd5e1"), secondary_y=True)
fig3.add_trace(go.Scatter(x=X, y=yearly["real_p50"], mode="lines+markers", name="Median company, after inflation",
                          line=dict(color="#2563eb", width=2)))
fig3.add_trace(go.Scatter(x=X, y=yearly["real_agg"], mode="lines+markers", name="All companies together, after inflation",
                          line=dict(color="#f97316", width=2)))
fig3.add_trace(go.Scatter(x=X, y=yearly["rgdp_g"], mode="lines+markers", name="Real GDP",
                          line=dict(color="#111827", width=2, dash="dot")))
fig3.update_yaxes(tickformat=".0%", title_text="Real growth", secondary_y=False)
fig3.update_yaxes(tickformat=".0%", range=[0, 1], title_text="Share of companies", showgrid=False, secondary_y=True)
fig3.update_layout(height=440, template="plotly_white", title="After inflation (CPI): who actually grew",
                   legend=dict(orientation="h", y=-0.15), margin=dict(l=60, r=60, t=60, b=80))

# 4. sectors: aggregate growth minus nominal GDP growth
sec = (pairs.filter(~pl.col("sector").is_in(DROP_SECTORS)).group_by(["sector", "yr"])
       .agg((pl.col("revenue").sum() / pl.col("rev_prev").sum() - 1).alias("g"), pl.len().alias("n"),
            pl.col("gdp_g").first()).with_columns((pl.col("g") - pl.col("gdp_g")).alias("excess")))
cum = (sec.group_by("sector").agg(((1 + pl.col("g")).product() / (1 + pl.col("gdp_g")).product()).alias("rel"))
       .sort("rel", descending=True))
order = cum["sector"].to_list()
lut = {(r["sector"], r["yr"]): r for r in sec.iter_rows(named=True)}
z = [[(lut[(s, y)]["excess"] if (s, y) in lut else None) for y in X] for s in order]
txt = [[(f"{lut[(s, y)]['excess'] * 100:+.0f}" if (s, y) in lut else "") for y in X] for s in order]
hov = [[(f"{s}, {y}: revenue {lut[(s, y)]['g']:+.1%} vs GDP {lut[(s, y)]['gdp_g']:+.1%}, "
         f"{lut[(s, y)]['n']:,} companies" if (s, y) in lut else "") for y in X] for s in order]
labels = [f"{s}  ({r:.2f}x)" for s, r in zip(order, cum["rel"])]
fig4 = go.Figure(go.Heatmap(z=z, x=[str(y) for y in X], y=labels, text=txt, texttemplate="%{text}",
                            customdata=hov, hovertemplate="%{customdata}<extra></extra>", colorscale="RdBu",
                            zmid=0, zmin=-0.25, zmax=0.25, xgap=2, ygap=2,
                            colorbar=dict(title="vs GDP", tickformat="+.0%")))
fig4.update_layout(height=120 + 30 * len(order), template="plotly_white",
                   title="Each sector's revenue growth minus nominal GDP growth, in percentage points",
                   yaxis=dict(autorange="reversed"), xaxis=dict(side="top"), margin=dict(l=330, r=20, t=90, b=20))

# 5. weight of each sector's revenue against GDP
allc = c.filter(pl.col("yr").is_between(YEARS[0] - 1, YEARS[1]) & ~pl.col("sector").is_in(DROP_SECTORS))
wt = (allc.group_by(["sector", "yr"]).agg(pl.col("revenue").sum()).join(macro.select("yr", "gdp"), on="yr")
      .with_columns((pl.col("revenue") / (pl.col("gdp") * 1e9)).alias("ratio")).sort("yr"))
fig5 = go.Figure()
for s in reversed(order):
    d = wt.filter(pl.col("sector") == s)
    fig5.add_trace(go.Scatter(x=d["yr"], y=d["ratio"], name=s, mode="lines", stackgroup="one",
                              hovertemplate=f"{s}, %{{x}}: %{{y:.1%}} of GDP<extra></extra>"))
fig5.update_layout(height=520, template="plotly_white", yaxis_tickformat=".0%",
                   yaxis_title="Public-company revenue as a share of GDP",
                   title="Public-company revenue against GDP, by sector (all filers each year)",
                   legend=dict(orientation="v", x=1.02, y=1, font=dict(size=11)), margin=dict(l=60, r=20, t=60, b=40))

# 6. company scorecard: years of ten that each large company outgrew nominal GDP
big = base.revenue_year(2024).filter(pl.col("revenue") >= BIG).select("cik")
w10 = pairs.filter(pl.col("yr").is_between(*STREAK)).join(big, on="cik")
card = (w10.group_by("cik").agg(pl.len().alias("years"), pl.col("beat").sum().alias("beat"),
                                pl.col("name").last(), pl.col("sector").last(),
                                ((1 + pl.col("g")).product() ** (1 / pl.len()) - 1).alias("cagr"))
        .filter(pl.col("years") == STREAK[1] - STREAK[0] + 1))
gdp_cagr = float(macro.filter(pl.col("yr").is_between(*STREAK))
                 .select(((1 + pl.col("gdp_g")).product() ** (1 / pl.len()) - 1)).item())
dist = (pl.DataFrame({"beat": list(range(0, 11))}, schema={"beat": pl.UInt32})
        .join(card.group_by("beat").agg(pl.len().alias("n")), on="beat", how="left").fill_null(0))
fig6 = go.Figure(go.Bar(x=dist["beat"], y=dist["n"], text=[f"{v:,}" for v in dist["n"]], textposition="outside",
                        marker_color=["#b91c1c"] * 2 + ["#cbd5e1"] * 7 + ["#15803d"] * 2))
fig6.update_layout(height=400, template="plotly_white", xaxis=dict(dtick=1, title="Years (of ten) the company's "
                   "revenue grew faster than nominal GDP"), yaxis_title="Companies",
                   title=f"Large companies (2024 revenue $1bn+), {STREAK[0]}-{STREAK[1]}",
                   margin=dict(l=60, r=20, t=60, b=60))


def table(df: pl.DataFrame) -> str:
    rows = "".join(f"<tr><td>{h.escape(r['name'] or str(r['cik']))}</td><td>{h.escape(r['sector'] or '')}</td>"
                   f"<td class=num>{r['beat']} of {r['years']}</td><td class=num>{r['cagr']:+.1%}</td></tr>"
                   for r in df.iter_rows(named=True))
    return ("<div class=wrap><table><thead><tr><th>Company</th><th>Sector</th><th class=num>Years beat GDP</th>"
            "<th class=num>Revenue growth a year</th></tr></thead><tbody>" + rows + "</tbody></table></div>")


always = card.filter(pl.col("beat") >= 9).sort(["beat", "cagr"], descending=True)
never = card.filter(pl.col("beat") <= 1).sort(["beat", "cagr"])

first = True


def div(fig) -> str:
    global first
    out = pio.to_html(fig, full_html=False, include_plotlyjs=first, config={"displayModeBar": False})
    first = False
    return out


ys = yearly.filter(pl.col("yr").is_between(*STREAK))
avg_beat = float(ys["beat_share"].mean())
n_card = card.height
n9 = always.height
n_low = never.height
built = datetime.now().strftime("%Y-%m-%d %H:%M")

page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Companies vs Economy</title>
<style>
:root {{ --bg:#ffffff; --fg:#1d1d1b; --muted:#5f5e58; --line:#e3e1da; --box:#f6f5f1; }}
body {{ background:var(--bg); color:var(--fg); font:15px/1.5 system-ui, "Segoe UI", sans-serif; margin:0; padding:24px 16px 60px; }}
main {{ max-width:1250px; margin:0 auto; }}
h1 {{ font-size:24px; margin:0 0 6px; }}
h2 {{ font-size:19px; margin:34px 0 6px; }}
h3 {{ font-size:15px; margin:18px 0 6px; }}
.muted {{ color:var(--muted); }}
.lead {{ background:var(--box); padding:12px 16px; margin:12px 0 16px; }}
.wrap {{ overflow-x:auto; }}
table {{ border-collapse:collapse; width:100%; font-size:14px; }}
th, td {{ border-bottom:1px solid var(--line); padding:5px 10px; text-align:left; vertical-align:top; }}
td.num, th.num {{ text-align:right; white-space:nowrap; }}
.two {{ display:grid; grid-template-columns:1fr 1fr; gap:24px; }}
@media (max-width: 900px) {{ .two {{ grid-template-columns:1fr; }} }}
{nav.CSS}
</style></head>
<body>{nav.html("Companies vs Economy")}<main>
<h1>Companies vs the Economy</h1>
<div class="lead">In an average year from {STREAK[0]} to {STREAK[1]}, <b>{avg_beat:.0%} of public companies grew
revenue faster than the economy</b> (nominal GDP). Of {n_card:,} large companies (2024 revenue $1bn or more) with all
ten years, {n9:,} outgrew GDP in nine or ten of them and {n_low:,} in at most one. Nominal GDP grew
{gdp_cagr:.1%} a year over those ten years.</div>
<p class="muted">Growth is one company's revenue against its own prior year, counting only companies with positive
revenue in both years, so companies entering or leaving do not move the numbers. A company's year is its fiscal year,
compared with the calendar year it mostly falls in. GDP and CPI are annual averages as published today; inflation is
the CPI's change{" (" + ", ".join(map(str, short_cpi)) + ": eleven months, FRED never published October 2025)" if short_cpi else ""}. Company revenue is from each 10-K, latest filed value, SPACs excluded (the base of Data Tests
8-16). Page built {built}.</p>

<h2>1. Who outgrew the economy, each year</h2>
<p>The bars are the share of companies whose revenue grew faster than nominal GDP that year; the line is GDP growth.</p>
{div(fig1)}

<h2>2. The typical company, the total and the economy</h2>
<p>The orange line adds every company's revenue together, so the largest companies dominate it; the blue line is the
middle company. When orange runs above blue, big companies are outgrowing small ones.</p>
{div(fig2)}

<h2>3. After inflation</h2>
<p>The same, with revenue growth deflated by the CPI and compared with real GDP. The bars are the share of companies
whose revenue grew faster than prices.</p>
{div(fig3)}

<h2>4. Sector by sector</h2>
<p>Each cell is a sector's revenue growth (its companies added together) minus nominal GDP growth that year: blue
outgrew the economy, red fell behind. The number after each sector is its revenue growth over the whole period
relative to GDP's: 1.20x means it grew 20% more than the economy did.</p>
{div(fig4)}

<h2>5. How big each sector's revenue is against GDP</h2>
<p>Red bars: outgrew GDP in at most one year; green: nine or ten.</p>
<p>Revenue is not value added: a company's revenue includes what it bought from others, so the total can exceed GDP
and is not a share of the economy. The trend is the point: which sectors' public companies weigh more against the
economy each year. All filers each year, so companies listing or delisting move these lines.</p>
{div(fig5)}

<h2>6. Company scorecard: steady outgrowers and steady laggards</h2>
{div(fig6)}
<div class="two">
<div><h3>Outgrew GDP in 9 or 10 of the ten years ({n9:,})</h3>{table(always)}</div>
<div><h3>Outgrew GDP in at most 1 of the ten years ({n_low:,})</h3>{table(never)}</div>
</div>
</main></body></html>"""

(HERE / "economy.html").write_text(page, encoding="utf-8")
print("wrote", HERE / "economy.html", f"avg_beat={avg_beat:.3f} cards={n_card} nine={n9} low={n_low} "
      f"years={YEARS} gdp_cagr={gdp_cagr:.4f}")
print(yearly.select("yr", "n", "beat_share", "p50", "agg_g", "gdp_g", "real_p50", "rgdp_g"))

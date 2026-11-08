"""People and Work: the top of the stack. Population, the working-age population, labor force and employment,
tied down to real GDP per person and per worker, and pay as a share of GDP.

Every series comes through base.series(): from Pharos (latest vintage) when stored, as the five D141 series and GDP
are, else from FRED live. UNRATE is not stored; it only cross-checks the derived unemployment rate.
Writes people.html next to this file."""
import math
import os
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

START = "1959-01-01"


def fred(series_id: str) -> pl.DataFrame:
    return base.series(series_id, START).rename({"v": series_id})


monthly = fred("POPTHM")
for s in ("CNP16OV", "CLF16OV", "CE16OV", "UNRATE"):
    monthly = monthly.join(fred(s), on="date", how="full", coalesce=True)
monthly = monthly.sort("date").drop_nulls(["POPTHM", "CNP16OV", "CLF16OV", "CE16OV"])
monthly = monthly.with_columns((pl.col("CLF16OV") / pl.col("CNP16OV")).alias("participation"),
                               ((pl.col("CLF16OV") - pl.col("CE16OV")) / pl.col("CLF16OV")).alias("unemployment"),
                               (pl.col("CE16OV") / pl.col("CNP16OV")).alias("employed_share"))
check = monthly.drop_nulls("UNRATE").select((pl.col("unemployment") * 100 - pl.col("UNRATE")).abs().max()).item()
if check > 0.051:   # FRED rounds UNRATE to one decimal
    raise SystemExit(f"derived unemployment differs from FRED's UNRATE by {check:.3f} points")

con = duckdb.connect(base.CATALOG, read_only=True)
gdp_q = con.sql("""
    select obs_date as date,
           max(value) filter (where series_id = 'fred:GDP') as GDP,
           max(value) filter (where series_id = 'fred:GDPC1') as GDPC1
    from (select series_id, obs_date, value from serving_observations where series_id in ('fred:GDP', 'fred:GDPC1')
          qualify row_number() over (partition by series_id, obs_date order by vintage desc) = 1)
    where value is not null group by 1 order by 1""").pl()
coe = fred("COE")
quarterly = gdp_q.join(coe, on="date", how="inner").with_columns((pl.col("COE") / pl.col("GDP")).alias("labor_share"))

# annual: a year counts once its last period is in (December, Q4), averaged over the periods reported. A month the
# source never published stays out of the average: October 2025, no household survey in the shutdown (D141)
mon_y = (monthly.with_columns(pl.col("date").dt.year().alias("yr")).group_by("yr")
         .agg(pl.len().alias("m"), pl.col("date").max().dt.month().alias("last"), pl.col("POPTHM").mean(),
              pl.col("CNP16OV").mean(), pl.col("CE16OV").mean())
         .filter(pl.col("last") == 12))
gdp_y = (gdp_q.with_columns(pl.col("date").dt.year().alias("yr")).group_by("yr")
         .agg(pl.col("date").max().dt.month().alias("last"), pl.col("GDPC1").mean()).filter(pl.col("last") == 10))
short = mon_y.filter(pl.col("m") < 12).sort("yr")["yr"].to_list()
yr = mon_y.join(gdp_y, on="yr").sort("yr").with_columns(
    (pl.col("POPTHM") / pl.col("POPTHM").shift(1) - 1).alias("pop_g"),
    (pl.col("CNP16OV") / pl.col("POPTHM")).alias("adult_share"),
    (pl.col("GDPC1") * 1e9 / (pl.col("POPTHM") * 1e3)).alias("gdp_per_person"),
    (pl.col("GDPC1") * 1e9 / (pl.col("CE16OV") * 1e3)).alias("gdp_per_worker"))
last_year = int(yr["yr"].max())

# 1. people
fig1 = make_subplots(specs=[[{"secondary_y": True}]])
fig1.add_trace(go.Bar(x=yr["yr"], y=yr["pop_g"], name="Population growth (left)", marker_color="#93c5fd"))
fig1.add_trace(go.Scatter(x=yr["yr"], y=yr["adult_share"], name="Share of the population aged 16+ (right)",
                          mode="lines", line=dict(color="#111827", width=2)), secondary_y=True)
fig1.update_yaxes(tickformat=".1%", title_text="Population growth", secondary_y=False)
fig1.update_yaxes(tickformat=".0%", title_text="Aged 16+", showgrid=False, secondary_y=True)
fig1.update_layout(height=420, template="plotly_white", title="People: how fast the population grows, and how adult it is",
                   legend=dict(orientation="h", y=-0.15), margin=dict(l=60, r=60, t=60, b=80))

# 2. work
fig2 = make_subplots(specs=[[{"secondary_y": True}]])
fig2.add_trace(go.Scatter(x=monthly["date"], y=monthly["participation"], name="Participation: in the labor force (left)",
                          line=dict(color="#2563eb", width=2)))
fig2.add_trace(go.Scatter(x=monthly["date"], y=monthly["employed_share"], name="Employed share of adults (left)",
                          line=dict(color="#15803d", width=2)))
fig2.add_trace(go.Scatter(x=monthly["date"], y=monthly["unemployment"], name="Unemployment rate (right)",
                          line=dict(color="#b91c1c", width=1.5)), secondary_y=True)
fig2.update_yaxes(tickformat=".0%", title_text="Share of adults (16+)", range=[0.5, 0.7], secondary_y=False)
fig2.update_yaxes(tickformat=".0%", title_text="Unemployment", range=[0, 0.16], showgrid=False, secondary_y=True)
fig2.update_layout(height=440, template="plotly_white", title="Work: how many adults are in the labor force and employed",
                   legend=dict(orientation="h", y=-0.15), margin=dict(l=60, r=60, t=60, b=80))

# 3. where real GDP growth comes from, by decade (log changes, so the parts add up)
dec = []
for d0 in range(1960, last_year, 10):
    a, b = d0 - 1, min(d0 + 9, last_year)
    ra, rb = yr.filter(pl.col("yr") == a), yr.filter(pl.col("yr") == b)
    if ra.height == 0 or rb.height == 0:
        continue
    n = b - a
    lg = lambda col: (math.log(rb[col][0]) - math.log(ra[col][0])) / n  # noqa: E731
    people = lg("POPTHM")
    employed = (math.log(rb["CE16OV"][0] / rb["POPTHM"][0]) - math.log(ra["CE16OV"][0] / ra["POPTHM"][0])) / n
    output = lg("gdp_per_worker")
    dec.append(dict(label=f"{d0}s" if b == d0 + 9 else f"{d0}-{b}", people=people, employed=employed, output=output,
                    total=lg("GDPC1")))
dd = pl.DataFrame(dec)
fig3 = go.Figure()
for col, name, c in (("people", "More people", "#93c5fd"), ("employed", "More of them employed", "#15803d"),
                     ("output", "More output per worker", "#f97316")):
    fig3.add_trace(go.Bar(x=dd["label"], y=dd[col], name=name, marker_color=c,
                          hovertemplate="%{x}: %{y:.2%} a year<extra>" + name + "</extra>"))
fig3.add_trace(go.Scatter(x=dd["label"], y=dd["total"], name="Real GDP growth", mode="markers+text",
                          marker=dict(color="#111827", size=10, symbol="diamond"),
                          text=[f"{v:.1%}" for v in dd["total"]], textposition="top center"))
fig3.update_layout(barmode="relative", height=440, template="plotly_white", yaxis_tickformat=".1%",
                   yaxis_title="Average a year", title="Where real GDP growth comes from, decade by decade",
                   legend=dict(orientation="h", y=-0.15), margin=dict(l=60, r=20, t=60, b=80))

# 3b. per person and per worker, indexed
base_yr = 1959
i0 = yr.filter(pl.col("yr") == base_yr)
fig3b = go.Figure()
for col, name, c in (("gdp_per_person", "Real GDP per person", "#2563eb"), ("gdp_per_worker", "Real GDP per worker", "#f97316")):
    fig3b.add_trace(go.Scatter(x=yr["yr"], y=yr[col] / i0[col][0] * 100, name=name, line=dict(color=c, width=2),
                               customdata=yr[col], hovertemplate="%{x}: %{y:.0f} ($%{customdata:,.0f})<extra>" + name + "</extra>"))
fig3b.update_layout(height=380, template="plotly_white", yaxis_title=f"{base_yr} = 100",
                    title="Real GDP per person and per worker", legend=dict(orientation="h", y=-0.15),
                    margin=dict(l=60, r=20, t=60, b=80))

# 4. labor share
fig4 = go.Figure(go.Scatter(x=quarterly["date"], y=quarterly["labor_share"], mode="lines",
                            line=dict(color="#7c3aed", width=2), name="Pay as a share of GDP"))
fig4.update_layout(height=400, template="plotly_white", yaxis_tickformat=".0%", yaxis_title="Share of GDP",
                   title="Who gets paid: compensation of employees as a share of GDP", margin=dict(l=60, r=20, t=60, b=40))

first = True


def div(fig) -> str:
    global first
    out = pio.to_html(fig, full_html=False, include_plotlyjs=first, config={"displayModeBar": False})
    first = False
    return out


L = yr.filter(pl.col("yr") == last_year)
lm = monthly.tail(1)
lq = quarterly.tail(1)
built = datetime.now().strftime("%Y-%m-%d %H:%M")
page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>People and Work</title>
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
<body>{nav.html("People and Work")}<main>
<h1>People and Work</h1>
<div class="lead">The top of the stack: people, then work, then output. In {last_year} the US had
{L['POPTHM'][0] / 1e3:,.1f} million people, growing {L['pop_g'][0]:.1%} a year; {L['adult_share'][0]:.0%} were adults
(16+) outside the military and institutions. In {lm['date'][0]:%B %Y}, {lm['participation'][0]:.1%} of those adults
were in the labor force and {lm['unemployment'][0]:.1%} of the labor force was unemployed. Pay was
{lq['labor_share'][0]:.0%} of GDP in {lq['date'][0].year} Q{(lq['date'][0].month - 1) // 3 + 1}.</div>
<p class="muted">Population (POPTHM), adults 16+ (CNP16OV: civilian, outside institutions), labor force (CLF16OV) and
employed (CE16OV) are monthly, from the household survey; pay is compensation of employees (COE), quarterly. These
five, GDP and real GDP come from Pharos, latest vintage. Participation
is labor force over adults; unemployment is (labor force minus employed) over labor force, which matches FRED's
UNRATE to its rounding. Annual figures average each finished year's reported months{
" (" + ", ".join(map(str, short)) + " lacks October: no household survey during the shutdown)" if short == [2025] else
(" (" + ", ".join(map(str, short)) + " has fewer than 12)" if short else "")}. Page built {built}.</p>

<h2>1. People</h2>
<p>Bars: population growth each year. Line: the share of the population aged 16 and over, the pool that can work.</p>
{div(fig1)}

<h2>2. Work</h2>
<p>Participation is adults working or looking for work; the employed share is adults with a job. Unemployment is the
share of the labor force without one.</p>
{div(fig2)}

<h2>3. Where growth comes from</h2>
<p>Real GDP growth splits exactly into three parts: population growth, the change in the share of people employed,
and growth in output per employed person. Each bar is a decade's average a year; the diamond is real GDP growth.</p>
{div(fig3)}
{div(fig3b)}

<h2>4. Who gets paid</h2>
<p>Wages, salaries and benefits paid to employees, as a share of GDP. Most of the rest is profits, interest, rent
and depreciation, which is where the companies layer begins.</p>
{div(fig4)}
</main></body></html>"""

(HERE / "people.html").write_text(page, encoding="utf-8")
print("wrote", HERE / "people.html", f"unrate_check={check:.3f}")
print(dd)

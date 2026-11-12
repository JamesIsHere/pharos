"""Test 4: total revenue of all filers per calendar year (SEC annual frames CYyyyy) vs nominal GDP. One-off, read-only."""
import duckdb
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import polars as pl

from pharos.paths import PROJECT_ROOT, data_root

ROOT = PROJECT_ROOT.as_posix()
CATALOG = (data_root() / "catalog.duckdb").as_posix()
con = duckdb.connect(CATALOG, read_only=True)

con.sql("""
    create temp table rev as
    select cik, cast(substr(frame, 3, 4) as int) as yr, max(val) as revenue
    from serving_company_facts
    where taxonomy = 'us-gaap' and unit = 'USD'
      and concept in ('Revenues', 'RevenueFromContractWithCustomerExcludingAssessedTax', 'SalesRevenueNet')
      and regexp_full_match(frame, 'CY[0-9]{4}')      -- annual duration frames only
    group by all
""")

out = con.sql("""
    with ranked as (
        select *, row_number() over (partition by yr order by revenue desc) as rk from rev where revenue > 0
    ),
    agg as (
        select yr, count(*) as companies, sum(revenue) / 1e12 as total_tn,
               sum(revenue) filter (where rk <= 500) / 1e12 as top500_tn
        from ranked group by yr
    ),
    gdp as (
        select year(obs_date) as yr, avg(value) / 1e3 as gdp_tn, count(*) as q
        from (select obs_date, value from serving_observations where series_id = 'fred:GDP'
              qualify row_number() over (partition by obs_date order by vintage desc) = 1)
        group by all
    )
    select a.*, g.gdp_tn, g.q, a.total_tn / g.gdp_tn as total_to_gdp, a.top500_tn / g.gdp_tn as top500_to_gdp
    from agg a join gdp g using (yr) where a.yr between 2006 and 2025 order by yr
""").pl()

with pl.Config(tbl_rows=40, tbl_width_chars=160):
    print(out)
    print(con.sql("""select r.yr, r.cik, s.name, r.revenue/1e9 as bn from rev r
                     left join serving_submission_details s using (cik) where yr = 2024
                     order by revenue desc limit 12""").pl())

fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.1,
                    subplot_titles=("Revenue summed over every filer vs nominal GDP, $ trillions",
                                    "Ratio to GDP"))
fig.add_trace(go.Scatter(x=out["yr"], y=out["gdp_tn"], name="Nominal GDP", mode="lines+markers",
                         line=dict(color="#059669")), row=1, col=1)
fig.add_trace(go.Scatter(x=out["yr"], y=out["total_tn"], name="All filers' revenue", mode="lines+markers",
                         line=dict(color="#2563eb")), row=1, col=1)
fig.add_trace(go.Scatter(x=out["yr"], y=out["top500_tn"], name="Largest 500 filers' revenue", mode="lines+markers",
                         line=dict(color="#93c5fd", dash="dot")), row=1, col=1)
fig.add_trace(go.Scatter(x=out["yr"], y=out["total_to_gdp"], name="All filers / GDP", mode="lines+markers",
                         line=dict(color="#2563eb"), showlegend=False), row=2, col=1)
fig.add_trace(go.Scatter(x=out["yr"], y=out["top500_to_gdp"], name="Largest 500 / GDP", mode="lines+markers",
                         line=dict(color="#93c5fd", dash="dot"), showlegend=False), row=2, col=1)
fig.update_yaxes(rangemode="tozero", row=1, col=1)
fig.update_yaxes(tickformat=".0%", rangemode="tozero", row=2, col=1)
fig.update_layout(height=720, template="plotly_white", legend=dict(orientation="h", y=-0.08),
                  title="Test 4: total revenue of SEC filers vs nominal GDP, by calendar year",
                  margin=dict(l=60, r=30, t=90, b=80))
fig.write_html(f"{ROOT}/explore/test4/test4.html", include_plotlyjs=True)
print("wrote", f"{ROOT}/explore/test4/test4.html")

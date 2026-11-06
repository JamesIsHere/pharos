"""Test 2: annual revenue, nominal and CPI-adjusted (2025 dollars), five watchlist names. One-off, read-only."""
import duckdb
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from pharos.paths import PROJECT_ROOT, data_root

ROOT = PROJECT_ROOT.as_posix()
CATALOG = (data_root() / "catalog.duckdb").as_posix()
con = duckdb.connect(CATALOG, read_only=True)
TICKERS = ["WMT", "AAPL", "MSFT", "NVDA", "MCD"]
CONCEPTS = ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"]

rev = con.sql(f"""
    with names as (
        select ticker, cik from serving_tickers where ticker in {tuple(TICKERS)}
    ),
    annual as (   -- one-year periods reported in 10-Ks, in USD
        select n.ticker, f.cik, f.concept, f.start_date, f.end_date, f.val, f.filed, f.accession
        from serving_company_facts f join names n using (cik)
        where f.taxonomy = 'us-gaap' and f.concept in {tuple(CONCEPTS)} and f.unit = 'USD'
          and f.form in ('10-K', '10-K/A')
          and date_diff('day', f.start_date, f.end_date) between 350 and 380
    ),
    latest as (   -- per concept and period: the most recently filed value (restatements win)
        select * from annual
        qualify row_number() over (partition by cik, concept, end_date order by filed desc, accession desc) = 1
    )
    -- per period: the largest of the revenue concepts (total revenue, not one segment of it)
    select ticker, cik, start_date, end_date, arg_max(concept, val) as concept, max(val) as revenue
    from latest group by all order by ticker, end_date
""")

cpi = con.sql("""
    select obs_date, value from serving_observations where series_id = 'fred:CPIAUCSL' and value is not null
    qualify row_number() over (partition by obs_date order by vintage desc) = 1
""")

out = con.sql("""
    with base as (select avg(value) as b from cpi where year(obs_date) = 2025),
    per as (
        select r.ticker, r.end_date, r.concept, r.revenue, avg(c.value) as cpi_avg, count(c.value) as cpi_months
        from rev r join cpi c on c.obs_date >= r.start_date and c.obs_date < r.end_date
        group by all
    )
    select ticker, year(end_date) as fy_end, end_date, concept,
           revenue / 1e9 as nominal_bn, revenue / 1e9 * b / cpi_avg as real_bn, cpi_months
    from per, base order by ticker, end_date
""").pl()

with __import__("polars").Config(tbl_rows=200, tbl_width_chars=160):
    print(out)

fig = make_subplots(rows=1, cols=len(TICKERS), subplot_titles=TICKERS, horizontal_spacing=0.05)
for i, t in enumerate(TICKERS, start=1):
    d = out.filter(out["ticker"] == t)
    fig.add_trace(go.Scatter(x=d["end_date"], y=d["nominal_bn"], name="As reported", mode="lines",
                             line=dict(color="#9ca3af", dash="dot"), showlegend=(i == 1)), row=1, col=i)
    fig.add_trace(go.Scatter(x=d["end_date"], y=d["real_bn"], name="In 2025 dollars (CPI)", mode="lines+markers",
                             line=dict(color="#2563eb"), showlegend=(i == 1)), row=1, col=i)
    fig.update_yaxes(rangemode="tozero", row=1, col=i)
fig.update_layout(height=430, template="plotly_white",
                  title="Test 2: annual revenue, $ billions, as reported vs in 2025 dollars, by fiscal year end",
                  legend=dict(orientation="h", y=-0.15), margin=dict(l=50, r=20, t=90, b=70))
fig.write_html(f"{ROOT}/explore/test2/test2.html", include_plotlyjs=True)
print("wrote", f"{ROOT}/explore/test2/test2.html")

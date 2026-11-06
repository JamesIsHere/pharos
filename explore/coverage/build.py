"""Coverage: how much of the US economy SEC filers account for, three ways, by calendar year.
Revenue vs GDP (mismatched), revenue vs gross output (sales vs sales), net income vs NIPA corporate profits.
One-off test. FRED series not in Pharos (GOAI, GOPI, CP) are fetched at build time and not stored."""
import json
import os
import urllib.parse
import urllib.request

import duckdb
import plotly.graph_objects as go
import polars as pl

from pharos.paths import PROJECT_ROOT, data_root

ROOT = PROJECT_ROOT.as_posix()
CATALOG = (data_root() / "catalog.duckdb").as_posix()
KEY = os.environ["FRED_API_KEY"]

def fred_annual(series_id):
    meta = json.load(urllib.request.urlopen("https://api.stlouisfed.org/fred/series?" + urllib.parse.urlencode(
        {"series_id": series_id, "api_key": KEY, "file_type": "json"})))["seriess"][0]
    print(series_id, "|", meta["title"], "|", meta["units"], "|", meta["frequency"])
    obs = json.load(urllib.request.urlopen("https://api.stlouisfed.org/fred/series/observations?" + urllib.parse.urlencode(
        {"series_id": series_id, "api_key": KEY, "file_type": "json", "observation_start": "2009-01-01"})))["observations"]
    df = pl.DataFrame({"yr": [int(o["date"][:4]) for o in obs],
                       "v": [float(o["value"]) if o["value"] != "." else None for o in obs]})
    return (df.group_by("yr").agg(pl.col("v").mean().alias(series_id), pl.col("v").count().alias("n"))
            .filter(pl.col("n") == 4).drop("n"))   # full years only; quarterly SAAR, so the mean is the annual level

macro = fred_annual("GOAI").join(fred_annual("GOPI"), on="yr").join(fred_annual("CP"), on="yr")

con = duckdb.connect(CATALOG, read_only=True)
gdp = con.sql("""
    select year(obs_date) as yr, avg(value) as GDP
    from (select obs_date, value from serving_observations where series_id = 'fred:GDP'
          qualify row_number() over (partition by obs_date order by vintage desc) = 1)
    group by 1 having count(*) = 4
""").pl()

# Not SEC's `frame` labels: for net income the frame often sits on a proxy statement's copy of the number
# (pay-versus-performance tables), some with scale errors. Use the 10-K's own one-year figures instead.
con.sql("""
    create temp table fy as
    with f as (
        select f.cik, concept, start_date, end_date, val, filed, accession
        from serving_company_facts f
        left join serving_submission_details d using (cik)
        where taxonomy = 'us-gaap' and unit = 'USD' and form in ('10-K', '10-K/A')
          and date_diff('day', start_date, end_date) between 350 and 380
          and end_date <= filed
          and coalesce(d.sic, '') <> '6770'
          and concept in ('Revenues', 'RevenueFromContractWithCustomerExcludingAssessedTax', 'SalesRevenueNet',
                          'RevenuesNetOfInterestExpense', 'InterestAndDividendIncomeOperating', 'NetIncomeLoss')
    ),
    latest as (   -- per company, concept and fiscal year: the most recently filed 10-K value
        select * from f
        qualify row_number() over (partition by cik, concept, end_date order by filed desc, accession desc) = 1
    )
    select cik, end_date,
           year(end_date - interval 182 day) as yr,   -- the calendar year holding most of the fiscal year
           max(val) filter (where concept <> 'NetIncomeLoss') as revenue,
           max(val) filter (where concept = 'NetIncomeLoss') as net_income
    from latest group by cik, end_date
""")
print("dropped as impossible (revenue over $1tn or |net income| over $250bn):")
print(con.sql("""select fy.*, d.name from fy left join serving_submission_details d using (cik)
                 where revenue > 1e12 or abs(net_income) > 2.5e11""").pl())
pub = con.sql("""
    select yr, sum(revenue) filter (where revenue > 0) / 1e9 as revenue, sum(net_income) / 1e9 as net_income,
           count(revenue) filter (where revenue > 0) as rev_cos, count(net_income) as ni_cos
    from fy
    where coalesce(revenue, 0) <= 1e12 and abs(coalesce(net_income, 0)) <= 2.5e11
    group by yr
""").pl()

t = (pub.join(gdp, on="yr").join(macro, on="yr").filter(pl.col("yr").is_between(2011, 2025)).sort("yr")
     .with_columns((pl.col("revenue") / pl.col("GDP")).alias("rev_to_gdp"),
                   (pl.col("revenue") / pl.col("GOAI")).alias("rev_to_gross_output"),
                   (pl.col("revenue") / pl.col("GOPI")).alias("rev_to_private_gross_output"),
                   (pl.col("net_income") / pl.col("CP")).alias("ni_to_corp_profits")))
with pl.Config(tbl_rows=30, tbl_width_chars=220, float_precision=3):
    print(t)

fig = go.Figure()
for col, name, color, dash in (
        ("rev_to_gdp", "Revenue / GDP (mismatched: sales vs value added)", "#9ca3af", "dot"),
        ("rev_to_gross_output", "Revenue / gross output (sales vs sales)", "#2563eb", "solid"),
        ("rev_to_private_gross_output", "Revenue / private-sector gross output", "#93c5fd", "solid"),
        ("ni_to_corp_profits", "Net income / corporate profits after tax (profits vs profits)", "#d97706", "solid")):
    fig.add_trace(go.Scatter(x=t["yr"], y=t[col], name=name, mode="lines+markers", line=dict(color=color, dash=dash)))
fig.update_layout(height=520, template="plotly_white", yaxis=dict(tickformat=".0%", rangemode="tozero"),
                  title="How much of the US economy SEC filers account for, three ways",
                  legend=dict(orientation="h", y=-0.15), margin=dict(l=60, r=30, t=70, b=120))
fig.write_html(f"{ROOT}/explore/coverage/coverage.html", include_plotlyjs=True)
print("wrote", f"{ROOT}/explore/coverage/coverage.html")

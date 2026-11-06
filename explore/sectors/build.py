"""Sector shares, 2024: GDP (BEA value added via FRED), gross output, and SEC filers' revenue by SIC mapped to BEA sectors.
One-off test. FRED is fetched at build time and not stored."""
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

SECTORS = {  # FRED code suffix -> BEA sector name
    "AFH": "Agriculture, forestry, fishing", "M": "Mining, oil and gas", "U": "Utilities",
    "C": "Construction", "MA": "Manufacturing", "W": "Wholesale trade", "R": "Retail trade",
    "T": "Transportation and warehousing", "I": "Information", "FI": "Finance and insurance",
    "RL": "Real estate, rental, leasing", "PBS": "Professional and business services",
    "ESHS": "Education, health care, social assistance", "AERAF": "Arts, recreation, hotels, food services",
    "G": "Government",
}

def fred_2024(series_id):
    u = "https://api.stlouisfed.org/fred/series/observations?" + urllib.parse.urlencode(
        {"series_id": series_id, "api_key": KEY, "file_type": "json",
         "observation_start": "2024-01-01", "observation_end": "2024-12-31"})
    obs = json.load(urllib.request.urlopen(u))["observations"]
    vals = [float(o["value"]) for o in obs]
    assert len(vals) == 4, (series_id, obs)
    return sum(vals) / 4

gdp = []
for measure, pre in (("va", "VA"), ("go", "GO")):
    total, private = fred_2024(pre + "AI"), fred_2024(pre + "PI")
    named_private = 0.0
    for code, name in SECTORS.items():
        v = fred_2024(pre + code)
        gdp.append((measure, name, v / total))
        if code != "G":
            named_private += v
    gdp.append((measure, "Other services", (private - named_private) / total))  # residual: no FRED series found
gdp = pl.DataFrame(gdp, schema=["measure", "sector", "share"], orient="row").pivot(on="measure", index="sector", values="share")

con = duckdb.connect(CATALOG, read_only=True)
pub = con.sql("""
    with rev as (
        select cik, max(val) as revenue
        from serving_company_facts
        where taxonomy = 'us-gaap' and unit = 'USD' and frame = 'CY2024'
          and concept in ('Revenues', 'RevenueFromContractWithCustomerExcludingAssessedTax', 'SalesRevenueNet',
                          'RevenuesNetOfInterestExpense', 'InterestAndDividendIncomeOperating')
        group by cik having max(val) > 0
    ),
    s as (select r.*, try_cast(d.sic as int) as sic from rev r left join serving_submission_details d using (cik))
    select *, case
        when sic is null then 'No SIC code'
        when sic = 6770 then 'Blank checks (SPACs)'
        when sic between 100 and 999 then 'Agriculture, forestry, fishing'
        when sic between 1000 and 1499 then 'Mining, oil and gas'
        when sic between 1500 and 1799 then 'Construction'
        when sic between 2000 and 3999 then 'Manufacturing'
        when sic between 4800 and 4899 then 'Information'
        when sic between 4900 and 4999 then 'Utilities'
        when sic between 4000 and 4799 then 'Transportation and warehousing'
        when sic between 5000 and 5199 then 'Wholesale trade'
        when sic between 5800 and 5899 then 'Arts, recreation, hotels, food services'
        when sic between 5200 and 5999 then 'Retail trade'
        when sic between 6500 and 6599 or sic = 6798 then 'Real estate, rental, leasing'
        when sic between 6000 and 6799 then 'Finance and insurance'
        when sic between 7000 and 7099 or sic between 7900 and 7999 then 'Arts, recreation, hotels, food services'
        when sic in (7370, 7372, 7374) or sic between 7800 and 7899 then 'Information'
        when sic between 7300 and 7399 or sic in (8100, 8111) or sic between 8700 and 8999 then 'Professional and business services'
        when sic between 8000 and 8099 or sic between 8200 and 8399 then 'Education, health care, social assistance'
        when sic between 7000 and 8699 then 'Other services'
        when sic between 9100 and 9799 then 'Government'
        else 'Unclassified (SIC 99xx)'
    end as sector
    from s
""").pl()

pub_s = (pub.filter(pl.col("sector") != "Blank checks (SPACs)")
         .group_by("sector").agg(pl.len().alias("companies"), pl.col("revenue").sum().alias("rev")))
pub_s = pub_s.with_columns((pl.col("rev") / pl.col("rev").sum()).alias("pub"),
                           (pl.col("companies") / pl.col("companies").sum()).alias("count_share"))

tbl = (gdp.join(pub_s, on="sector", how="full", coalesce=True).fill_null(0)
       .with_columns((pl.col("pub") / pl.col("go")).alias("pub_vs_go"))
       .sort("va", descending=True))
with pl.Config(tbl_rows=30, tbl_width_chars=200, fmt_str_lengths=45):
    print("public companies used:", pub_s["companies"].sum(), " revenue $tn:", round(pub_s["rev"].sum() / 1e12, 2))
    print(tbl.select("sector", "va", "go", "pub", "companies", "count_share", "pub_vs_go"))
    print("check: va sums to", round(tbl["va"].sum(), 4), " go sums to", round(tbl["go"].sum(), 4))

order = tbl["sector"].to_list()[::-1]
fig = go.Figure()
for col, name, color in (("va", "Share of GDP (value added)", "#059669"),
                         ("go", "Share of gross output (all sales)", "#86efac"),
                         ("pub", "Share of SEC filers' revenue", "#2563eb")):
    d = {r["sector"]: r[col] for r in tbl.iter_rows(named=True)}
    fig.add_trace(go.Bar(y=order, x=[d[s] for s in order], name=name, orientation="h", marker_color=color))
fig.update_layout(barmode="group", height=900, template="plotly_white",
                  title="Sector shares in 2024: the US economy vs SEC filers",
                  xaxis=dict(tickformat=".0%", title="Share of total"),
                  legend=dict(orientation="h", y=-0.07), margin=dict(l=300, r=30, t=70, b=90))
fig.write_html(f"{ROOT}/explore/sectors/sectors.html", include_plotlyjs=True)
print("wrote", f"{ROOT}/explore/sectors/sectors.html")

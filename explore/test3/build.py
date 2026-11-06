"""Test 3: equity / assets across all companies at 2024-12-31 (SEC frame CY2024Q4I), by SIC division. One-off, read-only."""
import duckdb
import plotly.graph_objects as go
import polars as pl

from pharos.paths import PROJECT_ROOT, data_root

ROOT = PROJECT_ROOT.as_posix()
CATALOG = (data_root() / "catalog.duckdb").as_posix()
con = duckdb.connect(CATALOG, read_only=True)

rows = con.sql("""
    with bs as (
        select cik,
               max(val) filter (where concept = 'Assets') as assets,
               max(val) filter (where concept = 'StockholdersEquity') as equity
        from serving_company_facts
        where taxonomy = 'us-gaap' and unit = 'USD' and frame = 'CY2024Q4I'
          and concept in ('Assets', 'StockholdersEquity')
        group by cik
    ),
    d as (
        select b.*, s.name, s.sic, try_cast(s.sic as int) as sic_n
        from bs b left join serving_submission_details s using (cik)
        where b.assets > 0 and b.equity is not null
    )
    select *, equity / assets as eq_ratio,
        case
            when sic_n is null then 'No SIC code'
            when sic_n = 6770 then 'Blank checks (SPACs)'
            when sic_n between 6000 and 6199 then 'Banks and credit'
            when sic_n between 100 and 999 then 'Agriculture'
            when sic_n between 1000 and 1499 then 'Mining, oil and gas'
            when sic_n between 1500 and 1799 then 'Construction'
            when sic_n between 2000 and 3999 then 'Manufacturing'
            when sic_n between 4000 and 4999 then 'Transport, telecom, utilities'
            when sic_n between 5000 and 5199 then 'Wholesale'
            when sic_n between 5200 and 5999 then 'Retail'
            when sic_n between 6200 and 6799 then 'Other finance, insurance, real estate'
            when sic_n between 7000 and 8999 then 'Services'
            else 'Other / nonclassifiable'
        end as division
    from d
""").pl()

summary = (rows.group_by("division")
           .agg(pl.len().alias("companies"),
                pl.col("eq_ratio").median().alias("median"),
                pl.col("eq_ratio").quantile(0.25).alias("p25"),
                pl.col("eq_ratio").quantile(0.75).alias("p75"),
                (pl.col("eq_ratio") < 0).mean().alias("share_negative"))
           .sort("median"))
with pl.Config(tbl_rows=30, tbl_width_chars=160):
    print("companies with both values:", rows.height)
    print(summary)
    print(rows.filter(pl.col("eq_ratio") < -5).select("cik", "name", "sic", "assets", "equity", "eq_ratio")
          .sort("eq_ratio").head(5))

order = summary["division"].to_list()
fig = go.Figure()
fig.add_trace(go.Box(x=rows["eq_ratio"].clip(-1, 1), y=rows["division"], orientation="h",
                     boxpoints=False, marker_color="#2563eb", name=""))
labels = [f"{d}  (n={n:,})" for d, n in zip(summary["division"], summary["companies"])]
fig.update_yaxes(categoryorder="array", categoryarray=order, tickvals=order, ticktext=labels)
fig.update_xaxes(range=[-1.05, 1.05], tickformat=".0%", zeroline=True, zerolinecolor="#9ca3af",
                 title="Stockholders' equity / total assets, 2024-12-31 (values beyond -100% shown at -100%)")
fig.update_layout(height=620, template="plotly_white", showlegend=False,
                  title=f"Test 3: equity as a share of assets, {rows.height:,} companies, by industry (SIC division)",
                  margin=dict(l=320, r=30, t=70, b=70))
fig.write_html(f"{ROOT}/explore/test3/test3.html", include_plotlyjs=True)
print("wrote", f"{ROOT}/explore/test3/test3.html")

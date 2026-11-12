"""Test 12: where the companies trade today: NYSE, Nasdaq, OTC or no ticker (2024 revenue companies)."""
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import polars as pl

import base

y = base.revenue_year(2024)
t = (y.group_by("exchange").agg(pl.len().alias("companies"), pl.col("revenue").sum().alias("rev"),
                                pl.col("revenue").median().alias("median_rev"),
                                (pl.col("net_income") < 0).mean().alias("loss_share"))
     .with_columns((pl.col("companies") / pl.col("companies").sum()).alias("count_share"),
                   (pl.col("rev") / pl.col("rev").sum()).alias("rev_share"))
     .sort("rev", descending=True))
with pl.Config(tbl_width_chars=200):
    print(t)
print(y.filter(pl.col("exchange") == "No ticker listed").sort("revenue", descending=True).select("name", "sic_description", "revenue").head(10))

order = t["exchange"].to_list()
fig = make_subplots(rows=1, cols=2, subplot_titles=("Share of companies and of revenue", "Median company revenue, $ millions"))
fig.add_trace(go.Bar(x=order, y=t["count_share"], name="Share of companies", marker_color="#93c5fd"), row=1, col=1)
fig.add_trace(go.Bar(x=order, y=t["rev_share"], name="Share of revenue", marker_color="#2563eb"), row=1, col=1)
fig.add_trace(go.Bar(x=order, y=t["median_rev"] / 1e6, name="Median revenue", marker_color="#059669", showlegend=False,
                     text=(t["median_rev"] / 1e6).round(0), textposition="outside"), row=1, col=2)
fig.update_yaxes(tickformat=".0%", row=1, col=1)
fig.update_yaxes(type="log", row=1, col=2)
fig.update_layout(barmode="group", height=440, template="plotly_white",
                  title="Companies with 2024 revenue, by where their shares trade today",
                  legend=dict(orientation="h", y=-0.15), margin=dict(l=60, r=20, t=80, b=80))

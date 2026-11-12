"""Test 15: where public companies are incorporated (2024 revenue companies)."""
import plotly.graph_objects as go
import polars as pl

import base

y = base.revenue_year(2024).with_columns(
    pl.when(pl.col("state").is_null()).then(pl.lit("Not recorded"))
      .when(pl.col("state") == "DE").then(pl.lit("Delaware"))
      .when(pl.col("state") == "NV").then(pl.lit("Nevada"))
      .when(pl.col("state") == "MD").then(pl.lit("Maryland"))
      .when(pl.col("state").str.contains(r"^[A-Z]{2}$")).then(pl.lit("Other US states"))
      .otherwise(pl.lit("Outside the US")).alias("where"))
t = (y.group_by("where").agg(pl.len().alias("companies"), pl.col("revenue").sum().alias("rev"))
     .with_columns((pl.col("companies") / pl.col("companies").sum()).alias("count_share"),
                   (pl.col("rev") / pl.col("rev").sum()).alias("rev_share"))
     .sort("companies", descending=True))
with pl.Config(tbl_width_chars=200):
    print(t)
print(y.group_by("state").len().sort("len", descending=True).head(12))
print("Maryland top SICs:", y.filter(pl.col("state") == "MD").group_by("sic_description").len().sort("len", descending=True).head(5))

order = t["where"].to_list()
fig = go.Figure()
fig.add_trace(go.Bar(x=order, y=t["count_share"], name="Share of companies", marker_color="#93c5fd",
                     text=[f"{v:.0%}" for v in t["count_share"]], textposition="outside"))
fig.add_trace(go.Bar(x=order, y=t["rev_share"], name="Share of revenue", marker_color="#2563eb",
                     text=[f"{v:.0%}" for v in t["rev_share"]], textposition="outside"))
fig.update_layout(barmode="group", height=420, template="plotly_white", yaxis=dict(tickformat=".0%", range=[0, 0.72]),
                  title="State of incorporation, companies with 2024 revenue",
                  legend=dict(orientation="h", y=-0.15), margin=dict(l=60, r=20, t=70, b=80))

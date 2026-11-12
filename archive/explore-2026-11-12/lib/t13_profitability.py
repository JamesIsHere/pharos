"""Test 13: who loses money (2024): share of companies with a net loss, by size and by sector."""
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import polars as pl

import base

y = base.revenue_year(2024).filter(pl.col("net_income").is_not_null()).with_columns(
    pl.when(pl.col("revenue") < 1e7).then(pl.lit("1. under $10m"))
      .when(pl.col("revenue") < 1e8).then(pl.lit("2. $10m-100m"))
      .when(pl.col("revenue") < 1e9).then(pl.lit("3. $100m-1bn"))
      .when(pl.col("revenue") < 1e10).then(pl.lit("4. $1bn-10bn"))
      .otherwise(pl.lit("5. $10bn and up")).alias("size"))
size = y.group_by("size").agg(pl.len().alias("companies"), (pl.col("net_income") < 0).mean().alias("loss_share"),
                              (pl.col("net_income").sum() / pl.col("revenue").sum()).alias("margin")).sort("size")
sector = (y.group_by("sector").agg(pl.len().alias("companies"), (pl.col("net_income") < 0).mean().alias("loss_share"),
                                   (pl.col("net_income").sum() / pl.col("revenue").sum()).alias("margin"))
          .filter(pl.col("companies") >= 20).sort("loss_share"))
with pl.Config(tbl_rows=30, tbl_width_chars=200):
    print("companies with revenue and net income:", y.height, " loss share:", round((y["net_income"] < 0).mean(), 3))
    print(size)
    print(sector)

fig = make_subplots(rows=1, cols=2, column_widths=[0.35, 0.65], horizontal_spacing=0.28,
                    subplot_titles=("By revenue size", "By sector (20+ companies)"))
fig.add_trace(go.Bar(x=[s[3:] for s in size["size"]], y=size["loss_share"], marker_color="#dc2626",
                     text=[f"{v:.0%}" for v in size["loss_share"]], textposition="outside"), row=1, col=1)
fig.add_trace(go.Bar(y=sector["sector"], x=sector["loss_share"], orientation="h", marker_color="#f87171",
                     text=[f"{v:.0%} (n={n})" for v, n in zip(sector["loss_share"], sector["companies"])],
                     textposition="outside"), row=1, col=2)
fig.update_yaxes(tickformat=".0%", range=[0, 1], row=1, col=1)
fig.update_xaxes(tickformat=".0%", range=[0, 1.0], row=1, col=2)
fig.update_layout(height=520, template="plotly_white", showlegend=False,
                  title="Share of companies reporting a net loss, 2024", margin=dict(l=60, r=30, t=80, b=60))

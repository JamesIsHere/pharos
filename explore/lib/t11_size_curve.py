"""Test 11: how few companies make up most public revenue (2024): cumulative share by company rank."""
import plotly.graph_objects as go
import polars as pl

import base

y = base.revenue_year(2024).sort("revenue", descending=True).with_columns(
    pl.int_range(1, pl.len() + 1).alias("rank"),
    (pl.col("revenue").cum_sum() / pl.col("revenue").sum()).alias("cum"))
marks = {}
for p in (0.25, 0.5, 0.8, 0.9, 0.99):
    marks[p] = y.filter(pl.col("cum") >= p)["rank"].min()
print("companies:", y.height, " reach:", marks)
for n in (10, 50, 100, 500, 1000):
    print(n, round(y.filter(pl.col("rank") == n)["cum"][0], 3), round(y.filter(pl.col("rank") == n)["revenue"][0] / 1e9, 2))
print("median company revenue $m:", round(y["revenue"].median() / 1e6, 1),
      " under $10m:", y.filter(pl.col("revenue") < 1e7).height)

fig = go.Figure(go.Scatter(x=y["rank"], y=y["cum"], mode="lines", line=dict(color="#2563eb", width=3),
                           customdata=y.select("name", "revenue").to_numpy(),
                           hovertemplate="rank %{x}: %{customdata[0]}<br>cumulative share %{y:.1%}<extra></extra>"))
for p, r in marks.items():
    fig.add_trace(go.Scatter(x=[r], y=[p], mode="markers+text", marker=dict(color="#dc2626", size=8),
                             text=[f"{p:.0%} of revenue: top {r:,}"], textposition="bottom right" if p < 0.85 else "middle left", showlegend=False))
fig.update_layout(height=480, template="plotly_white", showlegend=False,
                  title=f"Cumulative share of public-company revenue by company rank, {y.height:,} companies (2024)",
                  xaxis=dict(type="log", title="Companies ranked by revenue (log scale)"),
                  yaxis=dict(tickformat=".0%", range=[0, 1.02], title="Share of total revenue"),
                  margin=dict(l=70, r=30, t=70, b=60))

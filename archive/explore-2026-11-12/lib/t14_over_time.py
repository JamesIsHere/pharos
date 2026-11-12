"""Test 14: is public-company revenue concentrating over time? Top-10 / top-100 share of all revenue, and the median
industry HHI (2-digit SIC, 10+ companies), 2011-2025."""
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import polars as pl

import base

c = base.companies().filter(pl.col("revenue") > 0)
rows = []
for yr in range(2011, 2026):
    y = c.filter(pl.col("yr") == yr).sort("revenue", descending=True)
    tot = y["revenue"].sum()
    ind = base.concentration(y.filter(pl.col("sic").is_not_null()).with_columns((pl.col("sic") // 100).alias("sic2")), "sic2")
    ind = ind.filter(pl.col("companies") >= 10)
    rows.append(dict(yr=yr, companies=y.height, top10=y.head(10)["revenue"].sum() / tot,
                     top100=y.head(100)["revenue"].sum() / tot, median_hhi=ind["hhi"].median(),
                     industries=ind.height, top_name=y["name"][0]))
t = pl.DataFrame(rows)
with pl.Config(tbl_rows=20, tbl_width_chars=200, fmt_str_lengths=30):
    print(t)

fig = make_subplots(rows=1, cols=2, subplot_titles=("Share of all revenue, largest companies",
                                                    "Median industry HHI"))
fig.add_trace(go.Scatter(x=t["yr"], y=t["top100"], name="Largest 100", mode="lines+markers", line=dict(color="#2563eb")), row=1, col=1)
fig.add_trace(go.Scatter(x=t["yr"], y=t["top10"], name="Largest 10", mode="lines+markers", line=dict(color="#93c5fd")), row=1, col=1)
fig.add_trace(go.Scatter(x=t["yr"], y=t["median_hhi"], name="Median industry HHI", mode="lines+markers",
                         line=dict(color="#d97706"), showlegend=False), row=1, col=2)
fig.update_yaxes(tickformat=".0%", rangemode="tozero", row=1, col=1)
fig.update_yaxes(rangemode="tozero", row=1, col=2)
fig.update_layout(height=440, template="plotly_white", title="Is public-company revenue concentrating? 2011-2025",
                  legend=dict(orientation="h", y=-0.15), margin=dict(l=60, r=20, t=80, b=80))

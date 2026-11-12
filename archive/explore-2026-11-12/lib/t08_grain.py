"""Test 8: how many industries are big enough to measure, at each grain of SEC's industry codes (2024)."""
import plotly.graph_objects as go
import polars as pl

import base

y = base.revenue_year(2024).with_columns(
    (pl.col("sic") // 1).alias("sic4"), (pl.col("sic") // 10).alias("sic3"), (pl.col("sic") // 100).alias("sic2"))

rows = []
for label, col in (("4-digit SIC", "sic4"), ("3-digit SIC", "sic3"), ("2-digit SIC", "sic2"), ("15 BEA sectors", "sector")):
    g = y.filter(pl.col(col).is_not_null()).group_by(col).agg(pl.len().alias("n"), pl.col("revenue").sum().alias("rev"))
    big = g.filter(pl.col("n") >= 10)
    rows.append(dict(grain=label, groups=g.height, groups_10plus=big.height,
                     companies_in_10plus=big["n"].sum() / g["n"].sum(), revenue_in_10plus=big["rev"].sum() / g["rev"].sum(),
                     singletons=g.filter(pl.col("n") == 1).height))
t = pl.DataFrame(rows)
print("companies with 2024 revenue:", y.height)
with pl.Config(tbl_width_chars=200):
    print(t)

fig = go.Figure()
fig.add_trace(go.Bar(x=t["grain"], y=t["groups"], name="Industries with any company", marker_color="#cbd5e1",
                     text=t["groups"], textposition="outside"))
fig.add_trace(go.Bar(x=t["grain"], y=t["groups_10plus"], name="Industries with 10 or more companies", marker_color="#2563eb",
                     text=[f"{g}  ({c:.0%} of companies)" for g, c in zip(t["groups_10plus"], t["companies_in_10plus"])],
                     textposition="outside"))
fig.update_layout(barmode="group", height=460, template="plotly_white", yaxis_title="Number of industries",
                  title="How many industries hold enough companies to measure concentration (2024)",
                  legend=dict(orientation="h", y=-0.15), margin=dict(l=60, r=20, t=70, b=90))

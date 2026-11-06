"""Test 10: the most and least concentrated detailed industries (4-digit SIC, 10+ companies, 2024), as a table."""
import plotly.graph_objects as go
import polars as pl

import base

y = base.revenue_year(2024).filter(pl.col("sic").is_not_null())
names = y.group_by("sic").agg(pl.col("sic_description").first().alias("label"))
c = base.concentration(y, "sic").filter(pl.col("companies") >= 10).join(names, on="sic").sort("top1", descending=True)
print("4-digit industries with 10+ companies:", c.height)
pick = pl.concat([c.head(15), c.tail(5)])
with pl.Config(tbl_rows=40, tbl_width_chars=220, fmt_str_lengths=45):
    print(pick.select("sic", "label", "companies", "revenue_bn", "top_company", "top1", "cr4", "hhi"))

fig = go.Figure(go.Table(
    columnwidth=[50, 260, 70, 80, 220, 70, 70, 70],
    header=dict(values=["SIC", "Industry (SEC description)", "Companies", "Revenue $bn", "Largest company",
                        "Largest share", "Top 4 share", "HHI"],
                fill_color="#e5e7eb", align="left", font=dict(size=12)),
    cells=dict(values=[pick["sic"], pick["label"], pick["companies"], pick["revenue_bn"].round(0),
                       pick["top_company"], (pick["top1"] * 100).round(0).cast(int).cast(str) + "%",
                       (pick["cr4"] * 100).round(0).cast(int).cast(str) + "%", pick["hhi"].round(0)],
               align="left", height=30, font=dict(size=11),
               fill_color=[["#ffffff"] * 15 + ["#f1f5f9"] * 5])))
fig.update_layout(height=940, margin=dict(l=10, r=10, t=50, b=10),
                  title="Most concentrated 15 and least concentrated 5 detailed industries (white: most, grey: least)")

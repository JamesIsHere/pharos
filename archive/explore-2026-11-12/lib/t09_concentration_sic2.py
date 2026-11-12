"""Test 9: concentration inside each 2-digit SIC industry with 10+ companies (2024): top company's share and top-4 share."""
import plotly.graph_objects as go
import polars as pl

import base

y = base.revenue_year(2024).filter(pl.col("sic").is_not_null()).with_columns((pl.col("sic") // 100).alias("sic2"))
# a readable name per 2-digit group: the SEC description of its largest 4-digit code by revenue
names = (y.group_by("sic2", "sic_description").agg(pl.col("revenue").sum()).sort("revenue", descending=True)
         .group_by("sic2").agg(pl.col("sic_description").first().alias("label")))
c = (base.concentration(y, "sic2").filter(pl.col("companies") >= 10).join(names, on="sic2")
     .with_columns(pl.format("SIC {} ({})", pl.col("sic2").cast(pl.Utf8).str.zfill(2), pl.col("label")).alias("industry"))
     .sort("top1"))
with pl.Config(tbl_rows=80, tbl_width_chars=220, fmt_str_lengths=40):
    print(c.select("industry", "companies", "revenue_bn", "top_company", "top1", "cr4", "hhi", "to_50pct").sort("top1", descending=True))
    print("industries:", c.height, " median top1:", round(c["top1"].median(), 3), " median cr4:", round(c["cr4"].median(), 3),
          " HHI>1800:", c.filter(pl.col("hhi") > 1800).height)

fig = go.Figure()
fig.add_trace(go.Bar(y=c["industry"], x=c["cr4"], orientation="h", name="Top 4 companies' share", marker_color="#bfdbfe"))
fig.add_trace(go.Bar(y=c["industry"], x=c["top1"], orientation="h", name="Largest company's share", marker_color="#2563eb",
                     text=c["top_company"], textposition="inside", insidetextanchor="start",
                     textfont=dict(color="white", size=10),
                     customdata=c.select("companies", "hhi").to_numpy(),
                     hovertemplate="%{y}<br>largest: %{text} %{x:.0%}<br>companies: %{customdata[0]}<br>HHI: %{customdata[1]:.0f}<extra></extra>"))
fig.update_layout(barmode="overlay", height=max(500, 22 * c.height + 120), template="plotly_white",
                  title="Share of each industry's public-company revenue held by its largest company and its top 4 (2024)",
                  xaxis=dict(tickformat=".0%", range=[0, 1]), yaxis=dict(tickfont=dict(size=10)),
                  legend=dict(orientation="h", y=-0.04), margin=dict(l=330, r=20, t=70, b=70))

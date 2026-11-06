"""Test 16: Amazon's share of revenue at every grain of the industry codes, 2011-2025: the test-10 measure taken
every year, and rolled up from 4-digit SIC 5961 to division G and to all public companies."""
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import polars as pl

import base

c = base.with_grains(base.companies().filter(pl.col("revenue") > 0))
amzn = pl.col("name") == "AMAZON COM INC"
GRAINS = (("5961 Catalog and mail-order houses (4-digit)", pl.col("sic") == 5961, "#1e3a8a"),
          ("596 Nonstore retailers (3-digit)", pl.col("sic3") == 596, "#2563eb"),
          ("59 Miscellaneous retail (2-digit)", pl.col("sic2") == 59, "#60a5fa"),
          ("G Retail trade (division, 52-59)", pl.col("division") == "G", "#d97706"),
          ("Retail trade (Pharos sector, test 7)", pl.col("sector") == "Retail trade", "#fbbf24"),
          ("All public companies", pl.lit(True), "#9ca3af"))
shares = {label: c.filter(f).group_by("yr").agg((pl.col("revenue").filter(amzn).sum() / pl.col("revenue").sum())
                                                 .alias("share")).sort("yr") for label, f, _ in GRAINS}
for label, s in shares.items():
    print(label, [round(v, 3) for v in s["share"]])

t = (c.filter(pl.col("sic") == 5961).group_by("yr")
      .agg((pl.col("revenue").filter(amzn).sum() / 1e9).alias("amazon_bn"),
           (pl.col("revenue").filter(~amzn).sum() / 1e9).alias("rest_bn")).sort("yr"))

fig = make_subplots(rows=1, cols=2, column_widths=[0.62, 0.38],
                    subplot_titles=("Amazon's share of revenue, by grain", "SIC 5961 revenue, $bn"))
for label, _, color in GRAINS:
    s = shares[label]
    fig.add_trace(go.Scatter(x=s["yr"], y=s["share"], mode="lines+markers", name=label, line=dict(color=color),
                             hovertemplate=label + "<br>%{x}: %{y:.1%}<extra></extra>"), row=1, col=1)
fig.add_trace(go.Bar(x=t["yr"], y=t["amazon_bn"], name="Amazon", marker_color="#1e3a8a", showlegend=False,
                     hovertemplate="Amazon %{x}: $%{y:.0f}bn<extra></extra>"), row=1, col=2)
fig.add_trace(go.Bar(x=t["yr"], y=t["rest_bn"], name="Other 5961 companies", marker_color="#93c5fd", showlegend=False,
                     hovertemplate="Other 5961 companies %{x}: $%{y:.0f}bn<extra></extra>"), row=1, col=2)
fig.update_yaxes(tickformat=".0%", range=[0, 1], row=1, col=1)
fig.update_layout(barmode="stack", height=520, template="plotly_white",
                  title="Amazon's share at each grain, 2011-2025",
                  legend=dict(orientation="h", y=-0.12, font=dict(size=11)), margin=dict(l=60, r=20, t=80, b=130))

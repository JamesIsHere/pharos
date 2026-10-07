"""Home: the status bar and the watchlist's last year, one small chart per
ticker (design.md section 8). Never empty: before the first publish it says so."""

import plotly.graph_objects as go
import polars as pl
import streamlit as st
from plotly.subplots import make_subplots

from common import query, status_bar

COLS = 6

st.set_page_config(page_title="Pharos", layout="wide")
status_bar()
st.title("Pharos")

year = query("watchlist_year")
if year is None or year.is_empty():
    st.info("Nothing has been published yet. Run `uv run pharos refresh`.")
    st.stop()

tickers = year["ticker"].unique().sort().to_list()
change = {t: p[-1] / p[0] - 1 for t, p in
          year.group_by("ticker").agg(pl.col("value").sort_by("obs_date")).iter_rows()}
rows = -(-len(tickers) // COLS)
fig = make_subplots(rows=rows, cols=COLS, subplot_titles=[f"{t}  {change[t]:+.1%}" for t in tickers],
                    vertical_spacing=0.12, horizontal_spacing=0.04)
for i, t in enumerate(tickers):
    part = year.filter(pl.col("ticker") == t)
    fig.add_trace(go.Scatter(x=part["obs_date"].to_list(), y=part["value"].to_list(), mode="lines",
                             line=dict(color="#2a78d6", width=1.5), showlegend=False,
                             hovertemplate=f"{t}<br>%{{x|%Y-%m-%d}}: %{{y:,.2f}} USD<extra></extra>"),
                  row=i // COLS + 1, col=i % COLS + 1)
fig.update_xaxes(showticklabels=False, showgrid=False)
fig.update_yaxes(showticklabels=False, showgrid=False)
fig.update_layout(height=170 * rows, margin=dict(l=0, r=0, t=30, b=0))
st.subheader(f"Watchlist, last year to {year['obs_date'].max():%Y-%m-%d}")
st.plotly_chart(fig, width="stretch")
st.page_link("pages/2_Charts.py", label="Charts")
st.page_link("pages/1_Health.py", label="Health")

"""Health page (design.md section 7, as amended by D42)."""

import plotly.graph_objects as go
import polars as pl
import streamlit as st

from common import frame, run_failures, status_bar
from pharos import board, config

# sequential blue ramp, light -> dark (dataviz reference palette, steps 100-700)
COVERAGE_SCALE = [[0.0, "#cde2fb"], [0.5, "#5598e7"], [0.9, "#256abf"], [1.0, "#0d366b"]]
# status palette: good / warning / critical, always beside a label
RUN_COLOR = {"green": "#0ca30c", "yellow": "#fab219", "red": "#d03b3b"}


def failing_checks(results: list[dict]) -> None:
    """The table of checks that didn't pass, each drillable to its rows grouped by source (D42)."""
    st.dataframe(
        pl.DataFrame([{"check": r["check_id"], "kind": "gate" if r["gate"] else "monitor",
                       "status": r["status"], "rows": r["failing_row_count"],
                       "evaluated": "at run" if r["gate"] or r.get("context") == "run" else "now",
                       "description": r["description"]} for r in results]),
        hide_index=True, width="stretch")
    for r in results:
        with st.expander(f"{r['check_id']} {r['status']}: {r['failing_row_count'] or 0} rows. {r['description']}"):
            if r["error"]:
                st.code(r["error"])
            for source, rows in board.failing_rows_by_source(r).items():
                st.markdown(f"**{source}** ({len(rows)})")
                st.dataframe(pl.DataFrame(rows, infer_schema_length=None), hide_index=True, width="stretch")


st.set_page_config(page_title="Pharos: Health", layout="wide")
h = status_bar()
st.title("Health")

# --- A run that didn't publish: why, from its own record (#38) ---------------
if h.latest_run_status in ("blocked", "failed"):
    st.header(f"Latest run {h.latest_run_status}: {h.latest_run}")
    st.caption(f"Serving still shows {h.published_run or 'nothing'}. These results are the refused run's, "
               "recorded when it ran; everything below this section describes what is served.")
    crash, refused = run_failures(h.latest_run)
    if crash:
        st.code(crash)
    if refused:
        failing_checks(refused)
    st.divider()

# --- Overall traffic light -------------------------------------------------
counts = {s: sum(r["status"] == s for r in h.results) for s in ("pass", "acknowledged", "warn", "error", "broken")}
summary = frame("summary")
sources = list(summary.iter_rows(named=True)) if summary is not None else []
cols = st.columns([1, 1.6, 1, 1.9] + [1.4] * len(sources))
cols[0].metric("Status", h.status.upper())
cols[1].metric("Last successful run", f"{h.published_at.astimezone():%Y-%m-%d %H:%M}" if h.published_at else "never")
cols[2].metric("Rows served", f"{summary['rows'].sum():,}" if summary is not None else "0")
cols[3].metric("Checks pass / acknowledged / warn / fail",
               f"{counts['pass']} / {counts['acknowledged']} / {counts['warn']} / {counts['error'] + counts['broken']}")
for col, r in zip(cols[4:], sources):
    col.metric(f"Latest {r['source']} observation", f"{r['latest_obs_date']}")

# --- Failing checks, drillable to their rows grouped by source --------------
st.header("Failing checks")
failing = [r for r in h.results if r["status"] != "pass"]
if failing:
    failing_checks(failing)
else:
    st.write("Every check passes.")

if summary is None:
    st.info("Nothing has been published yet, so there is no coverage, run history or sample to show.")
    st.stop()

# --- Coverage heatmap -------------------------------------------------------
st.header("Coverage")
st.caption("Share of expected observations present per series per month (C04's definition). Blank: outside the series' window.")
coverage = frame("coverage")
start = config.sources()["backfill_start"]
if not st.toggle(f"Full history (default starts at the backfill start, {start})"):
    coverage = coverage.filter(pl.col("month") >= start)
series = coverage["series_id"].unique().sort().to_list()
months = coverage["month"].unique().sort().to_list()
grid = coverage.pivot(on="month", index="series_id", values="share_present").sort("series_id")
detail = coverage.with_columns(pl.format("{} of {}", "present", "expected").alias("txt")) \
    .pivot(on="month", index="series_id", values="txt").sort("series_id")
month_cols = [str(m) for m in months]
heat = go.Figure(go.Heatmap(
    z=grid.select([c for c in month_cols if c in grid.columns]).to_numpy(),
    x=[m for m in months if str(m) in grid.columns], y=grid["series_id"].to_list(),
    customdata=detail.select([c for c in month_cols if c in detail.columns]).to_numpy(),
    colorscale=COVERAGE_SCALE, zmin=0, zmax=1, xgap=0, ygap=2,
    colorbar=dict(title="present", tickformat=".0%"),
    hovertemplate="%{y}<br>%{x|%Y-%m}<br>%{customdata} expected dates<extra></extra>"))
heat.update_layout(height=max(300, 22 * len(series) + 80), margin=dict(l=0, r=0, t=10, b=0),
                   yaxis=dict(autorange="reversed"))
st.plotly_chart(heat, width="stretch")

# --- Row counts by run, per load --------------------------------------------
st.header("Rows landed by run")
rows = frame("rows_by_run")
loads = rows["load"].unique().sort().to_list()
for col, load in zip(st.columns(len(loads)), loads):
    part = rows.filter(pl.col("load") == load)
    fig = go.Figure(go.Bar(x=part["run_id"].to_list(), y=part["rows_landed"].to_list(), marker_color="#2a78d6",
                           hovertemplate="%{x}<br>%{y:,} rows<extra></extra>"))
    fig.update_layout(title=load, height=240, margin=dict(l=0, r=0, t=40, b=0), xaxis=dict(type="category"))
    col.plotly_chart(fig, width="stretch")

# --- Run strip ----------------------------------------------------------------
st.header("Last 30 runs")
strip = frame("run_strip")
label = {"green": "published, clean", "yellow": "published with warnings", "red": "blocked, failed or errored"}
fig = go.Figure(go.Scatter(
    x=list(range(strip.height)), y=[0] * strip.height, mode="markers", text=strip["run_id"].to_list(),
    marker=dict(symbol="square", size=22, color=[RUN_COLOR[c] for c in strip["color"]]),
    customdata=[[s, label[c], w, e + b] for s, c, w, e, b in
                strip.select("status", "color", "checks_warn", "checks_error", "checks_broken").iter_rows()],
    hovertemplate="%{text}<br>%{customdata[0]}: %{customdata[1]}<br>warn %{customdata[2]}, fail %{customdata[3]}<extra></extra>"))
fig.update_layout(height=80, width=30 * 34, margin=dict(l=0, r=0, t=0, b=0),
                  xaxis=dict(range=[-0.5, 29.5], showticklabels=False, showgrid=False, zeroline=False),
                  yaxis=dict(visible=False))
st.plotly_chart(fig)
st.caption("Oldest on the left. " + "  ·  ".join(f"{n} {label[c]}" for c, n in strip.group_by("color").len().sort("color").iter_rows()))

# --- Spot checks --------------------------------------------------------------
st.header("Spot checks")
st.caption(f"{board.SPOT_CHECKS_PER_SOURCE} served observations per source, the same five for this run (seeded by "
           f"{h.published_run}). Yahoo: compare with Close (split-adjusted), not Adj Close. FRED: ALFRED as of the vintage.")
st.dataframe(frame("spot_checks"), hide_index=True, width="stretch",
             column_config={"link": st.column_config.LinkColumn("source page", display_text="open"),
                            **{c: st.column_config.DateColumn(format="YYYY-MM-DD")
                               for c in ("obs_date", "vintage", "available_date")}})

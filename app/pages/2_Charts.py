"""Charts page (design.md section 8, D47): overlay any served series, dated by
when each value became public, rebased to 100 at the range start by default.
The chart's state lives in the URL, so a bookmark reopens the same chart."""

import math
from datetime import date, timedelta

import plotly.graph_objects as go
import polars as pl
import streamlit as st

from common import query, status_bar

# categorical slots in fixed order (dataviz reference palette); a series keeps its slot while selected
SLOTS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
RANGES = ["1Y", "5Y", "YTD", "Max", "Custom"]
DEFAULT = ["yf:close:NVDA", "fred:GDP", "fred:CPIAUCSL"]



def years_back(day: date, n: int) -> date:
    return day.replace(year=day.year - n, day=28 if (day.month, day.day) == (2, 29) else day.day)


st.set_page_config(page_title="Pharos: Charts", layout="wide")
status_bar()
st.title("Charts")

catalog = query("series_list")
if catalog is None:
    st.info("Nothing has been published yet, so there is nothing to chart.")
    st.stop()
names = dict(zip(catalog["series_id"], catalog["name"]))
frequency = dict(zip(catalog["series_id"], catalog["frequency"]))
units = dict(zip(catalog["series_id"], catalog["units"]))

# --- State from the URL ---------------------------------------------------------
# s: series by color slot, an empty entry for a free slot, so a reload keeps colors
qp = st.query_params
if "slots" not in st.session_state:
    from_url = qp["s"].split(",") if "s" in qp else DEFAULT
    st.session_state.slots = [s if s in names else "" for s in from_url][:len(SLOTS)]
    st.session_state.picked = [s for s in st.session_state.slots if s]
    st.session_state.range = qp.get("r", "1Y") if qp.get("r") in RANGES else "1Y"
    st.session_state.rebase = qp.get("rebase", "1") == "1"
    st.session_state.log = qp.get("log", "0") == "1"
    st.session_state.custom = (date.fromisoformat(qp["from"]), date.fromisoformat(qp["to"])) \
        if "from" in qp and "to" in qp else None

# --- Controls -------------------------------------------------------------------
picked = st.multiselect("Series", list(names), key="picked", max_selections=len(SLOTS),
                        format_func=lambda s: f"{s}  ({names[s]})")
slots = [s if s in picked else "" for s in st.session_state.slots]
for s in picked:
    if s in slots:
        continue
    if "" in slots:
        slots[slots.index("")] = s
    else:
        slots.append(s)
st.session_state.slots = slots
color = {s: SLOTS[i] for i, s in enumerate(slots) if s}

left, mid, right = st.columns([3, 1, 1])
span = left.segmented_control("Range", RANGES, key="range") or "1Y"
rebase = mid.toggle("Rebase to 100", key="rebase")
log = right.toggle("Log scale", key="log")

if not picked:
    st.info("Pick one or more series.")
    st.stop()

end = date.today()
points = query("chart_points", picked)
first = points["plot_date"].min()
if span == "Custom":
    chosen = st.date_input("Dates", value=st.session_state.custom or (end - timedelta(days=365), end),
                           min_value=first, max_value=end)
    start, end = chosen if isinstance(chosen, tuple) and len(chosen) == 2 else (end - timedelta(days=365), end)
    st.session_state.custom = (start, end)
else:
    start = {"1Y": years_back(end, 1), "5Y": years_back(end, 5),
             "YTD": date(end.year, 1, 1), "Max": first}[span]
    start = max(start, first)

# --- State to the URL -----------------------------------------------------------
state = {"s": ",".join(slots).rstrip(","), "r": span, "rebase": "1" if rebase else "0", "log": "1" if log else "0"}
if span == "Custom":
    state |= {"from": start.isoformat(), "to": end.isoformat()}
if dict(qp) != state:
    qp.from_dict(state)

# --- Chart ----------------------------------------------------------------------
data = query("chart", picked, start, end, rebase)
fig = go.Figure()
for s in picked:
    part = data.filter(pl.col("series_id") == s)
    released = [("release date not recorded; estimated" if e else "carried: the value in effect on this date" if c else "released this date")
                for e, c in part.select("estimated", "carried").iter_rows()]
    fig.add_trace(go.Scatter(
        x=part["plot_date"].to_list(), y=part["shown"].to_list(), name=s, mode="lines",
        line=dict(color=color[s], width=2, shape="linear" if frequency[s] == "D" else "hv"),
        customdata=list(zip(part["obs_date"].to_list(), part["value"].to_list(), released)),
        hovertemplate=(f"{s}<br>%{{x|%Y-%m-%d}}: %{{y:,.2f}}<br>value %{{customdata[1]:,.2f}} {units[s]}"
                       "<br>period %{customdata[0]|%Y-%m-%d}, %{customdata[2]}<extra></extra>")))
    if len(picked) <= 4 and part.height:      # a log axis places annotations in log10 units
        fig.add_annotation(x=part["plot_date"][-1], y=math.log10(part["shown"][-1]) if log else part["shown"][-1], text=s.split(":")[-1],
                           showarrow=False, xanchor="left", xshift=6)
axis = f"rebased, 100 at {start:%Y-%m-%d}" if rebase else \
    (units[picked[0]] if len({units[s] for s in picked}) == 1 else "mixed units")
fig.update_layout(height=520, margin=dict(l=0, r=60, t=10, b=0), hovermode="closest",
                  legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
                  yaxis=dict(title=axis, type="log" if log else "linear"), xaxis=dict(range=[start, end]))
st.plotly_chart(fig, width="stretch")
st.caption("Each value is drawn at the date it became public: a close at its trading day, a FRED value at its "
           "first release, revised to the latest vintage. Before ALFRED's records (GDP before 1991, CPI before "
           "1972) the release date is estimated as period end plus the usual lag (D47).")

with st.expander("Data"):
    st.dataframe(data, hide_index=True, width="stretch",
                 column_config={c: st.column_config.DateColumn(format="YYYY-MM-DD")
                                for c in ("obs_date", "period_end", "plot_date")})

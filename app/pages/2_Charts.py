"""Charts page (design.md section 8, D47, D48): a stack of panels sharing one
date axis. Each panel holds any served series, rebased to 100 at the range
start by default, each value dated by when it became public. A series keeps
its color in every panel. The whole layout lives in the URL, so a bookmark
reopens the same charts."""

import math
from datetime import date, timedelta

import plotly.graph_objects as go
import polars as pl
import streamlit as st
from plotly.subplots import make_subplots

from common import query, status_bar

# categorical slots in fixed order (dataviz reference palette); a series keeps its slot while shown
SLOTS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
RANGES = ["1Y", "5Y", "YTD", "Max", "Custom"]
MAX_PANELS = 4
DEFAULT = ["r~yf:close:NVDA", "r~fred:GDP,fred:CPIAUCSL"]


def years_back(day: date, n: int) -> date:
    return day.replace(year=day.year - n, day=28 if (day.month, day.day) == (2, 29) else day.day)


def parse_panel(text: str, known) -> dict:
    """URL form of a panel: flags, '~', series ids by comma. Flags: r = rebase, l = log."""
    flags, _, ids = text.partition("~")
    return {"series": [s for s in ids.split(",") if s in known], "rebase": "r" in flags, "log": "l" in flags}


def label_shifts(ends: list[float], low: float, high: float, log: bool,
                 height: float = 300, gap: float = 14) -> list[float]:
    """Upward pixel shifts that keep end labels at least `gap` px apart, scaled
    by the panel's data range (an approximation of plotly's autorange)."""
    scale = math.log10 if log else (lambda v: v)
    ys = [scale(y) for y in ends]
    span = (scale(high) - scale(low)) or 1
    order = sorted(range(len(ys)), key=lambda i: ys[i])
    shifts, last = [0.0] * len(ys), None
    for i in order:
        px = ys[i] / span * height
        if last is not None and px < last + gap:
            shifts[i], px = last + gap - px, last + gap
        last = px
    return shifts


def add_panel(series=(), rebase=True, log=False):
    pid = st.session_state.next_panel
    st.session_state.next_panel += 1
    st.session_state.panels.append(pid)
    st.session_state[f"series_{pid}"] = list(series)
    st.session_state[f"rebase_{pid}"] = rebase
    st.session_state[f"log_{pid}"] = log


def remove_panel(pid):
    st.session_state.panels.remove(pid)


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
# p: one per panel, e.g. "r~yf:close:NVDA,yf:close:AAPL"
qp = st.query_params
if "panels" not in st.session_state:
    st.session_state.panels, st.session_state.next_panel = [], 0
    for text in (qp.get_all("p") or DEFAULT)[:MAX_PANELS]:
        add_panel(**parse_panel(text, names))
    shown = [s for pid in st.session_state.panels for s in st.session_state[f"series_{pid}"]]
    st.session_state.slots = [s if s in shown else "" for s in qp["s"].split(",")][:len(SLOTS)] \
        if "s" in qp else []
    st.session_state.range = qp.get("r", "1Y") if qp.get("r") in RANGES else "1Y"
    st.session_state.custom = (date.fromisoformat(qp["from"]), date.fromisoformat(qp["to"])) \
        if "from" in qp and "to" in qp else None

# --- Controls -------------------------------------------------------------------
span = st.segmented_control("Range", RANGES, key="range") or "1Y"
end = date.today()
if span == "Custom":
    chosen = st.date_input("Dates", value=st.session_state.custom or (end - timedelta(days=365), end),
                           max_value=end)
    if isinstance(chosen, tuple) and len(chosen) == 2:
        st.session_state.custom = chosen
    start, end = st.session_state.custom or (end - timedelta(days=365), end)

panels = list(st.session_state.panels)
for n, pid in enumerate(panels, 1):
    with st.container(border=True):
        st.multiselect(f"Panel {n}", list(names), key=f"series_{pid}", max_selections=len(SLOTS),
                       format_func=lambda s: f"{s}  ({names[s]})")
        rb, lg, rm = st.columns(3, vertical_alignment="center")
        rb.toggle("Rebase", key=f"rebase_{pid}")
        lg.toggle("Log scale", key=f"log_{pid}")
        if len(panels) > 1:
            rm.button("Remove", key=f"remove_{pid}", on_click=remove_panel, args=(pid,))
if len(panels) < MAX_PANELS:
    st.button("Add panel", on_click=add_panel)

# --- Colors: one slot per series, kept while it is shown anywhere -------------------
shown = list(dict.fromkeys(s for pid in panels for s in st.session_state[f"series_{pid}"]))
if len(shown) > len(SLOTS):
    st.error(f"{len(shown)} series across panels; {len(SLOTS)} at most, one color each. Remove some to draw.")
    st.stop()
slots = [s if s in shown else "" for s in st.session_state.slots]
for s in shown:
    if s in slots:
        continue
    if "" in slots:
        slots[slots.index("")] = s
    else:
        slots.append(s)
st.session_state.slots = slots
color = {s: SLOTS[i] for i, s in enumerate(slots) if s}

filled = [pid for pid in panels if st.session_state[f"series_{pid}"]]
if not filled:
    st.info("Pick one or more series.")
    st.stop()

first = query("chart_points", shown)["plot_date"].min()
if span != "Custom":
    start = max({"1Y": years_back(end, 1), "5Y": years_back(end, 5),
                 "YTD": date(end.year, 1, 1), "Max": first}[span], first)

# --- State to the URL -----------------------------------------------------------
state = {"s": ",".join(slots).rstrip(","), "r": span,
         "p": [("r" if st.session_state[f"rebase_{pid}"] else "") + ("l" if st.session_state[f"log_{pid}"] else "")
               + "~" + ",".join(st.session_state[f"series_{pid}"]) for pid in panels]}
if span == "Custom":
    state |= {"from": start.isoformat(), "to": end.isoformat()}
if {k: qp.get_all(k) if k == "p" else qp.get(k) for k in qp} != state:
    qp.from_dict(state)

# --- Chart: one row per non-empty panel, one shared date axis ------------------------
fig = make_subplots(rows=len(filled), cols=1, shared_xaxes=True, vertical_spacing=0.06)
tables, legend = [], set()
for row, pid in enumerate(filled, 1):
    picked, rebase, log = (st.session_state[f"{k}_{pid}"] for k in ("series", "rebase", "log"))
    data = query("chart", picked, start, end, rebase)
    tables.append(data.with_columns(pl.lit(panels.index(pid) + 1).alias("panel")))
    ends = {s: data.filter(pl.col("series_id") == s)["shown"][-1] for s in picked
            if data.filter(pl.col("series_id") == s).height}
    shift = dict(zip(ends, label_shifts(list(ends.values()), data["shown"].min(), data["shown"].max(), log)))         if ends else {}
    for s in picked:
        part = data.filter(pl.col("series_id") == s)
        released = [("release date not recorded; estimated" if e else
                     "carried: the value in effect on this date" if c else "released this date")
                    for e, c in part.select("estimated", "carried").iter_rows()]
        fig.add_trace(go.Scatter(
            x=part["plot_date"].to_list(), y=part["shown"].to_list(), name=s, mode="lines",
            legendgroup=s, showlegend=s not in legend,
            line=dict(color=color[s], width=2, shape="linear" if frequency[s] == "D" else "hv"),
            customdata=list(zip(part["obs_date"].to_list(), part["value"].to_list(), released)),
            hovertemplate=(f"{s}<br>%{{x|%Y-%m-%d}}: %{{y:,.2f}}<br>value %{{customdata[1]:,.2f}} {units[s]}"
                           "<br>period %{customdata[0]|%Y-%m-%d}, %{customdata[2]}<extra></extra>")),
            row=row, col=1)
        legend.add(s)
        if len(picked) <= 4 and part.height:      # a log axis places annotations in log10 units
            y = part["shown"][-1]
            fig.add_annotation(x=part["plot_date"][-1], y=math.log10(y) if log else y, text=s.split(":")[-1],
                               showarrow=False, xanchor="left", xshift=6, yshift=shift[s], row=row, col=1)
    axis = f"rebased, 100 at {start:%Y-%m-%d}" if rebase else \
        (units[picked[0]] if len({units[s] for s in picked}) == 1 else "mixed units")
    fig.update_yaxes(title_text=axis, type="log" if log else "linear", row=row, col=1)
fig.update_xaxes(range=[start, end])
fig.update_layout(height=60 + 340 * len(filled), margin=dict(l=0, r=70, t=10, b=0), hovermode="closest",
                  legend=dict(orientation="h", yanchor="bottom", y=1.01, x=0))
st.plotly_chart(fig, width="stretch")
st.caption("Each value is drawn at the date it became public: a close at its trading day, a FRED value at its "
           "first release, revised to the latest vintage. Before ALFRED's records (GDP before 1991, CPI before "
           "1972) the release date is estimated as period end plus the usual lag (D47).")

with st.expander("Data"):
    st.dataframe(pl.concat(tables), hide_index=True, width="stretch",
                 column_config={c: st.column_config.DateColumn(format="YYYY-MM-DD")
                                for c in ("obs_date", "period_end", "plot_date")})

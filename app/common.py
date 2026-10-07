"""Shared by every page: cached reads and the health status bar.

Caching (design.md section 8, D43):
- Data is keyed on last_published_at, so a publish shows new data on the next
  render and nothing else re-queries.
- Health is keyed on (last_published_at, wall-clock minute): freshness is
  judged at view time, at most a minute late, and evaluated with record=False
  so the app never writes health/.
"""

from datetime import datetime, timezone

import streamlit as st

from pharos import board, health

STATUS_ICON = {"green": ":material/check_circle:", "yellow": ":material/warning:", "red": ":material/error:"}


@st.cache_data(show_spinner=False)
def _health(published_at, minute) -> health.Health:
    return health.evaluate(record=False)


def current_health() -> health.Health:
    minute = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    return _health(board.last_published_at(), minute)


@st.cache_resource(show_spinner=False)
def _connection(published_at):
    return board.connect()


@st.cache_data(show_spinner=False)
def _frame(name: str, published_at):
    con = _connection(published_at)
    return None if con is None else getattr(board, name)(con.cursor())


def frame(name: str):
    """board.<name>() over what is served, or None before the first publish."""
    return _frame(name, board.last_published_at())


def status_bar() -> health.Health:
    h = current_health()
    served = (f"serving {h.published_run}, published {h.published_at.astimezone():%Y-%m-%d %H:%M}"
              if h.published_at else "nothing published")
    text = f"**Health {h.status.upper()}**  ·  {served}  ·  checked {h.evaluated_at.astimezone():%H:%M}"
    if h.reasons:
        text += "  \n" + "  \n".join(h.reasons)
    show = {"green": st.success, "yellow": st.warning, "red": st.error}[h.status]
    show(text, icon=STATUS_ICON[h.status])
    return h

"""Home. Step 9 puts the watchlist chart here; until then, the status bar."""

import streamlit as st

from common import status_bar

st.set_page_config(page_title="Pharos", layout="wide")
status_bar()
st.title("Pharos")
st.page_link("pages/1_Health.py", label="Health")

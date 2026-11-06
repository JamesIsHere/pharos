"""Test 1: domestic 10-K filers per filing year vs real GDP. One-off, reads catalog.duckdb read-only."""
import duckdb
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from pharos.paths import PROJECT_ROOT, data_root

ROOT = PROJECT_ROOT.as_posix()
CATALOG = (data_root() / "catalog.duckdb").as_posix()
con = duckdb.connect(CATALOG, read_only=True)

counts = con.sql(f"""
    with forms as (
        select code from read_csv('{ROOT}/config/annual_forms.csv') where scope = 'domestic'
    )
    select year(date_filed) as yr, count(distinct cik) as filers, count(*) as filings
    from serving_filings
    where form in (select code from forms)
    group by 1 order by 1
""").pl()

gdp = con.sql("""
    with latest as (
        select obs_date, value,
               row_number() over (partition by obs_date order by vintage desc) as rn
        from serving_observations where series_id = 'fred:GDPC1'
    )
    select year(obs_date) as yr, avg(value) as rgdp, count(*) as quarters
    from latest where rn = 1 and year(obs_date) between 1993 and 2026
    group by 1 order by 1
""").pl()

print(counts)
print(gdp)

full = counts.filter((counts["yr"] >= 1997) & (counts["yr"] <= 2025))
part = counts.filter((counts["yr"] < 1997) | (counts["yr"] == 2026))

fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.08,
                    subplot_titles=("Companies filing a domestic annual report (10-K family), by filing year",
                                    "Real GDP, billions of chained 2017 dollars (annual average)"))
fig.add_trace(go.Scatter(x=full["yr"], y=full["filers"], mode="lines+markers", name="Filers",
                         line=dict(color="#2563eb")), row=1, col=1)
fig.add_trace(go.Scatter(x=part["yr"], y=part["filers"], mode="markers", name="Incomplete year",
                         marker=dict(color="#9ca3af", size=8)), row=1, col=1)
fig.add_trace(go.Scatter(x=gdp["yr"], y=gdp["rgdp"], mode="lines+markers", name="Real GDP",
                         line=dict(color="#059669")), row=2, col=1)
fig.update_layout(height=760, showlegend=False, template="plotly_white",
                  title="Test 1: number of public filers vs the size of the economy, 1993-2026",
                  margin=dict(l=70, r=30, t=90, b=60))
fig.update_yaxes(rangemode="tozero", row=1, col=1)
fig.add_annotation(text="Grey points: 1993-96 (EDGAR filing phased in) and 2026 (year to date).",
                   xref="paper", yref="paper", x=0, y=-0.09, showarrow=False, font=dict(size=12, color="#6b7280"))
fig.write_html(f"{ROOT}/explore/test1/test1.html", include_plotlyjs=True)
print("wrote", f"{ROOT}/explore/test1/test1.html")

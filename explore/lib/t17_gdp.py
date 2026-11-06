"""Test 17: GDP over time, from Pharos's FRED/ALFRED vintages. Left: nominal and real GDP as published today.
Right: real GDP growth each quarter as first published and as published today (the vintage dimension)."""
import duckdb
import plotly.graph_objects as go
from plotly.subplots import make_subplots

import base

con = duckdb.connect(base.CATALOG, read_only=True)

# Today's value per quarter: the newest vintage of each (series, quarter); a null there means not published today.
levels = con.sql("""
    select series_id, obs_date, value from serving_observations
    where series_id in ('fred:GDP', 'fred:GDPC1')
    qualify row_number() over (partition by series_id, obs_date order by vintage desc) = 1
""").pl().drop_nulls("value").sort("obs_date")
nom = levels.filter(levels["series_id"] == "fred:GDP")
real = levels.filter(levels["series_id"] == "fred:GDPC1")

# Real growth, quarter on quarter at an annual rate, computed inside one vintage so base-year changes cancel.
# First print: the first vintage holding the quarter, with the prior quarter as known in that same vintage.
# A true first print comes out within about 30 days of quarter end (200 days from quarter start allows for delays);
# a quarter whose first vintage is later entered the series by a historical revision, not a release.
growth = con.sql("""
    with o as (select obs_date, value, vintage from serving_observations where series_id = 'fred:GDPC1'),
    first_print as (
        select obs_date, value, vintage from o where value is not null
        qualify row_number() over (partition by obs_date order by vintage) = 1
    ),
    fp as (
        select f.obs_date, f.vintage,
               (f.value / (select p.value from o p
                           where p.obs_date = f.obs_date - interval 3 month and p.vintage <= f.vintage
                           order by p.vintage desc limit 1)) ** 4 - 1 as first_growth
        from first_print f
        where f.vintage <= f.obs_date + interval 200 day   -- 2025 Q3 came 175 days in (shutdown delay)
          and f.vintage > (select min(vintage) from o)        -- ALFRED's first vintage is not a first print
    ),
    latest as (
        select obs_date, value from o
        qualify row_number() over (partition by obs_date order by vintage desc) = 1
    ),
    lg as (select obs_date, (value / lag(value) over (order by obs_date)) ** 4 - 1 as latest_growth from latest)
    select fp.obs_date, fp.vintage as first_vintage, fp.first_growth, lg.latest_growth
    from fp join lg using (obs_date) order by obs_date
""").pl()
rev = (growth["latest_growth"] - growth["first_growth"]).abs()
print(growth.tail(8))
print("quarters with a first print:", growth.height, growth["obs_date"].min(), growth["obs_date"].max())
print("mean absolute revision, pts:", round(rev.mean() * 100, 2), " median:", round(rev.median() * 100, 2),
      " max:", round(rev.max() * 100, 2), growth["obs_date"][rev.arg_max()])
print("sign flips:", ((growth["first_growth"] > 0) != (growth["latest_growth"] > 0)).sum())
print("nominal", nom["obs_date"][0], nom["value"][0], nom["obs_date"][-1], nom["value"][-1])
print("real", real["obs_date"][0], real["value"][0], real["obs_date"][-1], real["value"][-1])

fig = make_subplots(rows=1, cols=2, column_widths=[0.45, 0.55],
                    subplot_titles=("GDP today, $bn, annual rate (log)", "Real growth, annual rate: first print vs today"))
fig.add_trace(go.Scatter(x=nom["obs_date"], y=nom["value"], name="Nominal GDP", line=dict(color="#d97706")), row=1, col=1)
fig.add_trace(go.Scatter(x=real["obs_date"], y=real["value"], name="Real GDP (chained 2017 $)",
                         line=dict(color="#2563eb")), row=1, col=1)
fig.add_trace(go.Scatter(x=growth["obs_date"], y=growth["first_growth"], name="Growth, first published",
                         line=dict(color="#9ca3af", width=1.5)), row=1, col=2)
fig.add_trace(go.Scatter(x=growth["obs_date"], y=growth["latest_growth"], name="Growth, as published today",
                         line=dict(color="#1e3a8a", width=1.5)), row=1, col=2)
fig.update_yaxes(type="log", tickvals=[200, 500, 1000, 2000, 5000, 10000, 20000, 30000], row=1, col=1)
fig.update_yaxes(tickformat=".0%", range=[-0.12, 0.12], row=1, col=2)
fig.update_layout(height=480, template="plotly_white", title="GDP over time",
                  legend=dict(orientation="h", y=-0.12), margin=dict(l=60, r=20, t=80, b=90))

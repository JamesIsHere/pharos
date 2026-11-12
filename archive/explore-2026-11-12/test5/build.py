"""Test 5: days from fiscal year-end to 10-K filing, by year, overall and by current filer category. One-off, read-only."""
import duckdb
import plotly.graph_objects as go
import polars as pl

from pharos.paths import PROJECT_ROOT, data_root

ROOT = PROJECT_ROOT.as_posix()
CATALOG = (data_root() / "catalog.duckdb").as_posix()
con = duckdb.connect(CATALOG, read_only=True)

con.sql("""
    create temp table k as
    -- one row per original 10-K: its fiscal year-end is the latest end date of a one-year us-gaap period in it
    select cik, accession, min(filed) as filed, max(end_date) as fy_end
    from serving_company_facts
    where form = '10-K' and taxonomy = 'us-gaap'
      and date_diff('day', start_date, end_date) between 350 and 380
      and end_date <= filed                                  -- drops mistyped future end dates
    group by cik, accession
""")

print(con.sql("""select count(*) as tenks, count(*) filter (where date_diff('day', fy_end, filed) > 400) as over_400_days
                 from k""").pl())

lag = con.sql("""
    select year(fy_end) as yr, s.category, date_diff('day', fy_end, filed) as days
    from k left join serving_submission_details s using (cik)
    where date_diff('day', fy_end, filed) between 1 and 400 and year(fy_end) between 2009 and 2025
""").pl()

def summarize(df, by):
    return (df.group_by(by).agg(pl.len().alias("tenks"), pl.col("days").median().alias("median_days"),
                                pl.col("days").quantile(0.1).alias("p10"), pl.col("days").quantile(0.9).alias("p90"),
                                (pl.col("days") > 90).mean().alias("share_over_90"))
            .sort(by))

yearly = summarize(lag, "yr")
raw_cats = summarize(lag.with_columns(pl.col("category").fill_null("(none)")), ["category"]).sort("tenks", descending=True)
with pl.Config(tbl_rows=60, tbl_width_chars=180, fmt_str_lengths=60):
    print(raw_cats)
# SEC's category field joins several flags with <br>; reduce to the filer tier that sets the deadline
cat = pl.col("category").fill_null("").str.to_lowercase()
lag = lag.with_columns(
    pl.when(cat.str.contains("large accelerated")).then(pl.lit("Large accelerated (60-day deadline)"))
      .when(cat.str.contains("accelerated") & ~cat.str.contains("non-accelerated"))
      .then(pl.lit("Accelerated (75-day deadline)"))
      .otherwise(pl.lit("Non-accelerated or smaller (90-day deadline)")).alias("category"))
cats = summarize(lag, ["category"]).sort("tenks", descending=True)
by_cat_year = summarize(lag, ["category", "yr"])

with pl.Config(tbl_rows=60, tbl_width_chars=180, fmt_str_lengths=60):
    print(yearly)
    print(cats)

fig = go.Figure()
fig.add_trace(go.Scatter(x=yearly["yr"], y=yearly["p90"], mode="lines", line=dict(width=0), showlegend=False))
fig.add_trace(go.Scatter(x=yearly["yr"], y=yearly["p10"], mode="lines", line=dict(width=0), fill="tonexty",
                         fillcolor="rgba(37,99,235,0.15)", name="Middle 80% of all 10-Ks"))
fig.add_trace(go.Scatter(x=yearly["yr"], y=yearly["median_days"], mode="lines+markers", name="Median, all 10-Ks",
                         line=dict(color="#2563eb", width=3)))
palette = ["#dc2626", "#d97706", "#059669", "#7c3aed", "#6b7280"]
for color, c in zip(palette, cats.filter(pl.col("tenks") >= 2000)["category"].to_list()):
    d = by_cat_year.filter(pl.col("category") == c)
    fig.add_trace(go.Scatter(x=d["yr"], y=d["median_days"], mode="lines", name=f"Median, {c}",
                             line=dict(color=color, dash="dot")))
for days in (60, 75, 90):
    fig.add_hline(y=days, line=dict(color="#9ca3af", width=1, dash="dash"),
                  annotation_text=f"{days} days", annotation_position="right")
fig.update_layout(height=600, template="plotly_white", yaxis_title="Days from fiscal year-end to filing",
                  xaxis_title="Fiscal year ending in",
                  title="Test 5: how long companies take to file their 10-K",
                  legend=dict(orientation="h", y=-0.18), margin=dict(l=70, r=80, t=70, b=120))
fig.write_html(f"{ROOT}/explore/test5/test5.html", include_plotlyjs=True)
print("wrote", f"{ROOT}/explore/test5/test5.html")

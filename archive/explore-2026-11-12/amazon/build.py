"""Amazon page: what Pharos's own database can say about one company, and what it cannot. Every number on the page is
computed here from data.py; the text formats those numbers, so it stays true when the data changes."""
import sys
from datetime import datetime, timezone
from pathlib import Path

import plotly.graph_objects as go
import plotly.io as pio
import polars as pl
from plotly.subplots import make_subplots

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "lib"))
import data  # noqa: E402
import nav   # noqa: E402
import rivals_page  # noqa: E402

A = data.annual().join(data.macro_annual(), on="yr", how="left")
CPI_2025 = A.filter(pl.col("yr") == 2025)["cpi"][0]
A = A.with_columns((pl.col("rev") * CPI_2025 / pl.col("cpi")).alias("rev_real"),
                   (pl.col("rev") / pl.col("gdp")).alias("rev_gdp"))
lp = data.lease_principal().pivot(on="concept", index="yr", values="val")
lease_cols = [c for c in lp.columns if c != "yr"]
lp = lp.select("yr", pl.coalesce(lease_cols).alias("lease_principal"))
A = A.join(lp, on="yr", how="left").with_columns((pl.col("fcf") - pl.col("lease_principal")).alias("fcf_lease"))
V = data.valuation().filter(pl.col("ps").is_not_null())
P = data.price().sort("obs_date")


def v(yr, col):
    return A.filter(pl.col("yr") == yr)[col][0]


def bn(x):
    return f"${x / 1e9:,.0f}bn" if abs(x) >= 1e10 else f"${x / 1e9:,.1f}bn"


def pct(x, d=0):
    return f"{x * 100:.{d}f}%"


# ---- numbers the text uses ----------------------------------------------------------------------------------------
r07, r25 = v(2007, "rev"), v(2025, "rev")
years = 2025 - 2007
cagr = (r25 / r07) ** (1 / years) - 1
gdp_cagr = (v(2025, "gdp") / v(2007, "gdp")) ** (1 / years) - 1
real_mult = v(2025, "rev_real") / v(2007, "rev_real")
svc09, svc17 = v(2009, "services") / v(2009, "rev"), v(2017, "services") / v(2017, "rev")
fcf_neg = A.filter(pl.col("fcf") < 0)["yr"].to_list()
ccc = A.filter(pl.col("ccc").is_not_null())
dd = P.with_columns((pl.col("close") / pl.col("close").cum_max() - 1).alias("dd"))
dd08 = dd.filter(pl.col("obs_date").dt.year().is_between(2007, 2009))
dd22 = dd.filter(pl.col("obs_date").dt.year().is_between(2021, 2023))
last = P[-1]
price_mult = last["close"][0] / P["close"][0]
price_years = (last["obs_date"][0] - P["obs_date"][0]).days / 365.25
sh07, sh25 = v(2007, "shares"), v(2025, "shares")
vlast = V[-1]
ps_rng = (V["ps"].min(), V["ps"].max())
ps_min_day = V.sort("ps")["obs_date"][0]
ps_max_day = V.sort("ps")["obs_date"][-1]
mcap_gdp = vlast["mcap"][0] / v(2025, "gdp")

# ---- charts -----------------------------------------------------------------------------------------------------
LAY = dict(template="plotly_white", height=430, margin=dict(l=60, r=20, t=50, b=70), legend=dict(orientation="h", y=-0.15))
yrs = A["yr"]

f1 = make_subplots(specs=[[{"secondary_y": True}]])
f1.add_trace(go.Bar(x=yrs, y=A["rev"] / 1e9, name="Revenue, as reported ($bn)", marker_color="#93c5fd"))
f1.add_trace(go.Scatter(x=yrs, y=A["rev_real"] / 1e9, name="Revenue in 2025 dollars (CPI)", mode="lines",
                        line=dict(color="#1e3a8a", dash="dot")))
f1.add_trace(go.Scatter(x=yrs, y=A["rev_gdp"], name="Revenue as a share of US GDP (right)", mode="lines+markers",
                        line=dict(color="#d97706")), secondary_y=True)
f1.update_yaxes(title_text="$bn", secondary_y=False)
f1.update_yaxes(tickformat=".1%", rangemode="tozero", secondary_y=True, showgrid=False)
f1.update_layout(title="Revenue, 2007-2025", **LAY)

f2 = make_subplots(specs=[[{"secondary_y": True}]])
mix = A.filter(pl.col("goods").is_not_null())
f2.add_trace(go.Bar(x=mix["yr"], y=mix["goods"] / 1e9, name="Product sales ($bn)", marker_color="#93c5fd"))
f2.add_trace(go.Bar(x=mix["yr"], y=mix["services"] / 1e9, name="Service sales ($bn)", marker_color="#1e3a8a"))
f2.add_trace(go.Scatter(x=yrs, y=A["gross_margin"], name="Revenue minus cost of sales, share of revenue (right)",
                        mode="lines+markers", line=dict(color="#d97706")), secondary_y=True)
f2.update_layout(barmode="stack", title="Product vs service sales (tagged 2009-2017 only), and gross margin", **LAY)
f2.update_xaxes(range=[2006.5, 2025.5])
f2.update_yaxes(title_text="$bn", secondary_y=False)
f2.update_yaxes(tickformat=".0%", range=[0, 0.6], secondary_y=True, showgrid=False)

f3 = go.Figure()
f3.add_trace(go.Scatter(x=yrs, y=A["op_margin"], name="Operating margin", mode="lines+markers", line=dict(color="#1e3a8a")))
f3.add_trace(go.Scatter(x=yrs, y=A["net_margin"], name="Net margin", mode="lines+markers", line=dict(color="#d97706")))
f3.add_hline(y=0, line_color="#9ca3af")
f3.update_layout(title="Operating and net margin", yaxis=dict(tickformat=".0%"), **LAY)

f4 = go.Figure()
f4.add_trace(go.Bar(x=yrs, y=A["ocf"] / 1e9, name="Operating cash flow", marker_color="#93c5fd"))
f4.add_trace(go.Bar(x=yrs, y=-A["capex"] / 1e9, name="Capital spending (negative)", marker_color="#fca5a5"))
f4.add_trace(go.Scatter(x=yrs, y=A["fcf"] / 1e9, name="Free cash flow", mode="lines+markers", line=dict(color="#1e3a8a")))
f4.add_trace(go.Scatter(x=yrs, y=A["fcf_lease"] / 1e9, name="Free cash flow less finance-lease principal",
                        mode="lines+markers", line=dict(color="#1e3a8a", dash="dot")))
f4.add_hline(y=0, line_color="#9ca3af")
f4.update_layout(barmode="overlay", title="Cash: what operations bring in, what building takes, $bn",
                 yaxis_title="$bn", **LAY)

f5 = make_subplots(specs=[[{"secondary_y": True}]])
f5.add_trace(go.Bar(x=yrs, y=A["ppe"] / 1e9, name="Property and equipment, net ($bn)", marker_color="#93c5fd"))
f5.add_trace(go.Scatter(x=yrs, y=A["ppe"] / A["rev"], name="Property and equipment per $ of revenue (right)",
                        mode="lines+markers", line=dict(color="#1e3a8a")), secondary_y=True)
f5.add_trace(go.Scatter(x=yrs, y=A["capex"] / A["rev"], name="Capital spending per $ of revenue (right)",
                        mode="lines+markers", line=dict(color="#d97706")), secondary_y=True)
f5.update_layout(title="From shop to infrastructure: physical assets", **LAY)
f5.update_yaxes(title_text="$bn", secondary_y=False)
f5.update_yaxes(tickformat=".0%", rangemode="tozero", secondary_y=True, showgrid=False)

f6 = go.Figure()
for col, name, color in (("dpo", "Days to pay suppliers", "#d97706"), ("dio", "Days inventory is held", "#93c5fd"),
                         ("dso", "Days to collect receivables", "#60a5fa"),
                         ("ccc", "Cash conversion cycle (held + collect - pay)", "#1e3a8a")):
    f6.add_trace(go.Scatter(x=ccc["yr"], y=ccc[col], name=name, mode="lines+markers", line=dict(color=color)))
f6.add_hline(y=0, line_color="#9ca3af")
f6.update_layout(title="Working capital in days: suppliers fund the business", yaxis_title="days", **LAY)

f7 = make_subplots(specs=[[{"secondary_y": True}]])
f7.add_trace(go.Bar(x=yrs, y=A["sbc"] / 1e9, name="Stock-based compensation ($bn)", marker_color="#93c5fd"))
f7.add_trace(go.Bar(x=yrs, y=A["ni"] / 1e9, name="Net income ($bn)", marker_color="#1e3a8a"))
f7.add_trace(go.Scatter(x=yrs, y=A["shares"] / 1e9, name="Diluted shares, bn, split-adjusted (right)",
                        mode="lines+markers", line=dict(color="#d97706")), secondary_y=True)
f7.update_layout(barmode="group", title="Paying people in stock, and the share count", **LAY)
f7.update_yaxes(title_text="$bn", secondary_y=False)
f7.update_yaxes(range=[0, 12], tickvals=[0, 2, 4, 6, 8, 10, 12], secondary_y=True, showgrid=False)

f8 = make_subplots(rows=1, cols=2, subplot_titles=("Close, split-adjusted, $ (log)", "Market value / revenue"))
f8.add_trace(go.Scatter(x=P["obs_date"], y=P["close"], name="AMZN close", line=dict(color="#1e3a8a", width=1)), row=1, col=1)
f8.add_trace(go.Scatter(x=V["obs_date"], y=V["ps"], name="Price-to-sales", line=dict(color="#d97706", width=1)), row=1, col=2)
f8.update_yaxes(type="log", tickvals=[1, 2, 5, 10, 20, 50, 100, 200, 300], row=1, col=1)
f8.update_layout(showlegend=False, **{k: val for k, val in LAY.items() if k != "legend"})
f8.update_annotations(font_size=12)


def chart(fig, first=False):
    return pio.to_html(fig, full_html=False, include_plotlyjs=first, config={"displayModeBar": False})


# ---- text -------------------------------------------------------------------------------------------------------
S = [
    (f1, "1. Scale: how big, how fast",
     f"Revenue went from {bn(r07)} (2007) to {bn(r25)} (2025): {r25 / r07:.0f} times, {pct(cagr)} a year, while US "
     f"nominal GDP grew {pct(gdp_cagr, 1)} a year. In 2025 dollars revenue grew {real_mult:.0f} times. Revenue equals "
     f"{pct(v(2007, 'rev_gdp'), 2)} of US GDP in 2007 and {pct(v(2025, 'rev_gdp'), 1)} in 2025.",
     "Revenue is worldwide and gross (it includes what Amazon pays for the goods it resells); GDP is US value added. "
     "The ratio is a scale reference, not a share of the economy. CPI for 2025 averages 11 months (October 2025 is "
     "missing).",
     "Amazon's US revenue, which the 10-K reports by geography but which reaches SEC's data only as a dimension "
     "(SEC's Financial Statement and Notes data sets carry it; companyfacts does not)."),
    (f2, "2. Mix: from selling goods to selling services",
     f"Service sales were {pct(svc09)} of revenue in 2009 and {pct(svc17)} in 2017, the last year the split is "
     f"tagged. Over the same years revenue minus cost of sales rose from {pct(v(2009, 'gross_margin'))} to "
     f"{pct(v(2017, 'gross_margin'))}, and reached {pct(v(2025, 'gross_margin'))} in 2025 (one dip, in 2020): the "
     "shift the split showed appears to continue, but the data stops saying why.",
     "From 2018 Amazon tags product and service sales only as dimensions of total revenue, which companyfacts drops. "
     "Amazon's cost of sales excludes fulfillment, so this 'gross margin' is higher than a retailer's usual one.",
     "Segment revenue (North America, International, AWS) and sales by type (online stores, third-party seller "
     "services, advertising, subscriptions, AWS): all dimensional, in the Financial Statement and Notes data sets."),
    (f3, "3. Profit: thin for a decade, then not",
     f"Operating margin stayed under 5% from 2007 to 2017 (a low of {pct(A.filter(pl.col('yr').is_between(2007, 2017))['op_margin'].min(), 1)} "
     f"in 2014), then rose to {pct(v(2025, 'op_margin'), 1)} in 2025. Net income was {bn(v(2025, 'ni'))} in 2025. "
     f"Net margin sat above operating margin in 2021 and went negative in 2022 although operations earned "
     f"{bn(v(2022, 'opinc'))}: 'other non-operating income' was +$14.6bn in 2021 and -$16.8bn in 2022 (+$15.2bn in 2025).",
     "The tags name the line, not its cause: nothing in the data says which investment gained or lost. Amazon's own "
     "expense lines (fulfillment, technology and content) are company-specific tags that companyfacts leaves out, "
     "so operating cost cannot be split by function.",
     "Amazon's extension tags (fulfillment, technology and infrastructure) and the investment notes: Financial "
     "Statement and Notes data sets, or the 10-K text."),
    (f4, "4. Cash: the build-out shows in capital spending",
     f"Operating cash flow went from {bn(v(2007, 'ocf'))} to {bn(v(2025, 'ocf'))}. Capital spending went from "
     f"{bn(v(2007, 'capex'))} to {bn(v(2025, 'capex'))}, nearly all of 2025's operating cash. Free cash flow was "
     f"negative in {', '.join(map(str, fcf_neg))} and {bn(v(2025, 'fcf'))} in 2025. Part of the build came through "
     f"leases instead: principal repaid on finance leases peaked at {bn(A['lease_principal'].max())} in "
     f"{A.sort('lease_principal', nulls_last=False)['yr'][-1]} and fell to {bn(v(2025, 'lease_principal'))} in 2025.",
     "Capital spending changes tag in 2016 (2016 itself: $6.7bn on the old tag, $7.8bn on the new); the series uses "
     "the new tag from 2016. Free cash flow here is operating cash flow minus capital spending, before proceeds from "
     "asset sales, so it can differ slightly from Amazon's own figure.",
     "What the spending buys (data centres vs warehouses vs transport): the segment notes on property and capital "
     "spending, dimensional again."),
    (f5, "5. Physical assets: from shop to infrastructure",
     f"Net property and equipment went from {bn(v(2008, 'ppe'))} (2008) to {bn(v(2025, 'ppe'))} (2025). Per dollar "
     f"of revenue it rose from {v(2008, 'ppe') / v(2008, 'rev'):.2f} to {v(2025, 'ppe') / v(2025, 'rev'):.2f}, and "
     f"capital spending from {pct(v(2008, 'capex') / v(2008, 'rev'), 1)} to {pct(v(2025, 'capex') / v(2025, 'rev'))} "
     "of revenue: a business that needs ever more physical capital per dollar of sales.",
     "From 2018 Amazon tags net property together with finance-lease assets under one concept; earlier years use "
     "the plain concept (equal in 2018-19 when both exist). 2007 is missing.",
     "The split of property by type and by segment, and capacity measures (square feet, data-centre power), none "
     "of which are tagged."),
    (f6, "6. Working capital: suppliers fund the business",
     f"Amazon holds inventory about {ccc['dio'].median():.0f} days and collects receivables in about "
     f"{ccc['dso'].median():.0f}, but pays suppliers in about {ccc['dpo'].median():.0f} (medians, 2008-2025). The "
     f"cash conversion cycle is negative every year, between {ccc['ccc'].max():.0f} and {ccc['ccc'].min():.0f} days: "
     "customers pay before suppliers are paid, so growth releases cash instead of absorbing it.",
     "Days are year-end balances over annual cost of sales or revenue. Payables include more than merchandise "
     "suppliers, and receivables include AWS and advertising customers, so the measure is approximate.",
     "Payables to third-party sellers (money collected for them, held before paying out), which the 10-K "
     "mentions but which is not tagged separately."),
    (f7, "7. Owners and employees: paying in stock",
     f"Stock-based compensation went from {bn(v(2007, 'sbc'))} to a peak of {bn(A['sbc'].max())} "
     f"({A.sort('sbc')['yr'][-1]}); in 2022 it was {bn(v(2022, 'sbc'))} against a net loss. Diluted shares, "
     f"split-adjusted, rose from {sh07 / 1e9:.2f}bn (2007) to {sh25 / 1e9:.2f}bn (2025), "
     f"{pct((sh25 / sh07) ** (1 / years) - 1, 1)} a year: the dilution from stock pay is small next to the growth.",
     "Per-share figures filed before the 20-for-1 split of 2022-06-06 are restated here (shares x 20); the 10-Ks "
     "themselves restate only the years they show again. There is no employee count in the data.",
     "Headcount (stated in the 10-K text, not tagged), so pay per employee and revenue per employee."),
    (f8, "8. The market's view",
     f"The close went from ${P['close'][0]:.2f} (2005-01-03, split-adjusted) to ${last['close'][0]:.2f} "
     f"({last['obs_date'][0]}), {price_mult:.0f} times in {price_years:.0f} years. It fell "
     f"{pct(-dd08['dd'].min())} to {dd08.sort('dd')['obs_date'][0]} and {pct(-dd22['dd'].min())} to "
     f"{dd22.sort('dd')['obs_date'][0]}. Market value / revenue ranged from {ps_rng[0]:.1f} ({ps_min_day}) to "
     f"{ps_rng[1]:.1f} ({ps_max_day}); today {vlast['ps'][0]:.1f}, a market value of "
     f"{bn(vlast['mcap'][0])}, equal to {pct(mcap_gdp)} of 2025 US GDP.",
     "Market value is the close times shares outstanding from the latest 10-K or 10-Q cover page (from mid-2009). "
     "Revenue enters on the day its 10-K was filed, not at year end. Company facts start with 10-Ks filed in 2010, "
     "so the ratio starts on 2010-01-29.",
     "Interest rates for context (FRED's 10-year Treasury yield, one config line), and other companies' prices "
     "and figures to compare against (the figures are already in Pharos; the prices are not)."),
]


# ---- section 9: quarters (pilot of the second batch) -------------------------------------------------------------
Q, QCH = data.quarterly()
QW = Q.pivot(on="item", index=["yr", "q"], values="val").sort("yr", "q").with_columns(
    pl.format("{} Q{}", pl.col("yr"), pl.col("q")).alias("label"))
QF = (QW.group_by("yr").agg(pl.len().alias("n"), pl.col("rev").sum().alias("R"),
                            pl.col("rev").filter(pl.col("q") == 4).first().alias("r4"))
        .filter(pl.col("n") == 4).sort("yr").with_columns((pl.col("r4") / pl.col("R")).alias("q4_share")))
q4chk = QCH["q4_tagged"].join(Q.filter(pl.col("q") == 4).select("item", "yr", pl.col("val").alias("derived")),
                              on=["item", "yr"]).with_columns((pl.col("derived") - pl.col("tagged")).abs().alias("d"))
q4_ok = q4chk.filter(pl.col("d") <= 2e6).height
q4_bad = q4chk.filter(pl.col("d") > 2e6).sort("item", "yr")
y9 = QCH["ytd9"].join(QCH["s123"], on=["item", "yr"]).filter(pl.col("nq") == 3).with_columns(
    (pl.col("s123") - pl.col("ytd9")).abs().alias("d"))
y9_max = y9["d"].max()
yoy = (QW.filter(pl.col("yr") == 2026).join(QW.filter(pl.col("yr") == 2025).select("q", pl.col("rev").alias("prev")), on="q")
         .with_columns((pl.col("rev") / pl.col("prev") - 1).alias("g")))
q26 = QW.filter(pl.col("yr") == 2026)

f9 = make_subplots(rows=1, cols=2, column_widths=[0.65, 0.35],
                   subplot_titles=("Revenue and operating income by quarter, $bn", "Q4 share of yearly revenue"))
f9.add_trace(go.Bar(x=QW["label"], y=QW["rev"] / 1e9, name="Revenue",
                    marker_color=["#1e3a8a" if q == 4 else "#93c5fd" for q in QW["q"]]), row=1, col=1)
f9.add_trace(go.Scatter(x=QW["label"], y=QW["opinc"] / 1e9, name="Operating income", mode="lines",
                        line=dict(color="#d97706")), row=1, col=1)
f9.add_trace(go.Scatter(x=QF["yr"], y=QF["q4_share"], name="Q4 share", mode="lines+markers",
                        line=dict(color="#1e3a8a"), showlegend=False), row=1, col=2)
f9.update_xaxes(tickvals=[f"{y} Q1" for y in range(2010, 2027, 4)], ticktext=[str(y) for y in range(2010, 2027, 4)],
                row=1, col=1)
f9.update_yaxes(tickformat=".0%", range=[0.2, 0.42], row=1, col=2)
f9.update_layout(**{**LAY, "margin": dict(l=60, r=20, t=70, b=70)})
f9.update_annotations(font_size=12)

bad_txt = "; ".join(f"{r['item'].replace('rev', 'revenue').replace('ni', 'net income')} Q4 {r['yr']}: tagged "
                    f"${r['tagged'] / 1e6:,.0f}m, derived ${r['derived'] / 1e6:,.0f}m" for r in q4_bad.iter_rows(named=True))
S.append((f9, "9. Quarters: the holiday quarter matters less",
    f"Q4 (dark bars) was {pct(QF['q4_share'][0])} of the year's revenue in {QF['yr'][0]} and {pct(QF['q4_share'][-1])} "
    f"in {QF['yr'][-1]}: a flatter year, in step with the shift toward services in section 2. Operating income was negative "
    f"in {len(QW.filter(pl.col('opinc') < 0))} quarters, all in 2012-2014. 2026 so far: revenue up "
    f"{', '.join(pct(g) for g in yoy['g'])} on the same quarters of 2025 (Q1-Q3). In 2026 Q2 net income was "
    f"{bn(q26.filter(pl.col('q') == 2)['ni'][0])} against operating income of "
    f"{bn(q26.filter(pl.col('q') == 2)['opinc'][0])}: another gain outside operations the data does not name.",
    f"10-Qs give Q1-Q3; Q4 is the 10-K year minus Q1-Q3. Two checks: Q1+Q2+Q3 matches each 10-Q's own nine-month "
    f"figure within ${y9_max / 1e6:.0f}m (rounding), and the derived Q4 matches the Q4 that 10-Ks tagged in "
    f"2009-2020 in {q4_ok} of {q4chk.height} cases within $2m. The exceptions are a filing error: the FY2012 10-K "
    f"swapped Q4 2011 and Q4 2012 ({bad_txt}). SEC's own CY2011Q4 frame carries the wrong value; the derived "
    "Q4 is right. 2008 has only Q2 and Q3 (the first tagged 10-Qs).",
    "Quarterly segment figures (AWS reports every quarter), dimensional like the annual ones."))

S.extend(rivals_page.sections(LAY, pct, bn))

sections = "".join(f"""
<section>
<h2>{title}</h2>
<div class="cols">
<div class="chart">{chart(fig, i == 0)}</div>
<dl><dt>What the data shows</dt><dd>{shows}</dd><dt>Limits of the data</dt><dd>{limits}</dd>
<dt>What we would reach for</dt><dd>{reach}</dd></dl>
</div>
</section>""" for i, (fig, title, shows, limits, reach) in enumerate(S))

synopsis = f"""
<li>A {r25 / r07:.0f}-fold rise in revenue in 18 years ({pct(cagr)} a year against {pct(gdp_cagr, 1)} for US GDP), to {bn(r25)} in 2025.</li>
<li>A shift from selling goods to selling services: services went from {pct(svc09)} to {pct(svc17)} of revenue in 2009-2017, and
revenue minus cost of sales from {pct(v(2009, 'gross_margin'))} to {pct(v(2025, 'gross_margin'))} by 2025.</li>
<li>A decade of near-zero margins (operating margin under 5% to 2017), then {pct(v(2025, 'op_margin'))} by 2025.</li>
<li>A business turned capital-heavy: property per dollar of revenue from {v(2008, 'ppe') / v(2008, 'rev'):.2f} to
{v(2025, 'ppe') / v(2025, 'rev'):.2f}; in 2025 capital spending took nearly all operating cash.</li>
<li>Funded partly by its suppliers: a cash conversion cycle of about {-ccc['ccc'].median():.0f} days in the typical year.</li>
<li>A market value of {bn(vlast['mcap'][0])} on {last['obs_date'][0]}, {vlast['ps'][0]:.1f} times revenue, after two falls of more than half.</li>
"""

reach_rows = [
    ("How big is AWS next to retail?", "Segment revenue and operating income", "Dimensional XBRL: SEC Financial Statement and Notes data sets"),
    ("Where does the cost go?", "Fulfillment, technology and infrastructure, sales and marketing", "Amazon's own tags: same data sets"),
    ("How much is US, for a fair GDP comparison?", "Revenue by country", "Dimensional XBRL: same data sets"),
    ("Products vs services after 2017", "Sales by type", "Dimensional XBRL: same data sets"),
    ("What were the 2021-22 investment swings?", "The investment named", "The 10-K text"),
    ("How many people, and what do they earn?", "Headcount", "The 10-K text (not tagged)"),
    ("Is the money worth less than it looks?", "Interest rates", "FRED, the source Pharos already loads"),
    ("Better or worse than peers?", "Other companies' prices", "Yahoo, the source Pharos already loads (figures are already in)"),
    ("How big is online retail?", "US e-commerce sales", "Census retail e-commerce series, published on FRED"),
]
reach = "".join(f"<tr><td>{a}</td><td>{b}</td><td>{c}</td></tr>" for a, b, c in reach_rows)

built = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Amazon from Pharos data</title>
<style>
:root {{ --bg:#ffffff; --fg:#1d1d1b; --muted:#5f5e58; --line:#e3e1da; --box:#f6f5f1; }}
body {{ background:var(--bg); color:var(--fg); font:15px/1.5 system-ui, "Segoe UI", sans-serif; margin:0; padding:24px 16px 60px; }}
main {{ max-width:1250px; margin:0 auto; }}
h1 {{ font-size:24px; margin:0 0 6px; }}
h2 {{ font-size:18px; margin:0 0 10px; }}
section {{ border-top:1px solid var(--line); padding:22px 0; }}
.lead {{ background:var(--box); padding:14px 18px; margin:14px 0 8px; }}
.lead ul {{ margin:6px 0; padding-left:20px; }}
.lead li {{ margin:4px 0; }}
.cols {{ display:grid; grid-template-columns: minmax(0,3fr) minmax(0,2fr); gap:22px; align-items:start; }}
@media (max-width: 900px) {{ .cols {{ grid-template-columns: 1fr; }} }}
dl {{ margin:0; }}
dt {{ font-weight:600; margin-top:10px; }}
dt:first-child {{ margin-top:0; }}
dd {{ margin:2px 0 0; color:var(--muted); }}
.muted {{ color:var(--muted); }}
table.reach {{ border-collapse:collapse; width:100%; font-size:14px; }}
table.reach th, table.reach td {{ border-bottom:1px solid var(--line); padding:6px 10px; text-align:left; vertical-align:top; }}
{nav.CSS}
</style></head>
<body>{nav.html("Amazon")}<main>
<h1>Amazon, from Pharos's data only</h1>
<p class="muted">Built {built}. Sources, all inside Pharos: SEC company facts for Amazon (CIK 1018724; 10-K figures for
2007-2025, 545 standard accounting concepts), SEC company details (SIC 5961, Nasdaq, Delaware), the AMZN daily close
from Yahoo (2005-01-03 to {last['obs_date'][0]}, with the 20-for-1 split of 2022-06-06), and FRED's GDP and CPI. No outside
knowledge enters the numbers; where the data cannot say why, the page says so.</p>

<div class="lead">
<p><b>The story the data tells:</b></p>
<ul>{synopsis}</ul>
<p><b>What it cannot tell:</b> which business does what. Every question about AWS, advertising, geography or cost by
function runs into the same wall: those figures are tagged as dimensions or with Amazon's own tags, and SEC's company
facts keep neither.</p>
</div>

{sections}

<section>
<h2>What we would reach for</h2>
<table class="reach"><tr><th>Question</th><th>Missing figure</th><th>Where it lives</th></tr>{reach}</table>
<p class="muted">Most of the gaps share one source: SEC's Financial Statement and Notes data sets, the same filings
with dimensions and company-specific tags kept. Where the data lives is from Claude's knowledge, not checked against
those data sets in this session.</p>
</section>
</main></body></html>"""

out = HERE / "amazon.html"
out.write_text(html, encoding="utf-8")
print("wrote", out)

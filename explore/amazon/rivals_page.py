"""Sections 10-14 of the Amazon page: Amazon among goods retailers. Figures come from rivals.py; the text formats them,
so it stays true when the data changes. sections() returns (figure, title, shows, limits, reach) tuples like build.py's."""
import plotly.graph_objects as go
import polars as pl
from plotly.subplots import make_subplots

import rivals

AMZN = rivals.AMZN
PEERS = {1018724: ("Amazon", "#1e3a8a"), 104169: ("Walmart", "#d97706"), 909832: ("Costco", "#059669"),
         354950: ("Home Depot", "#dc2626"), 27419: ("Target", "#7c3aed"), 764478: ("Best Buy", "#0891b2")}
SQUEEZED = {1310067: "Sears", 1166126: "J C Penney", 794367: "Macy's", 885639: "Kohl's", 764478: "Best Buy",
            886158: "Bed Bath & Beyond", 72333: "Nordstrom"}
WHY_COLOR = {"bankruptcy": "#dc2626", "taken over": "#d97706", "stopped filing, reason not in 8-Ks": "#9ca3af"}


def sections(LAY, pct, bn):
    P = rivals.panel()
    E = rivals.exits()
    S = []

    # ---- 10. who left -------------------------------------------------------------------------------------------
    gone = E.filter(pl.col("why") != "still files (tag gap)")
    by = gone.group_by("last_yr", "why").agg((pl.col("last_rev").sum() / 1e9).alias("bn"), pl.len().alias("n")).sort("last_yr")
    a = P.filter(pl.col("cik") == AMZN).sort("yr").with_columns((pl.col("rev").diff() / 1e9).alias("gain"))
    a = a.filter(pl.col("yr").is_between(2011, 2023))
    f10 = go.Figure()
    for why, color in WHY_COLOR.items():
        d = by.filter(pl.col("why") == why)
        f10.add_trace(go.Bar(x=d["last_yr"], y=d["bn"], name=f"Left: {why}", marker_color=color,
                             customdata=d["n"], hovertemplate="%{x}: $%{y:.1f}bn, %{customdata} companies<extra></extra>"))
    f10.add_trace(go.Scatter(x=a["yr"], y=a["gain"], name="Amazon's revenue gain that year", mode="lines+markers",
                             line=dict(color="#1e3a8a")))
    f10.update_layout(barmode="stack", title="Revenue of goods retailers in their last filed year, by why they left, $bn",
                      yaxis_title="$bn", **LAY)
    cnt = {r["why"]: (r["n"], r["bn"]) for r in gone.group_by("why").agg(pl.len().alias("n"),
                                                                         (pl.col("last_rev").sum() / 1e9).alias("bn")).iter_rows(named=True)}
    w = gone.filter(pl.col("last_yr").is_between(2011, 2023))
    left_bn = w["last_rev"].sum() / 1e9
    gain_bn = a["gain"].sum()
    top = gone.sort("last_rev", descending=True).head(8)
    top_txt = "; ".join(f"{r['trading_name']} ({r['last_yr']}, {bn(r['last_rev'])}, {r['why'].split(',')[0]})"
                        for r in top.iter_rows(named=True))
    gap = E.filter(pl.col("why") == "still files (tag gap)")
    S.append((f10, "10. Goods retail: who left while Amazon grew",
        f"Of the goods retailers filing with SEC since 2009, {gone.height} stopped filing before 2024: "
        f"{cnt['bankruptcy'][0]} after a bankruptcy ({bn(cnt['bankruptcy'][1] * 1e9)} of revenue in their last year), "
        f"{cnt['taken over'][0]} after a change of control ({bn(cnt['taken over'][1] * 1e9)}), and "
        f"{cnt['stopped filing, reason not in 8-Ks'][0]} for a reason their 8-Ks do not state. Those leaving in 2011-2023 "
        f"had {bn(left_bn * 1e9)} of revenue in their last year; Amazon added {bn(gain_bn * 1e9)} over the same years. "
        f"The largest: {top_txt}.",
        f"Goods retail is {rivals.GOODS_LABEL}: it still lets in some non-goods sellers (propane distributors under "
        "5990, a travel holding company under 5960). A takeover moves revenue to the buyer rather than ending it, and a "
        "bankrupt chain's stores often closed over years before the last 10-K. Dates of leaving are the last fiscal "
        "year with revenue; the reason comes from 8-K items 1.03 (bankruptcy) and 5.01 (change in control) filed "
        f"from a year before that year's end. {gap.height} companies stopped tagging revenue but still file 10-Ks; "
        "they are left out. Names are as SEC records them, under the name each company traded under at its last 10-K "
        "(SEC renames the shells: 'Old COPPER Company' was J C Penney).",
        "Store counts and closures, and private chains that never filed with SEC (the 10-K text, and Census retail "
        "totals on FRED for the whole market)."))

    # ---- 11. share of goods retail --------------------------------------------------------------------------------
    p11 = P.filter(pl.col("yr").is_between(2011, 2025))
    tot = p11.group_by("yr").agg(pl.col("rev").sum().alias("T"), pl.len().alias("n"))
    f11 = go.Figure()
    shares = {}
    for cik, (label, color) in PEERS.items():
        d = p11.filter(pl.col("cik") == cik).join(tot, on="yr").with_columns((pl.col("rev") / pl.col("T")).alias("s")).sort("yr")
        shares[label] = d
        f11.add_trace(go.Scatter(x=d["yr"], y=d["s"], name=label, mode="lines+markers", line=dict(color=color,
                                 width=3 if cik == AMZN else 2), hovertemplate=label + " %{x}: %{y:.1%}<extra></extra>"))
    f11.update_layout(title="Share of goods-retail revenue among SEC filers, 2011-2025", yaxis=dict(tickformat=".0%"), **LAY)
    am, wm = shares["Amazon"], shares["Walmart"]
    cross = am.join(wm.select("yr", pl.col("s").alias("w")), on="yr").filter(pl.col("s") > pl.col("w"))
    t0, t1 = tot.sort("yr")[0], tot.sort("yr")[-1]
    mid = ", ".join(f"{k} {pct(shares[k]['s'][0], 1)} to {pct(shares[k]['s'][-1], 1)}"
                    for k in ("Costco", "Home Depot", "Target", "Best Buy"))
    S.append((f11, "11. Share of goods retail: Amazon draws level with Walmart",
        f"Amazon's share of the revenue of SEC-filing goods retailers rose from {pct(am['s'][0], 1)} in 2011 to "
        f"{pct(am['s'][-1], 1)} in 2025; Walmart's went from {pct(wm['s'][0], 1)} to {pct(wm['s'][-1], 1)}"
        + (f"; Amazon is ahead from {cross['yr'][0]}, by {(am['s'][-1] - wm['s'][-1]) * 100:.1f} percentage points in 2025." if cross.height else ".")
        + f" The others (first year to last): {mid}. The field shrank from {t0['n'][0]} companies in 2011 to "
        f"{t1['n'][0]} in 2025.",
        "The denominator is only companies that file with SEC, so it moves as companies list, go private or fail; "
        "it is not the US market. Amazon's revenue includes AWS, advertising and subscriptions, which no other "
        "company here sells, and is worldwide, as is Walmart's: Amazon's share of goods sales is overstated.",
        "Amazon's online-store and third-party-seller revenue (dimensional, in the Financial Statement and Notes data "
        "sets), and Census US retail sales and e-commerce share (FRED) for a true market denominator."))

    # ---- 12. squeeze before shrink --------------------------------------------------------------------------------
    p12 = P.filter(pl.col("cik").is_in(list(SQUEEZED) + [AMZN])).with_columns(
        (pl.col("opinc") / pl.col("rev")).alias("om")).sort("cik", "yr")
    y0 = p12.group_by("cik").agg(pl.col("yr").min().alias("y0"))
    f12 = make_subplots(rows=1, cols=2, subplot_titles=("Operating margin", "Revenue / own peak"))
    pal = ["#dc2626", "#d97706", "#7c3aed", "#059669", "#0891b2", "#db2777", "#65a30d"]
    rows = []
    for (cik, label), color in zip(list(SQUEEZED.items()) + [(AMZN, "Amazon")], pal + ["#1e3a8a"]):
        d = p12.filter(pl.col("cik") == cik).with_columns((pl.col("rev") / pl.col("rev").max()).alias("idx"))
        wd = 3 if cik == AMZN else 1.6
        f12.add_trace(go.Scatter(x=d["yr"], y=d["om"], name=label, mode="lines", line=dict(color=color, width=wd),
                                 legendgroup=label, hovertemplate=label + " %{x}: %{y:.1%}<extra></extra>"), row=1, col=1)
        if cik != AMZN:
            f12.add_trace(go.Scatter(x=d["yr"], y=d["idx"], name=label, mode="lines", line=dict(color=color, width=wd),
                                     legendgroup=label, showlegend=False,
                                     hovertemplate=label + " %{x}: %{y:.2f} of peak<extra></extra>"), row=1, col=2)
            m = d.filter(pl.col("om").is_not_null())
            pk_m = m.sort("om")[-1]
            pk_r = d.sort("rev")[-1]
            rows.append((label, pk_m["yr"][0], pk_r["yr"][0], m["om"][-1], m["yr"][-1], d["yr"].min(), m["yr"].max()))
    f12.update_yaxes(tickformat=".0%", row=1, col=1)
    f12.update_layout(title="", **{**LAY, "margin": dict(l=60, r=20, t=60, b=70)})
    f12.update_annotations(font_size=12)
    stub = [r for r in rows if r[6] < 2015]
    full = [r for r in rows if r not in stub]
    first = [r for r in full if r[1] < r[2]]
    censored = [r for r in full if r[1] == r[5] and r[2] == r[5]]
    same = [r for r in full if r[1] == r[2] and r not in censored]
    S.append((f12, "12. The chains that shrank: margins and sales",
        f"For {len(first)} of the {len(full)} chains with margins through their last years, the best operating margin came years before the best revenue "
        "year (" + "; ".join(f"{r[0]} margin peak {r[1]}, revenue peak {r[2]}" for r in first) + "): the squeeze "
        f"came first. For {len(censored)} ({', '.join(r[0] for r in censored)}) both peaks fall in the first year "
        "of data, so the order cannot be seen; for " + ", ".join(f"{r[0]} ({r[1]})" for r in same)
        + " they fall in the same year. Latest margins: "
        + ", ".join(f"{r[0]} {pct(r[3], 1)} ({r[4]})" for r in rows if r not in stub)
        + ". Amazon's margin (heavy line), thin until 2017, rose over the years theirs fell.",
        "The chains are picked by Claude as the department-store and big-box names in Amazon's categories, not drawn "
        "by a rule. Margins falling before sales is the pattern of being undercut on price, but rent, debt, "
        "management and malls give the same shape; the data cannot separate them. Each chain's peaks are within "
        "2009-2025: an earlier peak would not show. "
        + (", ".join(r[0] for r in stub) + f" tags operating income only to {stub[0][6]}, so its margin line stops "
           "there while its revenue line continues. " if stub else "")
        + "Revenue (right) runs to each chain's last 10-K.",
        "Same-store sales and store counts (the 10-K text), and category-level sales to match Amazon line by line "
        "(not filed by anyone)."))

    # ---- 13. how the model differs --------------------------------------------------------------------------------
    p13 = P.filter(pl.col("cik").is_in(list(PEERS)) & pl.col("yr").is_between(2010, 2025)).with_columns(
        (pl.col("capex") / pl.col("rev")).alias("ci"), (pl.col("inv") / pl.col("cogs") * 365).alias("dio"),
        (pl.col("ap") / pl.col("cogs") * 365).alias("dpo"), (pl.col("opinc") / pl.col("rev")).alias("om"))
    f13 = make_subplots(rows=1, cols=3, horizontal_spacing=0.09, subplot_titles=("Capex / revenue", "Inventory, days",
                                                        "Payables less inventory, days"))
    for cik, (label, color) in PEERS.items():
        d = p13.filter(pl.col("cik") == cik).sort("yr")
        wd = 3 if cik == AMZN else 1.6
        for col, y in ((1, d["ci"]), (2, d["dio"]), (3, d["dpo"] - d["dio"])):
            f13.add_trace(go.Scatter(x=d["yr"], y=y, name=label, mode="lines", line=dict(color=color, width=wd),
                                     legendgroup=label, showlegend=col == 1), row=1, col=col)
    f13.update_yaxes(tickformat=".0%", row=1, col=1)
    f13.update_layout(title="", **{**LAY, "margin": dict(l=50, r=20, t=60, b=70)})
    f13.update_annotations(font_size=12)
    l13 = p13.filter(pl.col("yr") == 2025).with_columns(pl.col("cik").replace_strict({k: v[0] for k, v in PEERS.items()}).alias("who"))
    def g(who, c):
        return l13.filter(pl.col("who") == who)[c][0]
    others_ci = l13.filter(pl.col("cik") != AMZN)["ci"]
    S.append((f13, "13. Against the big chains: a different machine",
        f"In 2025 Amazon spent {pct(g('Amazon', 'ci'))} of revenue on property and equipment; the five chains spent "
        f"{pct(others_ci.min(), 1)} to {pct(others_ci.max(), 1)}. It held inventory {g('Amazon', 'dio'):.0f} days "
        f"against Walmart's {g('Walmart', 'dio'):.0f} and Costco's {g('Costco', 'dio'):.0f}, and its payables exceed "
        f"its inventory by {g('Amazon', 'dpo') - g('Amazon', 'dio'):.0f} days (Costco {g('Costco', 'dpo') - g('Costco', 'dio'):.0f}, "
        f"Walmart {g('Walmart', 'dpo') - g('Walmart', 'dio'):.0f}): suppliers finance more than the stock on hand.",
        "Fiscal years differ (Walmart and Target end in January, Costco in August); each is placed in the calendar "
        "year that holds most of it. Amazon's cost of sales excludes fulfillment and its capital spending funds AWS "
        "data centres, so its capital intensity and day counts are not like-for-like with a store chain's. Days are "
        "year-end balances over the year's cost of sales.",
        "Capital spending by segment, to separate AWS from retail (dimensional, Financial Statement and Notes data sets)."))

    # ---- 14. the market's verdict ---------------------------------------------------------------------------------
    pr = rivals.prices()
    base = pr.group_by("ticker").agg(pl.col("close").sort_by("obs_date").first().alias("c0"),
                                     pl.col("obs_date").min().alias("d0"))
    pr = pr.join(base, on="ticker").with_columns((pl.col("close") / pl.col("c0")).alias("idx"))
    col = {"AMZN": "#1e3a8a", "WMT": "#d97706", "COST": "#059669", "HD": "#dc2626"}
    f14 = go.Figure()
    for t, c in col.items():
        d = pr.filter(pl.col("ticker") == t)
        f14.add_trace(go.Scatter(x=d["obs_date"], y=d["idx"], name=t, mode="lines", line=dict(color=c, width=2.5 if t == "AMZN" else 1.6),
                                 hovertemplate=t + " %{x}: %{y:.1f}x<extra></extra>"))
    f14.update_layout(title="Split-adjusted close, first close = 1 (log scale)", yaxis=dict(type="log", dtick=1), **LAY)
    lastv = pr.sort("obs_date").group_by("ticker").agg(pl.col("idx").last(), pl.col("obs_date").last(), pl.col("d0").first())
    lv = {r["ticker"]: r for r in lastv.iter_rows(named=True)}
    S.append((f14, "14. The market's verdict on the survivors",
        f"From {lv['AMZN']['d0']} to {lv['AMZN']['obs_date']} the AMZN close rose {lv['AMZN']['idx']:.0f} times, "
        f"COST {lv['COST']['idx']:.0f} times, HD {lv['HD']['idx']:.0f} times and WMT {lv['WMT']['idx']:.0f} times: "
        "all four rose, whatever their share did in section 11 (Walmart's fell, Costco's rose); Amazon rose most.",
        "The close is split-adjusted but excludes dividends, which the three chains pay and Amazon does not, so it "
        "understates their total return. Only the survivors have prices in Pharos: none of the chains that left in "
        "section 10 are loaded, so the chart cannot show what their owners lost.",
        "Prices for Target, Best Buy, Macy's and Kohl's, and the delisted chains' price histories, which Yahoo "
        "often no longer serves (the ATVI case in the health checks)."))
    return S

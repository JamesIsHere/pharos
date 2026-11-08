"""Trade Flows: who trades goods with whom, for the 13 economies of the World page (the US, Canada, Mexico, Brazil,
the UK, Germany, France, Italy, China, Japan, South Korea, India, Australia).

Quick lane (two lanes, CLAUDE.md): fetched from the IMF's International Trade in Goods by partner dataset (IMTS,
api.imf.org, SDMX 2.1, no key) when the page is built; nothing is stored. Goods only: the IMF has no services by
partner. Values are US dollars as the IMF publishes them (OBS_VALUE is in dollars; the SCALE attribute is display
only, checked below against US imports from China). Exports are FOB as the exporter reports them; imports are CIF as
the importer reports them. Every value carries DERIVATION_TYPE 'M' (mixed), so reported and IMF-estimated values
cannot be told apart. Aggregations run in DuckDB over the parsed frames. Writes flows.html next to this file."""
import html
import json
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import duckdb
import plotly.graph_objects as go
import plotly.io as pio
import polars as pl
from plotly.subplots import make_subplots

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "lib"))
import nav  # noqa: E402

API = "https://api.imf.org/external/sdmx/2.1/"
UA = {"User-Agent": "pharos-explore"}
ECON = {"USA": "United States", "CAN": "Canada", "MEX": "Mexico", "BRA": "Brazil", "GBR": "United Kingdom",
        "DEU": "Germany", "FRA": "France", "ITA": "Italy", "CHN": "China", "JPN": "Japan", "KOR": "South Korea",
        "IND": "India", "AUS": "Australia"}
ORDER = list(ECON)
X, M = "XG_FOB_USD", "MG_CIF_USD"
START = 2000
# Colors follow the entity: the US blue and China orange everywhere; the US's other monthly partners take the next
# palette slots in order.
C = {"USA": "#2a78d6", "CHN": "#eb6834"}
SLOTS = ("#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948")
GRAY, INK, MUTED = "#b5b4ae", "#0b0b0b", "#5f5e58"


def get(path: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(API + path, headers=UA), timeout=300) as r:
        return r.read()


def imts(key: str, start: str) -> pl.DataFrame:
    """One IMTS query as rows (rep, ind, par, period, v). An empty answer stops the build."""
    root = ET.fromstring(get(f"data/IMF.STA,IMTS/{key}?" + urllib.parse.urlencode({"startPeriod": start})))
    rows = []
    for s in root.iter("Series"):
        a = s.attrib
        for o in s.iter("Obs"):
            v = o.get("OBS_VALUE")
            if v not in (None, "", "NaN"):
                rows.append((a["COUNTRY"], a["INDICATOR"], a["COUNTERPART_COUNTRY"], o.get("TIME_PERIOD"), float(v)))
    if not rows:
        raise SystemExit(f"flows: IMF returned no observations for {key}")
    print(f"{key[:60]}: {len(rows):,} values")
    return pl.DataFrame(rows, schema=["rep", "ind", "par", "period", "v"], orient="row")


def partner_names() -> dict:
    root = ET.fromstring(get("codelist/IMF.STA/CL_IMTS_COUNTRY/latest"))
    ns = {"str": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure",
          "com": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/common"}
    out = {}
    for code in root.iterfind(".//str:Code", ns):
        name = code.find("com:Name[@{http://www.w3.org/XML/1998/namespace}lang='en']", ns)
        out[code.get("id")] = name.text
    if len(out) < 200:
        raise SystemExit(f"flows: the IMF country codelist has {len(out)} codes, expected 200+")
    return out


SHORT = {"Hong Kong Special Administrative Region, People's Republic of China": "Hong Kong",
         "Taiwan Province of China": "Taiwan", "China, People's Republic of": "China", "Korea, Republic of": "South Korea",
         "Netherlands, The": "Netherlands", "Russian Federation": "Russia", "United States": "United States",
         "Viet Nam": "Vietnam", "Türkiye, Republic of": "Turkey", "Iran, Islamic Republic of": "Iran"}
NAMES = {k: SHORT.get(v, re.sub(r", (Republic of|The|Kingdom of the|Federal Republic of|State of)$", "", v))
         for k, v in partner_names().items()}
NAMES.update(ECON)

annual = imts("+".join(ORDER) + f".{X}+{M}..A", "1940")   # full history: the time lapse uses all of it
annual = annual.with_columns(pl.col("period").cast(pl.Int32).alias("yr")).drop("period")
# The US's six largest goods partners in the latest full year, from the annual data.
us_year = annual.filter((pl.col("rep") == "USA") & (pl.col("par") == "G001")).group_by("yr").len()     .filter(pl.col("len") == 2)["yr"].max()
US_PARTNERS = tuple(annual.filter((pl.col("rep") == "USA") & (pl.col("yr") == us_year)
                                  & pl.col("par").str.contains(r"^[A-Z]{3}$"))
                    .group_by("par").agg(pl.col("v").sum()).sort("v", descending=True)["par"].head(6))
if "CHN" not in US_PARTNERS:
    raise SystemExit(f"flows: China is not among the US's six largest partners in {us_year}: {US_PARTNERS}")
C.update(zip([p for p in US_PARTNERS if p != "CHN"], SLOTS))
monthly_us = imts(f"USA.{X}+{M}.{'+'.join(US_PARTNERS)}.M", "2014-01")
monthly_cn = imts(f"CHN.{X}+{M}.USA.M", "2014-01")
monthly = pl.concat([monthly_us, monthly_cn]).with_columns(
    pl.col("period").str.replace("-M", "-").str.to_date("%Y-%m").alias("date")).drop("period")

con = duckdb.connect()
con.register("a", annual)
con.register("m", monthly)

# The latest year every one of the 13 reports both its exports and its imports to the world (G001).
YEAR = con.sql(f"""select max(yr) from (select yr from a where par = 'G001' group by yr
                   having count(distinct rep || ind) = {2 * len(ORDER)})""").fetchone()[0]
if YEAR is None or YEAR < 2024:
    raise SystemExit(f"flows: the latest year all 13 economies report is {YEAR}")
# Unit check: US imports of goods from China, 2024. Census reports about $439bn (customs basis; unverified, from
# prior); CIF runs a little higher. A unit or scale error would miss by a factor of 1,000 or more.
us_cn = con.sql("select v from a where rep = 'USA' and ind = 'MG_CIF_USD' and par = 'CHN' and yr = 2024").fetchone()
if not us_cn or not 350e9 < us_cn[0] < 550e9:
    raise SystemExit(f"flows: US imports from China 2024 = {us_cn}, expected $350-550bn (unit or scale wrong)")
# Partner sums against the world total: the country rows should add up to nearly all of it.
cover = con.sql(f"""select rep, ind, sum(v) filter (where regexp_full_match(par, '[A-Z]{{3}}'))
                           / max(v) filter (where par = 'G001') as r
                    from a where yr = {YEAR} group by all order by r""").fetchall()
for rep, ind, r in cover:
    if not 0.9 < r < 1.02:
        raise SystemExit(f"flows: {rep} {ind} {YEAR}: country partners add to {r:.1%} of the world total")
print(f"year {YEAR}; partner rows cover {min(r for *_, r in cover):.1%} to {max(r for *_, r in cover):.1%} of world totals")

# Countries only (three-letter codes): drops the IMF's regional groups and 'not specified' rows.
con.sql("""create view c as select * from a where regexp_full_match(par, '[A-Z]{3}')""")
con.sql("""create view tot as select rep, yr, sum(v) as world from a where par = 'G001' group by all""")   # X + M

# ---- 1. matrix: exports from row to column, as the exporter reports them
inside = con.sql(f"""select rep, par, v from c where ind = '{X}' and yr = {YEAR} and par in {tuple(ORDER)}""").pl()
world_x = dict(con.sql(f"select rep, v from a where ind = '{X}' and par = 'G001' and yr = {YEAR}").fetchall())
lk = {(r, p): v for r, p, v in inside.iter_rows()}
z_bn = [[lk.get((r, p)) / 1e9 if r != p and (r, p) in lk else None for p in ORDER] for r in ORDER]
z_sh = [[lk[(r, p)] / world_x[r] * 100 if r != p and (r, p) in lk else None for p in ORDER] for r in ORDER]
labels = [ECON[c] for c in ORDER]
SEQ = [[0, "#f3f7fd"], [0.15, "#cde2fb"], [0.35, "#86b6ef"], [0.6, "#2a78d6"], [0.8, "#1c5cab"], [1, "#0d366b"]]
hm = go.Figure()
hm.add_trace(go.Heatmap(z=z_bn, x=labels, y=labels, colorscale=SEQ, xgap=2, ygap=2, zmin=0,
                        colorbar=dict(title="$bn"), visible=True,
                        hovertemplate="%{y} to %{x}: $%{z:,.1f}bn<extra></extra>"))
hm.add_trace(go.Heatmap(z=z_sh, x=labels, y=labels, colorscale=SEQ, xgap=2, ygap=2, zmin=0,
                        colorbar=dict(title="% of<br>exports"), visible=False,
                        hovertemplate="%{y} to %{x}: %{z:.1f}% of %{y}'s exports<extra></extra>"))
hm.update_layout(
    template="plotly_white", height=640, margin=dict(l=120, r=20, t=60, b=110),
    xaxis=dict(title="to (importer)", side="bottom", tickangle=-35), yaxis=dict(title="from (exporter)", autorange="reversed"),
    updatemenus=[dict(type="buttons", direction="right", x=0, xanchor="left", y=1.1, yanchor="top", showactive=True,
                      buttons=[dict(label="US dollars", method="update", args=[{"visible": [True, False]}]),
                               dict(label="Share of the exporter's exports", method="update",
                                    args=[{"visible": [False, True]}])])])

# ---- headline facts, computed
def biggest(yr: int) -> dict:
    """Each economy's largest partner by goods trade (exports + imports), among all countries."""
    rows = con.sql(f"""select rep, par from (select rep, par, sum(v) t from c where yr = {yr} group by all)
                       qualify row_number() over (partition by rep order by t desc) = 1""").fetchall()
    return dict(rows)


b0, b1 = biggest(START), biggest(YEAR)
us_top = lambda b: [ECON[r] for r in ORDER if r not in ("USA",) and b[r] == "USA"]
cn_top = lambda b: [ECON[r] for r in ORDER if r not in ("CHN",) and b[r] == "CHN"]
top_flow = con.sql(f"""select rep, par, v from c where ind = '{X}' and yr = {YEAR} order by v desc limit 1""").fetchone()
cn_x0, cn_x1 = (con.sql(f"select v from a where rep='CHN' and ind='{X}' and par='G001' and yr={y}").fetchone()[0]
                for y in (START, YEAR))
us_bal = con.sql(f"""select sum(v) filter (where ind = '{X}') - sum(v) filter (where ind = '{M}')
                     from a where rep = 'USA' and par = 'G001' and yr = {YEAR}""").fetchone()[0]


def names(xs):
    return ", ".join(xs) if xs else "none"


facts = [
    f"In {START} the United States was the largest goods-trade partner of {len(us_top(b0))} of the other 12 "
    f"economies ({names(us_top(b0))}); in {YEAR} it is for {len(us_top(b1))} ({names(us_top(b1))}).",
    (f"China was the largest partner of none of them in {START}" if not cn_top(b0) else
     f"China was the largest partner of {len(cn_top(b0))} in {START} ({names(cn_top(b0))})")
    + f" and of {len(cn_top(b1))} in {YEAR} ({names(cn_top(b1))}).",
    f"China's goods exports grew from ${cn_x0 / 1e9:,.0f}bn in {START} to ${cn_x1 / 1e9:,.0f}bn in {YEAR}, "
    f"{cn_x1 / cn_x0:.0f} times as much.",
    f"The largest single flow in {YEAR} is {NAMES[top_flow[0]]} to {NAMES[top_flow[1]]}: ${top_flow[2] / 1e9:,.0f}bn, "
    f"as {NAMES[top_flow[0]]} reports it.",
    f"The United States imported ${-us_bal / 1e9:,.0f}bn more goods than it exported in {YEAR} "
    "(imports valued including freight and insurance).",
]

# ---- 2. sankey: flows among the 13, colored by exporter for the US and China, gray otherwise
# A time lapse: one frame per year from the first year in the data. Each frame fills the same height, so it shows
# shares of that year's flows; the year's dollar total and its count of unreported pairs are printed on the frame.
# Nodes keep a fixed order (ORDER) and fixed positions within a frame, so a country stays where the eye left it.
flows_all = con.sql(f"""select yr, rep, par, v from c where ind = '{X}' and rep in {tuple(ORDER)}
                        and par in {tuple(ORDER)} and rep <> par and v > 0 order by yr""").pl()
YEARS = sorted(flows_all["yr"].unique().to_list())
if YEARS[-1] != YEAR or YEARS[0] > 1950:
    raise SystemExit(f"flows: the time lapse runs {YEARS[0]}-{YEARS[-1]}, expected from about 1948 to {YEAR}")
node_col = [{"USA": C["USA"], "CHN": C["CHN"]}.get(c, GRAY) for c in ORDER] * 2
LINK = {"USA": "rgba(42,120,214,0.45)", "CHN": "rgba(235,104,52,0.45)"}
PAD = 0.012   # vertical gap between nodes, as a share of the height


def positions(sizes: list[float]) -> list[float]:
    """Node centers top to bottom in ORDER, each node's height proportional to its size, gaps of PAD."""
    total = sum(sizes) or 1
    free = 1 - PAD * (len(sizes) - 1) - 0.02
    ys, at = [], 0.01
    for s in sizes:
        h = s / total * free
        ys.append(at + h / 2)
        at += h + PAD
    return ys


def frame(yr: int) -> tuple[go.Sankey, list]:
    d = flows_all.filter(pl.col("yr") == yr)
    out = {r: 0.0 for r in ORDER}
    inn = {r: 0.0 for r in ORDER}
    src, dst, val, col = [], [], [], []
    for _, r, p, v in d.iter_rows():
        src.append(ORDER.index(r))
        dst.append(len(ORDER) + ORDER.index(p))
        val.append(v / 1e9)
        col.append(LINK.get(r, "rgba(181,180,174,0.35)"))
        out[r] += v
        inn[p] += v
    # Plotly drops a node with no links and then applies x and y by position to the nodes left, which shifts every
    # later node (1950: the US and Canada as importers landed in the exporter column). So send only nodes with flows.
    ys = positions([out[r] for r in ORDER]) + positions([inn[r] for r in ORDER])
    # Exporter labels carry the net balance within the 13: exports to the other 12 minus their exports to it.
    def net(r):
        v = (out[r] - inn[r]) / 1e9
        return f"{v:+,.1f}bn" if abs(v) < 10 else f"{v:+,.0f}bn"
    names = [f"{ECON[r]}  {net(r).replace('+', '+$').replace('-', '-$')} net" for r in ORDER] + labels
    sizes = [out[r] for r in ORDER] + [inn[r] for r in ORDER]
    keep = [i for i, s in enumerate(sizes) if s > 0]
    new = {old: k for k, old in enumerate(keep)}
    src, dst = [new[i] for i in src], [new[i] for i in dst]
    tr = go.Sankey(
        arrangement="fixed",
        node=dict(label=[names[i] for i in keep], color=[node_col[i] for i in keep], pad=8,
                  thickness=14, line=dict(width=0),
                  x=[0.001 if i < len(ORDER) else 0.999 for i in keep], y=[ys[i] for i in keep],
                  hovertemplate="%{label}: $%{value:,.1f}bn<extra></extra>"),
        link=dict(source=src, target=dst, value=val, color=col,
                  hovertemplate="%{source.label} to %{target.label}: $%{value:,.2f}bn<extra></extra>"))
    missing = len(ORDER) * (len(ORDER) - 1) - d.height
    note = f"{missing} of the 156 flows not reported" if missing else "all 156 flows reported"
    total = d["v"].sum()
    amt = f"${total / 1e12:,.2f} trillion" if total >= 1e12 else f"${total / 1e9:,.0f} billion"
    ann = [dict(x=0, y=1.07, xref="paper", yref="paper", showarrow=False, xanchor="left", text="<b>Exporter</b>"),
           dict(x=1, y=1.07, xref="paper", yref="paper", showarrow=False, xanchor="right", text="<b>Importer</b>"),
           dict(x=0.5, y=1.085, xref="paper", yref="paper", showarrow=False, font=dict(size=26, color=INK),
                text=f"<b>{yr}</b>"),
           dict(x=0.5, y=1.035, xref="paper", yref="paper", showarrow=False, font=dict(size=13, color=MUTED),
                text=f"{amt} among the 13, in that year's dollars; {note}")]
    return tr, ann


# Plotly's own animation does not handle sankey reliably (the animate promise never settles and frames land out of
# order), so the page carries every year's trace and redraws with Plotly.react from a plain slider and Play button.
FRAMES = {}
for y in YEARS:
    t, a = frame(y)
    FRAMES[y] = {"data": [t.to_plotly_json()], "ann": a}
sk = go.Figure(data=[go.Sankey(**{k: v for k, v in FRAMES[YEAR]["data"][0].items() if k != "type"})])
sk.update_layout(template="plotly_white", height=800, margin=dict(l=20, r=20, t=110, b=20), font=dict(size=13),
                 annotations=FRAMES[YEAR]["ann"])
SK_ID = "streams"
timelapse = f"""<div class="tl"><button id="tl-play" type="button">Play</button>
<input id="tl-year" type="range" min="{YEARS[0]}" max="{YEARS[-1]}" step="1" value="{YEAR}" aria-label="Year">
<span id="tl-label">{YEAR}</span></div>
<script>
(() => {{
  const F = {json.dumps(FRAMES, default=str)};
  const div = document.getElementById("{SK_ID}"), range = document.getElementById("tl-year"),
        label = document.getElementById("tl-label"), btn = document.getElementById("tl-play");
  const years = Object.keys(F).map(Number);
  let timer = null;
  function show(y) {{
    if (!F[y]) return;
    range.value = y; label.textContent = y;
    Plotly.react(div, F[y].data, Object.assign({{}}, div.layout, {{annotations: F[y].ann}}));
  }}
  function stop() {{ clearInterval(timer); timer = null; btn.textContent = "Play"; }}
  range.addEventListener("input", () => {{ stop(); show(+range.value); }});
  btn.addEventListener("click", () => {{
    if (timer) return stop();
    let i = +range.value >= years[years.length - 1] ? 0 : years.indexOf(+range.value) + 1;
    btn.textContent = "Pause"; show(years[i]);
    timer = setInterval(() => {{ i += 1; if (i >= years.length) return stop(); show(years[i]); }}, 450);
  }});
  window.showYear = show;   // for render checks
}})();
</script>"""

# ---- 3. top five partners, START vs YEAR, by goods trade (exports + imports), share of the economy's total
top5 = con.sql(f"""with t as (select rep, yr, par, sum(v) v from c where yr in ({START}, {YEAR}) group by all)
                   select t.rep, t.yr, t.par, t.v / tot.world as sh from t join tot using (rep, yr)
                   qualify row_number() over (partition by t.rep, t.yr order by t.v desc) <= 5
                   order by t.rep, t.yr, sh desc""").fetchall()
cells = {}
for rep, yr, par, sh in top5:
    tag = f' class="{par.lower()}"' if par in ("USA", "CHN") else ""
    cells.setdefault((rep, yr), []).append(f"<span{tag}>{html.escape(NAMES.get(par, par))} {sh * 100:.0f}%</span>")
top_rows = "".join(f"<tr><td>{ECON[r]}</td><td>{', '.join(cells[(r, START)])}</td>"
                   f"<td>{', '.join(cells[(r, YEAR)])}</td></tr>" for r in ORDER)
top_table = (f"<table class=top><thead><tr><th>Economy</th><th>{START}</th><th>{YEAR}</th></tr></thead>"
             f"<tbody>{top_rows}</tbody></table>")

# ---- 4. share of each economy's goods trade with the US and with China, START-YEAR
sh = con.sql(f"""select c.rep, c.yr, c.par, sum(c.v) / any_value(tot.world) * 100 as s
                from c join tot using (rep, yr) where c.par in ('USA', 'CHN') and c.yr >= {START}
                group by all order by 1, 2""").pl()
COLS = 4
rows_n = -(-len(ORDER) // COLS)
sm = make_subplots(rows=rows_n, cols=COLS, shared_xaxes="all", shared_yaxes="all",
                   subplot_titles=labels, vertical_spacing=0.08, horizontal_spacing=0.03)
for i, r in enumerate(ORDER):
    rr, kk = i // COLS + 1, i % COLS + 1
    for p, nm in (("USA", "with the United States"), ("CHN", "with China")):
        d = sh.filter((pl.col("rep") == r) & (pl.col("par") == p))
        if d.is_empty():
            continue
        sm.add_trace(go.Scatter(x=d["yr"], y=d["s"], mode="lines", line=dict(color=C[p], width=2), name=f"Share {nm}",
                                legendgroup=p, showlegend=i == 2 if p == "USA" else i == 0,
                                hovertemplate=f"%{{x}}: %{{y:.1f}}%<extra>{ECON[r]} {nm}</extra>"), rr, kk)
sm.update_layout(height=230 * rows_n + 90, template="plotly_white", hovermode="x unified",
                 margin=dict(l=60, r=20, t=70, b=40),
                 legend=dict(orientation="h", yref="container", y=0.995, yanchor="top", x=1, xanchor="right"))
sm.update_yaxes(title_text="% of goods trade", col=1, rangemode="tozero")

# ---- 5. bilateral balance: row's exports to column minus row's imports from column, both as the row reports
bal = con.sql(f"""select rep, par, sum(v) filter (where ind = '{X}') - sum(v) filter (where ind = '{M}') as b
                  from c where yr = {YEAR} and par in {tuple(ORDER)} group by all""").fetchall()
bl = {(r, p): b for r, p, b in bal}
z_bal = [[bl[(r, p)] / 1e9 if r != p and bl.get((r, p)) is not None else None for p in ORDER] for r in ORDER]
lim = max(abs(v) for row in z_bal for v in row if v is not None)
DIV = [[0, "#c0392b"], [0.25, "#e98b84"], [0.5, "#f0efec"], [0.75, "#86b6ef"], [1, "#1c5cab"]]
bh = go.Figure(go.Heatmap(z=z_bal, x=labels, y=labels, colorscale=DIV, zmin=-lim, zmax=lim, xgap=2, ygap=2,
                          colorbar=dict(title="$bn"),
                          hovertemplate="%{y} with %{x}: %{z:+,.1f}bn<extra></extra>"))
bh.update_layout(template="plotly_white", height=620, margin=dict(l=120, r=20, t=30, b=110),
                 xaxis=dict(title="with", tickangle=-35), yaxis=dict(title="balance of", autorange="reversed"))

# ---- 6. mirror check: what the exporter says it shipped vs what the importer says it received
mir = con.sql(f"""select x.rep as exp, x.par as imp, x.v as xv, m.v as mv, m.v / x.v - 1 as gap
                  from c x join c m on m.rep = x.par and m.par = x.rep and m.yr = x.yr
                  where x.ind = '{X}' and m.ind = '{M}' and x.yr = {YEAR}
                    and x.rep in {tuple(ORDER)} and x.par in {tuple(ORDER)} order by abs(m.v - x.v) desc""").pl()
if mir.height != len(ORDER) * (len(ORDER) - 1):
    raise SystemExit(f"flows: mirror pairs {mir.height}, expected {len(ORDER) * (len(ORDER) - 1)}")
mg = go.Figure()
TICKS = [0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500]
lo = min(mir["xv"].min(), mir["mv"].min()) / 1e9 * 0.8
hi = max(mir["xv"].max(), mir["mv"].max()) / 1e9 * 1.2
mg.add_trace(go.Scatter(x=[lo, hi], y=[lo, hi], mode="lines", line=dict(color=GRAY, width=1, dash="dot"),
                        name="Both sides agree", hoverinfo="skip"))
mg.add_trace(go.Scatter(
    x=mir["xv"] / 1e9, y=mir["mv"] / 1e9, mode="markers", name="One flow",
    marker=dict(size=9, color=["#eb6834" if e == "CHN" or i == "CHN" else "#2a78d6" for e, i in
                               zip(mir["exp"], mir["imp"])], line=dict(color="#fcfcfb", width=1.5)),
    customdata=list(zip([ECON[e] for e in mir["exp"]], [ECON[i] for i in mir["imp"]], mir["gap"] * 100)),
    hovertemplate="%{customdata[0]} to %{customdata[1]}<br>exporter says $%{x:,.1f}bn<br>"
                  "importer says $%{y:,.1f}bn (%{customdata[2]:+.0f}%)<extra></extra>"))
mg.update_layout(template="plotly_white", height=560, margin=dict(l=70, r=20, t=30, b=60), showlegend=False,
                 xaxis=dict(type="log", title="exporter reports, $bn (FOB)", tickvals=TICKS),
                 yaxis=dict(type="log", title="importer reports, $bn (CIF)", tickvals=TICKS))
mir_rows = "".join(
    f"<tr><td>{ECON[e]} to {ECON[i]}</td><td>{xv / 1e9:,.1f}</td><td>{mv / 1e9:,.1f}</td>"
    f"<td>{(mv - xv) / 1e9:+,.1f}</td><td>{g * 100:+.0f}%</td></tr>"
    for e, i, xv, mv, g in mir.head(10).iter_rows())
mir_table = ("<table><thead><tr><th>Flow</th><th>Exporter says, $bn</th><th>Importer says, $bn</th>"
             "<th>Difference, $bn</th><th>Difference, %</th></tr></thead><tbody>" + mir_rows + "</tbody></table>")
med_gap = mir["gap"].median() * 100

# ---- 7. monthly: US goods trade with its largest partners (US-reported), 12-month sums
gaps = con.sql("""select rep, ind, par, count(*) n, min(date) lo, max(date) hi from m group by all
                  having n <> date_diff('month', min(date), max(date)) + 1""").fetchall()
if gaps:
    raise SystemExit(f"flows: monthly series with missing months: {gaps}")
roll = con.sql("""with t as (select par, date, sum(v) v from m where rep = 'USA' group by all)
                  select par, date, sum(v) over w / 1e9 as v, count(*) over w as n from t
                  window w as (partition by par order by date rows between 11 preceding and current row)
                  qualify n = 12 order by par, date""").pl()
mo = go.Figure()
for p in US_PARTNERS:
    d = roll.filter(pl.col("par") == p)
    mo.add_trace(go.Scatter(x=d["date"], y=d["v"], mode="lines", line=dict(color=C[p], width=2), name=NAMES[p],
                            hovertemplate=f"%{{x|%b %Y}}: $%{{y:,.0f}}bn<extra>{NAMES[p]}</extra>"))
us_last = roll["date"].max()
mo.update_layout(template="plotly_white", height=480, hovermode="x unified", margin=dict(l=70, r=20, t=30, b=40),
                 yaxis=dict(title="$bn, exports + imports, past 12 months", rangemode="tozero"),
                 legend=dict(orientation="h", y=1.08, x=0))

# ---- 8. US-China, both sides: what China says it shipped vs what the US says it received
both = con.sql("""with t as (select case when rep = 'CHN' then 'China reports exports to the US'
                                       else 'US reports imports from China' end as s, date, v
                             from m where (rep = 'CHN' and ind = 'XG_FOB_USD') or (rep = 'USA' and ind = 'MG_CIF_USD'
                                                                                  and par = 'CHN'))
                  select s, date, sum(v) over w / 1e9 as v, count(*) over w as n from t
                  window w as (partition by s order by date rows between 11 preceding and current row)
                  qualify n = 12 order by s, date""").pl()
uc = go.Figure()
for s, color in (("China reports exports to the US", C["CHN"]), ("US reports imports from China", C["USA"])):
    d = both.filter(pl.col("s") == s)
    uc.add_trace(go.Scatter(x=d["date"], y=d["v"], mode="lines", line=dict(color=color, width=2), name=s,
                            hovertemplate=f"%{{x|%b %Y}}: $%{{y:,.0f}}bn<extra>{s}</extra>"))
uc.update_layout(template="plotly_white", height=420, hovermode="x unified", margin=dict(l=70, r=20, t=30, b=40),
                 yaxis=dict(title="$bn, past 12 months", rangemode="tozero"), legend=dict(orientation="h", y=1.1, x=0))
cn_last = both["date"].max()
gap_at = lambda dt: (both.filter((pl.col("date") == dt) & (pl.col("s").str.starts_with("China")))["v"][0]
                     - both.filter((pl.col("date") == dt) & (pl.col("s").str.starts_with("US")))["v"][0])
g18, g_now = gap_at(datetime(2018, 12, 1).date()), gap_at(cn_last)


def more(g: float, when: str) -> str:
    who = ("China reported shipping", "the US recorded receiving") if g > 0 else           ("the US recorded receiving", "China reported shipping")
    return f"In the 12 months to {when}, {who[0]} ${abs(g):,.0f}bn more than {who[1]}."

# ---- page
first = True


def div(fig, div_id: str | None = None) -> str:
    global first
    out = pio.to_html(fig, full_html=False, include_plotlyjs=first, config={"displayModeBar": False}, div_id=div_id)
    first = False
    return out


built = datetime.now().strftime("%Y-%m-%d %H:%M")
lis = "".join(f"<li>{f}</li>" for f in facts)
page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Trade Flows</title>
<style>
:root {{ --bg:#ffffff; --fg:#1d1d1b; --muted:#5f5e58; --line:#e3e1da; }}
body {{ background:var(--bg); color:var(--fg); font:15px/1.5 system-ui, "Segoe UI", sans-serif; margin:0; padding:24px 16px 60px; }}
main {{ max-width:1250px; margin:0 auto; }}
h1 {{ font-size:24px; margin:0 0 6px; }}
h2 {{ font-size:19px; margin:38px 0 6px; }}
.muted {{ color:var(--muted); }}
.wrap {{ overflow-x:auto; }}
table {{ border-collapse:collapse; font-size:14px; margin:12px 0; }}
th, td {{ padding:6px 12px; border-bottom:1px solid var(--line); text-align:right; vertical-align:top; }}
th:first-child, td:first-child {{ text-align:left; white-space:nowrap; }}
table.top td, table.top th {{ text-align:left; }}
th {{ font-weight:600; }}
span.usa {{ color:#1c5cab; font-weight:600; }}
span.chn {{ color:#b8441a; font-weight:600; }}
ul.facts li {{ margin:4px 0; }}
.tl {{ display:flex; align-items:center; gap:14px; margin:4px 0 0; }}
.tl input {{ flex:1; }}
.tl span {{ font-weight:600; min-width:3em; }}
.tl button {{ font:inherit; padding:5px 16px; border:1px solid var(--line); background:#fff; border-radius:4px; cursor:pointer; }}
{nav.CSS}
</style></head>
<body>{nav.html("Trade Flows")}<main>
<h1>Trade Flows</h1>
<p class="muted">Goods trade between the US and the 12 other economies of the World page (the euro area as a
whole is left out; Germany, France and Italy are in), from the IMF's trade-by-partner dataset (IMTS),
fetched when the page was built ({built}) and not stored in Pharos. Goods only: the IMF publishes no services trade by
partner, which leaves out much of the US's and the UK's trade. Each country reports its own exports (valued at its
border, FOB) and its own imports (valued including freight and insurance, CIF), so the same flow has two numbers:
the exporter's and the importer's. Unless a chart says otherwise it uses the reporting country's own figure. The IMF
marks every value as mixed, so reported figures and IMF estimates can't be told apart. {YEAR} is the latest year all
13 economies have reported.</p>

<h2>In short</h2>
<ul class="facts">{lis}</ul>

<h2>Who ships to whom, {YEAR}</h2>
<p>Each row is an exporter, each column the country it ships to, as the exporter reports it. Switch to shares to see
how much of each exporter's goods go to each destination; the rest of each row's exports go to countries outside the
13.</p>
<div class="wrap">{div(hm)}</div>

<h2>The same flows, as streams, {YEARS[0]} to {YEAR}</h2>
<p>Every flow among the 13, exporters on the left and importers on the right, one year at a time: press Play, or drag
the slider below the chart to a year. The US's exports are blue and China's orange; hover a stream for its value. The
figure beside each exporter is its net balance with the other 12 that year: plus for a net exporter, minus for a net
importer. Each year fills the
same height, so the picture shows shares of that year's flows; the dollar total above it shows the growth (not
adjusted for inflation). A flow the exporter did not report is simply absent: the IMF has China's exports by partner
only for 1961 and from 1978, the US embargoed China until 1971, and South Korea and China had no relations until 1992. The IMF
keeps East Germany as a separate country, so Germany before 1991 is West Germany.</p>
{div(sk, SK_ID)}
{timelapse}

<h2>Biggest partners, {START} and {YEAR}</h2>
<p>Each economy's five largest partners among all countries, by goods trade (exports plus imports), as a share of its
total goods trade. The US is in blue, China in orange.</p>
<div class="wrap">{top_table}</div>

<h2>The US or China?</h2>
<p>Share of each economy's goods trade (exports plus imports) done with the United States and with China, {START} to
{YEAR}. All panels share one scale.</p>
{div(sm)}

<h2>Who runs a surplus with whom, {YEAR}</h2>
<p>Each row's goods exports to the column country minus its imports from it, both as the row country reports them.
Blue: the row sells more than it buys; red: it buys more.</p>
<div class="wrap">{div(bh)}</div>

<h2>Do both sides agree?</h2>
<p>Each dot is one flow between two of the 13 in {YEAR}: what the exporter says it shipped (across) against what the
importer says it received (up). Imports include freight and insurance, so the importer's figure should run a little
higher, and the dots should sit just above the dotted line. For the median flow the importer's figure is
{med_gap:+.0f}% against the exporter's. Flows to or from
China are orange. Large gaps come from goods routed through a third country (Hong Kong, the Netherlands), from
shipments counted in different years, and from the destination the exporter records not being where the goods end
up. Because the IMF fills some missing values with estimates, possibly from the partner's own figure, a close match
does not prove both countries reported.</p>
{div(mg)}
<p>The ten largest differences in dollars:</p>
<div class="wrap">{mir_table}</div>

<h2>The US's largest partners, month by month</h2>
<p>US goods trade (exports plus imports) with its six largest partners of {us_year}, as the US reports it, summed
over the past 12 months so each point is a full year, to {us_last:%B %Y}.</p>
{div(mo)}

<h2>US-China trade from both sides</h2>
<p>China's goods exports to the US as China reports them, against US imports from China as the US reports them,
each summed over the past 12 months, to {cn_last:%B %Y}. They describe the same goods. {more(g18, "December 2018")} {more(g_now, f"{cn_last:%B %Y}")}</p>
{div(uc)}
</main></body></html>"""
(HERE / "flows.html").write_text(page, encoding="utf-8")
print("wrote", HERE / "flows.html")

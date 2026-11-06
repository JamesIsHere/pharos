"""Industry Code Breakdown: a visual guide to the SIC codes SEC assigns, built from the 2024 companies in Pharos,
and how they relate to NAICS and BEA's industries. Writes industry-codes.html next to this file."""
import sys
from datetime import datetime, timezone
from pathlib import Path

import plotly.graph_objects as go
import plotly.io as pio
import polars as pl

ROOT = Path(__file__).resolve().parents[1]   # explore/
sys.path.insert(0, str(ROOT / "lib"))
import base   # noqa: E402
import nav    # noqa: E402

YR = 2024

# 2-digit major group names, 1987 SIC manual (Claude's knowledge: SEC's data names only 4-digit codes).
MAJOR = {1: "Agricultural production, crops", 2: "Agricultural production, livestock", 7: "Agricultural services",
         8: "Forestry", 9: "Fishing, hunting, trapping", 10: "Metal mining", 12: "Coal mining",
         13: "Oil and gas extraction", 14: "Nonmetallic minerals, except fuels",
         15: "Building construction, general contractors", 16: "Heavy construction, except building",
         17: "Construction, special trade contractors", 20: "Food and kindred products", 21: "Tobacco products",
         22: "Textile mill products", 23: "Apparel", 24: "Lumber and wood products", 25: "Furniture and fixtures",
         26: "Paper and allied products", 27: "Printing and publishing", 28: "Chemicals and allied products",
         29: "Petroleum refining", 30: "Rubber and plastics products", 31: "Leather products",
         32: "Stone, clay, glass, concrete", 33: "Primary metal industries", 34: "Fabricated metal products",
         35: "Industrial machinery and computer equipment", 36: "Electronic and electrical equipment",
         37: "Transportation equipment", 38: "Instruments; medical, optical, photographic goods",
         39: "Miscellaneous manufacturing", 40: "Railroad transportation", 41: "Local and suburban transit",
         42: "Motor freight transportation, warehousing", 43: "US Postal Service", 44: "Water transportation",
         45: "Transportation by air", 46: "Pipelines, except natural gas", 47: "Transportation services",
         48: "Communications", 49: "Electric, gas, sanitary services", 50: "Wholesale trade, durable goods",
         51: "Wholesale trade, nondurable goods", 52: "Building materials, hardware, garden supply",
         53: "General merchandise stores", 54: "Food stores", 55: "Auto dealers, gasoline stations",
         56: "Apparel and accessory stores", 57: "Home furniture and equipment stores", 58: "Eating and drinking places",
         59: "Miscellaneous retail", 60: "Depository institutions", 61: "Nondepository credit institutions",
         62: "Security and commodity brokers", 63: "Insurance carriers", 64: "Insurance agents and brokers",
         65: "Real estate", 67: "Holding and other investment offices", 70: "Hotels and lodging",
         72: "Personal services", 73: "Business services", 75: "Auto repair and parking",
         76: "Miscellaneous repair services", 78: "Motion pictures", 79: "Amusement and recreation services",
         80: "Health services", 81: "Legal services", 82: "Educational services", 83: "Social services",
         84: "Museums, galleries, gardens", 86: "Membership organizations",
         87: "Engineering, accounting, research, management", 88: "Private households", 89: "Services, not elsewhere classified",
         91: "Executive, legislative, general government", 99: "Nonclassifiable establishments"}
DIV_NAME = {d[0]: d[3] for d in base.DIVISIONS}
DIV_COLOR = {"A": "#65a30d", "B": "#78716c", "C": "#ea580c", "D": "#2563eb", "E": "#0891b2", "F": "#7c3aed",
             "G": "#db2777", "H": "#16a34a", "I": "#ca8a04", "J": "#64748b", "K": "#94a3b8"}

y = base.with_grains(base.revenue_year(YR)).filter(pl.col("sic").is_not_null() & (pl.col("sic") > 0))
y = y.with_columns(pl.col("sic_description").str.replace(r"^[A-Za-z ]+-", "").alias("desc4"))  # "Retail-Catalog ..." -> "Catalog ..."


def level(cols):
    return (y.sort("revenue", descending=True).group_by(cols, maintain_order=True)
            .agg(pl.len().alias("n"), (pl.col("revenue").sum() / 1e9).alias("bn"),
                 pl.col("name").first().alias("top"), pl.col("desc4").first().alias("desc4")))


# ---- sunburst: division -> 2-digit -> 3-digit -> 4-digit -------------------------------------------------------
ids, labels, parents, n, bn, hover, colors = [], [], [], [], [], [], []
for r in level(["division"]).iter_rows(named=True):
    ids.append(r["division"]); parents.append(""); labels.append(f"{r['division']} {DIV_NAME[r['division']]}")
    n.append(r["n"]); bn.append(r["bn"]); colors.append(DIV_COLOR[r["division"]])
    hover.append(f"Division {r['division']}: {DIV_NAME[r['division']]}")
for r in level(["division", "sic2"]).iter_rows(named=True):
    ids.append(f"{r['division']}/{r['sic2']:02d}"); parents.append(r["division"])
    name = MAJOR.get(r["sic2"], "")
    labels.append(f"{r['sic2']:02d} {name}"); n.append(r["n"]); bn.append(r["bn"]); colors.append(DIV_COLOR[r["division"]])
    hover.append(f"Major group {r['sic2']:02d}: {name}")
for r in level(["division", "sic2", "sic3"]).iter_rows(named=True):
    ids.append(f"{r['division']}/{r['sic2']:02d}/{r['sic3']:03d}"); parents.append(f"{r['division']}/{r['sic2']:02d}")
    labels.append(f"{r['sic3']:03d}"); n.append(r["n"]); bn.append(r["bn"]); colors.append(DIV_COLOR[r["division"]])
    hover.append(f"Industry group {r['sic3']:03d} (the manual's name is not in SEC's data)")
for r in level(["division", "sic2", "sic3", "sic"]).iter_rows(named=True):
    ids.append(f"{r['division']}/{r['sic2']:02d}/{r['sic3']:03d}/{r['sic']}")
    parents.append(f"{r['division']}/{r['sic2']:02d}/{r['sic3']:03d}")
    labels.append(f"{r['sic']} {r['desc4']}"); n.append(r["n"]); bn.append(r["bn"]); colors.append(DIV_COLOR[r["division"]])
    hover.append(f"Industry {r['sic']}: {r['desc4']}")
tops = {}
for cols, key in ((["division"], lambda r: r["division"]),
                  (["division", "sic2"], lambda r: f"{r['division']}/{r['sic2']:02d}"),
                  (["division", "sic2", "sic3"], lambda r: f"{r['division']}/{r['sic2']:02d}/{r['sic3']:03d}"),
                  (["division", "sic2", "sic3", "sic"], lambda r: f"{r['division']}/{r['sic2']:02d}/{r['sic3']:03d}/{r['sic']}")):
    for r in level(cols).iter_rows(named=True):
        tops[key(r)] = r["top"]
custom = [[h, c, b, tops[i]] for h, c, b, i in zip(hover, n, bn, ids)]
HOVER = ("%{customdata[0]}<br>%{customdata[1]:,} companies, $%{customdata[2]:,.0f}bn revenue"
         "<br>Largest: %{customdata[3]}<extra></extra>")

sun = go.Figure(go.Sunburst(ids=ids, labels=labels, parents=parents, values=bn, branchvalues="total",
                            marker=dict(colors=colors, line=dict(color="white", width=1)), customdata=custom,
                            hovertemplate=HOVER, insidetextorientation="radial", maxdepth=2,
                            textfont=dict(size=11)))
sun.update_layout(height=760, margin=dict(l=10, r=10, t=50, b=10),
                  updatemenus=[dict(type="buttons", direction="right", x=0, y=1.06, xanchor="left", showactive=True,
                                    buttons=[dict(label="Size by revenue", method="restyle", args=[{"values": [bn]}]),
                                             dict(label="Size by number of companies", method="restyle",
                                                  args=[{"values": [n]}])])])

# ---- sankey: SIC division -> Pharos sector ----------------------------------------------------------------------
flow = (y.group_by("division", "sector").agg(pl.len().alias("n"), (pl.col("revenue").sum() / 1e9).alias("bn"))
        .sort("division", "sector"))
divs = [d for d in DIV_NAME if d in flow["division"].to_list()]
secs = (flow.group_by("sector").agg(pl.col("bn").sum()).sort("bn", descending=True)["sector"].to_list())
node_labels = [f"{d} {DIV_NAME[d]}" for d in divs] + secs
src = [divs.index(d) for d in flow["division"]]
dst = [len(divs) + secs.index(s) for s in flow["sector"]]
link_color = [DIV_COLOR[d].replace("#", "rgba(") for d in flow["division"]]
link_color = ["rgba({},{},{},0.35)".format(int(DIV_COLOR[d][1:3], 16), int(DIV_COLOR[d][3:5], 16), int(DIV_COLOR[d][5:7], 16))
              for d in flow["division"]]
sankey = go.Figure(go.Sankey(
    arrangement="snap",
    node=dict(label=node_labels, pad=12, thickness=16,
              color=[DIV_COLOR[d] for d in divs] + ["#9ca3af"] * len(secs)),
    link=dict(source=src, target=dst, value=flow["bn"], color=link_color,
              customdata=flow["n"], hovertemplate="%{source.label} -> %{target.label}<br>$%{value:,.0f}bn, "
                                                  "%{customdata:,} companies<extra></extra>")))
sankey.update_layout(height=640, margin=dict(l=10, r=10, t=50, b=10), font=dict(size=12),
                     updatemenus=[dict(type="buttons", direction="right", x=0, y=1.08, xanchor="left",
                                       buttons=[dict(label="Width by revenue", method="restyle",
                                                     args=[{"link.value": [flow["bn"].to_list()]}]),
                                                dict(label="Width by number of companies", method="restyle",
                                                     args=[{"link.value": [flow["n"].to_list()]}])])])

# ---- Amazon's walk down the ladder ----------------------------------------------------------------------------
amzn = y.filter(pl.col("name") == "AMAZON COM INC").row(0, named=True)
steps = [("Division", f"{amzn['division']}", DIV_NAME[amzn["division"]], pl.col("division") == amzn["division"]),
         ("Major group, 2 digits", f"{amzn['sic2']}", MAJOR[amzn["sic2"]], pl.col("sic2") == amzn["sic2"]),
         ("Industry group, 3 digits", f"{amzn['sic3']}", "Nonstore retailers", pl.col("sic3") == amzn["sic3"]),
         ("Industry, 4 digits", f"{amzn['sic']}", amzn["desc4"], pl.col("sic") == amzn["sic"])]
walk = ""
for i, (lvl, code, name, f) in enumerate(steps):
    g = y.filter(f)
    walk += (f'<div class="step" style="margin-left:{i * 28}px; border-left-color:{DIV_COLOR["G"]}">'
             f'<span class="lvl">{lvl}</span><span class="code">{code}</span><span class="nm">{name}</span>'
             f'<span class="num">{g.height:,} companies, ${g["revenue"].sum() / 1e9:,.0f}bn</span></div>')
walk += (f'<div class="step co" style="margin-left:{4 * 28}px">'
         f'<span class="lvl">Company</span><span class="code"></span><span class="nm">Amazon</span>'
         f'<span class="num">${amzn["revenue"] / 1e9:,.0f}bn</span></div>')


def chart(fig, first):
    return pio.to_html(fig, full_html=False, include_plotlyjs=first, config={"displayModeBar": False})


built = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Industry code breakdown</title>
<style>
:root {{ --bg:#ffffff; --fg:#1d1d1b; --muted:#5f5e58; --line:#e3e1da; --box:#f6f5f1; }}
body {{ background:var(--bg); color:var(--fg); font:15px/1.5 system-ui, "Segoe UI", sans-serif; margin:0; padding:24px 16px 60px; }}
main {{ max-width:1250px; margin:0 auto; }}
h1 {{ font-size:24px; margin:0 0 6px; }}
h2 {{ font-size:18px; margin:0 0 6px; }}
section {{ border-top:1px solid var(--line); padding:22px 0; }}
.muted {{ color:var(--muted); }}
.step {{ display:grid; grid-template-columns: 190px 60px minmax(0,1fr) 230px; gap:10px; align-items:baseline;
         background:var(--box); border-left:5px solid; padding:8px 12px; margin:6px 0; }}
.step.co {{ border-left:5px solid var(--fg); }}
.step .lvl {{ color:var(--muted); font-size:13px; }}
.step .code {{ font-weight:700; font-variant-numeric:tabular-nums; }}
.step .num {{ text-align:right; color:var(--muted); font-variant-numeric:tabular-nums; }}
.systems {{ display:grid; grid-template-columns: repeat(3, minmax(0,1fr)); gap:16px; }}
.sys {{ background:var(--box); padding:14px 16px; }}
.sys h3 {{ font-size:15px; margin:0 0 2px; }}
.sys p {{ margin:0 0 10px; font-size:13px; color:var(--muted); }}
.rung {{ background:var(--bg); border:1px solid var(--line); padding:6px 10px; margin:0 auto 6px; font-size:13px;
         display:flex; justify-content:space-between; gap:8px; }}
.rung b {{ font-variant-numeric:tabular-nums; }}
@media (max-width: 900px) {{ .systems {{ grid-template-columns: 1fr; }}
  .step {{ grid-template-columns: 1fr; margin-left:0 !important; }} .step .num {{ text-align:left; }} }}
{nav.CSS}
</style></head>
<body>{nav.html("Industry Code Breakdown")}<main>
<h1>Industry code breakdown</h1>
<p class="muted">Built {built} from the {y.height:,} companies with {YR} revenue and a SIC code in Pharos (serving
version 20261105T103527Z). SEC's data names only the 4-digit codes; the division and 2-digit names come from the 1987
SIC manual.</p>

<section>
<h2>One company, four levels</h2>
<p class="muted">SEC gives each company one 4-digit SIC code. Dropping a digit moves up a level, and each level holds
more companies. Amazon's code is 5961:</p>
{walk}
</section>

<section>
<h2>Every code in use, as one picture</h2>
<p class="muted">The centre ring is the divisions, the next ring the 2-digit groups. Click a slice to open it down
to 3 and 4 digits; click the centre to go back up. Hover for the company count, revenue and largest company.
Colours follow the division.</p>
{chart(sun, True)}
</section>

<section>
<h2>How SIC divisions map to Pharos's 15 sectors</h2>
<p class="muted">Pharos's sectors (test 7 on the Test page) regroup SIC codes into the 15 sectors BEA uses for GDP
by industry, so company revenue can be set next to the economy. Most divisions flow into one sector; the splits are
where the two systems disagree. Hover a band for its revenue and company count.</p>
{chart(sankey, False)}
</section>

<section>
<h2>Three systems side by side</h2>
<p class="muted">SEC classifies companies with SIC; the Census and BEA classify establishments (each plant, store or
office) with NAICS, which replaced SIC in 1997. The NAICS and BEA counts are approximate and from Claude's knowledge,
not Pharos data.</p>
<div class="systems">
<div class="sys"><h3>SIC (1987)</h3><p>Used by SEC, one code per company. Counts: codes in use in Pharos, {YR}.</p>
<div class="rung" style="width:40%"><span>Division</span><b>{y['division'].n_unique()}</b></div>
<div class="rung" style="width:60%"><span>2-digit</span><b>{y['sic2'].n_unique()}</b></div>
<div class="rung" style="width:80%"><span>3-digit</span><b>{y['sic3'].n_unique()}</b></div>
<div class="rung" style="width:100%"><span>4-digit</span><b>{y['sic'].n_unique()}</b></div>
</div>
<div class="sys"><h3>NAICS</h3><p>Used by the Census and BEA, one code per establishment. Revised every 5 years.</p>
<div class="rung" style="width:36%"><span>Sector, 2-digit</span><b>20</b></div>
<div class="rung" style="width:52%"><span>Subsector, 3-digit</span><b>~100</b></div>
<div class="rung" style="width:68%"><span>Industry group, 4-digit</span><b>~310</b></div>
<div class="rung" style="width:84%"><span>Industry, 5-digit</span><b>~690</b></div>
<div class="rung" style="width:100%"><span>National industry, 6-digit</span><b>~1,000</b></div>
</div>
<div class="sys"><h3>BEA GDP by industry</h3><p>Groupings of NAICS codes at which BEA publishes value added and
gross output.</p>
<div class="rung" style="width:40%"><span>Sectors (annual)</span><b>15</b></div>
<div class="rung" style="width:60%"><span>Quarterly release</span><b>22</b></div>
<div class="rung" style="width:80%"><span>Summary (annual, from 1997)</span><b>71</b></div>
<div class="rung" style="width:100%"><span>Detail (every 5 years)</span><b>~400</b></div>
</div>
</div>
</section>
</main></body></html>"""

out = ROOT / "codes" / "industry-codes.html"
out.write_text(html, encoding="utf-8")
print("wrote", out)

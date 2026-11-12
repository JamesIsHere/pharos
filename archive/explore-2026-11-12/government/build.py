"""Government: how much each of the 13 economies of the World page spends, taxes and borrows, as % of GDP.

Quick lane (two lanes, CLAUDE.md): fetched from the IMF DataMapper API (imf.org/external/datamapper, no key) when the
page is built; nothing is stored. The series are from the IMF's Public Finances in Modern History dataset: total
government expenditure (exp), revenue (rev) and gross public debt (d), each % of GDP, annual. The balance is revenue
minus expenditure. One source for every year and country, so the time lapse has no splice; the Fiscal Monitor's
2025 figures are not mixed in (for South Korea its spending figure is a different, larger coverage, see the caption).
Writes government.html next to this file."""
import json
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

import plotly.graph_objects as go
import plotly.io as pio

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "lib"))
import nav  # noqa: E402

API = "https://www.imf.org/external/datamapper/api/v1/"
ECON = {"USA": "United States", "CAN": "Canada", "MEX": "Mexico", "BRA": "Brazil", "GBR": "United Kingdom",
        "DEU": "Germany", "FRA": "France", "ITA": "Italy", "CHN": "China", "JPN": "Japan", "KOR": "South Korea",
        "IND": "India", "AUS": "Australia"}
ORDER = list(ECON)
# The dataset switches what it counts for several economies before 1980: spending and revenue both jump 7-23 points
# of GDP in one year (Australia 1960, Canada 1961, Brazil 1962, Italy 1968, Germany 1970, France 1978), and US
# revenue jumps from 15.6 to 28.1 in 1960 while spending does not. Measured 2026-11-08; the time lapse starts after
# the last of them, and the check below stops the build if a switch shows up inside the window.
START = 1980
JUMP = 5   # points of GDP in one year, in spending and revenue together
# Jumps inside the window that are not a switch. Brazil 2001: the return from a one-year dip in 2000 (spending 39.6,
# 34.5, 39.8 in 1999-2001), not a new level.
KNOWN = {("BRA", 2001)}
C = {"USA": "#2a78d6", "CHN": "#eb6834"}
GRAY, GREEN, INK, MUTED, SURFACE = "#b5b4ae", "#1baf7a", "#0b0b0b", "#5f5e58", "#fcfcfb"


def dm(ind: str, countries=ORDER) -> dict:
    """{country: {year: value}} for one DataMapper indicator. An unknown indicator comes back without 'values'."""
    # No User-Agent header: the DataMapper answers 403 to a custom one (tested 2026-11-08), 200 to urllib's default.
    req = urllib.request.Request(API + ind + "/" + "/".join(countries))
    with urllib.request.urlopen(req, timeout=120) as r:
        body = json.load(r)
    if "values" not in body:
        raise SystemExit(f"government: the IMF DataMapper has no values for {ind}")
    out = {c: {int(y): v for y, v in s.items() if v is not None} for c, s in body["values"][ind].items()}
    missing = [c for c in countries if not out.get(c)]
    if missing:
        raise SystemExit(f"government: {ind} has nothing for {missing}")
    print(f"{ind}: {sum(len(s) for s in out.values()):,} values")
    return out


EXP, REV, DEBT = dm("exp"), dm("rev"), dm("d")
# The latest year all 13 have both spending and revenue: the time lapse ends there.
YEAR = max(y for y in EXP["USA"] if all(y in EXP[c] and y in REV[c] for c in ORDER))
if YEAR < 2024:
    raise SystemExit(f"government: the latest year all 13 report spending and revenue is {YEAR}")
# Units check: US spending in 2020 (the pandemic year) was in the 40s as % of GDP, not a share or a level.
if not 40 < EXP["USA"][2020] < 50:
    raise SystemExit(f"government: US spending 2020 is {EXP['USA'][2020]}, expected 40-50 (% of GDP)")
YEARS = list(range(START, YEAR + 1))
switches = [(c, y) for c in ORDER for y in YEARS
            if all(y in s[c] and y - 1 in s[c] and abs(s[c][y] - s[c][y - 1]) >= JUMP for s in (EXP, REV))
            and (c, y) not in KNOWN]
if switches:
    raise SystemExit(f"government: spending and revenue both jump {JUMP}+ points in one year: {switches}")
# The US charts run from 1929; revenue before 1960 counts less of government than spending does. The marker on the
# chart stands only while the jump is there.
US_BREAK = 1960
if not REV["USA"][US_BREAK] - REV["USA"][US_BREAK - 1] >= 2 * JUMP:
    raise SystemExit(f"government: US revenue {US_BREAK - 1}-{US_BREAK} no longer jumps; revisit the chart marker")

# South Korea's spending against the Fiscal Monitor's general government figure, for the caption; the other 12
# economies' gap is measured too, so the caption's claim that they agree is a number, not a hope.
FM = dm("G_X_G01_GDP_PT")
gap = {c: EXP[c][YEAR] - FM[c][YEAR] for c in ORDER if YEAR in FM.get(c, {})}
if "KOR" not in gap:
    raise SystemExit(f"government: the Fiscal Monitor has no {YEAR} spending for South Korea")
other_gap = max(abs(v) for c, v in gap.items() if c != "KOR")
print(f"PFMH minus Fiscal Monitor spending {YEAR}: " + ", ".join(f"{c} {v:+.1f}" for c, v in gap.items()))

# ---- time lapse: spending across, balance up, debt as dot area
MAX_PX = 64
DEBT_MAX = max(v for c in ORDER for y, v in DEBT[c].items() if START <= y <= YEAR)
SIZEREF = 2 * DEBT_MAX / MAX_PX ** 2
NO_DEBT = 9   # a hollow ring of this diameter marks a year with spending and revenue but no debt figure


def frame(yr: int) -> dict:
    # Largest debt drawn first, so a small dot sitting on a large one stays on top and hoverable.
    pts = sorted((c for c in ORDER if yr in EXP[c] and yr in REV[c]), key=lambda c: -DEBT[c].get(yr, 0))
    debt = [DEBT[c].get(yr) for c in pts]
    fill = [C.get(c, GRAY) if d is not None else "rgba(0,0,0,0)" for c, d in zip(pts, debt)]
    t = go.Scatter(
        x=[EXP[c][yr] for c in pts], y=[REV[c][yr] - EXP[c][yr] for c in pts], mode="markers+text",
        text=[ECON[c] for c in pts], textposition="top center", textfont=dict(size=12, color=INK),
        marker=dict(size=[d if d is not None else (NO_DEBT ** 2) * SIZEREF / 2 for d in debt], sizemode="area",
                    sizeref=SIZEREF, sizemin=3, color=fill, opacity=0.85,
                    line=dict(width=2,
                              color=[SURFACE if d is not None else C.get(c, MUTED) for c, d in zip(pts, debt)])),
        customdata=[[ECON[c], f"{d:.0f}% of GDP" if d is not None else "not in the dataset"]
                    for c, d in zip(pts, debt)],
        hovertemplate="<b>%{customdata[0]}</b><br>spending %{x:.1f}% of GDP<br>"
                      "revenue minus spending %{y:+.1f}%<br>debt %{customdata[1]}<extra></extra>")
    absent = [ECON[c] for c in ORDER if c not in pts]
    ann = [dict(text=f"<b>{yr}</b>", x=0.99, y=0.03, xref="paper", yref="paper", xanchor="right", yanchor="bottom",
                showarrow=False, font=dict(size=56, color="#d6d4cd")),
           dict(text=("Not in the dataset this year: " + ", ".join(absent)) if absent else "", x=0, y=1.0,
                xref="paper", yref="paper", xanchor="left", yanchor="bottom", showarrow=False,
                font=dict(size=12, color=MUTED))]
    return {"data": [t.to_plotly_json()], "ann": ann}


FRAMES = {y: frame(y) for y in YEARS}
lo_bal = min(REV[c][y] - EXP[c][y] for c in ORDER for y in YEARS if y in EXP[c] and y in REV[c])
hi_bal = max(REV[c][y] - EXP[c][y] for c in ORDER for y in YEARS if y in EXP[c] and y in REV[c])
hi_exp = max(EXP[c][y] for c in ORDER for y in YEARS if y in EXP[c] and y in REV[c])
dots = go.Figure(data=[go.Scatter(**{k: v for k, v in FRAMES[YEAR]["data"][0].items() if k != "type"})])
dots.update_layout(
    template="plotly_white", height=640, margin=dict(l=80, r=20, t=40, b=60), showlegend=False,
    annotations=FRAMES[YEAR]["ann"],
    xaxis=dict(title="government spending, % of GDP", range=[0, hi_exp + 6], zeroline=False),
    yaxis=dict(title="revenue minus spending, % of GDP<br>(below 0: deficit, above: surplus)",
               range=[lo_bal - 2.5, hi_bal + 2.5], zeroline=True, zerolinecolor="#8a8984", zerolinewidth=1))
DOTS_ID = "dots"
timelapse = f"""<div class="tl"><button id="tl-play" type="button">Play</button>
<input id="tl-year" type="range" min="{YEARS[0]}" max="{YEARS[-1]}" step="1" value="{YEAR}" aria-label="Year">
<span id="tl-label">{YEAR}</span></div>
<script>
(() => {{
  const F = {json.dumps(FRAMES, default=str)};
  const div = document.getElementById("{DOTS_ID}"), range = document.getElementById("tl-year"),
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
    timer = setInterval(() => {{ i += 1; if (i >= years.length) return stop(); show(years[i]); }}, 300);
  }});
  window.showYear = show;   // for render checks
}})();
</script>"""

# ---- the US over its full history in the dataset
us0 = min(EXP["USA"])
uy = [y for y in range(us0, YEAR + 1) if y in EXP["USA"] and y in REV["USA"]]
if len(uy) != YEAR - us0 + 1:
    raise SystemExit(f"government: US spending or revenue has gaps between {us0} and {YEAR}")
us = go.Figure()
for name, s, color in (("Spending", EXP["USA"], C["USA"]), ("Revenue", REV["USA"], GREEN)):
    us.add_trace(go.Scatter(x=uy, y=[s[y] for y in uy], mode="lines", name=name, line=dict(color=color, width=2),
                            hovertemplate=f"%{{x}}: %{{y:.1f}}% of GDP<extra>{name}</extra>"))
us.add_vline(x=US_BREAK - 0.5, line=dict(color=MUTED, width=1, dash="dot"))
us.add_annotation(x=US_BREAK - 0.5, y=1, yref="paper", xanchor="right", yanchor="top", showarrow=False,
                  text=f"before {US_BREAK}, revenue counts less of government than spending does ",
                  font=dict(size=12, color=MUTED))
us.update_layout(template="plotly_white", height=420, hovermode="x unified", margin=dict(l=70, r=20, t=30, b=40),
                 yaxis=dict(title="% of GDP", rangemode="tozero"), legend=dict(orientation="h", y=1.1, x=0))
dy = [y for y in range(us0, YEAR + 1) if y in DEBT["USA"]]
usd = go.Figure(go.Scatter(x=dy, y=[DEBT["USA"][y] for y in dy], mode="lines", line=dict(color=C["USA"], width=2),
                           hovertemplate="%{x}: %{y:.0f}% of GDP<extra>Gross debt</extra>"))
usd.update_layout(template="plotly_white", height=360, hovermode="x unified", margin=dict(l=70, r=20, t=30, b=40),
                  yaxis=dict(title="gross debt, % of GDP", rangemode="tozero"))
peak = max(uy, key=lambda y: EXP["USA"][y])
worst = min((y for y in uy if y >= US_BREAK), key=lambda y: REV["USA"][y] - EXP["USA"][y])

# ---- page
first = True


def div(fig, div_id: str | None = None) -> str:
    global first
    out = pio.to_html(fig, full_html=False, include_plotlyjs=first, config={"displayModeBar": False}, div_id=div_id)
    first = False
    return out


built = datetime.now().strftime("%Y-%m-%d %H:%M")
page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Government</title>
<style>
:root {{ --bg:#ffffff; --fg:#1d1d1b; --muted:#5f5e58; --line:#e3e1da; }}
body {{ background:var(--bg); color:var(--fg); font:15px/1.5 system-ui, "Segoe UI", sans-serif; margin:0; padding:24px 16px 60px; }}
main {{ max-width:1250px; margin:0 auto; }}
h1 {{ font-size:24px; margin:0 0 6px; }}
h2 {{ font-size:19px; margin:38px 0 6px; }}
.muted {{ color:var(--muted); }}
.tl {{ display:flex; align-items:center; gap:14px; margin:4px 0 0; }}
.tl input {{ flex:1; }}
.tl span {{ font-weight:600; min-width:3em; }}
.tl button {{ font:inherit; padding:5px 16px; border:1px solid var(--line); background:#fff; border-radius:4px; cursor:pointer; }}
{nav.CSS}
</style></head>
<body>{nav.html("Government")}<main>
<h1>Government</h1>
<p class="muted">How much the governments of the 13 economies of the World page spend, collect and owe, each as a
percentage of the economy's GDP, from the IMF's Public Finances in Modern History dataset, fetched when the page was
built ({built}) and not stored in Pharos. It runs to {YEAR}. Spending is everything the government pays out, including
transfers such as pensions and the interest on its debt, so it is not the government's share of GDP: GDP counts only
what the government itself buys and produces, while transfers are spent by the people who receive them. Revenue
minus spending is the balance; a deficit is the part of spending paid for by borrowing.</p>

<h2>Spending, borrowing and debt, {START} to {YEAR}</h2>
<p>One dot per economy, one year at a time: press Play, or drag the slider below the chart to a year. Further right,
the government spends more of GDP; lower down, it borrows more of what it spends. Each dot's area is the government's
gross debt as a percentage of GDP. The US is blue, China orange. A hollow ring is a year with spending and revenue
but no debt figure; an economy missing from a year is named above the chart. South Korea's spending here
({EXP["KOR"][YEAR]:.1f}% of GDP in {YEAR}) is well below the IMF's figure for its general government in the Fiscal
Monitor ({FM["KOR"][YEAR]:.1f}%), so its dot sits further left than a like-for-like comparison would put it; for the
other 12 the two sources agree within {other_gap:.1f} points of GDP in {YEAR}. The dataset goes back further, but
before {START} it changes what it counts for several of these economies: in one year spending and revenue both jump
by 7 to 23 points of GDP (Australia 1960, Canada 1961, Brazil 1962, Italy 1968, Germany 1970, France 1978), so earlier
years would not compare.</p>
{div(dots, DOTS_ID)}
{timelapse}

<h2>The United States, {us0} to {YEAR}</h2>
<p>US government spending and revenue as a percentage of GDP; where spending is above revenue the gap is the
deficit. Spending peaked at {EXP["USA"][peak]:.1f}% in {peak}. Before {US_BREAK} the dataset's revenue covers less of
government than its spending does (revenue jumps from {REV["USA"][US_BREAK - 1]:.1f}% to {REV["USA"][US_BREAK]:.1f}%
of GDP in {US_BREAK}, while spending barely moves), so the gap between the lines before then overstates the
deficit. Since {US_BREAK} the largest deficit was {EXP["USA"][worst] - REV["USA"][worst]:.1f}% of GDP, in
{worst}.</p>
{div(us)}
<p>US gross government debt as a percentage of GDP.</p>
{div(usd)}
</main></body></html>"""
(HERE / "government.html").write_text(page, encoding="utf-8")
print("wrote", HERE / "government.html")

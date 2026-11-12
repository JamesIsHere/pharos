"""Exploratory charts (archived 2026-11-12): WEO GDP and unemployment, SEC incorporation, joined through config/countries.csv.
Run from the pharos root: uv run --with matplotlib python archive/countries-2026-11-12/charts.py"""
import base64
import csv
import io
import sys
import xml.etree.ElementTree as XT
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

ROOT = Path("C:/Users/james/Desktop/workshop/pharos")
HERE = Path(__file__).parent
sys.path.insert(0, str(ROOT / "scaffold"))
from common import CSS, esc  # noqa: E402

AXIS_START, AXIS_END, LAST_ACTUAL = 2000, 2031, 2025
COLORS = ["#2563eb", "#dc2626", "#16a34a", "#9333ea", "#ea580c", "#0891b2", "#4b5563", "#ca8a04"]
plt.rcParams.update({"font.family": "Segoe UI", "font.size": 11, "axes.spines.top": False,
                     "axes.spines.right": False, "axes.grid": True, "grid.color": "#e5e7eb", "grid.linewidth": .8})

# ---- data ----
cty = {r["code"]: r for r in csv.DictReader(open(ROOT / "config/countries.csv", encoding="utf-8"))}
by_imf = {r["imf_code"]: r["code"] for r in cty.values() if r["imf_code"]}
name = {k: r["name"] for k, r in cty.items()}
SHORT = {"USA": "United States", "CHN": "China", "KOR": "Korea", "RUS": "Russia", "TUR": "Turkiye", "IRN": "Iran",
         "EGY": "Egypt", "VGB": "British Virgin Is.", "CYM": "Cayman Islands", "MHL": "Marshall Islands",
         "HKG": "Hong Kong", "GBR": "United Kingdom", "ARE": "UAE", "SAU": "Saudi Arabia", "NLD": "Netherlands"}


def nm(c):
    return SHORT.get(c, name[c])


root = XT.parse(HERE / "inputs" / "weo.xml").getroot()
gdp, lur, world, lur_def = {}, {}, {}, {}
for e in root.iter():
    t = e.tag.rsplit("}", 1)[-1]
    if t == "Series":
        obs = {int(o.get("TIME_PERIOD")): float(o.get("OBS_VALUE")) for o in e if o.tag.endswith("Obs")}
        ind, c = e.get("INDICATOR"), e.get("COUNTRY")
        if c == "G001" and ind == "NGDPD":
            world = obs
        elif c in by_imf:
            (gdp if ind == "NGDPD" else lur)[by_imf[c]] = obs
    elif t == "Group" and e.get("INDICATOR") == "LUR" and e.get("COUNTRY") in by_imf:
        lur_def[by_imf[e.get("COUNTRY")]] = e.get("LABOR_SECTOR_EMPLOYMENT_TYPE") or ""
if not world or len(gdp) < 190:
    raise SystemExit("charts: WEO file missing world GDP or countries")

con = duckdb.connect(str(ROOT / "data/catalog.duckdb"), read_only=True)
SECJOIN = """WITH s AS (
  SELECT cik, upper(state_of_incorporation) inc, upper(json_extract_string(addresses,'$.business.stateOrCountry')) hq
  FROM serving_submission_details WHERE exchanges IS NOT NULL AND exchanges <> '[]')
SELECT s.cik, ki.country inc_c, ki.subdivision inc_sub, kh.country hq_c, kh.subdivision hq_sub
FROM s
LEFT JOIN read_csv('config/sec_country_codes.csv', all_varchar=true) ki ON ki.sec_code = s.inc
LEFT JOIN read_csv('config/sec_country_codes.csv', all_varchar=true) kh ON kh.sec_code = s.hq"""
firms = con.execute(SECJOIN).fetchall()
inc_n = Counter(r[1] for r in firms if r[1])


# ---- helpers ----
def save(fig):
    b = io.BytesIO()
    fig.savefig(b, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(b.getvalue()).decode()


def fig(h=5.2):
    return plt.subplots(figsize=(12.5, h))


def ts_axis(ax):
    ax.set_xlim(AXIS_START, AXIS_END + .5)
    ax.set_xticks(range(AXIS_START, AXIS_END + 1, 5))
    ax.axvspan(LAST_ACTUAL + .5, AXIS_END + .5, color="#f3f4f6", zorder=0)
    ax.text(LAST_ACTUAL + .8, ax.get_ylim()[1], "IMF projection", va="top", color="#6b7280", fontsize=9)


def series(d, start=AXIS_START):
    ys = sorted(y for y in d if start <= y <= AXIS_END)
    return ys, [d[y] for y in ys]


tn = FuncFormatter(lambda v, _: f"{v / 1e12:,.0f}T" if v >= 1e12 else f"{v / 1e9:,.0f}B")
CAPTIONS, charts = {}, defaultdict(list)


def add(tab, png, cap):
    charts[tab].append((png, cap))


# ---- GDP ----
f, ax = fig()
for i, c in enumerate(["USA", "CHN", "DEU", "JPN", "IND", "GBR"]):
    ax.plot(*series(gdp[c]), color=COLORS[i], lw=2.2, label=nm(c))
ax.yaxis.set_major_formatter(tn)
ts_axis(ax)
ax.legend(frameon=False, ncol=6, loc="upper left")
add("GDP", save(f), "GDP in current US dollars, six largest economies of 2025, 2000-2031 (IMF WEO, NGDPD).")

top10 = sorted(gdp, key=lambda c: -gdp[c].get(LAST_ACTUAL, 0))[:10]
f, ax = fig()
ys = list(range(AXIS_START, AXIS_END + 1))
shares = [[gdp[c].get(y, 0) / world[y] * 100 for y in ys] for c in top10]
rest = [100 - sum(s[i] for s in shares) for i in range(len(ys))]
ax.stackplot(ys, *shares, rest, labels=[nm(c) for c in top10] + ["All other countries"],
             colors=COLORS + ["#a3a3a3", "#d4d4d4", "#ececec"][:len(top10) - len(COLORS) + 1] + ["#f5f5f5"], alpha=.95)
ax.set_ylim(0, 100)
ax.set_ylabel("% of world GDP")
ts_axis(ax)
ax.legend(frameon=False, ncol=1, loc="center left", bbox_to_anchor=(1.0, .5), fontsize=9)
add("GDP", save(f), "Share of world GDP (IMF G001 World), ten largest economies of 2025 and all others, current US "
                    "dollars, 2000-2031.")

f, ax = fig()
conc = {n: [sum(sorted((gdp[c].get(y, 0) for c in gdp), reverse=True)[:n]) / world[y] * 100 for y in ys]
        for n in (1, 5, 10, 20)}
for i, (n, v) in enumerate(conc.items()):
    ax.plot(ys, v, color=COLORS[i], lw=2.2, label=f"Largest {n}")
ax.set_ylabel("% of world GDP")
ts_axis(ax)
ax.legend(frameon=False, ncol=4, loc="upper left")
add("GDP", save(f), "Share of world GDP held by the largest 1, 5, 10 and 20 economies in each year, current US dollars, "
                    "2000-2031.")

f, ax = fig(6.5)
r00 = sorted(gdp, key=lambda c: -gdp[c].get(2000, 0))
r25 = sorted(gdp, key=lambda c: -gdp[c].get(LAST_ACTUAL, 0))
pick = list(dict.fromkeys(r25[:15] + r00[:15]))
for c in pick:
    a, b = r00.index(c) + 1, r25.index(c) + 1
    col = "#16a34a" if b < a else "#dc2626" if b > a else "#6b7280"
    ax.plot([0, 1], [a, b], color=col, lw=2, marker="o")
    ax.text(-.02, a, f"{nm(c)} {a}", ha="right", va="center", fontsize=9)
    ax.text(1.02, b, f"{b} {nm(c)}", ha="left", va="center", fontsize=9)
ax.set_xlim(-.45, 1.45)
ax.set_ylim(max(max(r00.index(c), r25.index(c)) for c in pick) + 2, 0)
ax.set_xticks([0, 1], ["2000", str(LAST_ACTUAL)])
ax.set_yticks([])
ax.grid(False)
ax.spines["left"].set_visible(False)
add("GDP", save(f), f"GDP rank in 2000 and {LAST_ACTUAL}, current US dollars, for every country in the top 15 in "
                    "either year; green rose, red fell.")

f, ax = fig()
v = sorted(gdp[c][LAST_ACTUAL] / 1e9 for c in gdp if LAST_ACTUAL in gdp[c])
bins = [10 ** (x / 4) for x in range(-4, 23)]
ax.hist(v, bins=bins, color=COLORS[0], edgecolor="white")
ax.set_xscale("log")
ax.xaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{x:,.0f}B" if x >= 1 else f"{x:.1f}B"))
ax.set_xlabel(f"GDP {LAST_ACTUAL}, US$ billions (log scale)")
ax.set_ylabel("countries")
add("GDP", save(f), f"Number of countries by {LAST_ACTUAL} GDP in current US dollars, log scale, {len(v)} countries "
                    "with a value.")

# ---- Unemployment ----
f, ax = fig()
for i, c in enumerate(["USA", "DEU", "JPN", "FRA", "ESP", "GBR", "ZAF"]):
    if c in lur:
        ax.plot(*series(lur[c]), color=COLORS[i], lw=2.2, label=nm(c))
ax.set_ylabel("% of labour force")
ts_axis(ax)
ax.legend(frameon=False, ncol=7, loc="upper left", fontsize=10)
add("Unemployment", save(f), "Unemployment rate, seven economies, 2000-2031 (IMF WEO, LUR), each on the IMF's "
                             "stated definition for that country.")

f, ax = fig(6.2)
big = [c for c in sorted(gdp, key=lambda c: -gdp[c].get(LAST_ACTUAL, 0)) if c in lur and LAST_ACTUAL in lur[c]][:25]
big = sorted(big, key=lambda c: lur[c][LAST_ACTUAL])
def dcol(c):
    d = lur_def.get(c, "")
    return "#2563eb" if "ILO" in d else "#ea580c" if d == "National definition" else "#9ca3af"


cols = [dcol(c) for c in big]
ax.barh([nm(c) for c in big], [lur[c][LAST_ACTUAL] for c in big], color=cols)
ax.set_xlabel(f"Unemployment rate {LAST_ACTUAL}, %")
ax.grid(axis="y", visible=False)
from matplotlib.patches import Patch  # noqa: E402

ax.legend(handles=[Patch(color="#2563eb", label="Harmonized ILO definition"),
                   Patch(color="#ea580c", label="National definition"),
                   Patch(color="#9ca3af", label="Other label (OECD, Other, n/a)")], frameon=False, loc="lower right")
add("Unemployment", save(f), f"Unemployment rate in {LAST_ACTUAL} for the 25 largest economies that have one, colored "
                             "by the IMF's definition label.")

f, ax = fig(3.6)
d = Counter(lur_def.get(c, "") or "no label" for c in lur).most_common()[::-1]
ax.barh([k for k, _ in d], [n for _, n in d], color=COLORS[1])
for i, (k, n) in enumerate(d):
    ax.text(n, i, f" {n}", va="center")
ax.set_xlabel("countries")
ax.grid(axis="y", visible=False)
add("Unemployment", save(f), f"Countries with an unemployment series by the IMF's definition label, {len(lur)} "
                             "countries.")

f, ax = fig(6.2)
chg = {c: lur[c][LAST_ACTUAL] - lur[c][2019] for c in lur if 2019 in lur[c] and LAST_ACTUAL in lur[c]}
ends = sorted(chg, key=chg.get)
sel = ends[:12] + ends[-12:]
ax.barh([nm(c) for c in sel], [chg[c] for c in sel], color=["#16a34a" if chg[c] < 0 else "#dc2626" for c in sel])
ax.axvline(0, color="#111", lw=.8)
ax.set_xlabel(f"Change in unemployment rate, 2019 to {LAST_ACTUAL}, percentage points")
ax.grid(axis="y", visible=False)
add("Unemployment", save(f), f"Change in unemployment rate from 2019 to {LAST_ACTUAL}: the 12 largest falls and 12 "
                             f"largest rises among {len(chg)} countries with both years.")

# ---- Companies (SEC incorporation, through the country key) ----
f, ax = fig(6)
top = [c for c, _ in inc_n.most_common() if c != "USA"][:20][::-1]
ax.barh([nm(c) for c in top], [inc_n[c] for c in top], color=COLORS[0])
for i, c in enumerate(top):
    ax.text(inc_n[c], i, f" {inc_n[c]}", va="center", fontsize=9)
ax.set_xlabel("listed companies")
ax.grid(axis="y", visible=False)
add("Companies", save(f), f"Exchange-listed SEC filers by country of incorporation, outside the US, top 20; the US "
                          f"has {inc_n['USA']:,}.")

st = Counter(r[2] for r in firms if r[1] == "USA" and r[2])
f, ax = fig(5.6)
topst = [s for s, _ in st.most_common(15)][::-1]
ax.barh(topst, [st[s] for s in topst], color=COLORS[2])
for i, s in enumerate(topst):
    ax.text(st[s], i, f" {st[s]:,}", va="center", fontsize=9)
ax.set_xlabel("listed companies")
ax.grid(axis="y", visible=False)
add("Companies", save(f), f"Exchange-listed SEC filers incorporated in the US, by state of incorporation, top 15 of "
                          f"{sum(st.values()):,} with a state.")

havens = ["CYM", "VGB", "BMU", "MHL", "ISR", "CAN", "GBR", "IRL"]
hq_top = Counter(r[3] for r in firms if r[1] in havens and r[3] and r[3] != r[1])
keep = [c for c, _ in hq_top.most_common(6)]
f, ax = fig(5.6)
left = [0] * len(havens)
CATS = ["same"] + keep + ["other", "blank"]
CCOL = ["#111827"] + COLORS[:len(keep)] + ["#d4d4d4", "#f3f4f6"]
LAB = {"same": "same as incorporation", "other": "another country", "blank": "no country in address (blank or XX)"}


def cat(r):
    if not r[3]:
        return "blank"
    if r[3] == r[1]:
        return "same"
    return r[3] if r[3] in keep else "other"


for i, h in enumerate(CATS):
    vals = []
    for inc in havens:
        rows = [r for r in firms if r[1] == inc]
        vals.append(sum(1 for r in rows if cat(r) == h) / len(rows) * 100)
    ax.barh([f"{nm(c)} ({inc_n[c]})" for c in havens], vals, left=left, color=CCOL[i], label=LAB.get(h) or nm(h),
            edgecolor="white", linewidth=.5)
    left = [a + b for a, b in zip(left, vals)]
ax.invert_yaxis()
ax.set_xlim(0, 100)
ax.set_xlabel("% of the country's listed incorporations, by headquarters country")
ax.legend(frameon=False, ncol=4, loc="upper center", bbox_to_anchor=(.5, -.12))
ax.grid(axis="y", visible=False)
add("Companies", save(f), "Where companies incorporated in eight jurisdictions have their business address: share of "
                          "each jurisdiction's exchange-listed SEC filers (count in brackets), by the country of its business address.")

f, ax = fig(6.5)
pts = [(gdp[c][LAST_ACTUAL] / 1e9, inc_n[c], c) for c in inc_n if c in gdp and LAST_ACTUAL in gdp[c]]
ax.scatter([p[0] for p in pts], [p[1] for p in pts], s=36, color=COLORS[0], alpha=.75)
for x, y, c in pts:
    if y >= 30 or x >= 2500:
        ax.annotate(nm(c), (x, y), xytext=(4, 3), textcoords="offset points", fontsize=9)
ax.set_xscale("log")
ax.set_yscale("log")
ax.set_xlabel(f"GDP {LAST_ACTUAL}, US$ billions (IMF WEO, log scale)")
ax.set_ylabel("listed companies incorporated there (log scale)")
add("Companies", save(f), f"Exchange-listed SEC filers by country of incorporation against that country's {LAST_ACTUAL} "
                          f"GDP, {len(pts)} countries; the SEC code joins to the IMF code through config/countries.csv.")

# ---- page ----
built = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M ET")
tabs = list(charts)
nav = "".join(f'<a href="#" data-t="{t}"{" class=on" if i == 0 else ""}>{t}</a>' for i, t in enumerate(tabs))
secs = "".join(
    f'<section id="{t}"{"" if i == 0 else " hidden"}>'
    + "".join(f'<figure><img src="data:image/png;base64,{p}" alt=""><figcaption>{esc(c)}</figcaption></figure>'
              for p, c in charts[t]) + "</section>" for i, t in enumerate(tabs))
html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Charts</title>
<style>{CSS}
.tabs {{ display:flex; gap:4px; border-bottom:1px solid var(--line); margin:6px 0 14px; }}
.tabs a {{ padding:8px 12px; color:var(--muted); text-decoration:none; border-bottom:3px solid transparent; }}
.tabs a.on {{ color:var(--fg); font-weight:600; border-bottom-color:#16a34a; }}
figure {{ margin:0 0 30px; }} figure img {{ width:100%; max-width:1300px; display:block; }}
figcaption {{ color:var(--muted); margin-top:4px; }}
</style></head>
<body><nav><b>COUNTRIES</b><a href="../../scaffold/archive.html">Scaffold</a><a href="country_key.html">Data</a><a class=on href="charts.html">Charts</a><a href="trusted.html">Trusted</a></nav><main>
<div class="head"><h1>Charts</h1><div class="stamps"><span>Built {built}</span>
<span>WEO fetched {datetime.fromtimestamp((HERE / 'inputs' / 'weo.xml').stat().st_mtime, ZoneInfo('America/New_York')):%Y-%m-%d %H:%M} ET</span></div></div>
<div class=tabs>{nav}</div>{secs}</main>
<script>document.querySelectorAll('.tabs a').forEach(a => a.onclick = e => {{ e.preventDefault();
document.querySelectorAll('.tabs a').forEach(x => x.classList.toggle('on', x === a));
document.querySelectorAll('section').forEach(s => s.hidden = s.id !== a.dataset.t); }});</script></body></html>"""
(HERE / "charts.html").write_text(html, encoding="utf-8")
print("charts:", {t: len(v) for t, v in charts.items()})

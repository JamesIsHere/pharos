"""World: the 56 FRED series of milestone W (D142, docs/plan.md) for 12 economies and the euro area, one grid of
small charts per measure, with the latest values in a table on top.

Quick lane (two lanes, CLAUDE.md): the series are fetched from FRED when the page is built, today's values only,
and nothing is stored; the nightly lands them with every vintage once W step 3 is committed. The US reference
lines (real GDP, CPI) come from Pharos (today's vintage). Writes world.html next to this file."""
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path

import duckdb
import plotly.graph_objects as go
import plotly.io as pio
import polars as pl
from plotly.subplots import make_subplots

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "lib"))
import base  # noqa: E402
import nav  # noqa: E402

KEY = os.environ["FRED_API_KEY"]
START = "2000-01-01"
NAMES = {"US": "United States", "CA": "Canada", "GB": "United Kingdom", "DE": "Germany", "FR": "France", "IT": "Italy", "JP": "Japan",
         "BR": "Brazil", "MX": "Mexico", "KR": "South Korea", "AU": "Australia", "IN": "India", "CN": "China",
         "EZ": "Euro area"}
ORDER = list(NAMES)

GDP = {**{c: f"NGDPRSAXDC{c}Q" for c in ("CA", "GB", "DE", "FR", "IT", "JP", "BR", "MX", "KR", "AU")},
       "IN": "NGDPRNSAXDCINQ", "CN": "NGDPRXDCCNA", "EZ": "CLVMEURSCAB1GQEA19"}
HICP = {"DE": "CP0000DEM086NEST", "FR": "CP0000FRM086NEST", "IT": "CP0000ITM086NEST", "EZ": "CP0000EZ19M086NEST"}
UNEMP = {c: f"LRHUTTTT{c}M156S" for c in ("CA", "GB", "DE", "FR", "IT", "JP", "MX", "KR", "AU")}
SHORT = {**{c: f"IRSTCI01{c}M156N" for c in ("CA", "GB", "JP", "IN", "BR", "MX", "KR", "AU")},
         "CN": "IR3TIB01CNM156N", "EZ": "ECBDFR"}
LONG = {**{c: f"IRLTLT01{c}M156N" for c in ("CA", "GB", "DE", "FR", "IT", "JP", "MX", "KR", "AU")},
        "IN": "INDIRLTLT01STM"}
# FX: (series, True when FRED quotes it as local currency per USD, so it is inverted to USD per local)
FX = {"CA": ("EXCAUS", True), "GB": ("EXUSUK", False), "EZ": ("EXUSEU", False), "JP": ("EXJPUS", True),
      "CN": ("EXCHUS", True), "IN": ("EXINUS", True), "BR": ("EXBZUS", True), "MX": ("EXMXUS", True),
      "KR": ("EXKOUS", True), "AU": ("EXUSAL", False)}
n_series = len(GDP) + len(HICP) + len(UNEMP) + len(SHORT) + len(LONG) + len(FX)
if n_series != 56:   # W's list; the US series are extra (OECD US rows, the broad dollar index, Pharos)
    raise SystemExit(f"world: {n_series} series configured here, W lists 56")


def fred(series_id: str, **extra) -> pl.DataFrame:
    params = {"series_id": series_id, "api_key": KEY, "file_type": "json", "observation_start": START, **extra}
    obs = json.load(urllib.request.urlopen("https://api.stlouisfed.org/fred/series/observations?"
                                           + urllib.parse.urlencode(params), timeout=60))["observations"]
    df = pl.DataFrame({"date": [date.fromisoformat(o["date"]) for o in obs],
                       "v": [float(o["value"]) if o["value"] != "." else None for o in obs]},
                      schema={"date": pl.Date, "v": pl.Float64}).drop_nulls("v")
    if df.is_empty():
        raise SystemExit(f"world: FRED returned no values for {series_id}")
    return df


def index_2019(df: pl.DataFrame) -> pl.DataFrame:
    """Each series over its 2019 average, x100: GDP in its own currency and FX levels can't share an axis otherwise."""
    b = df.filter(pl.col("date").dt.year() == 2019)["v"].mean()
    return df.with_columns(pl.col("v") / b * 100)


def yoy(df: pl.DataFrame, lag: int) -> pl.DataFrame:
    return df.sort("date").with_columns((pl.col("v") / pl.col("v").shift(lag) - 1) * 100).drop_nulls("v")


data = {"gdp": {}, "gdp_g": {}, "hicp": {}, "unemp": {}, "short": {}, "long": {}, "fx": {}, "fx_g": {}}
for c, s in GDP.items():
    raw = fred(s)
    data["gdp"][c] = index_2019(raw)
    data["gdp_g"][c] = yoy(raw, 1 if c == "CN" else 4)   # China is annual
for c, s in HICP.items():
    data["hicp"][c] = yoy(fred(s), 12)
# the US: the same OECD tables as the others where FRED has them; GDP and CPI from Pharos (below)
# (the OECD's US unemployment row, LRHUTTTTUSM156S, stopped at Sep 2025: the US rate is derived from Pharos below)
US_FRED = {"short": "IRSTCI01USM156N", "long": "IRLTLT01USM156N"}
for c, s in UNEMP.items():
    data["unemp"][c] = fred(s)
for c, s in SHORT.items():
    data["short"][c] = fred(s, frequency="m", aggregation_method="avg") if s == "ECBDFR" else fred(s)
for c, s in LONG.items():
    data["long"][c] = fred(s)
for c, (s, per_usd) in FX.items():
    raw = fred(s)
    usd_per_local = raw.with_columns((1 / pl.col("v")).alias("v")) if per_usd else raw   # unaliased, polars names it "literal"
    data["fx"][c] = index_2019(usd_per_local)
    data["fx_g"][c] = yoy(usd_per_local, 12)
for m, s in US_FRED.items():
    data[m]["US"] = fred(s)
dollar = fred("DTWEXBGS", frequency="m", aggregation_method="avg")   # broad trade-weighted dollar, daily -> monthly
data["fx"]["US"] = index_2019(dollar)
data["fx_g"]["US"] = yoy(dollar, 12)
# the direction check: the yen fell against the dollar from 2019 to 2024 (about 109 to 151 per USD)
jp = data["fx"]["JP"].filter(pl.col("date").dt.year() == 2024)["v"].mean()
if not jp < 80:
    raise SystemExit(f"world: the yen's 2024 index is {jp:.0f}, expected well under 100 (FX inversion wrong)")

con = duckdb.connect(base.CATALOG, read_only=True)
us = con.sql("""select series_id, obs_date as date, value as v from serving_observations
                where series_id in ('fred:GDPC1', 'fred:CPIAUCSL', 'fred:CLF16OV', 'fred:CE16OV') and value is not null
                qualify row_number() over (partition by series_id, obs_date order by vintage desc) = 1
                order by 1, 2""").pl()
us_gdp = index_2019(us.filter(pl.col("series_id") == "fred:GDPC1").select("date", "v")
                    .filter(pl.col("date") >= date(2000, 1, 1)))
us_cpi = yoy(us.filter(pl.col("series_id") == "fred:CPIAUCSL").select("date", "v"), 12) \
    .filter(pl.col("date") >= date(2000, 1, 1))
us_gdpc1 = us.filter(pl.col("series_id") == "fred:GDPC1").select("date", "v")
data["gdp"]["US"], data["gdp_g"]["US"], data["hicp"]["US"] = us_gdp, yoy(us_gdpc1, 4), us_cpi
# US unemployment: (labor force - employed) / labor force, the household survey (BLS's definition)
lab = us.filter(pl.col("series_id").is_in(["fred:CLF16OV", "fred:CE16OV"])) \
    .pivot(on="series_id", index="date", values="v").drop_nulls().sort("date")
data["unemp"]["US"] = lab.select("date", ((pl.col("fred:CLF16OV") - pl.col("fred:CE16OV")) / pl.col("fred:CLF16OV")
                                          * 100).alias("v")).filter(pl.col("date") >= date(2000, 1, 1))

BLUE, GRAY = "#2563eb", "#9ca3af"
COLS = 4


def grid(series: dict, title: str, ytitle: str, fmt: str, ref: pl.DataFrame | None = None, ref_name: str = "",
         zero: bool = False) -> go.Figure:
    codes = [c for c in ORDER if c in series]
    rows = -(-len(codes) // COLS)
    fig = make_subplots(rows=rows, cols=COLS, shared_xaxes="all", shared_yaxes="all",
                        subplot_titles=[NAMES[c] for c in codes], vertical_spacing=0.09, horizontal_spacing=0.03)
    for i, c in enumerate(codes):
        r, k = i // COLS + 1, i % COLS + 1
        if ref is not None and c != "US":
            fig.add_trace(go.Scatter(x=ref["date"], y=ref["v"], mode="lines", line=dict(color=GRAY, width=1.5),
                                     name=ref_name, legendgroup="ref", showlegend=i == 0,
                                     hovertemplate=f"%{{x|%b %Y}}: %{{y:{fmt}}}<extra>{ref_name}</extra>"), r, k)
        d = series[c]
        fig.add_trace(go.Scatter(x=d["date"], y=d["v"], mode="lines", line=dict(color=BLUE, width=2),
                                 name=NAMES[c], showlegend=False,
                                 hovertemplate=f"%{{x|%b %Y}}: %{{y:{fmt}}}<extra>{NAMES[c]}</extra>"), r, k)
        if zero:
            fig.add_hline(y=0, line=dict(color="#d1d5db", width=1), row=r, col=k)
    fig.update_layout(height=230 * rows + 90, template="plotly_white", title=title, hovermode="x unified",
                      margin=dict(l=60, r=20, t=90, b=40),
                      legend=dict(orientation="h", yref="container", y=0.995, yanchor="top", x=1, xanchor="right"))
    fig.update_yaxes(title_text=ytitle, col=1)
    return fig


figs = [
    ("1. Output", "Real GDP, each economy in its own currency, indexed to its 2019 average. The US is GDPC1 from Pharos. India's series is not "
                  "seasonally adjusted, so it saw-tooths within each year; China's is annual.",
     grid(data["gdp"], "Real GDP (2019 = 100)", "2019 = 100", ".1f", us_gdp, "United States")),
    ("2. Prices", "Consumer prices, percent change from a year earlier: HICP for the euro countries, CPI (CPIAUCSL, "
                  "from Pharos) for the US. FRED carries current monthly CPI only for these; the OECD feed for the "
                  "rest stopped in 2025.",
     grid(data["hicp"], "Inflation, % a year", "% a year", ".1f", us_cpi, "United States (CPI)", zero=True)),
    ("3. Work", "Unemployment rate, harmonized by the OECD so the countries compare. The US is computed from Pharos "
                "(labor force minus employed, over labor force, CLF16OV and CE16OV): the OECD's own US row stopped "
                "in September 2025.",
     grid(data["unemp"], "Unemployment rate, %", "%", ".1f", data["unemp"]["US"], "United States")),
    ("4. Short rates", "Overnight interbank rate (OECD), a stand-in for the policy rate. China: the 3-month interbank "
                       "rate. Euro area: the ECB deposit rate, monthly average.",
     grid(data["short"], "Short-term interest rate, %", "%", ".2f", data["short"]["US"], "United States", zero=True)),
    ("5. Long rates", "10-year government bond yield. India's comes from a different OECD table (INDIRLTLT01STM).",
     grid(data["long"], "10-year government bond yield, %", "%", ".2f", data["long"]["US"], "United States", zero=True)),
    ("6. Currencies", "US dollars per unit of local currency, indexed to the 2019 average: above 100, the currency buys "
                      "more dollars than in 2019. Monthly averages. The US panel is the dollar against a basket of its "
                      "trading partners' currencies (the Fed's broad index, DTWEXBGS): above 100, the dollar is "
                      "stronger than in 2019.",
     grid(data["fx"], "Currency vs the US dollar (2019 = 100)", "2019 = 100", ".1f")),
]


def latest(df: pl.DataFrame, fmt: str, quarterly: bool = False) -> str:
    row = df.sort("date").tail(1)
    d = row["date"][0]
    when = f"{d.year}" if quarterly is None else (f"{d.year} Q{(d.month - 1) // 3 + 1}" if quarterly else f"{d:%b %Y}")
    return f"{format(row['v'][0], fmt)}<span class=when>{when}</span>"


head = ["Economy", "Real GDP, % y/y", "Inflation, % y/y", "Unemployment, %", "Short rate, %", "10-year yield, %",
        "Currency vs USD, % y/y"]
trs = []
for c in ORDER:
    cells = [NAMES[c],
             latest(data["gdp_g"][c], "+.1f", None if c == "CN" else True),
             latest(data["hicp"][c], "+.1f") if c in data["hicp"] else "",
             latest(data["unemp"][c], ".1f") if c in data["unemp"] else "",
             latest(data["short"][c], ".2f") if c in data["short"] else "",
             latest(data["long"][c], ".2f") if c in data["long"] else "",
             latest(data["fx_g"][c], "+.1f") if c in data["fx_g"] else ""]
    trs.append("<tr>" + "".join(f"<td>{x}</td>" for x in cells) + "</tr>")
table = ("<table><thead><tr>" + "".join(f"<th>{h}</th>" for h in head) + "</tr></thead><tbody>"
         + "".join(trs) + "</tbody></table>")

first = True


def div(fig) -> str:
    global first
    out = pio.to_html(fig, full_html=False, include_plotlyjs=first, config={"displayModeBar": False})
    first = False
    return out


built = datetime.now().strftime("%Y-%m-%d %H:%M")
sections = "".join(f"<h2>{h}</h2><p>{p}</p>{div(f)}" for h, p, f in figs)
page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>World</title>
<style>
:root {{ --bg:#ffffff; --fg:#1d1d1b; --muted:#5f5e58; --line:#e3e1da; --box:#f6f5f1; }}
body {{ background:var(--bg); color:var(--fg); font:15px/1.5 system-ui, "Segoe UI", sans-serif; margin:0; padding:24px 16px 60px; }}
main {{ max-width:1250px; margin:0 auto; }}
h1 {{ font-size:24px; margin:0 0 6px; }}
h2 {{ font-size:19px; margin:34px 0 6px; }}
.muted {{ color:var(--muted); }}
.wrap {{ overflow-x:auto; }}
table {{ border-collapse:collapse; font-size:14px; margin:12px 0; }}
th, td {{ padding:6px 12px; border-bottom:1px solid var(--line); text-align:right; white-space:nowrap; }}
th:first-child, td:first-child {{ text-align:left; }}
th {{ font-weight:600; }}
.when {{ display:block; color:var(--muted); font-size:12px; }}
{nav.CSS}
</style></head>
<body>{nav.html("World")}<main>
<h1>World</h1>
<p class="muted">The US and the 56 FRED series planned for milestone W: 12 economies and the euro area, six
measures. They are fetched from FRED when the page is built (today's values, not stored in Pharos yet); US real GDP and
CPI come from Pharos. The gray line in each panel is the US, for comparison. A blank cell means FRED has no current series for that economy and measure. Each grid shares one y-axis, so
the panels compare directly. Page built {built}.</p>
<h2>Latest values</h2>
<p>The most recent value of each series, with the period it is for. Real GDP and inflation are the change from a
year earlier (China's GDP is annual); the currency column is the change in US dollars per unit of local currency
over the year (positive: the currency strengthened; for the US, the dollar against the Fed's broad basket).</p>
<div class="wrap">{table}</div>
{sections}
</main></body></html>"""

(HERE / "world.html").write_text(page, encoding="utf-8")
print("wrote", HERE / "world.html", f"{n_series} series")

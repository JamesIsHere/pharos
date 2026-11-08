"""Peers (M3 step 2, D151): each non-financial company's annual statements from its latest 10-K, by the concept map
(config/concepts.csv, D152), its ratios, and where it ranks at every SIC rung and Pharos sector (D149). Quick lane:
reads the served Company Facts read-only; all transformation is DuckDB SQL. Writes peers.html next to this file."""
import json
import sys
from datetime import date, datetime
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]   # explore/
sys.path.insert(0, str(ROOT / "lib"))
import base   # noqa: E402
import nav    # noqa: E402

AS_OF = date.today()
CONCEPTS = (base.PROJECT_ROOT / "config" / "concepts.csv").as_posix()

# Ratios: key, family, label, formula as shown, SQL over the statement row s (avg_ = mean of opening and closing,
# both from the same 10-K; days = the revenue period's actual days).
RATIOS = [
    ("gm", "Margins and returns", "Gross margin", "(revenue - cost of revenue) / revenue",
     "(revenue - cost_of_revenue) / revenue", "pct"),
    ("om", "Margins and returns", "Operating margin", "operating income / revenue", "operating_income / revenue", "pct"),
    ("nm", "Margins and returns", "Net margin", "net income / revenue", "net_income / revenue", "pct"),
    ("roa", "Margins and returns", "Return on assets", "net income / average total assets",
     "net_income / avg_total_assets", "pct"),
    ("roe", "Margins and returns", "Return on equity", "net income / average equity (blank if equity is not positive)",
     "case when equity > 0 and avg_equity > 0 then net_income / avg_equity end", "pct"),
    ("dso", "Working-capital days", "Receivable days", "average receivables / revenue x days in the year",
     "avg_receivables / revenue * days", "days"),
    ("dio", "Working-capital days", "Inventory days", "average inventory / cost of revenue x days in the year",
     "avg_inventory / cost_of_revenue * days", "days"),
    ("dpo", "Working-capital days", "Payable days", "average payables / cost of revenue x days in the year",
     "avg_payables / cost_of_revenue * days", "days"),
    ("ccc", "Working-capital days", "Cash conversion cycle", "receivable days + inventory days - payable days",
     "(avg_receivables / revenue + (avg_inventory - avg_payables) / cost_of_revenue) * days", "days"),
    ("capex_rev", "Capital intensity", "Capex to revenue", "capex / revenue", "capex / revenue", "pct"),
    ("capex_da", "Capital intensity", "Capex to D&A", "capex / depreciation and amortization",
     "capex / nullif(d_and_a, 0)", "x"),
    ("ppe_turn", "Capital intensity", "PP&E turnover", "revenue / average net PP&E", "revenue / avg_net_ppe", "x"),
    ("asset_turn", "Capital intensity", "Asset turnover", "revenue / average total assets",
     "revenue / avg_total_assets", "x"),
    ("cur", "Leverage and liquidity", "Current ratio", "current assets / current liabilities",
     "current_assets / nullif(current_liabilities, 0)", "x"),
    ("eqm", "Leverage and liquidity", "Assets to equity", "total assets / equity (blank if equity is not positive)",
     "case when equity > 0 then total_assets / equity end", "x"),
]
RUNGS = [("sic4", "4-digit SIC"), ("sic3", "3-digit SIC"), ("sic2", "2-digit SIC"), ("division", "SIC division"),
         ("sector", "Pharos sector")]

con = duckdb.connect(base.CATALOG, read_only=True)
con.sql("set memory_limit='12GB'")
con.sql(f"create temp table cmap as select * from read_csv('{CONCEPTS}', header=true)")
measures = [r[0] for r in con.sql("select distinct measure from cmap order by 1").fetchall()]
stocks = [r[0] for r in con.sql("select distinct measure from cmap where period = 'instant' order by 1").fetchall()]

# Each company's latest original 10-K filed in the 12 months before the as-of date; its own period end is the
# latest us-gaap end date in it that is not after the filing date.
con.sql(f"""
create temp table k as
with acc as (
    select cik, accession, max(end_date) as period_end, min(filed) as filed
    from serving_company_facts
    where taxonomy = 'us-gaap' and form = '10-K' and end_date <= filed and filed <= date '{AS_OF}'
    group by all
)
select * from acc where filed > date '{AS_OF}' - interval 1 year
qualify row_number() over (partition by cik order by filed desc, accession desc) = 1
""")
con.sql("""
create temp table x as
select f.cik, k.accession, k.period_end, k.filed, c.measure, c.priority, c.concept, c.period,
       f.start_date, f.end_date, f.val
from serving_company_facts f join k using (cik, accession)
join cmap c on c.concept = f.concept and c.unit = f.unit
where f.taxonomy = 'us-gaap'
  and ((c.period = 'annual' and f.end_date = k.period_end and date_diff('day', f.start_date, f.end_date) between 350 and 380)
    or (c.period = 'instant' and f.start_date is null
        and (f.end_date = k.period_end or date_diff('day', f.end_date, k.period_end) between 350 and 380)))
""")
conflicts = con.sql("""
select count(*) from (select cik, concept, start_date, end_date from x group by all having count(distinct val) > 1)
""").fetchone()[0]
print("facts disagreeing within one 10-K (same concept and period, two values):", conflicts)

# One value per measure: the first concept in priority order tagged for the own period; a balance's opening value
# comes from the same concept, at the latest earlier date in the same 10-K.
con.sql("""
create temp table line as
with v as (
    select cik, measure, priority, concept, period,
           max(val) filter (where end_date = period_end) as close,
           arg_max(val, end_date) filter (where end_date < period_end) as opening,
           max(date_diff('day', start_date, end_date) + 1) filter (where period = 'annual') as days
    from x group by all
)
select * from v where close is not null
qualify row_number() over (partition by cik, measure order by priority) = 1
""")
cols = ",\n    ".join(
    [f"max(close) filter (where measure = '{m}') as {m}" for m in measures]
    + [f"(max(close) filter (where measure = '{m}') + max(opening) filter (where measure = '{m}')) / 2 as avg_{m}"
       for m in stocks]
    + ["max(days) filter (where measure = 'revenue') as days"])
con.sql(f"create temp table s as select cik,\n    {cols}\nfrom line group by cik")

ratio_sql = ",\n    ".join(f"{expr} as {key}" for key, _, _, _, expr, _ in RATIOS)
con.sql(f"""
create temp table co as
with d as (
    select cik, name, try_cast(sic as int) as sic, sic_description, tickers[1] as ticker, category
    from serving_submission_details
)
select k.cik, d.ticker, d.name, d.sic, d.sic_description, d.category, k.accession, k.period_end, k.filed,
       lpad(cast(d.sic as varchar), 4, '0') as sic4, lpad(cast(d.sic // 10 as varchar), 3, '0') as sic3,
       lpad(cast(d.sic // 100 as varchar), 2, '0') as sic2,
       case {" ".join(f"when d.sic // 100 between {lo} and {hi} then '{l}'" for l, lo, hi, _ in base.DIVISIONS)} end
           as division,
       {base.SECTOR_SQL.replace("sic", "d.sic")} as sector,
       s.equity <= 0 as negative_equity,
    {ratio_sql}
from k join d using (cik) join s using (cik)
where d.sic is not null and d.sic <> 6770 and not (d.sic between 6000 and 6999) and s.revenue > 0
""")
print("companies:", con.sql("select count(*) from co").fetchone()[0])

# Rank every company in every peer group, per ratio, among the peers that have the ratio.
unpivot = " union all ".join(f"select cik, '{key}' as ratio, {key} as v from co where {key} is not null"
                             for key, *_ in RATIOS)
rungs = " union all ".join(f"select cik, '{r}' as rung, {r} as code from co" for r, _ in RUNGS)
con.sql(f"""
create temp table ranked as
select g.rung, g.code, v.ratio, v.cik, v.v,
       percent_rank() over (partition by g.rung, g.code, v.ratio order by v.v) as pct,
       count(*) over (partition by g.rung, g.code, v.ratio) as n,
       median(v.v) over (partition by g.rung, g.code, v.ratio) as med
from ({unpivot}) v join ({rungs}) g using (cik)
""")

companies = {}
for r in con.sql("""select cik, ticker, name, sic, sic_description, sic4, sic3, sic2, division, sector,
                           accession, period_end, filed, negative_equity from co""").fetchall():
    companies[r[0]] = dict(t=r[1] or "", n=r[2], sic=r[3], sd=r[4], g=[r[5], r[6], r[7], r[8], r[9]], acc=r[10],
                           pe=str(r[11]), f=str(r[12]), neg=r[13], l={}, r={})
for cik, m, p, concept, close, opening, days in con.sql(
        "select cik, measure, priority, concept, close, opening, days from line where cik in (select cik from co)").fetchall():
    companies[cik]["l"][m] = [round(close), None if opening is None else round(opening), p]
    if m == "revenue":
        companies[cik]["days"] = days
ri = {r: i for i, (r, _) in enumerate(RUNGS)}
for rung, cik, ratio, v, pct, n, med in con.sql(
        "select rung, cik, ratio, v, pct, n, med from ranked").fetchall():
    e = companies[cik]["r"].setdefault(ratio, [round(v, 4), [None] * len(RUNGS)])
    e[1][ri[rung]] = [round(pct * 100), n, round(med, 4)]

cmap = [dict(m=m, p=p, c=c, per=per, note=note or "") for m, p, c, per, note in
        con.sql("select measure, priority, concept, period, note from cmap order by measure, priority").fetchall()]
version = (base.data_root() / "serving" / "CURRENT").read_text(encoding="utf-8").strip()
data = json.dumps(dict(co=companies, cmap=cmap, ratios=[dict(k=k, fam=f, lab=lb, fx=fx, fmt=fm)
                                                       for k, f, lb, fx, _, fm in RATIOS],
                       rungs=[lb for _, lb in RUNGS], divs={l: nm for l, _, _, nm in base.DIVISIONS}),
                  separators=(",", ":"), default=str)
data = data.replace("</", "<\\/")
options = "".join(f'<option value="{(c["t"] + " " if c["t"] else "")}{c["n"]} [{cik}]">'
                  for cik, c in sorted(companies.items(), key=lambda kv: (kv[1]["t"] == "", kv[1]["t"], kv[1]["n"])))

built = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Peers</title>
<style>
:root {{ --bg:#ffffff; --fg:#1d1d1b; --muted:#5f5e58; --line:#e3e1da; --box:#f6f5f1; --flag:#b45309; }}
body {{ background:var(--bg); color:var(--fg); font:15px/1.5 system-ui, "Segoe UI", sans-serif; margin:0; padding:24px 16px 60px; }}
main {{ max-width:1250px; margin:0 auto; }}
h1 {{ font-size:24px; margin:0 0 6px; }}
h2 {{ font-size:18px; margin:0 0 6px; }}
section {{ border-top:1px solid var(--line); padding:22px 0; }}
.muted {{ color:var(--muted); }}
input#pick {{ font:inherit; width:min(560px, 100%); padding:8px 10px; border:1px solid var(--line); }}
.wrap {{ overflow-x:auto; }}
table {{ border-collapse:collapse; width:100%; font-variant-numeric:tabular-nums; }}
th, td {{ text-align:right; padding:6px 10px; border-bottom:1px solid var(--line); vertical-align:top; }}
th:first-child, td:first-child, td.l, th.l {{ text-align:left; }}
th {{ font-weight:600; font-size:13px; color:var(--muted); }}
tr.fam td {{ background:var(--box); font-weight:600; text-align:left; }}
td small, th small {{ display:block; color:var(--muted); font-size:12px; font-weight:400; }}
.flag {{ color:var(--flag); }}
.pct {{ font-weight:600; }}
</style>
<style>{nav.CSS}</style></head>
<body>{nav.html("Peers")}<main>
<h1>Peers</h1>
<p class="muted">Built {built} from serving version {version}, as of {AS_OF}. Each non-financial company's latest 10-K
filed in the 12 months to that date, its figures as that 10-K reported them. Financial companies (SIC 6000-6999) and
SPACs are left out.</p>
<p><input id="pick" list="cos" placeholder="Type a ticker or company name" autocomplete="off">
<datalist id="cos">{options}</datalist></p>
<div id="out"></div>
</main>
<script>
const D = {data};
const NAMES = {{}};
for (const m of D.cmap) {{ (NAMES[m.m] = NAMES[m.m] || {{}})[m.p] = m; }}
const LINES = [["revenue","Revenue"],["cost_of_revenue","Cost of revenue"],["operating_income","Operating income"],
  ["net_income","Net income"],["d_and_a","Depreciation and amortization"],["capex","Capex"],
  ["receivables","Receivables"],["inventory","Inventory"],["payables","Payables"],["net_ppe","Net PP&E"],
  ["current_assets","Current assets"],["current_liabilities","Current liabilities"],["total_assets","Total assets"],
  ["equity","Equity (parent)"]];
const esc = s => String(s).replace(/[&<>"]/g, c => ({{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}})[c]);
const money = v => v == null ? "" : (Math.abs(v) >= 1e9 ? (v / 1e9).toFixed(2) + "bn" : (v / 1e6).toFixed(1) + "m");
function fmt(v, f) {{
  if (v == null) return "";
  if (f === "pct") return (v * 100).toFixed(1) + "%";
  if (f === "days") return v.toFixed(0);
  return v.toFixed(2) + "x";
}}
function show(cik) {{
  const c = D.co[cik]; if (!c) return;
  const groups = [c.g[0] + " " + c.sd, c.g[1], c.g[2], c.g[3] + " " + D.divs[c.g[3]], c.g[4]];
  let h = `<section><h2>${{esc(c.n)}}${{c.t ? " (" + esc(c.t) + ")" : ""}}</h2>
  <p class="muted">CIK ${{cik}}. Fiscal year ended ${{c.pe}} (${{c.days || "?"}} days), 10-K filed ${{c.f}},
  accession ${{c.acc}}. SIC ${{esc(groups[0])}}; Pharos sector ${{esc(c.g[4])}}.
  ${{c.neg ? '<span class="flag">Equity is negative: return on equity and assets to equity are left blank.</span>' : ""}}</p>`;
  h += `<h2>Ratios against peers</h2><p class="muted">Each peer cell is the company's percentile among the peers that
  have the ratio (0 lowest, 100 highest), the group median, and the number of companies in the group with the
  ratio, this one included.</p><div class="wrap"><table>
  <tr><th class="l">Ratio</th><th>Company</th>${{D.rungs.map((r, i) => `<th>${{r}}<small>${{esc(groups[i])}}</small></th>`).join("")}}</tr>`;
  let fam = "";
  for (const r of D.ratios) {{
    if (r.fam !== fam) {{ fam = r.fam; h += `<tr class="fam"><td colspan="${{2 + D.rungs.length}}">${{fam}}</td></tr>`; }}
    const e = c.r[r.k];
    h += `<tr><td class="l">${{r.lab}}<small>${{esc(r.fx)}}</small></td><td>${{e ? fmt(e[0], r.fmt) : "n/a"}}</td>`;
    for (let i = 0; i < D.rungs.length; i++) {{
      const p = e && e[1][i];
      h += p ? `<td><span class="pct">${{p[0]}}</span><small>median ${{fmt(p[2], r.fmt)}}, ${{p[1].toLocaleString()}} companies</small></td>` : "<td></td>";
    }}
    h += "</tr>";
  }}
  h += `</table></div></section><section><h2>Statement lines from this 10-K</h2><p class="muted">Opening balances come
  from the same 10-K. The concept is the first one in the concept map's priority order that the 10-K tags.</p>
  <div class="wrap"><table><tr><th class="l">Line</th><th>This year</th><th>Opening</th><th class="l">Concept</th></tr>`;
  for (const [m, lab] of LINES) {{
    const v = c.l[m];
    if (!v) {{ h += `<tr><td class="l">${{lab}}</td><td>not tagged</td><td></td><td class="l"></td></tr>`; continue; }}
    const cm = NAMES[m][v[2]];
    const flag = cm.note.startsWith("FLAG") ? `<small class="flag">${{esc(cm.note)}}</small>` : "";
    h += `<tr><td class="l">${{lab}}</td><td>${{money(v[0])}}</td><td>${{cm.per === "instant" ? money(v[1]) : ""}}</td>
          <td class="l">${{esc(cm.c)}} (priority ${{v[2]}})${{flag}}</td></tr>`;
  }}
  h += "</table></div></section>";
  document.getElementById("out").innerHTML = h;
}}
const pick = document.getElementById("pick");
pick.addEventListener("change", () => {{ const m = pick.value.match(/\\[(\\d+)\\]$/); if (m) show(m[1]); }});
const first = Object.keys(D.co).find(k => D.co[k].t === "WMT") || Object.keys(D.co)[0];
pick.value = (D.co[first].t ? D.co[first].t + " " : "") + D.co[first].n + " [" + first + "]";
show(first);
</script></body></html>"""

out = ROOT / "peers" / "peers.html"
out.write_text(html, encoding="utf-8")
print("wrote", out, f"({out.stat().st_size / 1e6:.1f} MB)")

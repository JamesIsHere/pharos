"""Combine the tests into one page: runs each test's script, takes its figure, adds the narrative."""
import contextlib
import io
import runpy
import sys
from pathlib import Path

import plotly.io as pio

ROOT = Path(__file__).resolve().parents[1]   # explore/
sys.path.insert(0, str(ROOT / "lib"))   # tests 8+ import lib/base.py
SCRIPTS = {1: "test1/build.py", 2: "test2/build.py", 3: "test3/build.py", 4: "test4/build.py", 5: "test5/build.py",
           6: "coverage/build.py", 7: "sectors/build.py",
           8: "lib/t08_grain.py", 9: "lib/t09_concentration_sic2.py", 10: "lib/t10_concentration_sic4.py",
           11: "lib/t11_size_curve.py", 12: "lib/t12_listing.py", 13: "lib/t13_profitability.py",
           14: "lib/t14_over_time.py", 15: "lib/t15_incorporation.py", 16: "lib/t16_amazon_share.py",
           17: "lib/t17_gdp.py"}
import nav   # noqa: E402
import industry_cheatsheet   # noqa: E402  (lib/ is on sys.path above)
CHEAT = industry_cheatsheet.html(2024)
figs = {}
for n, s in SCRIPTS.items():
    with contextlib.redirect_stdout(io.StringIO()):
        figs[n] = runpy.run_path(str(ROOT / s))["fig"]

def chart(n):
    figs[n].update_layout(title=None, margin=dict(t=40))
    if n == 7:
        figs[n].update_layout(margin=dict(l=250, r=10, t=20), legend=dict(orientation="h", y=-0.06))
    return pio.to_html(figs[n], full_html=False, include_plotlyjs=(n == 1), config={"displayModeBar": False})

TESTS = [
    (1, "How many companies file an annual report each year, next to real GDP",
     "The answer is already known: the number of US public companies roughly halved from its late-1990s peak while "
     "the economy kept growing. If our filing index is complete, it has to reproduce that.",
     "It does. 12,994 companies filed a domestic annual report in 2000 and 6,565 in 2025, down 49%; real GDP rose "
     "70% over the same years. The 2021-22 bump is the SPAC boom.",
     "The count includes funds, shells and SPACs, not only operating companies. Any statistic about 'all companies' "
     "needs that population narrowed first."),
    (2, "Revenue for five companies, as reported and in 2025 dollars",
     "Each company's revenue is published and easy to check, and CPI turns it into real growth. It tests that "
     "single-company numbers come out right and that the SEC and FRED data join.",
     "The first values match published figures (Apple FY2007 $24.6bn, Walmart FY2008 $377.0bn). In 2025 dollars, "
     "Walmart was flat from 2014 to 2020 and McDonald's real revenue fell by about a third.",
     "Revenue is filed under different concept names over the years: Apple used three. A query on one name has "
     "holes, so each company needs a mapping. October 2025 CPI is missing from FRED, a gap that reaches every "
     "fiscal year spanning it."),
    (3, "Equity as a share of assets for every company at the end of 2024, by industry",
     "Industries have known shapes: banks run about 10% equity, SPACs show negative equity because of how their "
     "shares are classified. If the balance-sheet data is sound across thousands of companies, those shapes appear.",
     "They do, across 5,856 companies. Banks sit tightly at 11%; 84% of SPACs show negative equity.",
     "The extreme values are tiny shell companies, some with under $200 of assets. Among companies with $100m or "
     "more of assets only 8% have negative equity; under $1m, 82% do. Averages over 'all filers' are dominated by "
     "shells."),
    (4, "Revenue summed over every filer, next to nominal GDP",
     "There is no exact known answer here (revenue is not GDP), so the test is stability: if coverage is "
     "consistent, the ratio should hold steady from year to year.",
     "From 2011 the ratio stays between 77% and 90%. It dips in 2015 (oil prices) and rises in 2021-22 "
     "(inflation). The largest company is Walmart at $681bn, so no filer reports in the wrong scale at the top.",
     "Data before 2011 is incomplete because XBRL was phased in. The level means little: sales between companies "
     "count twice, foreign sales are included, private companies are missing, and subsidiaries that file their "
     "own 10-K count twice."),
    (5, "Days from fiscal year-end to 10-K filing",
     "SEC sets filing deadlines by company size: 60, 75 and 90 days. If both the year-end dates and the filing "
     "dates are right, each group has to cluster just inside its deadline.",
     "They do: medians of 56, 67 and 88 days. The slowest tenth files at about 105 days every year, which is the "
     "90-day deadline plus the 15-day extension a late filer can claim.",
     "SEC's company-size label is today's label, not the one the company had that year, and it is stored as "
     "several flags run together. A company that grew is filed under its current size for every year."),
    (6, "How much of the US economy public companies account for",
     "It is a sizing question, and the obvious way to answer it is wrong: revenue divided by GDP compares sales "
     "with value added. GDP counts each product once; revenue counts it again at every company it passes "
     "through. The test sets three measures side by side: the mismatched one, sales against sales (BEA's gross "
     "output, the economy's total sales), and profits against profits (net income against the national "
     "accounts' corporate profits after tax).",
     "Sales against sales: SEC filers' revenue is about 48% of US gross output in every year since 2011, about "
     "53% of the private sector's. Profits against profits: their net income is 51-71% of corporate profits in "
     "normal years, 36% in 2020. Revenue against GDP reads 79-90%, which overstates the share. Broad size: "
     "public companies are roughly half of US business activity. Their revenue includes foreign sales, so the "
     "domestic share is lower than half by an amount these data cannot measure.",
     "SEC's calendar-year labels are not safe for net income. For 2024, 2,548 of about 6,100 labelled net-income "
     "figures sit on a filing other than the 10-K, 1,747 of them proxy statements, and two proxy statements "
     "carry net income apparently scaled wrong by a factor of a million (-$22 trillion and -$13 trillion), turning the "
     "2023-2025 totals negative. This test therefore reads net income and revenue from each 10-K's own one-year "
     "figures, assigned to the calendar year holding most of the fiscal year, and drops one company whose 10-K/A "
     "reports $1.6 trillion of net income. Tests 4 and 7 used the labels for revenue; on the 10-K figures 2024 "
     "revenue is $24.1 trillion against $24.8 trillion, a 3% difference."),
    (7, "Which industries public companies cover, next to the whole economy (2024)",
     "It joins the public companies to the economy's own industry breakdown, which BEA publishes as value added "
     "(GDP) and gross output (total sales) by industry. The rough answer is known: the stock market is not the "
     "economy. Public companies should be heavy in manufacturing and finance and absent from government.",
     "Manufacturing is 10% of GDP but 30% of public companies' revenue; finance 8% vs 21%; retail 6% vs 15%. "
     "Real estate (14% of GDP), government (11%), health care and education (9%) and construction (4%) are each "
     "1% or less of public revenue. Public markets hold the big, capital-heavy businesses; most hospitals, "
     "contractors, law firms and all of government are outside them, and roughly half of real estate's GDP is the "
     "rent homeowners notionally pay themselves (BEA's own convention; that share is not computed here).",
     "Each company carries one SEC industry code, so all its revenue lands in one sector: UnitedHealth counts as "
     "insurance, CVS as retail, Amazon's cloud business as retail. SEC's industry codes are an older system than "
     "BEA's and were matched by hand, roughly. The BEA industry data is not in Pharos: it was pulled from FRED "
     "when the chart was built and not stored (BEA data is milestone M6 in the plan)."),
]

EXPLORE = [
    (8, "How many industries are big enough to measure?",
     "Before measuring concentration, which grain of SEC's industry codes leaves enough companies in each "
     "industry. An industry with two companies always shows one of them at 50% or more, which says nothing. "
     "Base for tests 8-15: companies with 2024 revenue from their 10-K's own figures, SPACs excluded (the method of test 6).",
     "4,613 companies had 2024 revenue. At the finest grain (4-digit SIC) they spread over 387 industries; only "
     "108 hold 10 or more companies, and those cover 77% of the companies and 65% of the revenue; 59 industries "
     "hold a single company. At 2-digit SIC, 58 of 70 groups clear 10 companies and cover 99% of everything. "
     "The 15 BEA sectors are all large.",
     "Tests 9 and 10 use a floor of 10 companies. The floor is a choice, not a rule: at 5 companies more "
     "industries qualify and the shares look more extreme."),
    (9, "Concentration inside each industry (2-digit SIC)",
     "Within each of the 58 broad industries with 10 or more companies: how much of the industry's public revenue "
     "the largest company holds (dark bar, with its name), and the top four together (light bar). Hover a bar "
     "for the company count and HHI, the sum of squared shares that US merger guidelines use.",
     "The typical industry's largest company holds 25% of its revenue and the top four hold 63% (medians). 18 of "
     "the 58 score above 1,800 on HHI, the level US merger guidelines treat as highly concentrated. Most "
     "concentrated: Netflix 72% (SEC's code for it is video tape rental), Best Buy 70% of consumer electronics "
     "stores, UPS 64% of trucking and courier, Walmart 57% of variety stores, Disney 56% of amusement. Least: REITs (American Tower 4%), "
     "pharmaceuticals (Johnson & Johnson 8%), medical instruments, electric and gas services.",
     "Shares are of public companies' revenue only, not market share: private firms and companies filing "
     "foreign annual reports (20-F, 40-F) are missing, and foreign sales are included. Each industry is labelled "
     "by its largest 4-digit code. Subsidiaries that file their own 10-K count twice: the largest company in "
     "security brokers (SIC 62) is Citigroup Global Markets Holdings, a Citigroup subsidiary."),
    (10, "The most and least concentrated detailed industries (4-digit SIC)",
     "The same measure at the finest grain, for the 108 detailed industries with 10 or more companies: the 15 "
     "most concentrated and the 5 least.",
     "Newmont holds 93% of gold and silver ore revenue, Amazon 85% of catalog and mail-order retail, Disney 84% "
     "of amusement and recreation, Medtronic 82% of electromedical apparatus, Lam Research 81% of special "
     "industry machinery. At the other end, the largest REIT holds 4% and the largest electric utility "
     "10%.",
     "A detailed code with one giant and a tail of small companies always reads as concentrated. Newmont's 93% "
     "probably reflects that most large gold miners are Canadian and file Canadian or foreign annual reports, "
     "not that Newmont dominates gold (not checked here). Some 'companies' are subsidiaries (Flagstar Bank, "
     "National Association is a bank owned by a listed holding company)."),
    (11, "How few companies make up most public revenue",
     "Rank every company by 2024 revenue and add up shares from the top: how many companies it takes to reach "
     "a quarter, half, 80%, 90% and 99% of all public-company revenue. The axis is logarithmic so the top "
     "companies are readable.",
     "20 companies make up a quarter of all revenue, 88 make up half, 448 make 80% and 845 make 90%. The "
     "largest 10 hold 17.0%. The median company has $421m of revenue, and 802 companies have under $10m.",
     "The top of the list includes some revenue twice (subsidiaries that file their own 10-K, test 12), which "
     "makes the top look slightly larger than it is."),
    (12, "Where the companies trade",
     "Split the 2024 companies by where their shares trade today, from SEC's exchange field: NYSE, Nasdaq, OTC "
     "(over-the-counter, outside the exchanges), or no ticker at all.",
     "NYSE: 33% of companies, 62% of revenue, median company $2.1bn. Nasdaq: 43% of companies, 28% of "
     "revenue, median $241m. OTC: 10% of companies, 1.3% of revenue, median $4.0m, and 74% of them lost money. "
     "No ticker: 13% of companies and 8% of revenue.",
     "The exchange is today's, the revenue 2024's. Exxon shows as 'no ticker' because its XOM ticker moved to "
     "a new holding company with a different SEC number (a trap noted in the data map). Most of the no-ticker "
     "revenue is subsidiaries that file a 10-K because they issue public debt: JPMorgan Chase Financial Co., "
     "Citigroup Global Markets Holdings, CCO Holdings (part of Charter), Metropolitan Life Insurance Co. Their "
     "revenue is also inside their parents' (issue #55)."),
    (13, "Who loses money",
     "The share of companies reporting a net loss in 2024, by revenue size and by sector.",
     "47% of the 4,383 companies with both figures lost money. By size: 88% of companies under $10m of revenue, "
     "67% at $10-100m, 45% at $100m-1bn, 22% at $1-10bn and 8% at $10bn and up. By sector, utilities (18%) and "
     "finance (22%) have the fewest loss-makers; information has the most (66%) yet the highest overall margin "
     "(16%): a few very profitable giants and a long tail of loss-making small companies.",
     "A net loss can be one-off (write-downs, litigation). Companies with no revenue at all, such as early "
     "biotechs, are not in this set, so the true share of loss-makers among filers is higher."),
    (14, "Is public-company revenue concentrating over time?",
     "Two views, 2011-2025: the share of all public revenue held by the largest 10 and 100 companies (left), "
     "and the median concentration (HHI) across the broad industries (right).",
     "The largest 100 held 52% in 2011, fell to 49% by 2017, and rose to 54% by 2025; the largest 10 went from "
     "16% to a low of 14% (2016-17) to 18%. The largest company was Exxon in 2011, Walmart from 2012 to 2024 "
     "and Amazon in 2025. Inside industries, the median HHI stays between about 1,150 and 1,450 with no trend.",
     "2025 is incomplete: 4,237 companies against 4,613 in 2024, because fiscal years ending in 2026 are not "
     "all filed yet, which pushes 2025's top shares up. The population also shrank over the period (test 1), "
     "which raises the top companies' share slightly on its own."),
    (15, "Where companies are incorporated",
     "The state of incorporation from SEC's company details, for the 2024 companies.",
     "Delaware: 52% of companies and 62% of revenue. Nevada: 9% of companies but under 1% of revenue, a home "
     "for small companies. Maryland: 5%, almost all REITs (165 of its 248). Incorporated outside the US: 5%. "
     "Not recorded: 8%.",
     "The field is today's value, not the state at the time."),
    (16, "Amazon's share at every grain of the industry codes, 2011-2025",
     "Test 10's 85% for Amazon in 2024, taken every year and rolled up the code ladder in the cheat sheet above: "
     "Amazon's revenue as a share of its 4-digit industry (5961), 3-digit group (596), 2-digit group (59), "
     "division (G, retail trade), Pharos's retail sector, and all public companies. The bars on the right are "
     "Amazon and the other 5961 companies in dollars.",
     "Every line rises every year; only the level depends on the grain. 2011 to 2024: 57% to 85% of 5961 (596 is "
     "the same within a point: its six extra companies are tiny), 8% to 47% of 59, 2.4% to 17% of retail trade, "
     "0.3% to 2.6% of all public-company revenue. In 2025: 86%, 54%, 18% and 2.8%. Amazon's revenue grew about "
     "15 times ($48bn to $717bn); the other 5961 companies about 3 times ($37bn to $117bn).",
     "Shares rise when companies leave, not only when Amazon grows. In 59, Medco (2012) and Express Scripts "
     "(2018) stop filing after acquisitions, and Walgreens has no 2025 10-K after going private, which is most "
     "of 2025's jump from 47% to 54%. In 2011 Express Scripts counts twice (parent and subsidiary both filed). "
     "These are shares of US 10-K filers, not market share: Walmart (5331), eBay (7389), JD.com (5990) and "
     "Alibaba (7389, a foreign filer) are outside 5961, and Amazon's revenue includes AWS and advertising. Codes "
     "are today's, applied to every year. 2025 is incomplete."),
]

def section(n, title, labels, texts):
    items = "".join(f"<dt>{l}</dt><dd>{t}</dd>" for l, t in zip(labels, texts))
    return f"""
<section>
<h2>Test {n}: {title}</h2>
<div class="cols">
<div class="chart">{chart(n)}</div>
<dl>{items}</dl>
</div>
</section>"""

sections = [section(n, title, ("Why it is a good test", "What it showed", "What it taught about the data"), rest)
            for n, title, *rest in TESTS]
MACRO = [
    (17, "GDP over time",
     "Pharos's macro data is three FRED series, each with every vintage ALFRED holds: nominal GDP and real GDP "
     "(quarterly, from 1947) and CPI (monthly, from 1947). Left: nominal and real GDP as published today. Right: "
     "real GDP growth for each quarter, once as first published and once as published today, both computed "
     "inside one vintage.",
     "Nominal GDP went from $243bn (1947 Q1, annual rate) to $33.0 trillion (2026 Q3); real GDP from $2.18 to "
     "$24.6 trillion in 2017 dollars. All 140 quarters from 1991 Q4 to 2026 Q3 have a first print. A quarter's "
     "growth moves 1.2 points on average between first print and today (median 1.05). 8 quarters changed sign: "
     "2001 Q1 was first +2.0% and is now -1.3%, 2008 Q1 +0.6% now -1.7%. 2008 Q4 was first -3.8% and is now "
     "-8.5%: the worst of the financial crisis was not visible in real time.",
     "Real GDP's units change with each benchmark (1987 dollars up to chained 2017 dollars across vintages), so "
     "levels from different vintages never mix; growth is computed within one vintage. In the 1996, 1999 and "
     "2003 comprehensive revisions BEA published history as blank, so those vintages hold nulls. ALFRED's "
     "vintages start in December 1991 with history from 1959 (earlier quarters were added in 1992), so only "
     "1991 Q4 onward has a true first print. The growth axis stops at 12%: 2020 Q2 (-33% first, -28% today) "
     "and Q3 (+33% first, +35% today) run off it. "
     "2025 Q3 was first published 2025-12-23, late (shutdown). CPI for October 2025 is still missing."),
]

macro_sections = [section(n, title, ("What it asks", "What it shows", "Watch out for"), rest)
                  for n, title, *rest in MACRO]
explore_sections = [section(n, title, ("What it asks", "What it shows", "Watch out for"), rest)
                    for n, title, *rest in EXPLORE]

html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pharos data tests</title>
<style>
:root {{ --bg:#ffffff; --fg:#1d1d1b; --muted:#5f5e58; --line:#e3e1da; --box:#f6f5f1; }}
body {{ background:var(--bg); color:var(--fg); font:15px/1.5 system-ui, "Segoe UI", sans-serif; margin:0; padding:24px 16px 60px; }}
main {{ max-width:1250px; margin:0 auto; }}
h1 {{ font-size:24px; margin:0 0 6px; }}
h2 {{ font-size:18px; margin:0 0 10px; }}
section {{ border-top:1px solid var(--line); padding:22px 0; }}
.lead {{ background:var(--box); padding:14px 18px; margin:14px 0 8px; }}
.lead p {{ margin:6px 0; }}
.cols {{ display:grid; grid-template-columns: minmax(0,3fr) minmax(0,2fr); gap:22px; align-items:start; }}
@media (max-width: 900px) {{ .cols {{ grid-template-columns: 1fr; }} }}
dl {{ margin:0; }}
dt {{ font-weight:600; margin-top:10px; }}
dt:first-child {{ margin-top:0; }}
dd {{ margin:2px 0 0; color:var(--muted); }}
.muted {{ color:var(--muted); }}
ol li {{ margin:4px 0; }}
.cheat {{ background:var(--box); padding:16px 18px; border-top:none; margin-top:8px; }}
.cheat h3 {{ font-size:15px; margin:4px 0 6px; }}
.cheat-cols {{ display:grid; grid-template-columns: minmax(0,3fr) minmax(0,2fr); gap:22px; }}
@media (max-width: 900px) {{ .cheat-cols {{ grid-template-columns: 1fr; }} }}
.cheat table {{ border-collapse:collapse; width:100%; font-size:13px; background:var(--bg); }}
.cheat th, .cheat td {{ border-bottom:1px solid var(--line); padding:4px 8px; text-align:left; vertical-align:top; }}
.cheat th {{ font-weight:600; }}
.cheat .r {{ text-align:right; }}
.cheat tr.off td {{ color:var(--muted); font-style:italic; }}
{nav.CSS}
</style></head>
<body>{nav.html('Test')}<main>
<h1>Pharos data tests</h1>
<p class="muted">One-off queries on the data in Pharos's database today (serving version 20261105T103527Z), run 2026-11-05. None of them is part of the plan. Part 1 (tests 1-7): tests 1-5 check that the data is right; tests 6 and 7 size public companies against the economy. Part 2 (tests 8-16): exploration of the public companies themselves. Part 3 (test 17): the macro data.</p>

<div class="lead">
<p><b>What these tests say:</b> the data is sound. Tests 1-5 each have an answer known from outside Pharos, and the data reproduced all five. One exception surfaced in test 6: SEC's own calendar-year labels are not safe for net income. It is not yet usable for analysis, though: every test needed a hand fix for a known trap, and those fixes are the curation work the data needs before any real analysis.</p>
<p>A good test of a dataset is one whose answer you already know. If the result matches, the data is probably right; if not, something is broken. Pretty charts alone prove nothing.</p>
</div>

{''.join(sections)}

<section>
<h2>Putting the seven together</h2>
<p><b>What they establish.</b> All four SEC datasets and all three FRED series answered a known-answer question correctly: the filing index (test 1), company financials at one company (test 2), across thousands of companies (test 3) and over time (test 4), and the dates on all of it (test 5). Nothing came back broken. Tests 6 and 7 then used the data to size public companies against the economy: roughly half of US business activity by sales and 51-71% of corporate profits in normal years, concentrated in manufacturing, finance and retail.</p>
<p><b>The fixes every test needed by hand:</b></p>
<ol>
<li>Narrow 'every filer' to operating companies: shells, SPACs and funds distort every population-wide number (tests 1, 3, 4).</li>
<li>Map each company's concept names to one meaning: revenue alone is filed under at least three names (tests 2, 4). The same holds for industries: SEC's codes need a crosswalk to BEA's sectors (test 7).</li>
<li>Pin each number to the right period and the right point in time: fiscal years differ, SEC's calendar labels approximate (and for net income often point at a proxy statement's copy), and company-size labels are today's, not that year's (tests 2, 4, 5, 6).</li>
<li>Handle holes: the missing October 2025 CPI month, mistyped end dates, reporting in other currencies, values scaled wrong by a factor of a million (tests 2, 3, 5, 6).</li>
</ol>
<p class="muted">Claude's read, not a plan item: item 1 is the work of the parked Universe milestone (U); items 2-4 are the curation work in M3. Price-to-book for the watchlist, proposed earlier, has not been run.</p>
</section>

<section>
<h2 style="font-size:22px">Part 2: exploring the public companies</h2>
<div class="lead">
<p>Tests 8-16 cut the public companies several ways: industry concentration, size, listing, profitability, change over time, incorporation, and one company over time (Amazon). They are exploration, not checks: each says what it asks, what it shows, and what to watch out for.</p>
<p><b>The short version:</b> 83 companies make up half of all public-company revenue. Inside the typical broad industry the largest company holds about a quarter and the top four about 60%. Below the top sits a long tail: 762 companies with under $10m of revenue, 89% of them loss-making, many trading over the counter.</p>
</div>
</section>

{CHEAT}

{''.join(explore_sections)}

<section>
<h2>What the exploration adds</h2>
<ol>
<li>Concentration depends on the grain: the finer the industry codes, the more concentrated everything looks, and many detailed codes hold too few companies to measure (tests 8-10).</li>
<li>Public-company revenue is top-heavy and has grown slightly more so since 2017 (tests 11, 14).</li>
<li>Subsidiaries that file their own 10-K show up everywhere: in the no-ticker group, at the top of brokerage, among banks. Every total and share in this page counts their revenue twice until they are removed (tests 9, 10, 12; issue #55).</li>
<li>Every company label used here is today's: exchange, state, industry code. Applying them to past years misplaces companies that moved, merged or went private (tests 12, 14, 15).</li>
</ol>
</section>
<section>
<h2 style="font-size:22px">Part 3: macro data</h2>
</section>

{''.join(macro_sections)}
</main></body></html>"""

out = ROOT / "tests" / "data-tests.html"
out.write_text(html, encoding="utf-8")
print("wrote", out)

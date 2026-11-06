"""Industry-code cheat sheet: an HTML block for the top of any page that cuts companies by industry.
Counts are computed from the data (companies with revenue in the given year), so the sheet stays true to the page."""
import polars as pl

import base


def html(yr: int = 2024) -> str:
    y = base.with_grains(base.revenue_year(yr)).filter(pl.col("sic").is_not_null() & (pl.col("sic") > 0))
    n = y.height

    def stats(col):
        g = y.group_by(col).agg(pl.len().alias("n"))
        return g.height, g.filter(pl.col("n") >= 10).height, int(g["n"].median())

    ladder = (
        ("Division", "letter A-K, a range of 2-digit groups", "division", "G", "Retail trade (52-59)"),
        ("Major group", "2 digits", "sic2", "59", "Miscellaneous retail"),
        ("Industry group", "3 digits", "sic3", "596", "Nonstore retailers"),
        ("Industry", "4 digits: the code SEC assigns", "sic", "5961", "Catalog and mail-order houses"),
    )
    rows = ""
    for level, form, col, code, name in ladder:
        codes, big, med = stats(col)
        rows += (f"<tr><td>{level}</td><td>{form}</td><td>{code} {name}</td>"
                 f"<td class='r'>{codes}</td><td class='r'>{big}</td><td class='r'>{med}</td></tr>")
    s_codes, s_big, s_med = stats("sector")
    rows += (f"<tr class='off'><td>Pharos sector</td><td>Pharos's own grouping of SIC ranges (base.py)</td>"
             f"<td>Retail trade</td><td class='r'>{s_codes}</td><td class='r'>{s_big}</td><td class='r'>{s_med}</td></tr>")

    d = (y.group_by("division").agg(pl.len().alias("n"), pl.col("revenue").sum().alias("rev"))
         .join(pl.DataFrame({"division": [x[0] for x in base.DIVISIONS]}), on="division", how="right"))
    tot = y["revenue"].sum()
    div_rows = ""
    for letter, lo, hi, name in base.DIVISIONS:
        r = d.filter(pl.col("division") == letter)
        cnt = r["n"][0] or 0
        rev = (r["rev"][0] or 0) / tot
        rng = f"{lo:02d}" if lo == hi else f"{lo:02d}-{hi:02d}"
        div_rows += (f"<tr><td>{letter}</td><td>{rng}</td><td>{name}</td>"
                     f"<td class='r'>{cnt:,}</td><td class='r'>{rev:.0%}</td></tr>")

    return f"""
<section class="cheat">
<h2>Cheat sheet: industry codes</h2>
<p>SEC gives every company one 4-digit SIC code (Standard Industrial Classification, 1987). The digits nest:
drop the last digit to roll up a level, so 5961 sits inside 596, inside 59, inside division G. Any test can be
run at any level, and the levels can be mixed (test 16 does it for Amazon). Counts are for the {n:,} companies
with {yr} revenue and a SIC code (test 8's base, less the companies SEC left without one).</p>
<div class="cheat-cols">
<div>
<h3>The levels, coarse to fine</h3>
<table>
<tr><th>Level</th><th>Form</th><th>Amazon's code</th><th class='r'>Codes in use</th><th class='r'>With 10+ companies</th><th class='r'>Median companies per code</th></tr>
{rows}
</table>
<p class="muted">The Pharos sector (test 7) is not a step on the ladder: it regroups SIC ranges into 15
sectors close to BEA's, so it can be set next to GDP. It moves some codes across divisions: restaurants (58)
leave retail for food services; telecom (48), software and data processing (7370, 7372, 7374) and film (78)
form Information.</p>
</div>
<div>
<h3>The divisions</h3>
<table>
<tr><th>Div.</th><th>2-digit groups</th><th>Name</th><th class='r'>Companies</th><th class='r'>Revenue share</th></tr>
{div_rows}
</table>
</div>
</div>
<p class="muted"><b>Before reading any industry number:</b> each company has one code, for its main line of business
as SEC judged it, so conglomerates sit in one place (Amazon, with AWS, is a mail-order house). The code is today's,
applied to every past year. SEC's data names only the 4-digit codes; the 2-digit, 3-digit and division names here come
from the 1987 SIC manual. The finer the level, the fewer companies per code, and a code with a handful of companies
always looks concentrated (test 8).</p>
</section>"""

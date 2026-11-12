"""Scaffold: Appendix. Earlier exploration worth re-reading, in one place (2026-11-12; was the Archive page).

The old Explore set (archive/explore-2026-11-12/) is retired: its analyses are listed here by topic. Pages that
Scaffold replaced (Data, Data Tests copies 1-7, Code Tests, Log, Explore's own Appendix) are left out. Each listed
Explore page is a snapshot from the day it was built. Its old Explore menu is swapped for one link back here on
every build, so a page can't strand the reader in the old menu (the pages are gitignored build output; rebuilding
one from its archived script brings the old menu back until this runs again).
Explore and countries pages missing on disk stop the build.
Run: uv run python scaffold/appendix.py"""
import os
import re
from datetime import datetime
from pathlib import Path

from common import ET, esc, now_et, page

HERE = Path(__file__).parent
ROOT = HERE.parent
EXPLORE = ROOT / "archive" / "explore-2026-11-12"
COUNTRIES = ROOT / "archive" / "countries-2026-11-12"

SECTIONS = (
    ("Economy", EXPLORE, (
        ("World", "world/world.html", "The US and 12 economies plus the euro area: output, prices, work, short and long rates, currencies (56 FRED series)"),
        ("People and Work", "people/people.html", "US population, employment, where growth comes from, who gets paid"),
        ("Growth math", "people/growth-math.html", "The identity behind People and Work's growth chart, with a worked decade"),
        ("Where GDP Goes", "trade/trade.html", "Who spends US GDP, trade against GDP, the monthly trade balance, companies abroad"),
        ("Trade Flows", "flows/flows.html", "IMF bilateral trade 1948-2025: who ships to whom, biggest partners, surpluses, do both sides agree"),
        ("Government", "government/government.html", "Spending, borrowing and debt by country 1980-2024; the US 1929-2024"),
        ("Companies vs Economy", "economy/economy.html", "Which companies outgrew GDP each year, after inflation, by sector; a scorecard"),
    )),
    ("Companies", EXPLORE, (
        ("Industry Code Breakdown", "codes/industry-codes.html", "SIC codes at four levels, every code in use, SIC divisions against Pharos's 15 sectors"),
        ("Peers", "peers/peers.html", "One company's ratios against its peers, with its 10-K statement lines"),
        ("Amazon", "amazon/amazon.html", "Amazon from Pharos's data only: scale, mix, profit, cash, assets, working capital, stock pay"),
        ("Reporting Consistency", "consistency/consistency.html", "How often reported net income changes after first filing, and where"),
        ("Insiders", "insiders/insiders.html", "Trial: SEC insider Forms 3/4/5, four quarters, not stored"),
    )),
    ("Data tests", EXPLORE, (
        ("Data Tests 1-7", "tests/data-tests.html", "Filer counts, revenue and equity against GDP, filing lag, how much of the economy public companies cover"),
    )),
    ("Countries", COUNTRIES, (
        ("Country key", "country_key.html", "How the IMF, World Bank and SEC country codes become config/countries.csv and config/sec_country_codes.csv"),
        ("Charts", "charts.html", "13 exploratory charts: GDP, unemployment, and listed companies by country"),
        ("Trusted", "trusted.html", "The trusted bar (D163) measured against the SEC sources and IMF WEO"),
    )),
)

OLD_NAV = re.compile(r'<nav class="top">.*?</nav>', re.S)


def back_link(f: Path) -> None:
    """Swap an Explore page's old menu for one link back to this page."""
    s = f.read_text(encoding="utf-8")
    up = "../" * (len(f.relative_to(ROOT).parts) - 1)
    new = f'<nav class="top"><div><a href="{up}scaffold/appendix.html">Back to the Scaffold Appendix</a></div></nav>'
    s2, n = OLD_NAV.subn(new, s, count=1)
    if n and s2 != s:
        st = f.stat()
        f.write_text(s2, encoding="utf-8")
        os.utime(f, (st.st_atime, st.st_mtime))   # keep the page's build date


def built(f):
    return f"{datetime.fromtimestamp(f.stat().st_mtime, ET):%Y-%m-%d}"


parts = []
for title, base, pages in SECTIONS:
    rows = []
    for label, rel, what in pages:
        f = base / rel
        if not f.exists():
            raise SystemExit(f"appendix: {f} not on disk")
        when = built(f)
        if base == EXPLORE:
            back_link(f)
        else:   # the countries pages' own menu named the Archive page, renamed Appendix
            t = f.read_text(encoding="utf-8")
            if "scaffold/archive.html" in t:
                st = f.stat()
                f.write_text(t.replace("scaffold/archive.html", "scaffold/appendix.html"), encoding="utf-8")
                os.utime(f, (st.st_atime, st.st_mtime))
        rows.append(f'<tr><td><a href="../{f.relative_to(ROOT).as_posix()}">{esc(label)}</a></td>'
                    f'<td>{esc(what)}</td><td class=sub>{when}</td></tr>')
    parts.append(f'<h2>{title}</h2><table class="list"><tr><th>Page</th><th>What</th><th>Built</th></tr>{"".join(rows)}</table>')


body = f"""<div class="head"><h1>Appendix</h1>
<div class="stamps"><span>Page built {now_et()}</span></div></div>
<p class=sub>Earlier exploration. Each page is a snapshot from the day it was built.</p>
{"".join(parts)}"""

(HERE / "appendix.html").write_text(page("Appendix", "Appendix", body), encoding="utf-8")
print(f"wrote {HERE / 'appendix.html'}")

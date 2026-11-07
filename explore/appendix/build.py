"""Appendix: an index of every page that is not in the main menu, so each stays reachable without being front and
center. Nothing is moved: each link points at the file where it already lives. A listed file that is missing stops
the build (fail loud). Writes index.html next to this file."""
import html as h
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
EXPLORE = HERE.parent
ROOT = EXPLORE.parent
sys.path.insert(0, str(EXPLORE / "lib"))
import nav  # noqa: E402

# (section, [(file relative to the repo root, title, what it is)])
SECTIONS = (
    ("Status and how Pharos works", (
        ("scratch/folder-map.html", "Folder map",
         "What each folder holds, which are in git, and the 'Where we stand' box (done, now, queued) updated after every commit."),
        ("scratch/run-flow.html", "Run flow",
         "Diagrams: how a nightly run works, what happened when five FRED series were added on 2026-11-07, and the "
         "two-lane way of working (quick lane during a session, pipeline lane nightly)."),
        ("scratch/pharos-operating-manual.html", "Operating manual",
         "How Pharos runs: the nightly data refresh and the code-testing cycle, with a brief operating manual."),
        ("scratch/plan-map.html", "Plan map",
         "Snapshot of docs/plan.md from 2026-11-05: what was done, what was not, and what did not add up."),
        ("scratch/work-heatmap.html", "Work heatmap",
         "Snapshot from 2026-11-05: where the work went, by folder and by kind of work."),
    )),
    ("Reference", (
        ("scratch/data_map/data-map.html", "Data map",
         "All eight served tables and every field: where each comes from, how they connect, traps before querying."),
        ("scratch/sec-user-agent-explainer.html", "SEC User-Agent explainer",
         "Why a new install failed on SEC downloads without SEC_USER_AGENT in .env, and the one-line fix. "
         "Historical: README.md now documents the setting (commit c87ed41)."),
    )),
    ("Data Tests 1-7 as standalone pages", (
        ("explore/test1/test1.html", "Test 1", "Companies filing an annual report each year, next to real GDP."),
        ("explore/test2/test2.html", "Test 2", "Revenue for five companies, as reported and in 2025 dollars."),
        ("explore/test3/test3.html", "Test 3", "Equity as a share of assets for every company at the end of 2024, by industry."),
        ("explore/test4/test4.html", "Test 4", "Revenue summed over every filer, next to nominal GDP."),
        ("explore/test5/test5.html", "Test 5", "Days from fiscal year-end to 10-K filing."),
        ("explore/coverage/coverage.html", "Test 6", "How much of the US economy public companies account for."),
        ("explore/sectors/sectors.html", "Test 7", "Which industries public companies cover, next to the whole economy (2024)."),
    )),
)

missing = [rel for _, items in SECTIONS for rel, _, _ in items if not (ROOT / rel).is_file()]
if missing:
    raise SystemExit("appendix: listed files missing: " + ", ".join(missing))


def row(rel: str, title: str, what: str) -> str:
    p = ROOT / rel
    href = Path("../..", rel).as_posix()
    when = datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    return (f'<tr><td><a href="{href}">{h.escape(title)}</a></td><td>{h.escape(what)}</td>'
            f'<td class="path">{h.escape(rel)}</td><td class="num">{when}</td></tr>')


body = "".join(
    f"<h2>{h.escape(name)}</h2><div class=wrap><table><thead><tr><th>Page</th><th>What it is</th><th>File</th>"
    f"<th class=num>Last changed</th></tr></thead><tbody>{''.join(row(*it) for it in items)}</tbody></table></div>"
    for name, items in SECTIONS)

built = datetime.now().strftime("%Y-%m-%d %H:%M")
page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pharos appendix</title>
<style>
:root {{ --bg:#ffffff; --fg:#1d1d1b; --muted:#5f5e58; --line:#e3e1da; --box:#f6f5f1; }}
body {{ background:var(--bg); color:var(--fg); font:15px/1.5 system-ui, "Segoe UI", sans-serif; margin:0; padding:24px 16px 60px; }}
main {{ max-width:1250px; margin:0 auto; }}
h1 {{ font-size:24px; margin:0 0 6px; }}
h2 {{ font-size:18px; margin:26px 0 8px; }}
.muted {{ color:var(--muted); }}
.wrap {{ overflow-x:auto; }}
table {{ border-collapse:collapse; width:100%; font-size:14px; }}
th, td {{ border-bottom:1px solid var(--line); padding:6px 10px; text-align:left; vertical-align:top; }}
th {{ white-space:nowrap; }}
td:first-child {{ white-space:nowrap; font-weight:600; }}
td.num, th.num {{ text-align:right; white-space:nowrap; }}
td.path {{ font:12px ui-monospace, Consolas, monospace; color:var(--muted); white-space:nowrap; }}
a {{ color:#2563eb; }}
{nav.CSS}
</style></head>
<body>{nav.html("Appendix")}<main>
<h1>Appendix</h1>
<p class="muted">Every page that is not in the menu above, linked where it already lives. Pages under scratch/ have
no menu; use the browser's back button to return. Page built {built}.</p>
{body}
</main></body></html>"""

(HERE / "index.html").write_text(page, encoding="utf-8")
print("wrote", HERE / "index.html")

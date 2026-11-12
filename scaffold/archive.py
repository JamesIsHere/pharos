"""Scaffold: Archive. Links to retired work worth re-reading (2026-11-12).

First entry: the old Explore pages, archived whole to archive/explore-2026-11-12/. The list is Explore's own menu,
read from its lib/nav.py, so the groups and names are the ones those pages show. Built = when each page was last
built, from the file's time on disk. A menu page missing on disk stops the build.
Run: uv run python scaffold/archive.py"""
import importlib.util
from datetime import datetime
from pathlib import Path

from common import ET, esc, now_et, page

HERE = Path(__file__).parent
EXPLORE = HERE.parent / "archive" / "explore-2026-11-12"

spec = importlib.util.spec_from_file_location("explore_nav", EXPLORE / "lib" / "nav.py")
nav = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nav)

rows = []
for group, pages in nav.GROUPS:
    for label, href in pages:
        f = EXPLORE / href.removeprefix("../")
        if not f.exists():
            raise SystemExit(f"archive: Explore menu page {f} not on disk")
        built = datetime.fromtimestamp(f.stat().st_mtime, ET)
        rel = f.relative_to(HERE.parent).as_posix()
        rows.append(f'<tr><td>{esc(group or "")}</td><td><a href="../{rel}">{esc(label)}</a></td>'
                    f'<td class=sub>{built:%Y-%m-%d %H:%M} ET</td></tr>')

body = f"""<div class="head"><h1>Archive</h1>
<div class="stamps"><span>Page built {now_et()}</span></div></div>

<h2>Explore (archived 2026-11-12)</h2>
<p class=sub>The pages before Scaffold, in their own menu's order. They still open and link to each other; their
Appendix lists the pages that were never on the menu.</p>
<table class="list"><tr><th>Group</th><th>Page</th><th>Built</th></tr>{"".join(rows)}</table>"""

(HERE / "archive.html").write_text(page("Archive", "Archive", body), encoding="utf-8")
print(f"wrote {HERE / 'archive.html'}: {len(rows)} Explore pages")

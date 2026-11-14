"""Ideas page: data we might add, one row each, grouped by what it would join to (James, 2026-11-14).

Everything is typed in ideas.csv, which opens in Excel; nothing is measured. Status is 'idea', 'next' (being added
now) or 'added' (it is in Pharos; the row stays so the list shows what came of it). Source and ease were written
from memory when the row was added and are not checked until an idea is picked up. Writes ideas.html.
Run: uv run python scaffold/ideas.py"""
import csv
from pathlib import Path

from common import esc, now_et, page

HERE = Path(__file__).parent
COLS = ("joins_to", "idea", "adds", "source", "ease", "status")
EASE = ("easy", "medium", "hard")
STATUS = ("next", "idea", "added")

with (HERE / "ideas.csv").open(encoding="utf-8", newline="") as f:
    reader = csv.reader(f)
    header = next(reader)
    if tuple(header) != COLS:
        raise SystemExit(f"ideas.csv: columns are {header}, expected {list(COLS)}")
    rows = []
    for n, r in enumerate(reader, 2):
        if len(r) != len(COLS):
            raise SystemExit(f"ideas.csv line {n}: {len(r)} fields, expected {len(COLS)} (a comma inside a cell needs quotes): {r}")
        rows.append(dict(zip(COLS, r)))
bad = [r["idea"] for r in rows if r["ease"] not in EASE or r["status"] not in STATUS or not r["joins_to"] or not r["idea"]]
if bad:
    raise SystemExit(f"ideas.csv: rows need a group, an idea, ease in {EASE} and status in {STATUS}: {bad}")

groups = {}
for r in rows:
    groups.setdefault(r["joins_to"], []).append(r)
out = []
for g, rs in groups.items():
    out.append(f'<tr class="g1"><td colspan="5">{esc(g)} <span class="cnt">{len(rs)}</span></td></tr>')
    for r in sorted(rs, key=lambda r: STATUS.index(r["status"])):   # 'next' first; the CSV's order within a status
        out.append(f'<tr class="{r["status"]}"><td class=nw>{esc(r["idea"])}</td><td>{esc(r["adds"])}</td>'
                   f'<td>{esc(r["source"])}</td><td class=nw>{esc(r["ease"])}</td><td class="nw st">{esc(r["status"])}</td></tr>')

STYLE = """<style>
tr.g1 td { background:var(--box); font-weight:700; border-bottom:2px solid var(--fg); padding-top:10px; }
.cnt { color:var(--muted); font-weight:400; margin-left:6px; }
tr.next td.st { color:#15803d; } tr.idea td.st { color:var(--muted); font-weight:400; } tr.added td { color:#9ca3af; }
</style>"""

body = f"""<div class="head"><h1>Ideas: data we might add</h1>
<div class="stamps"><span>Page built {now_et()}</span></div></div>
<p class=sub>{len(rows)} ideas, grouped by what each would join to in Pharos. Source and ease are from memory, not
checked, until an idea is picked up. Edit the list in scaffold/ideas.csv.</p>
<table><tr><th>Idea</th><th>What it adds</th><th>Source</th><th>Ease</th><th>Status</th></tr>
{"".join(out)}</table>"""

(HERE / "ideas.html").write_text(page("Ideas", "Ideas", body, head=STYLE), encoding="utf-8")
print(f"wrote {HERE / 'ideas.html'}: {len(rows)} ideas in {len(groups)} groups")

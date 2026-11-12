"""Notes page: renders scaffold/notes.md (headings, list items, paragraphs; nothing else). Writes notes.html.
Run: uv run python scaffold/notes.py"""
import re
from pathlib import Path

from common import esc, now_et, page

HERE = Path(__file__).parent
out, in_list = [], False
for line in (HERE / "notes.md").read_text(encoding="utf-8").splitlines():
    if line.startswith("- "):
        if not in_list:
            out.append("<ul>")
            in_list = True
        text = esc(line[2:])
        text = re.sub(r"^(\d{4}-\d{2}-\d{2}):", r'<span class="muted">\1</span>', text)
        out.append(f"<li>{text}</li>")
        continue
    if in_list:
        out.append("</ul>")
        in_list = False
    if line.startswith("## "):
        out.append(f"<h2>{esc(line[3:])}</h2>")
    elif line.startswith("# "):
        out.append(f"<h1>{esc(line[2:])}</h1>")
    elif line.strip():
        out.append(f"<p class=muted>{esc(line)}</p>")
if in_list:
    out.append("</ul>")
body = "\n".join(out) + f'<p class="muted">Source: scaffold/notes.md. Built {now_et()}.</p>'
body = body.replace("<ul>", '<ul style="max-width:1000px; line-height:1.55">')
(HERE / "notes.html").write_text(page("Notes", "Notes", body), encoding="utf-8")
print(f"wrote {HERE / 'notes.html'}")

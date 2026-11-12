"""Scaffold: a shareable copy of the built pages, for a private claude.ai link (James, 2026-11-12).

Reads the built scaffold/*.html (run each page's script first) and writes scaffold/share/:
- every page except Archive (its links lead to archive/ folders outside scaffold/); the Archive menu item is removed;
- links to files on this machine (file:///...) become plain text: the code they point to is shown in the page;
- index.html: the Catalog, as the landing page, without its document wrapper (the publisher adds one).
A link that still leaves scaffold/ after this stops the script (fail loud).
Run: uv run python scaffold/share.py, then publish scaffold/share/index.html with the other pages as files."""
import re
import shutil
from pathlib import Path

HERE = Path(__file__).parent
OUT = HERE / "share"
SKIP = {"archive.html"}

pages = sorted(p for p in HERE.glob("*.html") if p.name not in SKIP)
if not pages:
    raise SystemExit("share: no built pages in scaffold/")
if OUT.exists():
    shutil.rmtree(OUT)
OUT.mkdir()

for p in pages:
    s = p.read_text(encoding="utf-8")
    s = re.sub(r'<a href="archive\.html"[^>]*>Archive</a>', "", s)
    s = re.sub(r'<a href="file:[^"]*">([^<]*)</a>', r"\1", s)
    bad = sorted(set(re.findall(r'href="((?:file:|\.\./)[^"]*|archive\.html[^"]*)"', s)))
    if bad:
        raise SystemExit(f"share: {p.name} still links outside the shared pages: {bad[:5]}")
    (OUT / p.name).write_text(s, encoding="utf-8")

# the landing page: the Catalog's head contents and body, without the document wrapper
cat = (OUT / "catalog.html").read_text(encoding="utf-8")
head = re.search(r"<head>(.*?)</head>", cat, re.S).group(1)
body = re.search(r"<body>(.*?)</body>", cat, re.S).group(1)
head = re.sub(r"<title>.*?</title>", "<title>Pharos Scaffold</title>", head)
head = re.sub(r'<meta charset[^>]*>\s*|<meta name="viewport"[^>]*>\s*', "", head)
(OUT / "index.html").write_text(head.strip() + "\n" + body.strip() + "\n", encoding="utf-8")

print(f"wrote {OUT}: index.html + {', '.join(p.name for p in pages)}")

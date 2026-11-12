"""Scaffold: a shareable copy of the built pages, for a private claude.ai link (James, 2026-11-12).

Reads the built scaffold/*.html (run each page's script first) and writes scaffold/share/:
- every Scaffold page, and every archived page the Archive page links to, at its own path under archive/;
- links to files on this machine (file:///...) and to pages not in the copy become plain text;
- index.html: the Catalog, as the landing page, without its document wrapper (the publisher adds one).
A link that still leaves scaffold/ after this stops the script (fail loud).
Run: uv run python scaffold/share.py, then publish scaffold/share/index.html with the other pages as files."""
import re
import shutil
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent
OUT = HERE / "share"

pages = sorted(HERE.glob("*.html"))
if not pages:
    raise SystemExit("share: no built pages in scaffold/")
if OUT.exists():
    shutil.rmtree(OUT)
OUT.mkdir()

# the archived pages the Archive page links to, kept at their paths under archive/ (published as archive/...)
archived = sorted(set(re.findall(r'href="\.\./(archive/[^"#]+)"', (HERE / "archive.html").read_text(encoding="utf-8"))))
for a in archived:
    if not (ROOT / a).is_file():
        raise SystemExit(f"share: the Archive page links to {a}, which is not on disk")
SHIPPED = {p.name for p in pages} | set(archived)   # every path in the shared copy, relative to its root


def unlink_dead(s: str, here: str) -> str:
    """A relative link to a page not in the shared copy becomes its text; file:/// links likewise."""
    def fix(m):
        href, text = m.group(1), m.group(2)
        if href.startswith(("http:", "https:", "#", "mailto:")):
            return m.group(0)
        if not href.startswith("file:"):
            target = (Path(here).parent / href.split("#")[0]).as_posix()
            parts = []
            for part in target.split("/"):
                if part == "..":
                    if not parts:
                        return text
                    parts.pop()
                elif part not in ("", "."):
                    parts.append(part)
            if "/".join(parts) in SHIPPED:
                return m.group(0)
        return text
    # outside <script> blocks only: inlined libraries (Plotly) carry '<a href=' in their own code
    pieces = re.split(r"(<script\b.*?</script>)", s, flags=re.S | re.I)
    return "".join(p if p[:7].lower() == "<script" else re.sub(r'<a href="([^"]*)"[^>]*>([^<]*(?:<(?!/a>)[^<]*)*)</a>', fix, p)
                   for p in pieces)


for p in pages:
    s = p.read_text(encoding="utf-8")
    s = s.replace('href="../archive/', 'href="archive/')
    (OUT / p.name).write_text(unlink_dead(s, p.name), encoding="utf-8")

for a in archived:
    s = (ROOT / a).read_text(encoding="utf-8")
    depth = a.count("/")
    s = s.replace('href="../../scaffold/', f'href="{"../" * depth}')   # back to the shared Scaffold pages
    out = OUT / a
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(unlink_dead(s, a), encoding="utf-8")

for f in OUT.rglob("*.html"):
    bad = sorted(set(re.findall(r'href="(file:[^"]*)"', f.read_text(encoding="utf-8"))))
    if bad:
        raise SystemExit(f"share: {f.relative_to(OUT)} still links to this machine: {bad[:3]}")

# the landing page: the Catalog's head contents and body, without the document wrapper
cat = (OUT / "catalog.html").read_text(encoding="utf-8")
head = re.search(r"<head>(.*?)</head>", cat, re.S).group(1)
body = re.search(r"<body>(.*?)</body>", cat, re.S).group(1)
head = re.sub(r"<title>.*?</title>", "<title>Pharos Scaffold</title>", head)
head = re.sub(r'<meta charset[^>]*>\s*|<meta name="viewport"[^>]*>\s*', "", head)
(OUT / "index.html").write_text(head.strip() + "\n" + body.strip() + "\n", encoding="utf-8")

print(f"wrote {OUT}: index.html + {len(pages)} Scaffold pages + {len(archived)} archived pages")
for f in sorted(SHIPPED):
    print(f"  {f}")

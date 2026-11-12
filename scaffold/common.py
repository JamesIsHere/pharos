"""Shared look and menu for the Scaffold pages. One page per menu item; source sheets hang off the Catalog."""
import html
import re
from datetime import datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
MENU = (("Catalog", "catalog.html"), ("Pipeline", "pipeline.html"), ("Notes", "notes.html"), ("Appendix", "appendix.html"))
MERMAID = "https://cdn.jsdelivr.net/npm/mermaid@11.9.0/dist/mermaid.min.js"

CSS = """
:root { --bg:#ffffff; --fg:#1d1d1b; --muted:#5f5e58; --line:#e3e1da; --box:#f6f5f1; --flag:#fef3c7; }
body { background:var(--bg); color:var(--fg); font:15px/1.45 system-ui, "Segoe UI", sans-serif; margin:0; padding:0 16px 60px; }
main { max-width:1400px; margin:0 auto; }
nav { max-width:1400px; margin:0 auto 18px; border-bottom:1px solid var(--line); display:flex; gap:4px; align-items:center; }
nav b { padding:12px 14px 12px 0; letter-spacing:.04em; }
nav a { padding:12px 10px; color:var(--muted); text-decoration:none; border-bottom:3px solid transparent; }
nav a.on { color:var(--fg); font-weight:600; border-bottom-color:#2563eb; }
nav span.crumb { padding:12px 4px; color:var(--muted); }
h1 { font-size:22px; margin:0 0 8px; }
h2 { font-size:18px; margin:30px 0 6px; border-bottom:2px solid var(--fg); }
h3 { font-size:15px; margin:18px 0 4px; }
table { border-collapse:collapse; font-size:16px; margin:4px 0; }
th, td { border-bottom:1px solid var(--line); padding:4px 10px; text-align:left; vertical-align:top; }
th { font-size:13px; color:var(--muted); font-weight:600; white-space:nowrap; }
td.num { text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }
td.mono, .mono { font:inherit; }  /* one font per page (James, 2026-11-11) */
tr.flag td { background:var(--flag); }
.sub, .muted { color:var(--muted); font-size:16px; }
.facts td:first-child { color:var(--muted); }
.st { font-weight:700; white-space:nowrap; }
.trusted { color:#15803d; } .landed { color:#1d4ed8; }
.quick-lane { color:#a16207; } .trial { color:#c2410c; } .retired { color:#9ca3af; }
.ok { color:#15803d; white-space:nowrap; } .warn { color:#a16207; white-space:nowrap; } .bad { color:#b91c1c; font-weight:600; white-space:nowrap; }
.wrap { overflow-x:auto; }
.nw { white-space:nowrap; }
.cols { display:flex; flex-wrap:wrap; gap:28px; align-items:flex-start; }
details { margin:3px 0; } summary { cursor:pointer; }
ul { margin:4px 0; padding-left:20px; }
.list td { padding:3px 12px 3px 0; }

.head { display:flex; justify-content:space-between; align-items:flex-start; gap:16px; flex-wrap:wrap; }
.stamps { display:flex; flex-direction:column; align-items:flex-end; gap:4px; }
.stamps span { color:#15803d; font-weight:700; font-size:13px; white-space:nowrap; }
"""


def esc(x):
    return html.escape("" if x is None else str(x))


def num(x):
    return "" if x is None else f"{x:,}"


def now_et():
    return datetime.now(ET).strftime("%Y-%m-%d %H:%M ET")


_D = r"\d{4}-\d{2}-\d{2}(?: \d{2}:\d{2}(?: ET)?)?"
DATE = re.compile(f"{_D}(?: to {_D})?")   # a date, or a "date to date" span, kept on one line


def keep_dates(body):
    """A date never breaks across lines ("2026-" / "11-10"): each one in visible text gets a nowrap span.
    Tags, <pre> (Mermaid source) and <script> are left as they are."""
    parts = re.split(r"(<pre\b.*?</pre>|<script\b.*?</script>|<[^>]+>)", body, flags=re.S | re.I)
    return "".join(p if p.startswith("<") else DATE.sub(lambda m: f"<span class=nw>{m.group(0)}</span>", p)
                   for p in parts)


def page(title, active, body, crumb=None, head="", parent=None):
    """parent: (label, href) of the page this one hangs off, shown as a link before the crumb."""
    body = keep_dates(body)
    menu = "".join(f'<a href="{h}"{" class=on" if lb == active else ""}>{lb}</a>' for lb, h in MENU)
    crumb_html = f'<span class="crumb">/ {esc(crumb)}</span>' if crumb else ""
    if parent:
        crumb_html = f'<span class="crumb">/ <a href="{parent[1]}" style="padding:0">{esc(parent[0])}</a></span>' + crumb_html
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Scaffold: {esc(title)}</title>
<style>{CSS}</style>{head}</head>
<body><nav><b>SCAFFOLD</b>{menu}{crumb_html}</nav><main>
{body}
</main></body></html>"""

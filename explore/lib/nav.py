"""Shared horizontal menu for the exploration pages in explore/. Each page lives in its own folder under explore/,
so links go up one level."""
PAGES = (("Test", "../tests/data-tests.html"),
         ("Industry Code Breakdown", "../codes/industry-codes.html"),
         ("Amazon", "../amazon/amazon.html"))

CSS = """
nav.top { position:sticky; top:0; z-index:10; background:var(--bg); border-bottom:1px solid var(--line);
          margin:-24px -16px 18px; padding:0 16px; }
nav.top div { max-width:1250px; margin:0 auto; display:flex; gap:4px; flex-wrap:wrap; }
nav.top a { padding:12px 14px; color:var(--muted); text-decoration:none; border-bottom:3px solid transparent; }
nav.top a:hover { color:var(--fg); }
nav.top a.on { color:var(--fg); font-weight:600; border-bottom-color:#2563eb; }
"""


def html(active: str) -> str:
    links = "".join(f'<a href="{href}"{" class=on" if label == active else ""}>{label}</a>' for label, href in PAGES)
    return f'<nav class="top"><div>{links}</div></nav>'

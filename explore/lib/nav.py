"""Shared horizontal menu for the exploration pages in explore/. Each page lives in its own folder under explore/,
so links go up one level. Pages are grouped (James, 2026-11-08): Economy is the top-down stack (world, US people,
US output, companies), Companies is what the SEC data says about firms, Pipeline is whether the data is right.
Appendix, last and ungrouped, lists every page not in the menu."""
GROUPS = (
    ("Economy", (("World", "../world/world.html"),
                 ("People and Work", "../people/people.html"),
                 ("Where GDP Goes", "../trade/trade.html"),
                 ("Trade Flows", "../flows/flows.html"),
                 ("Government", "../government/government.html"),
                 ("Companies vs Economy", "../economy/economy.html"))),
    ("Companies", (("Industry Code Breakdown", "../codes/industry-codes.html"),
                   ("Peers", "../peers/peers.html"),
                   ("Amazon", "../amazon/amazon.html"),
                   ("Insiders", "../insiders/insiders.html"),
                   ("Reporting Consistency", "../consistency/consistency.html"))),
    ("Pipeline", (("Data", "../data/data.html"),
                  ("Data Tests", "../tests/data-tests.html"),
                  ("Code Tests", "../suite/code-tests.html"),
                  ("Log", "../log/log.html"))),
    (None, (("Appendix", "../appendix/index.html"),)),
)
PAGES = tuple(p for _, pages in GROUPS for p in pages)

CSS = """
nav.top { position:sticky; top:0; z-index:10; background:var(--bg); border-bottom:1px solid var(--line);
          margin:-24px -16px 18px; padding:0 16px; }
nav.top div { max-width:1250px; margin:0 auto; display:flex; flex-wrap:wrap; align-items:center; column-gap:22px; }
nav.top span.grp { display:flex; flex-wrap:wrap; align-items:center; gap:4px; }
nav.top a { padding:12px 10px; color:var(--muted); text-decoration:none; border-bottom:3px solid transparent; }
nav.top a:hover { color:var(--fg); }
nav.top a.on { color:var(--fg); font-weight:600; border-bottom-color:#2563eb; }
nav.top span.g { padding-right:2px; color:var(--muted); font-size:12px; font-weight:600; letter-spacing:.04em;
                 text-transform:uppercase; }
"""


def html(active: str) -> str:
    def link(label, href):
        return f'<a href="{href}"{" class=on" if label == active else ""}>{label}</a>'
    groups = []   # each group wraps as one unit, so a label never sits apart from its pages
    for group, pages in GROUPS:
        label = f'<span class="g">{group}</span>' if group else ""
        groups.append(f'<span class="grp">{label}{"".join(link(lb, href) for lb, href in pages)}</span>')
    return f'<nav class="top"><div>{"".join(groups)}</div></nav>'

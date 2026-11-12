"""Scaffold: Pipeline. How a run moves data, drawn from the pipeline's own code at build time (2026-11-11).

Structure only, no run results. Every box comes from a registry in src/pharos: the commands (cli.py), the source
groups (groups.GROUPS), each group's loaders (refresh.LOADERS, REFERENCE_LOADERS) and the raw datasets they write
(each loader module's write_raw calls), the stagers (stage.STAGERS) and the checks (checks.discover, from each
check file's header). A registry name that can't be resolved, a group with no loader or stager, a raw dataset no
loader writes, or a run step that is no longer in the code stops the build (fail loud).
Run: uv run python scaffold/pipeline.py"""
import ast
import inspect
import io
import re
import textwrap
import tokenize
from pathlib import Path

from common import MERMAID, esc, now_et, page

from pharos import checks as checks_mod
from pharos import cli, refresh, stage
from pharos.groups import GROUPS, RUN, nightly

HERE = Path(__file__).parent

# ---- read the code -------------------------------------------------------------------------------------------------

CHECKS = checks_mod.discover()
CLI_SRC = inspect.getsource(cli)
COMMANDS = set(re.findall(r'add_parser\("([\w-]+)"', CLI_SRC))


def need(cond, msg):
    if not cond:
        raise SystemExit(f"pipeline: {msg}")


def writes(module):
    """(source, dataset) pairs a loader module writes, from its write_raw(frame, source, dataset, ...) calls."""
    out = []
    for node in ast.walk(ast.parse(inspect.getsource(module))):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "write_raw":
            pair = []
            for arg in node.args[1:3]:
                if isinstance(arg, ast.Constant):
                    pair.append(arg.value)
                else:
                    need(isinstance(arg, ast.Name) and hasattr(module, arg.id),
                         f"{module.__name__}: write_raw argument {ast.dump(arg)} doesn't resolve")
                    pair.append(getattr(module, arg.id))
            out.append(tuple(pair))
    need(out, f"{module.__name__} has no write_raw call")
    return out


def calls(fn):
    """The loader calls inside a group's load function: [(module, function name)] in order."""
    out = []
    for node in ast.walk(ast.parse(inspect.getsource(fn).strip())):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            mod = getattr(refresh, node.func.value.id, None)
            if mod is not None and inspect.ismodule(mod):
                need(hasattr(mod, node.func.attr), f"{mod.__name__}.{node.func.attr} not found")
                out.append((mod, node.func.attr))
    need(out, f"{fn.__name__} calls no loader")
    return out


# the run's steps, in order, as refresh._run names them in its step lines; a label that left the code stops the build
RUN_SRC = inspect.getsource(refresh._run) + inspect.getsource(stage.stage)
STEPS = ["{name} load", "reference {key[0]}/{key[1]} load", "{group.name} stage", "checks and publish",
         "catalog rebuild", "manifest and health"]
for s in STEPS:
    need(f'step(f"{s}")' in RUN_SRC or f'step("{s}")' in RUN_SRC, f"run step {s!r} is no longer in refresh._run")

# command -> groups it runs: refresh attempts the nightly groups; an on-demand group runs by its own command
need("refresh" in COMMANDS, "cli has no refresh command")
CMD = {"refresh": [g.name for g in nightly()]}
for g in GROUPS.values():
    if g.on_demand:
        need(g.name in COMMANDS, f"on-demand group {g.name} has no `pharos {g.name}` command")
        CMD[g.name] = [g.name]
need("companyfacts" in COMMANDS, "cli has no companyfacts command")
CMD["companyfacts"] = ["sec"]
CMD_NOTE = {"refresh": "", "sec": "", "companyfacts": "rare: a new full Company Facts baseline"}

GROUP = {}
for name, g in GROUPS.items():
    need(name in refresh.LOADERS, f"group {name} has no loader in refresh.LOADERS")
    need(name in stage.STAGERS, f"group {name} has no stager in stage.STAGERS")
    loads = [(m, f, writes(m)) for m, f in calls(refresh.LOADERS[name])]
    refs = []
    for key, fn in refresh.REFERENCE_LOADERS.items():
        if key in g.reference:
            refs += [(m, f, writes(m)) for m, f in calls(fn)]
    written = {ds for _, _, w in loads + refs for ds in w}
    for ds in g.datasets:
        need(ds in written, f"group {name}: raw dataset {ds[0]}/{ds[1]} is written by none of its loaders")
    mine = [c for c in CHECKS if c.group == name]
    GROUP[name] = dict(g=g, loads=loads, refs=refs, stager=stage.STAGERS[name].__name__,
                       gates=[c for c in mine if c.gate], monitors=[c for c in mine if not c.gate])
RUN_CHECKS = [c for c in CHECKS if c.group == RUN]
need(len(CHECKS) == sum(len(v["gates"]) + len(v["monitors"]) for v in GROUP.values()) + len(RUN_CHECKS),
     "a check names a group that isn't registered")

# ---- diagrams ------------------------------------------------------------------------------------------------------

STYLE = """
    classDef cmd fill:#F6EFFF,stroke:#000,stroke-width:2px,color:#000
    classDef work fill:#F3F7FF,stroke:#000,stroke-width:2px,color:#000
    classDef data fill:#FFFEE5,stroke:#000,stroke-width:2px,color:#000
    classDef gate fill:#FCE9E6,stroke:#000,stroke-width:2px,color:#000
    classDef good fill:#EFFFF2,stroke:#000,stroke-width:2px,color:#000"""


def ids(cs):
    return " ".join(c.id for c in cs)


def overview():
    L = ["flowchart TB"]
    for cmd, groups in CMD.items():
        note = f"<br/><i>{CMD_NOTE[cmd]}</i>" if CMD_NOTE[cmd] else ""
        L.append(f'    c_{cmd}["pharos {cmd}{note}"]:::cmd')
        for gname in groups:
            L.append(f"    c_{cmd} --> g_{gname}")
    for gname, v in GROUP.items():
        n_raw = len(v["g"].datasets)
        srcs = ", ".join(dict.fromkeys(m.__name__.rsplit(".", 1)[-1] for m, _, _ in v["loads"] + v["refs"]))
        L.append(f'    subgraph g_{gname}["{gname}"]')
        L.append("        direction TB")
        L.append(f'        {gname}_load["Load: {srcs}<br/>save {n_raw} raw files, write-once"]:::work')
        L.append(f'        {gname}_stage["Stage<br/>clean and combine into<br/>{len(v["g"].tables)} tables"]:::work')
        n = len(v["gates"])
        gate = f"{n} gate checks:<br/>does one with<br/>severity error fail?" if n else "No gate checks:<br/>nothing can<br/>block a publish"
        L.append(f'        {gname}_check{{"{gate}"}}:::gate')
        L.append(f'        {gname}_pub["Publish<br/>new tables served"]:::good')
        L.append(f'        {gname}_keep["Keep the last<br/>good tables"]:::data')
        L.append(f"        {gname}_load --> {gname}_stage --> {gname}_check")
        L.append(f"        {gname}_check -->|{'no' if n else 'always'}| {gname}_pub")
        if n:
            L.append(f"        {gname}_check -->|yes| {gname}_keep")
        L.append(f"        {gname}_load -.->|fails| {gname}_keep")
        L.append(f"        {gname}_stage -.->|fails| {gname}_keep")
        L.append("    end")
    L.append('    served["Served version<br/>serving/, catalog.duckdb rebuilt"]:::good')
    for gname in GROUP:
        L.append(f"    {gname}_pub --> served")
        L.append(f"    {gname}_keep --> served")
    mon = sum(len(v["monitors"]) for v in GROUP.values())
    L.append(f'    health["Run record and health<br/>{mon + len(RUN_CHECKS)} monitor checks,<br/>evaluated again whenever health is read"]:::work')
    L.append('    pages["Pages and DBeaver<br/>read served data only"]:::good')
    L.append("    served --> health --> pages")
    for node, pid in CLICKS.items():   # a box with a drill-in panel opens it on click
        L.append(f'    click {node} call openDrawer("{pid}")')
    return "\n".join(L) + STYLE


def rows5(cs):
    """Check IDs five to a line, so a long list stays a compact box."""
    return "<br/>".join(" ".join(c.id for c in cs[i:i + 5]) for i in range(0, len(cs), 5))


def source_diagram(gname, load, ref):
    """One source in detail, left to right: its loader, the raw files it writes, the group's stager."""
    v = GROUP[gname]
    m, f, w = load
    mod = m.__name__.rsplit(".", 1)[-1]
    arrow = "-.->" if ref else "-->"
    L = ["flowchart LR", f'    l["{mod}.{f}{"<br/><i>reference: never required</i>" if ref else ""}"]:::work',
         f'    st["stage.{v["stager"]}"]:::work']
    for s, d in w:
        if (s, d) in v["g"].datasets:
            L.append(f'    r_{s}_{d}["raw/{s}/{d}"]:::data')
            L.append(f"    l {arrow} r_{s}_{d} {arrow} st")
    return "\n".join(L) + STYLE


def group_details(gname):
    v = GROUP[gname]
    return "".join(f'<pre class="mermaid">{esc(source_diagram(gname, x, x in v["refs"]))}</pre>'
                   for x in v["loads"] + v["refs"])


def check_table(cs):
    rows = "".join(f"<tr><td>{c.id}</td><td>{'gate' if c.gate else 'monitor'}</td><td>{c.severity}</td>"
                   f"<td>{esc(c.description)}</td></tr>" for c in cs)
    return f"<table><tr><th>Check</th><th>Kind</th><th>Severity</th><th>What it catches</th></tr>{rows}</table>"


def runs_by(gname):
    return ", ".join(f"pharos {c}" for c, gs in CMD.items() if gname in gs)


# ---- drill-in panels (mock-up, 2026-11-12: the SEC Load box only) ------------------------------------------------
# A box on the summary opens a side panel: each function's real code, pulled from src/ at build time, with numbered
# notes pinned to lines. A note is pinned by a piece of text on its line, not a line number: when the code changes and
# that text is gone, the build stops, so a note can never sit beside the wrong line.

NOTES = {
    "sec_index.load_index": ("Filing index", "Every filing EDGAR lists, one file per quarter.", [
        ("for yq in due(today, have)", "Which quarters to fetch: any never landed, plus the current and previous "
                                       "quarter, which SEC is still adding to."),
        ("got = sec.fetch(URL", "Download the quarter's master.gz from SEC."),
        ("got.last_modified < held", "SEC sometimes serves an older copy than one already landed: skip it, and say "
                                     "so in the load record (D121)."),
        ("rows = parse(", "Parse the file. Every row must have exactly five fields, or the load stops."),
        ("if parsed != sum(counts.values())", "Count control: rows read back must equal rows parsed."),
        ("write_raw(index", "Save one write-once raw file for this run: raw/sec/full_index/<run>.parquet."),
        ("shutil.rmtree(tmp", "Delete the temporary files, whether the load worked or not."),
    ]),
    "sec_submissions.load_submissions": ("Submissions", "Each company's details and its filing history, "
                                                        "from one nightly zip.", [
        ("cutoff = watermark_cutoff", "Only filings since the last landing, less a few days of overlap, "
                                      "are kept from the filing history (the overlap is in config/sources.yaml)."),
        ("got = sec.fetch(URL, to=", "Download submissions.zip to a temporary folder; it is deleted once parsed."),
        ("details_file, filing_files, mains = parse(", "Parse every company's file in the zip into two tables: "
                                                        "details (one row per company) and filings."),
        ("if n_details != mains", "Count control: one details row per company file in the zip."),
        ("write_raw(details", "Save the two raw files: raw/sec/submission_details and raw/sec/submission_filings."),
    ]),
    "sec_companyfacts.load_incremental_companyfacts": ("Company Facts", "Every XBRL number a company reported; "
                                                                        "only companies that filed since the last run.", [
        ("if not has_baseline()", "First run on a new data root: download the whole companyfacts.zip instead."),
        ("ciks = changed_ciks(run_id)", "Which companies to fetch: those with new filings in this run's submissions."),
        ("got = sec.fetch(COMPANY_URL", "One request per company, about 9 a second, under SEC's limit of 10."),
        ("if e.status != 404", "A company with no XBRL data answers 404. That is normal; any other error stops the load."),
        ("result = _land(", "Save three raw files: the facts, the concept list, and which companies changed, with a note of how many."),
    ]),
    "stage_sec.stage_group": ("Stage the five tables", "The SEC group's stager: one table at a time.", [
        ("One connection per dataset", "One table at a time, each with its own DuckDB connection, so memory never "
                                       "holds two of the 27-million-row tables at once."),
        ("sec_base = current_base(", "The base: the tables served now. New landings merge into them."),
        ("facts_base = current_base(", "Company Facts was once its own group. If the served version still records it "
                                       "that way, its tables come from that run; this ends after the first combined publish."),
        ("merged = _stage(FILING_DATASETS", "First the three filing tables: filings, submission filings, submission "
                                            "details."),
        ("merged.update(_stage(FACT_DATASETS", "Then the two Company Facts tables."),
    ]),
    "stage_sec._merge": ("The merge rule: newest value per key", "Every SEC table is built by this rule (D132).", [
        ("values = [c for c in columns if c not in ds.lineage]", "A row's values are what SEC says. Its lineage (which "
                                                                  "run landed it, when) is not a value: an identical "
                                                                  "re-landing changes nothing."),
        ("twice = con.execute(", "If one landing gives two different values for the same key, the group stops: staging "
                                 "never picks between two things SEC said at once."),
        ("CREATE TABLE {ds.table}_touched", "The keys the new landings carry."),
        ("QUALIFY changed AND", "For each of those keys, keep the newest value. A value that did not change keeps the "
                                "run that first landed it."),
        ("SELECT b.* FROM {ds.table}_base AS b ANTI JOIN", "Every key the new landings don't carry keeps its served "
                                                           "row: a key SEC stops listing is kept, not dropped (D84)."),
        ("_require_unique(con, ds)", "The key must be unique in the result, or the group stops."),
    ]),
    "checks._verdict": ("One group's verdict", "Each group's gate checks give it one verdict.", [
        ('if any(r["gate"] and r["status"] == "broken"', "A gate check whose SQL failed to run: the group fails. A "
                                                         "control that didn't run proves nothing (D77)."),
        ('if any(r["gate"] and r["status"] == "error"', "A gate check at error severity returned rows: the group is "
                                                        "blocked. Warn-severity and monitor checks never block (D17)."),
        ('return "passed"', "Otherwise the group passed."),
    ]),
    "publish.publish": ("What each group serves", "Runs the checks, then decides group by group (D81).", [
        ("evaluation = run_checks(run_id)", "Every check runs on this run's staged tables; each group gets its "
                                            "verdict (the card above)."),
        ('reasons[g] = f"gate checks {verdict}', "A group that didn't pass records which gate checks stopped it."),
        ("if not passed:", "No group passed: nothing publishes. No new version, CURRENT unchanged."),
        ("if g not in tried:", "A group this command didn't run (SEC in pharos refresh, prices in pharos sec): "
                               "served unchanged, and not a failure (D135)."),
        ("elif g in passed:", "Passed: its tables from this run are published."),
        ("elif g in held:", "Didn't pass but is served now: carried forward, its last good tables stay."),
        ('"outcome": NEVER_PUBLISHED', "Didn't pass and was never published: the version goes out without it (D83)."),
        ("_point(run_id)", "The swap: CURRENT now names the new version. Readers move to it at once."),
        ("prune_serving(keep=", "Every older version is deleted except the one just replaced (D133)."),
    ]),
    "publish._build": ("Building the new version", "Assembled where no reader looks, then renamed into place.", [
        ('building = serving_dir() / f".building-{run_id}"', "Built under a temporary name that nothing reads."),
        ('if o["outcome"] == PUBLISHED:', "A published group: tables from this run's staging, plus its inputs "
                                          "(what view-time health reads instead of raw/ and config/, D76)."),
        ('elif o["outcome"] in (CARRIED_FORWARD, NOT_RUN)', "Carried forward or not run: tables and inputs copied "
                                                            "from the version served now."),
        ('_copy(source / f"{table}.parquet"', "Every copy checks its row count against its source, or the publish "
                                              "stops."),
        ("COPY g TO", "groups.parquet records each group's outcome, the run its tables came from, and why."),
        ("os.rename(building, version)", "Only now is the folder renamed into place."),
    ]),
}


def code_card(fn, key, rows=()):
    """One function's card: name, what it does, facts rows, the code with numbered notes (NOTES[key])."""
    need(key in NOTES, f"no drill-in notes for {key}")
    src, first = inspect.getsourcelines(fn)
    # real comments only, by Python's tokenizer: line index -> column where the comment starts
    dedented = textwrap.dedent("".join(src))
    indent = len(src[0]) - len(src[0].lstrip())
    comments = {t.start[0] - 1: t.start[1] + indent for t in tokenize.generate_tokens(io.StringIO(dedented).readline)
                if t.type == tokenize.COMMENT}
    return card(key, src, first, Path(inspect.getsourcefile(fn)), comments, [("Function", key)] + list(rows))


def card(key, src, first, path, comments, rows):
    """name, what it does, facts rows, the code with numbered notes (NOTES[key]) pinned by text on their lines."""
    need(key in NOTES, f"no drill-in notes for {key}")
    name, what, notes = NOTES[key]
    marks = {}
    for i, (anchor, _) in enumerate(notes, 1):
        hits = [n for n, line in enumerate(src) if anchor in line]
        need(len(hits) == 1, f"{key}: note {i} anchor {anchor!r} matches {len(hits)} lines, not 1")
        marks[hits[0]] = i
    lines = []
    for n, line in enumerate(src):
        line = line.rstrip("\n")
        if n in comments:
            at = comments[n]
            text = f'{esc(line[:at])}<span class="cm">{esc(line[at:])}</span>'
        else:
            text = esc(line)
        mark = f'<span class="mk">{marks[n]}</span>' if n in marks else '<span class="mk0"></span>'
        lines.append(f'<div class="ln{" hit" if n in marks else ""}"><span class="no">{first + n}</span>{mark}{text}</div>')
    facts = "".join(f"<tr><td>{esc(k)}</td><td>{esc(v)}</td></tr>" for k, v in rows)
    note_list = "".join(f"<li>{esc(t)}</li>" for _, t in notes)
    return f"""<section class="card"><h3>{esc(name)}</h3><p>{esc(what)}</p>
<table class="facts">{facts}
<tr><td>File</td><td><a href="{path.as_uri()}">{esc(path.name)}</a>, lines {first}-{first + len(src) - 1}</td></tr></table>
<ol class="notes">{note_list}</ol><div class="code">{"".join(lines)}</div></section>"""


def plain_code(fn):
    """A short function shown whole, line-numbered, no notes."""
    src, first = inspect.getsourcelines(fn)
    return '<div class="code">' + "".join(
        f'<div class="ln"><span class="no">{first + n}</span><span class="mk0"></span>{esc(l.rstrip())}</div>'
        for n, l in enumerate(src)) + "</div>"


def load_panel(gname):
    cards = ""
    for m, f, _ in GROUP[gname]["loads"]:
        url = next((getattr(m, a) for a in ("URL", "COMPANY_URL") if hasattr(m, a)), "")
        raw = ", ".join(f"raw/{s}/{d}" for s, d in writes(m))
        cards += code_card(getattr(m, f), f"{m.__name__.rsplit('.', 1)[-1]}.{f}", [("Reads", url), ("Writes", raw)])
    return (f"Load: {gname}", f"""<p class=sub>The {len(GROUP[gname]["loads"])} loaders, in the order a run calls them (refresh.py):</p>
{plain_code(refresh.LOADERS[gname])}{cards}""")


def sec_stage_panel():
    """Stage: the six tables, the route each takes, and the merge rule they share."""
    from pharos import stage_sec as ss
    need({d.table for d in ss.DATASETS} == set(GROUP["sec"]["g"].tables),
         "stage_sec.DATASETS and the sec group's tables differ")
    route = {}
    for d in ss.DATASETS:   # mirrors stage_sec._stage_one; a dataset none of these covers stops the build
        route[d.table] = ("one folder per company; only changed companies re-merged" if d in ss.FACT_DATASETS else
                          "one folder per quarter; only quarters in the new landing re-merged" if d in (ss.INDEX, ss.SUBMISSIONS)
                          else "one file, merged whole")
    one = inspect.getsource(ss._stage_one)
    need("if ds in FACT_DATASETS:" in one and "if ds in (INDEX, SUBMISSIONS):" in one,
         "stage_sec._stage_one's routes changed: update the How it is written column")
    sql_dir = Path(inspect.getsourcefile(ss)).parent / "transform"
    rows = ""
    for d in ss.DATASETS:
        sql = sql_dir / f"{d.sql}.sql"
        need(sql.exists(), f"{sql} not on disk")
        rows += (f"<tr><td>{d.table}</td><td>raw/sec/{d.raw}</td><td><a href=\"{sql.as_uri()}\">{sql.name}</a></td>"
                 f"<td>{esc(', '.join(d.key))}</td><td>{route[d.table]}</td></tr>")
    table = ("<table><tr><th>Staged table</th><th>From raw</th><th>Cleaned by</th><th>Key</th><th>How it is written</th></tr>"
             f"{rows}</table>")
    return ("Stage: sec", f"""<p class=sub>{len(ss.DATASETS)} tables, in the order a run stages them. Each raw landing is cleaned by its
SQL file, then merged into the table served now by one rule: newest value per key.</p>{table}
{code_card(ss.stage_group, "stage_sec.stage_group")}{code_card(ss._merge, "stage_sec._merge")}""")


def publish_panel():
    """Check and publish: the same code for every group."""
    from pharos import publish as pub
    return ("Check and publish", f"""<p class=sub>The same for every group: its gate checks give it a verdict,
then each group's tables are published, carried forward, or left as they are.</p>
{code_card(checks_mod._verdict, "checks._verdict")}{code_card(pub.publish, "publish.publish")}
{code_card(pub._build, "publish._build")}""")


PANELS = {"sec_load": lambda: load_panel("sec"), "sec_stage": sec_stage_panel, "publish": publish_panel}
# summary box -> the panel it opens: each group's check, publish and keep boxes all open Check and publish
CLICKS = {"sec_load": "sec_load", "sec_stage": "sec_stage",
          **{f"{g}_{box}": "publish" for g in GROUPS for box in ("check", "pub", "keep")}}


def drawer(pid):
    title, html_ = PANELS[pid]()
    return f"""<aside class="drawer" id="drawer_{pid}" hidden>
<div class="dhead"><h2>{esc(title)}</h2><button onclick="closeDrawer()">Close (Esc)</button></div>
{html_}</aside>"""


DRAWER_CSS = """<style>
.drawer { position:fixed; top:0; right:0; bottom:0; width:min(980px, 94vw); background:#fff; border-left:2px solid #1d1d1b;
          box-shadow:-8px 0 24px rgba(0,0,0,.12); overflow:auto; padding:0 24px 40px; z-index:10; }
.dhead { position:sticky; top:0; background:#fff; display:flex; justify-content:space-between; align-items:center;
         border-bottom:1px solid var(--line); }
.dhead h2 { border:0; margin:14px 0; }
.dhead button { font:inherit; padding:4px 12px; cursor:pointer; }
.card { border-top:2px solid #1d1d1b; margin-top:26px; }
.code { background:var(--box); border:1px solid var(--line); padding:8px 0; overflow-x:auto; font-size:14px; }
.ln { white-space:pre; padding:0 10px 0 0; }
.ln.hit { background:#fff6d6; }
.no { display:inline-block; width:3.2em; color:var(--muted); text-align:right; margin-right:8px; user-select:none; }
.mk, .mk0 { display:inline-block; width:1.6em; margin-right:6px; text-align:center; }
.mk { background:#2563eb; color:#fff; border-radius:9px; font-size:12px; font-weight:700; }
.cm { color:var(--muted); }
ol.notes { padding-left:1.6em; } ol.notes li { margin:3px 0; }
</style>"""

DRAWER_JS = """<script>
function openDrawer(id) { closeDrawer(); const d = document.getElementById("drawer_" + id); d.hidden = false; d.scrollTop = 0; }
function closeDrawer() { document.querySelectorAll(".drawer").forEach(d => d.hidden = true); }
document.addEventListener("keydown", e => { if (e.key === "Escape") closeDrawer(); });
window.addEventListener("load", () => { const h = location.hash.slice(1); if (document.getElementById("drawer_" + h)) openDrawer(h); });
</script>"""

sections = "".join(
    f"""<h2 id="{g}">{g}</h2>
<p class=sub>Run by {esc(runs_by(g))}. Raw files: {len(v['g'].datasets)}.
Staged tables: {esc(', '.join(v['g'].tables))}. Checks: {len(v['gates'])} gate, {len(v['monitors'])} monitor.</p>
{group_details(g)}
{check_table(v['gates'] + v['monitors'])}"""
    for g, v in GROUP.items())

body = f"""<div class="head"><h1>Pipeline</h1>
<div class="stamps"><span>Page built {now_et()}</span></div></div>
<p class=sub>Drawn from the pipeline's code when this page was built: commands, source groups, loaders, stagers and
check headers. A gate check runs on the staged tables before publish: if one with severity error fails, or the group's
load or stage fails, the group is not published and its last good tables stay served. A monitor check never blocks.</p>

<h2>Summary: one run</h2>
<pre class="mermaid">{esc(overview())}</pre>
<h3>Checks on the run itself</h3>
{check_table(RUN_CHECKS)}
{sections}
{''.join(drawer(p) for p in PANELS)}"""

head = DRAWER_CSS + DRAWER_JS + (f'<script src="{MERMAID}"></script><script>document.addEventListener("DOMContentLoaded", () => '
        f'mermaid.initialize({{startOnLoad:true, securityLevel:"loose", theme:"base", flowchart:{{useMaxWidth:true, htmlLabels:true}}, '
        f'themeVariables:{{fontFamily:"system-ui, Segoe UI, sans-serif", fontSize:"14px"}}}}));</script>')
(HERE / "pipeline.html").write_text(page("Pipeline", "Pipeline", body, head=head), encoding="utf-8")
print(f"wrote {HERE / 'pipeline.html'}: {len(GROUP)} groups, {len(CHECKS)} checks, commands {sorted(CMD)}")

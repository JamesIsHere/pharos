"""Scaffold: Pipeline. How a run moves data, drawn from the pipeline's own code at build time (2026-11-11).

Structure only, no run results. Every box comes from a registry in src/pharos: the commands (cli.py), the source
groups (groups.GROUPS), each group's loaders (refresh.LOADERS, REFERENCE_LOADERS) and the raw datasets they write
(each loader module's write_raw calls), the stagers (stage.STAGERS) and the checks (checks.discover, from each
check file's header). A registry name that can't be resolved, a group with no loader or stager, a raw dataset no
loader writes, or a run step that is no longer in the code stops the build (fail loud).
Run: uv run python scaffold/pipeline.py"""
import ast
import inspect
import re
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
        L.append(f'    subgraph g_{gname}["{gname}"]')
        L.append("        direction TB")
        L.append(f'        {gname}_load["Load<br/>download each source,<br/>save {n_raw} raw files, write-once"]:::work')
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
    return "\n".join(L) + STYLE


def rows5(cs):
    """Check IDs five to a line, so a long list stays a compact box."""
    return "<br/>".join(" ".join(c.id for c in cs[i:i + 5]) for i in range(0, len(cs), 5))


def group_diagram(gname):
    v = GROUP[gname]
    L = ["flowchart TB"]
    for i, (m, f, w) in enumerate(v["loads"] + v["refs"]):
        ref = (m, f, w) in v["refs"]
        mod = m.__name__.rsplit(".", 1)[-1]
        L.append(f'    l{i}["{mod}.{f}{"<br/><i>reference: never required</i>" if ref else ""}"]:::work')
        for s, d in w:
            if (s, d) in v["g"].datasets:
                node = f"r_{s}_{d}"
                L.append(f'    {node}["raw/{s}/{d}"]:::data')
                L.append(f"    l{i} {'-.->' if ref else '-->'} {node}")
    L.append(f'    st["stage.{v["stager"]}"]:::work')
    for s, d in v["g"].datasets:
        L.append(f"    r_{s}_{d} {'-.->' if (s, d) in v['g'].reference else '-->'} st")
    L.append(f'    tb["Staged tables<br/>{"<br/>".join(v["g"].tables)}"]:::data')
    gate = f'Gate checks<br/>{rows5(v["gates"])}' if v["gates"] else "Gate checks<br/>none"
    L.append(f'    gk["{gate}"]:::gate')
    L.append('    pub["Publish<br/>or keep the<br/>last good tables"]:::good')
    mon = f'Monitor checks<br/>{rows5(v["monitors"])}' if v["monitors"] else "Monitor checks<br/>none"
    L.append(f'    mk["{mon}"]:::work')
    L.append("    st --> tb --> gk --> pub --> mk")
    return "\n".join(L) + STYLE


def check_table(cs):
    rows = "".join(f"<tr><td>{c.id}</td><td>{'gate' if c.gate else 'monitor'}</td><td>{c.severity}</td>"
                   f"<td>{esc(c.description)}</td></tr>" for c in cs)
    return f"<table><tr><th>Check</th><th>Kind</th><th>Severity</th><th>What it catches</th></tr>{rows}</table>"


def runs_by(gname):
    return ", ".join(f"pharos {c}" for c, gs in CMD.items() if gname in gs)


sections = "".join(
    f"""<h2 id="{g}">{g}</h2>
<p class=sub>Run by {esc(runs_by(g))}. Raw files: {len(v['g'].datasets)}. Tables: {len(v['g'].tables)}.
Checks: {len(v['gates'])} gate, {len(v['monitors'])} monitor.</p>
<pre class="mermaid">{esc(group_diagram(g))}</pre>
{check_table(v['gates'] + v['monitors'])}"""
    for g, v in GROUP.items())

body = f"""<div class="head"><h1>Pipeline</h1>
<div class="stamps"><span>Page built {now_et()}</span></div></div>
<p class=sub>Drawn from the pipeline's code when this page was built: commands, source groups, loaders, stagers and
check headers. A gate check runs on the staged tables before publish: if one with severity error fails, or the group's
load or stage fails, the group is not published and its last good tables stay served. A monitor check never blocks.</p>

<h2>One run</h2>
<pre class="mermaid">{esc(overview())}</pre>
<h3>Checks on the run itself</h3>
{check_table(RUN_CHECKS)}
{sections}"""

head = (f'<script src="{MERMAID}"></script><script>document.addEventListener("DOMContentLoaded", () => '
        f'mermaid.initialize({{startOnLoad:true, theme:"base", flowchart:{{useMaxWidth:true, htmlLabels:true}}, '
        f'themeVariables:{{fontFamily:"system-ui, Segoe UI, sans-serif", fontSize:"14px"}}}}));</script>')
(HERE / "pipeline.html").write_text(page("Pipeline", "Pipeline", body, head=head), encoding="utf-8")
print(f"wrote {HERE / 'pipeline.html'}: {len(GROUP)} groups, {len(CHECKS)} checks, commands {sorted(CMD)}")

"""Run pytest and build a browser page containing every test's result and runtime.

The XML is temporary. Rebuilding the HTML runs the suite again, so the page
always describes one real run on this machine.
"""
import html as h
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from pharos.paths import PROJECT_ROOT

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "lib"))
import nav  # noqa: E402


def seconds(value: float) -> str:
    if value >= 60:
        return f"{int(value) // 60}m {value % 60:04.1f}s"
    if value >= 1:
        return f"{value:.2f}s"
    return f"{value * 1000:.0f}ms"


def status_of(case: ET.Element) -> tuple[str, str]:
    for status in ("failure", "error", "skipped"):
        child = case.find(status)
        if child is not None:
            return status, child.get("message", "")
    return "passed", ""


def sec_area(file: str) -> str:
    name = Path(file).stem
    if name.startswith("test_stage_sec"):
        return "SEC staging"
    if name.startswith("test_sec_"):
        return "SEC loaders"
    return "Other"


started_at = datetime.now().astimezone()
with tempfile.TemporaryDirectory(prefix="pharos-test-timing-") as tmp:
    xml = Path(tmp) / "results.xml"
    command = [
        sys.executable, "-m", "pytest", "-q", "--disable-warnings",
        "--junitxml", str(xml), "-o", "junit_family=xunit1",
    ]
    clock = time.perf_counter()
    run = subprocess.run(
        command, cwd=PROJECT_ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    wall_seconds = time.perf_counter() - clock
    if not xml.exists():
        raise RuntimeError("pytest produced no timing XML:\n" + (run.stdout + run.stderr)[-4000:])
    root = ET.parse(xml).getroot()

cases = []
for case in root.iter("testcase"):
    file = case.get("file") or case.get("classname", "").replace(".", "/") + ".py"
    status, message = status_of(case)
    duration = float(case.get("time", 0))
    cases.append({
        "file": file.replace("\\", "/"),
        "line": case.get("line", ""),
        "name": case.get("name", ""),
        "duration": duration,
        "status": status,
        "message": message,
        "area": sec_area(file),
    })

if not cases:
    raise RuntimeError("pytest timing XML contained no tests")

by_file = defaultdict(lambda: {"tests": 0, "seconds": 0.0, "failed": 0, "area": "Other"})
by_area = defaultdict(lambda: {"tests": 0, "seconds": 0.0})
for case in cases:
    file = by_file[case["file"]]
    file["tests"] += 1
    file["seconds"] += case["duration"]
    file["failed"] += case["status"] in ("failure", "error")
    file["area"] = case["area"]
    by_area[case["area"]]["tests"] += 1
    by_area[case["area"]]["seconds"] += case["duration"]

counts = {
    status: sum(case["status"] == status for case in cases)
    for status in ("passed", "failure", "error", "skipped")
}
test_seconds = sum(case["duration"] for case in cases)
sec_seconds = by_area["SEC loaders"]["seconds"] + by_area["SEC staging"]["seconds"]
sec_tests = by_area["SEC loaders"]["tests"] + by_area["SEC staging"]["tests"]

file_rows = []
for file, values in sorted(by_file.items(), key=lambda item: item[1]["seconds"], reverse=True):
    share = 100 * values["seconds"] / test_seconds if test_seconds else 0
    file_rows.append(
        f"<tr data-area='{h.escape(values['area'])}'><td><code>{h.escape(file)}</code></td>"
        f"<td>{h.escape(values['area'])}</td><td class=num>{values['tests']}</td>"
        f"<td class=num>{seconds(values['seconds'])}</td><td class=num>{share:.1f}%</td>"
        f"<td class=num>{values['failed'] or ''}</td></tr>"
    )

test_rows = []
for case in sorted(cases, key=lambda item: item["duration"], reverse=True):
    node = f"{case['file']}::{case['name']}"
    search = h.escape(node.lower(), quote=True)
    message = f"<div class=message>{h.escape(case['message'])}</div>" if case["message"] else ""
    test_rows.append(
        f"<tr data-search='{search}' data-area='{h.escape(case['area'])}' "
        f"data-status='{case['status']}' data-seconds='{case['duration']:.9f}'>"
        f"<td><code>{h.escape(case['file'])}</code></td>"
        f"<td><code>{h.escape(case['name'])}</code>{message}</td>"
        f"<td>{h.escape(case['area'])}</td>"
        f"<td><span class='status {case['status']}'>{case['status']}</span></td>"
        f"<td class=num>{seconds(case['duration'])}</td></tr>"
    )

areas = []
for area in ("SEC loaders", "SEC staging", "Other"):
    values = by_area[area]
    share = 100 * values["seconds"] / test_seconds if test_seconds else 0
    areas.append(
        f"<div class=area><b>{h.escape(area)}</b><span>{values['tests']} tests</span>"
        f"<strong>{seconds(values['seconds'])}</strong>"
        f"<small>{share:.1f}% of measured test time</small></div>"
    )

outcome = "passed" if run.returncode == 0 else "failed"
output_tail = (run.stdout + "\n" + run.stderr).strip().splitlines()[-8:]
console = "\n".join(output_tail)
built = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")

page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pharos code tests</title>
<style>
:root {{ --bg:#fff; --fg:#1d1d1b; --muted:#5f5e58; --line:#e3e1da; --box:#f6f5f1;
         --green:#166534; --green-bg:#dcfce7; --red:#991b1b; --red-bg:#fee2e2; }}
body {{ background:var(--bg); color:var(--fg); font:15px/1.5 system-ui,"Segoe UI",sans-serif;
        margin:0; padding:24px 16px 60px; }}
main {{ max-width:1250px; margin:0 auto; }}
h1 {{ font-size:26px; margin:0 0 6px; }}
h2 {{ font-size:20px; margin:30px 0 8px; }}
p {{ margin:6px 0; }}
.muted {{ color:var(--muted); }}
.lead {{ background:var(--box); padding:14px 16px; margin:12px 0 18px; }}
.summary {{ display:grid; grid-template-columns:repeat(4,minmax(130px,1fr)); gap:10px; margin:16px 0; }}
.stat,.area {{ background:var(--box); padding:12px 14px; }}
.stat span,.area span,.area small {{ color:var(--muted); display:block; }}
.stat strong,.area strong {{ display:block; font-size:22px; margin-top:2px; }}
.areas {{ display:grid; grid-template-columns:repeat(3,minmax(180px,1fr)); gap:10px; margin:12px 0; }}
.wrap {{ overflow-x:auto; }}
table {{ border-collapse:collapse; width:100%; font-size:13px; }}
th,td {{ border-bottom:1px solid var(--line); padding:6px 9px; text-align:left; vertical-align:top; }}
th {{ background:var(--bg); white-space:nowrap; }}
td.num,th.num {{ text-align:right; white-space:nowrap; }}
code {{ font:12px ui-monospace,Consolas,monospace; }}
.status {{ border-radius:10px; padding:1px 7px; }}
.status.passed {{ color:var(--green); background:var(--green-bg); }}
.status.failure,.status.error {{ color:var(--red); background:var(--red-bg); }}
.status.skipped {{ color:var(--muted); background:var(--box); }}
.message {{ color:var(--red); font-size:12px; margin-top:2px; }}
.controls {{ display:flex; gap:8px; flex-wrap:wrap; align-items:center; margin:10px 0; }}
.controls input {{ flex:1 1 320px; padding:7px 9px; border:1px solid var(--line); font:inherit; }}
.controls button {{ padding:7px 10px; border:1px solid var(--line); background:var(--bg); color:var(--fg); font:inherit; }}
.controls button.on {{ background:var(--fg); color:var(--bg); }}
pre {{ background:var(--box); padding:10px 12px; overflow-x:auto; font-size:12px; }}
{nav.CSS}
@media(max-width:720px) {{ .summary,.areas {{ grid-template-columns:1fr 1fr; }} }}
</style></head>
<body>{nav.html("Code Tests")}<main>
<h1>Code tests and timing</h1>
<p class=muted>One complete pytest run, built {built}. Timings include each test's setup, call and teardown.</p>
<div class=summary>
  <div class=stat><span>Outcome</span><strong>{outcome}</strong><small>{counts['passed']} passed, {counts['failure'] + counts['error']} failed, {counts['skipped']} skipped</small></div>
  <div class=stat><span>All tests</span><strong>{len(cases)}</strong><small>Every collected test is listed below</small></div>
  <div class=stat><span>Wall-clock run</span><strong>{seconds(wall_seconds)}</strong><small>What a developer waited</small></div>
  <div class=stat><span>SEC tests</span><strong>{seconds(sec_seconds)}</strong><small>{sec_tests} tests; {100 * sec_seconds / test_seconds if test_seconds else 0:.1f}% of measured test time</small></div>
</div>

<div class=lead><b>What counts as an expensive SEC test?</b> SEC loader tests exercise parsing and strict
source-shape validation. SEC staging tests repeatedly build and merge representative Parquet tables with DuckDB.
The file totals below show which groups actually dominate this run; no test is called slow merely because its name
contains SEC.</div>

<h2>Where the time goes</h2>
<div class=areas>{''.join(areas)}</div>
<div class=wrap><table>
<thead><tr><th>Test file</th><th>Area</th><th class=num>Tests</th><th class=num>Time</th><th class=num>Share</th><th class=num>Failed</th></tr></thead>
<tbody>{''.join(file_rows)}</tbody></table></div>

<h2>Every test, slowest first</h2>
<div class=controls>
  <input id=search type=search placeholder="Filter by file or test name" aria-label="Filter tests">
  <button class=on data-filter=all>All</button><button data-filter=sec>SEC only</button>
  <button data-filter=slow>At least 1 second</button><button data-filter=failed>Failed</button>
  <span id=shown class=muted></span>
</div>
<div class=wrap><table id=tests>
<thead><tr><th>File</th><th>Test</th><th>Area</th><th>Result</th><th class=num>Time</th></tr></thead>
<tbody>{''.join(test_rows)}</tbody></table></div>

<h2>Pytest output</h2><pre>{h.escape(console)}</pre>
</main>
<script>
const rows=[...document.querySelectorAll('#tests tbody tr')], search=document.getElementById('search'), shown=document.getElementById('shown');
let filter='all';
function apply() {{
  const q=search.value.trim().toLowerCase(); let n=0;
  for (const row of rows) {{
    const area=row.dataset.area, status=row.dataset.status, duration=Number(row.dataset.seconds);
    const kind=filter==='all' || (filter==='sec' && area.startsWith('SEC')) ||
      (filter==='slow' && duration>=1) || (filter==='failed' && ['failure','error'].includes(status));
    const visible=kind && (!q || row.dataset.search.includes(q));
    row.hidden=!visible; if(visible) n++;
  }}
  shown.textContent=`${{n}} of ${{rows.length}} tests`;
}}
search.addEventListener('input',apply);
for (const button of document.querySelectorAll('[data-filter]')) button.addEventListener('click',()=>{{
  filter=button.dataset.filter;
  document.querySelectorAll('[data-filter]').forEach(b=>b.classList.toggle('on',b===button));
  apply();
}});
apply();
</script></body></html>"""

out = HERE / "code-tests.html"
out.write_text(page, encoding="utf-8")
print(f"wrote {out} | {len(cases)} tests | {outcome} | wall {seconds(wall_seconds)} | SEC {seconds(sec_seconds)}")

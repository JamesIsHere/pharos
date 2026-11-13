"""Rebuild every Scaffold page in one command, in order. Each page script runs in its own process, as it would by
hand; the first one that fails stops the rebuild (fail loud) and names itself.
Run: uv run python scaffold/rebuild.py"""
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
# Catalog first (the source sheets hang off it), then the sheets, then the menu pages.
PAGES = ["build", "fred", "prices", "company_facts", "weo", "m3", "qfr", "housing", "housing_need", "pipeline", "notes", "appendix"]

missing = [p for p in PAGES if not (HERE / f"{p}.py").exists()]
if missing:
    raise SystemExit(f"rebuild: no script for {missing} in {HERE}")

t0 = time.perf_counter()
for p in PAGES:
    t = time.perf_counter()
    r = subprocess.run([sys.executable, str(HERE / f"{p}.py")], cwd=HERE.parent, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    last = (r.stdout.strip().splitlines() or [""])[-1]
    print(f"{p:<14} {time.perf_counter() - t:6.1f}s  {last[:160]}", flush=True)
    if r.returncode:
        sys.stderr.write(r.stderr)
        raise SystemExit(f"rebuild: {p}.py failed (exit {r.returncode}); later pages not rebuilt")
print(f"all {len(PAGES)} pages rebuilt in {time.perf_counter() - t0:.0f}s")

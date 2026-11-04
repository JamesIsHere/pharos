"""`pharos rebuild-staging`: rebuild an old run's staging from raw, at its own code (D103).

Staging is pruned at each publish, so a run's staging/ folder is gone once a
later run publishes. It is a fixed function of raw (kept forever), the code and
the config the run used, and the run manifest records the commit (git_commit).
A rebuild therefore:
- copies the run's raw inputs (every raw file and load record up to and
  including the run, the snapshot log staging reads, D18) into a throwaway
  data root under the system temp folder,
- checks out the run's commit as a detached git worktree in the same folder,
  so the code and config/ are the run's own, and
- runs that code's stage() there.
The real data root is only read. The worktree is removed afterwards; the
rebuilt staging folder stays for inspection and its path is returned. A run
recorded with a dirty tree (git_dirty) ran code the commit doesn't capture
exactly, so the result says so. A published run can be compared with its
version in serving/, table by table, row for row.
"""

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import duckdb

from pharos.db import query
from pharos.paths import PROJECT_ROOT, data_root
from pharos.runs import RUN_ID


class RebuildError(RuntimeError):
    pass


@dataclass(frozen=True)
class Rebuild:
    run_id: str
    commit: str
    dirty: bool
    staging: Path                     # <throwaway root>/staging/<run_id>/
    differences: dict[str, tuple[int, int]] | None   # table -> (rows only rebuilt, rows only served); None if not published


def run_code(run_id: str) -> tuple[str, bool]:
    """The commit and dirty flag the run recorded in its manifest."""
    path = data_root() / "health" / "run_manifest" / f"{run_id}.parquet"
    if not path.exists():
        raise RebuildError(f"no manifest for run {run_id}: {path}")
    cols, rows = query(f"SELECT * FROM read_parquet('{path.as_posix()}') LIMIT 1")
    row = dict(zip(cols, rows[0]))
    if not row.get("git_commit"):
        raise RebuildError(f"run {run_id} recorded no git commit (runs before D103 did not); "
                           "its staging can't be rebuilt at its own code")
    return row["git_commit"], bool(row["git_dirty"])


def rebuild_staging(run_id: str, code_dir: Path | None = None) -> Rebuild:
    """Rebuild staging/<run_id>/ in a throwaway root. code_dir overrides the
    worktree checkout (tests use the working copy); default is the run's commit."""
    if not RUN_ID.fullmatch(run_id):
        raise RebuildError(f"{run_id!r} is not a run id")
    commit, dirty = run_code(run_id)
    root = data_root()
    work = Path(tempfile.mkdtemp(prefix=f"pharos-rebuild-{run_id}-"))
    data = work / "data"
    _copy_inputs(root, data, run_id)

    worktree = None
    if code_dir is None:
        worktree = work / "code"
        _git("worktree", "add", "--detach", str(worktree), commit)
        code_dir = worktree
    try:
        env = {**os.environ, "PHAROS_TEST_MODE": "1", "PHAROS_DATA_ROOT": str(data)}
        done = subprocess.run(["uv", "run", "--project", str(code_dir), "python", "-c",
                               f"from pharos.stage import stage; stage({run_id!r})"],
                              cwd=code_dir, env=env, capture_output=True, text=True)
        if done.returncode != 0:
            raise RebuildError(f"stage() at {commit[:7]} failed for {run_id}:\n{done.stderr[-2000:]}")
    finally:
        if worktree is not None:
            _git("worktree", "remove", "--force", str(worktree))

    staging = data / "staging" / run_id
    served = root / "serving" / run_id
    return Rebuild(run_id, commit, dirty, staging, compare(staging, served) if served.is_dir() else None)


def compare(rebuilt: Path, served: Path) -> dict[str, tuple[int, int]]:
    """Row-for-row difference of every table in either folder: (rows only in
    the rebuild, rows only in the served version). (0, 0) is identical."""
    tables = sorted({p.stem for p in rebuilt.glob("*.parquet")} | {p.stem for p in served.glob("*.parquet")})
    out = {}
    con = duckdb.connect()
    for t in tables:
        a, b = rebuilt / f"{t}.parquet", served / f"{t}.parquet"
        if not a.exists() or not b.exists():
            raise RebuildError(f"table {t} is in only one of {rebuilt} and {served}")
        ra, rb = (f"read_parquet('{p.as_posix()}')" for p in (a, b))
        only_a = con.execute(f"SELECT count(*) FROM (SELECT * FROM {ra} EXCEPT ALL SELECT * FROM {rb})").fetchone()[0]
        only_b = con.execute(f"SELECT count(*) FROM (SELECT * FROM {rb} EXCEPT ALL SELECT * FROM {ra})").fetchone()[0]
        out[t] = (only_a, only_b)
    con.close()
    return out


def _copy_inputs(root: Path, data: Path, run_id: str) -> None:
    """Raw files and load records of every run up to run_id: what staging reads."""
    for f in (root / "raw").glob("*/*/*.parquet"):
        if f.stem <= run_id:
            target = data / f.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, target)
    loads = data / "health" / "loads"
    loads.mkdir(parents=True, exist_ok=True)
    for f in (root / "health" / "loads").glob("*.parquet"):
        if f.name.split("__", 1)[0] <= run_id:
            shutil.copy2(f, loads / f.name)


def _git(*args: str) -> None:
    done = subprocess.run(["git", "-C", str(PROJECT_ROOT), *args], capture_output=True, text=True)
    if done.returncode != 0:
        raise RebuildError(f"git {' '.join(args)} failed: {done.stderr.strip()}")

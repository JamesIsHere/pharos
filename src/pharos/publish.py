"""Write-audit-publish: staging/<run_id>/ -> serving/<run_id>/, behind the checks.

serving/ holds one write-once folder per published run and CURRENT, a one-line
file naming the version readers use (D30). Publishing copies the staged tables
into a new folder nobody reads yet, then replaces CURRENT. That replace is the
swap: one small file, so a reader holding a Parquet file of the old version
(DBeaver, a page build) never blocks it, and the old version stays readable.

Rules applied here:
- The checks run first, per source group (D81). A run publishes when at least
  one group passes. Each group that passed contributes its staged tables and
  its inputs; each group that didn't (it failed to load or stage, or a gate
  blocked it) is carried forward: its tables and inputs are copied from the
  current version, which always holds every group's latest good data. A group
  that has never published has nothing to carry: the version is published
  without it (D83). serving/<run_id>/groups.parquet records, per group, the
  outcome, the run its tables came from and why it wasn't fresh.
- A group the run didn't attempt (D135: an on-demand group in a refresh, the
  nightly groups in an on-demand run) is `not_run`: its tables and inputs are
  copied from the current version, as a carried-forward group's are, but it
  is no failure and C35/C36 don't count it.
- When no group passes, nothing publishes: no folder, CURRENT unchanged.
- A version folder is never rewritten. It is built under a temporary name,
  its row counts are reconciled to their source, and only then renamed into
  place. A version that already exists is re-pointed, not rebuilt.
- If CURRENT is held open elsewhere the swap retries briefly, then fails loudly
  and leaves the old pointer in place (open issue #7).
- After a publish two versions remain, the one CURRENT names and the one it
  replaced; older ones are deleted (prune_serving, D133).
- A publish deletes every staging folder older than the run it publishes:
  staging rebuilds from raw at the run's recorded commit (`pharos
  rebuild-staging`), and a published run's tables live on in its version. A
  blocked or failed run keeps its folder until a later run publishes (D103).
- A version carries inputs/: config/ and this run's Tiingo file as the checks
  read them, so view-time health never reads config/ or raw/ (D76).
"""

import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

import duckdb

from pharos.db import records
from pharos.checks import INPUTS_DIR, Evaluation, bind_view_inputs, groups_in, run_checks
from pharos.groups import BLOCKED, CARRIED_FORWARD, FAILED, GROUPS, HELD, NEVER_PUBLISHED, NOT_RUN, PUBLISHED
from pharos.parquet import row_count
from pharos.paths import data_root
from pharos.runs import RUN_ID
from pharos.stage import any_complete_runs, attempted

SWAP_ATTEMPTS = 5
SWAP_WAIT_SECONDS = 0.2
GROUPS_FILE = "groups.parquet"
# group -> the function that binds its inputs at run time, for copying into inputs/
INPUT_BINDERS = {"prices_macro": bind_view_inputs}


class PublishError(RuntimeError):
    pass


class PointerLockedError(PublishError):
    pass


@dataclass(frozen=True)
class Publication:
    run_id: str
    evaluation: Evaluation
    version: Path | None   # serving/<run_id>/ when published, else None
    # group -> {"outcome", "source_run", "reason"}, one per declared group
    groups: dict[str, dict] = field(default_factory=dict)


def serving_dir() -> Path:
    return data_root() / "serving"


def current_version() -> Path | None:
    """The folder CURRENT names, or None before the first publish."""
    pointer = serving_dir() / "CURRENT"
    if not pointer.exists():
        return None
    run_id = pointer.read_text(encoding="utf-8").strip()
    version = serving_dir() / run_id
    if not version.is_dir():
        raise PublishError(f"{pointer} names {run_id!r}, but {version} does not exist")
    return version


def composition(version: Path) -> dict[str, dict]:
    """Per group in a version: outcome, source_run, reason. A version published
    before groups (no groups.parquet) holds each group whose tables it has,
    published by itself."""
    record = version / GROUPS_FILE
    if not record.exists():
        return {g.name: {"outcome": PUBLISHED, "source_run": version.name, "reason": None}
                for g in groups_in(version)}
    # may be read from more than one thread: its own connection, never the shared default (#68)
    return {r.pop("group"): r for r in records(f"SELECT * FROM read_parquet('{record.as_posix()}')")}


def publish(run_id: str | None = None, not_staged: dict[str, str] | None = None) -> Publication:
    """Audit staging/<run_id>/ (default: latest complete run) and publish the
    groups that pass, carrying the others forward. not_staged: why a group
    has no tables in this run (refresh passes the load and stage failures)."""
    runs = any_complete_runs()
    if not runs:
        raise PublishError("no complete run in health/loads")
    run_id = run_id or runs[-1]

    evaluation = run_checks(run_id)
    tried = {g.name for g in attempted(run_id)}
    reasons = {g: (not_staged or {}).get(g) or "not staged for this run" for g in tried
               if g not in evaluation.groups}
    for g, verdict in evaluation.groups.items():
        if verdict != "passed":
            stops = sorted(r["check_id"] for r in evaluation.results
                           if r["group"] == g and r["gate"] and r["status"] in ("error", "broken"))
            reasons[g] = f"gate checks {verdict}: {', '.join(stops)}"

    passed = [g for g, v in evaluation.groups.items() if v == "passed"]
    current = current_version()
    held = {g: c["source_run"] for g, c in (composition(current) if current else {}).items()
            if c["outcome"] in HELD and c["source_run"] is not None}
    if not passed:
        outcomes = {g: {"outcome": BLOCKED if evaluation.groups.get(g) == "blocked" else FAILED,
                        "source_run": None, "reason": reasons[g]} if g in tried else
                    {"outcome": NOT_RUN, "source_run": held.get(g), "reason": None} for g in GROUPS}
        return Publication(run_id, evaluation, None, outcomes)

    outcomes = {}
    for g in GROUPS:
        if g not in tried:
            outcomes[g] = {"outcome": NOT_RUN, "source_run": held.get(g), "reason": None}
        elif g in passed:
            outcomes[g] = {"outcome": PUBLISHED, "source_run": run_id, "reason": None}
        elif g in held:
            outcomes[g] = {"outcome": CARRIED_FORWARD, "source_run": held[g], "reason": reasons[g]}
        else:
            outcomes[g] = {"outcome": NEVER_PUBLISHED, "source_run": None, "reason": reasons[g]}

    version = serving_dir() / run_id
    if not version.exists():
        _build(run_id, version, outcomes, current)
    _point(run_id)
    prune_staging(run_id)
    prune_serving(keep={run_id} | ({current.name} if current is not None else set()))
    return Publication(run_id, evaluation, version, outcomes)


def prune_staging(published_run: str) -> list[str]:
    """Delete staging/<run_id>/ for every run older than published_run (D103).
    Only run-id folders are touched; returns the run ids removed."""
    folder = data_root() / "staging"
    old = sorted(p for p in folder.iterdir() if p.is_dir() and RUN_ID.fullmatch(p.name)
                 and p.name < published_run) if folder.is_dir() else []
    for p in old:
        shutil.rmtree(p)
    return [p.name for p in old]


def prune_serving(keep: set[str]) -> list[str]:
    """Delete every serving version folder not in keep: after a publish, the
    version CURRENT names and the one it replaced (D133). Raw is the record;
    any version rebuilds from raw at its commit. Only run-id folders are
    touched; returns the run ids removed."""
    folder = serving_dir()
    old = sorted(p for p in folder.iterdir() if p.is_dir() and RUN_ID.fullmatch(p.name)
                 and p.name not in keep) if folder.is_dir() else []
    for p in old:
        shutil.rmtree(p)
    return [p.name for p in old]


def _build(run_id: str, version: Path, outcomes: dict[str, dict], current: Path | None) -> None:
    staged = data_root() / "staging" / run_id
    building = serving_dir() / f".building-{run_id}"
    if building.exists():
        shutil.rmtree(building)   # a crashed earlier attempt; never pointed at
    building.mkdir(parents=True)
    inputs = building / INPUTS_DIR
    inputs.mkdir()
    for name, o in outcomes.items():
        group = GROUPS[name]
        if o["outcome"] == PUBLISHED:
            source = staged
            if group.inputs:
                con = duckdb.connect()
                INPUT_BINDERS[name](con, run_id)
                for table in group.inputs:
                    con.execute(f"COPY (SELECT * FROM {table}) TO '{(inputs / f'{table}.parquet').as_posix()}' "
                                "(FORMAT parquet)")
                con.close()
        elif o["outcome"] in (CARRIED_FORWARD, NOT_RUN) and o["source_run"] is not None:
            source = current
            for table in group.inputs:
                _copy(current / INPUTS_DIR / f"{table}.parquet", inputs / f"{table}.parquet")
        else:
            continue
        for table in group.tables:
            _copy(source / f"{table}.parquet", building / f"{table}.parquet")
    con = duckdb.connect()
    con.execute("CREATE TABLE g (\"group\" VARCHAR, outcome VARCHAR, source_run VARCHAR, reason VARCHAR)")
    con.executemany("INSERT INTO g VALUES (?, ?, ?, ?)",
                    [(n, o["outcome"], o["source_run"], o["reason"]) for n, o in outcomes.items()])
    con.execute(f"COPY g TO '{(building / GROUPS_FILE).as_posix()}' (FORMAT parquet)")
    con.close()
    os.rename(building, version)


def _copy(src: Path, dst: Path) -> None:
    if src.is_dir():
        shutil.copytree(src, dst, copy_function=_link_or_copy)
    else:
        shutil.copy2(src, dst)
    n_src, n_dst = _rows(src), _rows(dst)
    if n_src != n_dst:
        raise PublishError(f"{dst.name}: {n_src:,} rows in {src.parent}, {n_dst:,} copied")


def _point(run_id: str) -> None:
    pointer = serving_dir() / "CURRENT"
    pending = serving_dir() / "CURRENT.pending"
    pending.write_text(run_id + "\n", encoding="utf-8")
    for attempt in range(SWAP_ATTEMPTS):
        try:
            os.replace(pending, pointer)
            return
        except PermissionError as e:
            if attempt == SWAP_ATTEMPTS - 1:
                pending.unlink(missing_ok=True)
                raise PointerLockedError(f"{pointer} is open in another program; "
                                         f"serving still points at the previous version") from e
            time.sleep(SWAP_WAIT_SECONDS)


def _rows(path: Path) -> int:
    return row_count(path)


def _link_or_copy(src: str, dst: str) -> str:
    """Keep immutable partition snapshots cheap on one volume."""
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)
    return dst

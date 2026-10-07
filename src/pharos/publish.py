"""Write-audit-publish: staging/<run_id>/ -> serving/<run_id>/, behind the checks.

serving/ holds one write-once folder per published run and CURRENT, a one-line
file naming the version readers use (D30). Publishing copies the staged tables
into a new folder nobody reads yet, then replaces CURRENT. That replace is the
swap: one small file, so a reader holding a Parquet file of the old version
(DBeaver, the app) never blocks it, and the old version stays readable.

Rules applied here:
- The checks run first, and only verdict `passed` publishes. `blocked` and
  `failed` leave serving/ untouched: no folder, CURRENT unchanged.
- A version folder is never rewritten. It is built under a temporary name,
  its row counts are reconciled to staging, and only then renamed into place.
  A version that already exists is re-pointed, not rebuilt.
- If CURRENT is held open elsewhere the swap retries briefly, then fails loudly
  and leaves the old pointer in place (open issue #7).
- Old versions are kept. Delete = archive; pruning would be its own decision.
"""

import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

import duckdb

from pharos.checks import Evaluation, run_checks
from pharos.paths import data_root
from pharos.stage import complete_runs

TABLES = ("observations", "series_catalog")
SWAP_ATTEMPTS = 5
SWAP_WAIT_SECONDS = 0.2


class PublishError(RuntimeError):
    pass


class PointerLockedError(PublishError):
    pass


@dataclass(frozen=True)
class Publication:
    run_id: str
    evaluation: Evaluation
    version: Path | None   # serving/<run_id>/ when published, else None


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


def publish(run_id: str | None = None) -> Publication:
    """Audit staging/<run_id>/ (default: latest complete run) and publish it if it passes."""
    runs = complete_runs()
    if not runs:
        raise PublishError("no complete run in health/loads")
    run_id = run_id or runs[-1]

    evaluation = run_checks(run_id)
    if evaluation.verdict != "passed":
        return Publication(run_id, evaluation, None)

    version = serving_dir() / run_id
    if not version.exists():
        _build(run_id, version)
    _point(run_id)
    return Publication(run_id, evaluation, version)


def _build(run_id: str, version: Path) -> None:
    staged = data_root() / "staging" / run_id
    building = serving_dir() / f".building-{run_id}"
    if building.exists():
        shutil.rmtree(building)   # a crashed earlier attempt; never pointed at
    building.mkdir(parents=True)
    for table in TABLES:
        shutil.copy2(staged / f"{table}.parquet", building / f"{table}.parquet")
        n_staged, n_copied = (_rows(folder / f"{table}.parquet") for folder in (staged, building))
        if n_staged != n_copied:
            raise PublishError(f"{table}: {n_staged:,} rows staged, {n_copied:,} copied")
    os.rename(building, version)


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
    return duckdb.sql(f"SELECT sum(num_rows) FROM parquet_file_metadata('{path.as_posix()}')").fetchone()[0]

"""Run identity, and the only way anything is written to raw/.

design.md D18: raw is a snapshot log, one write-once file per source and dataset
per run. write_raw() is the single writer, so the rule is enforced in one place:
it refuses to overwrite, never leaves a half-written file under a real name, and
records a control total (rows downloaded vs rows actually in the file) for C06.

The load record (health/loads/<run_id>__<source>__<dataset>.parquet) also holds
the file's sha256 as written, which C05 recomputes (D108), and two values a
loader may pass: the download's HTTP Last-Modified and the count the source
itself states for the download (D91, D119). Records written before these
columns existed lack them; every reader reads the log with union_by_name, and
an older record reads NULL (D91).
"""

import hashlib

import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from pharos.paths import PROJECT_ROOT, data_root

RUN_ID = re.compile(r"\d{8}T\d{6}Z")


class RawExistsError(RuntimeError):
    pass


class ProvenanceError(RuntimeError):
    pass


def new_run_id() -> str:
    """UTC timestamp to the second, e.g. 20261007T153012Z. Sorts in time order."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(PROJECT_ROOT), *args], capture_output=True,
                          text=True, check=True).stdout.strip()


def provenance() -> tuple[str, bool]:
    """The git commit this code runs at, and whether the working tree differs
    from it (tracked changes or untracked files; data/ is gitignored). Recorded
    on every run so its staging can be rebuilt from raw at that commit (D103).
    Unreadable is an error: a run that can't name its code can't be rebuilt."""
    try:
        commit = _git("rev-parse", "HEAD")
        dirty = _git("status", "--porcelain") != ""
    except (OSError, subprocess.CalledProcessError) as e:
        detail = getattr(e, "stderr", "") or str(e)
        raise ProvenanceError(f"can't read the git commit of {PROJECT_ROOT}: {detail.strip()} (D103)") from e
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ProvenanceError(f"git rev-parse HEAD returned {commit!r}, not a commit hash (D103)")
    return commit, dirty


def write_raw(download, source: str, dataset: str, run_id: str, *,
              last_modified: datetime | None = None, source_count: int | None = None) -> Path | None:
    """Write one download to raw/<source>/<dataset>/<run_id>.parquet.

    `download` is anything DuckDB can read as a table (a pandas DataFrame from a
    loader). Every row gets run_id and loaded_at for lineage. An empty download
    writes no data file but is still recorded, so "the source returned nothing"
    is evidence, not silence. Returns the file path, or None if empty.

    last_modified: the HTTP Last-Modified of the download (timezone-aware), for
    the freshness checks. source_count: the count the source states for this
    download (e.g. the CIK files in submissions.zip), for completeness checks.
    """
    root = data_root()
    target = root / "raw" / source / dataset / f"{run_id}.parquet"
    if target.exists():
        raise RawExistsError(f"{target} already exists; raw files are write-once")

    rows_downloaded = len(download)
    loaded_at = datetime.now(timezone.utc).isoformat()
    con = duckdb.connect()
    path_written = None
    rows_written = 0
    digest = None
    if last_modified is not None and last_modified.tzinfo is None:
        raise ValueError("last_modified must be timezone-aware")

    if rows_downloaded > 0:
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_suffix(".partial")
        con.register("download", download)
        con.execute(
            f"""COPY (SELECT *, '{run_id}' AS run_id, TIMESTAMPTZ '{loaded_at}' AS loaded_at
                     FROM download)
                TO '{partial.as_posix()}' (FORMAT parquet)"""
        )
        rows_written = _count_rows(con, partial)
        # still write-once if target appeared meanwhile, on any OS: a hard link
        # refuses an existing name everywhere, where rename overwrites on POSIX (#72)
        try:
            os.link(partial, target)
        except FileExistsError as e:
            partial.unlink()
            raise RawExistsError(f"{target} appeared during the write; raw files are write-once") from e
        partial.unlink()
        path_written = target
        digest = sha256_of(target)   # of the file as written: C05 recomputes it (D108)

    record = root / "health" / "loads" / f"{run_id}__{source}__{dataset}.parquet"
    record.parent.mkdir(parents=True, exist_ok=True)
    con.execute(
        f"""COPY (SELECT '{run_id}' AS run_id, '{source}' AS source, '{dataset}' AS dataset,
                         {f"'{path_written.as_posix()}'" if path_written else "NULL"}::VARCHAR AS path,
                         {rows_downloaded}::BIGINT AS rows_downloaded,
                         {rows_written}::BIGINT AS rows_written,
                         TIMESTAMPTZ '{loaded_at}' AS loaded_at,
                         ?::VARCHAR AS sha256,
                         ?::TIMESTAMPTZ AS last_modified,
                         ?::BIGINT AS source_count)
            TO '{record.as_posix()}' (FORMAT parquet)""",
        [digest, last_modified, source_count],
    )
    con.close()
    return path_written


def sha256_of(path: Path) -> str:
    """Hex sha256 of a file's bytes, read in 1 MB blocks."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _count_rows(con: duckdb.DuckDBPyConnection, path: Path) -> int:
    """Rows actually in the file, read back from disk: the C06 control total."""
    return con.execute(f"SELECT count(*) FROM read_parquet('{path.as_posix()}')").fetchone()[0]


def raw_select(source: str, dataset: str, run_ids: list[str] | None = None) -> str | None:
    """SELECT over the raw files of one dataset (all runs, or only run_ids).
    None when no file exists. The one place raw is read back, so the pre-D20
    shim lives here: Yahoo files from before D20 have no pull window, and when
    none of the files has one, the columns are added as NULL."""
    folder = data_root() / "raw" / source / dataset
    files = sorted(folder.glob("*.parquet"))
    if run_ids is not None:
        files = [f for f in files if f.stem in set(run_ids)]
    if not files:
        return None
    select = f"SELECT * FROM read_parquet({[f.as_posix() for f in files]}, union_by_name = true)"
    if (source, dataset) == ("yf", "prices"):
        cols = {c[0] for c in duckdb.sql(f"DESCRIBE {select}").fetchall()}
        if "pull_start" not in cols:
            select = select.replace("SELECT *", "SELECT *, NULL::DATE AS pull_start, NULL::DATE AS pull_end, "
                                                "NULL::VARCHAR AS pull_reason", 1)
        select = f"SELECT *, {FULL_PULL_SQL} AS full_pull FROM ({select})"
    return select


# A Yahoo row came from a full-history pull by what its own pull recorded, never
# by today's config: a watchlist or backfill_start edit must not reclassify
# history (D72, #61). The loader and staging both read it through raw_select,
# so they can't disagree about which pull sets the vintage (#59). A pre-D20 file
# has no pull window and was always a full pull. An unknown reason fails loudly.
FULL_PULL_REASONS = ("bootstrap", "rebase", "split_flag", "no_overlap", "window_extended")
PARTIAL_PULL_REASONS = ("incremental", "correction")
FULL_PULL_SQL = (
    "CASE WHEN pull_start IS NULL THEN true "
    f"WHEN pull_reason IN {FULL_PULL_REASONS} THEN true "
    f"WHEN pull_reason IN {PARTIAL_PULL_REASONS} THEN false "
    "ELSE error('yf prices: unknown pull_reason ' || coalesce(pull_reason, 'NULL') "
    "|| ' in run ' || run_id || ' (runs.FULL_PULL_SQL, D72)') END")

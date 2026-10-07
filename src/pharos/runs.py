"""Run identity, and the only way anything is written to raw/.

design.md D18: raw is a snapshot log, one write-once file per source and dataset
per run. write_raw() is the single writer, so the rule is enforced in one place:
it refuses to overwrite, never leaves a half-written file under a real name, and
records a control total (rows downloaded vs rows actually in the file) for C06.
"""

from datetime import datetime, timezone
from pathlib import Path

import duckdb

from pharos.paths import data_root


class RawExistsError(RuntimeError):
    pass


def new_run_id() -> str:
    """UTC timestamp to the second, e.g. 20261007T153012Z. Sorts in time order."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def write_raw(download, source: str, dataset: str, run_id: str) -> Path | None:
    """Write one download to raw/<source>/<dataset>/<run_id>.parquet.

    `download` is anything DuckDB can read as a table (a pandas DataFrame from a
    loader). Every row gets run_id and loaded_at for lineage. An empty download
    writes no data file but is still recorded, so "the source returned nothing"
    is evidence, not silence. Returns the file path, or None if empty.
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
        partial.rename(target)  # fails if target appeared meanwhile: still write-once
        path_written = target

    record = root / "health" / "loads" / f"{run_id}__{source}__{dataset}.parquet"
    record.parent.mkdir(parents=True, exist_ok=True)
    con.execute(
        f"""COPY (SELECT '{run_id}' AS run_id, '{source}' AS source, '{dataset}' AS dataset,
                         {f"'{path_written.as_posix()}'" if path_written else "NULL"}::VARCHAR AS path,
                         {rows_downloaded}::BIGINT AS rows_downloaded,
                         {rows_written}::BIGINT AS rows_written,
                         TIMESTAMPTZ '{loaded_at}' AS loaded_at)
            TO '{record.as_posix()}' (FORMAT parquet)"""
    )
    con.close()
    return path_written


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
    return select

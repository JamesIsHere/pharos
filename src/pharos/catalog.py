"""Regenerates catalog.duckdb: views over the Parquet layers, for DBeaver.

catalog.duckdb holds views only, never data (D1), so it is rebuilt from scratch
each time and nothing is lost by deleting it. Views use absolute paths: DuckDB
resolves a relative path against the client's working directory, which for
DBeaver is not the project.

The new file is built under a temporary name and swapped in. If DBeaver has the
old file open, Windows refuses the swap: that fails loudly and leaves the old
catalog in place (review concern 7).
"""

import os
from pathlib import Path

import duckdb

from pharos.paths import data_root

RAW_DATASETS = [("yf", "prices"), ("fred", "observations"), ("fred", "series")]


class CatalogLockedError(RuntimeError):
    pass


def views() -> dict[str, str]:
    """View name -> SELECT, for every layer that currently has files."""
    root = data_root()
    out = {}
    for source, dataset in RAW_DATASETS:
        folder = root / "raw" / source / dataset
        if any(folder.glob("*.parquet")):
            out[f"raw_{source}_{dataset}"] = _read(folder / "*.parquet")
    staged = sorted(p for p in (root / "staging").glob("*") if p.is_dir())
    if staged:
        for table in ("observations", "series_catalog"):
            out[f"staging_{table}"] = _read(staged[-1] / f"{table}.parquet")
    loads = root / "health" / "loads"
    if any(loads.glob("*.parquet")):
        out["health_loads"] = _read(loads / "*.parquet")
    return out


def rebuild() -> Path:
    target = data_root() / "catalog.duckdb"
    building = target.with_suffix(".building")
    building.unlink(missing_ok=True)
    con = duckdb.connect(str(building))
    for name, select in views().items():
        con.execute(f"CREATE VIEW {name} AS {select}")
    con.close()
    try:
        os.replace(building, target)
    except PermissionError as e:
        building.unlink(missing_ok=True)
        raise CatalogLockedError(f"{target} is open in another program (DBeaver?); close it and rerun") from e
    return target


def _read(path: Path) -> str:
    return f"SELECT * FROM read_parquet('{path.as_posix()}', union_by_name = true)"

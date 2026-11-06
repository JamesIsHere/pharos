"""Parquet table paths shared by staging, checks, publishing and readers.

Most Pharos tables are one ``<table>.parquet`` file.  Large tables may use a
directory with that same name containing Hive-style Parquet partitions.  The
logical table contract stays the same; this module is the one place that
turns either physical form into a DuckDB scan.
"""

from pathlib import Path

import duckdb


def table_path(folder: Path, table: str) -> Path:
    return folder / f"{table}.parquet"


def parquet_files(path: Path) -> list[Path]:
    """The physical Parquet files behind one logical table."""
    if path.is_dir():
        return sorted(path.rglob("*.parquet"))
    return [path] if path.is_file() else []


def scan(path: Path) -> str:
    """DuckDB relation SQL for a single-file or partition-directory table."""
    if path.is_dir():
        pattern = (path / "**" / "*.parquet").as_posix()
        if not parquet_files(path):
            raise FileNotFoundError(f"partitioned Parquet table is empty: {path}")
        return (f"read_parquet('{pattern}', union_by_name = true, "
                "hive_partitioning = true)")
    return f"read_parquet('{path.as_posix()}', union_by_name = true)"


def table_scan(folder: Path, table: str) -> str:
    return scan(table_path(folder, table))


def row_count(path: Path) -> int:
    """Count rows from Parquet footers without scanning table values."""
    if not parquet_files(path):
        raise FileNotFoundError(f"Parquet table not found: {path}")
    con = duckdb.connect()
    try:
        # DuckDB pushes count(*) into the Parquet metadata, while one scan over
        # the partition glob avoids issuing a query per CIK.
        return con.execute(f"SELECT count(*) FROM {scan(path)}").fetchone()[0]
    finally:
        con.close()

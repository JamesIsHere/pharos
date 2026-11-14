"""One-off DuckDB queries, each on its own connection.

DuckDB's module-level duckdb.sql() runs on one default connection shared by
the whole process, so two threads interleaving on it throw
InvalidInputException (#68). Code that may run on more than one thread queries
through here instead: each call gets its own connection.
"""

import duckdb


def query(sql: str) -> tuple[list[str], list[tuple]]:
    """(columns, rows) of `sql`, run on a connection of its own."""
    con = duckdb.connect()
    try:
        rel = con.sql(sql)
        return rel.columns, rel.fetchall()
    finally:
        con.close()


def records(sql: str) -> list[dict]:
    """The rows of `sql` as dicts, run on a connection of its own."""
    columns, rows = query(sql)
    return [dict(zip(columns, row)) for row in rows]

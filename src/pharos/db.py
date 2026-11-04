"""One-off DuckDB queries for code that runs on the app's threads.

DuckDB's module-level duckdb.sql() runs on one default connection shared by
the whole process. Streamlit renders each page on its own thread, so two tabs
interleaving on that connection throw InvalidInputException (#68). Code the
app calls queries through here instead: each call gets its own connection.
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

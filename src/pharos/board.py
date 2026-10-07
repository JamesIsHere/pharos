"""What the dashboard reads: serving/CURRENT and health/, nothing else.

Every query here is DuckDB SQL; results leave as Polars frames for the UI.
Nothing here writes: the app reads health/, it never records (D43).

- connect(): an in-memory connection with the published version, the run
  manifest and the XNYS calendar bound, plus transform/expected_dates.sql,
  the definition C04 uses, so the heatmap's holes are C04's (D44).
- last_published_at(): the cache key for everything served (D31).
- The health page's sections, one function each (design.md section 7).
"""

import calendar
import json
from datetime import datetime
from pathlib import Path

import duckdb
import exchange_calendars as xc
import polars as pl

from pharos import config
from pharos.paths import PROJECT_ROOT, data_root
from pharos.publish import current_version

TRANSFORM = PROJECT_ROOT / "src" / "pharos" / "transform"
SPOT_CHECKS_PER_SOURCE = 5


def last_published_at() -> datetime | None:
    folder = data_root() / "health" / "run_manifest"
    if not any(folder.glob("*.parquet")):
        return None
    return duckdb.sql(f"""SELECT max(published_at) FROM
                          read_parquet('{folder.as_posix()}/*.parquet', union_by_name = true)
                          WHERE status = 'published'""").fetchone()[0]


def connect() -> duckdb.DuckDBPyConnection | None:
    """None before the first publish: the page shows the status bar and says so."""
    version = current_version()
    if version is None:
        return None
    con = duckdb.connect()
    for table in ("observations", "series_catalog"):
        con.execute(f"CREATE VIEW {table} AS SELECT * FROM read_parquet('{(version / f'{table}.parquet').as_posix()}')")
    con.execute("CREATE TABLE this_version AS SELECT ? AS run_id", [version.name])
    manifests = data_root() / "health" / "run_manifest"
    con.execute(f"CREATE VIEW run_manifest AS SELECT * FROM "
                f"read_parquet('{manifests.as_posix()}/*.parquet', union_by_name = true)")
    sessions = xc.get_calendar("XNYS", start=config.sources()["backfill_start"]).sessions
    con.execute("CREATE TABLE trading_days (session DATE)")
    con.executemany("INSERT INTO trading_days VALUES (?)", [(d.date(),) for d in sessions])
    con.execute(f"CREATE VIEW expected_dates AS {(TRANSFORM / 'expected_dates.sql').read_text(encoding='utf-8')}")
    return con


def summary(con) -> pl.DataFrame:
    """Per source: series, rows and the latest observation in what is served."""
    return con.sql("""SELECT c.source, count(DISTINCT o.series_id) AS series, count(*) AS rows,
                             max(o.obs_date) AS latest_obs_date
                      FROM observations AS o JOIN series_catalog AS c USING (series_id)
                      GROUP BY c.source ORDER BY c.source""").pl()


def coverage(con) -> pl.DataFrame:
    """Share of expected observations present, per series per month. A
    quarterly date covers its quarter's three months, so GDP reads as a solid
    row, not stripes. A month outside a series' window has no row (blank on
    the heatmap, not 0%)."""
    return con.sql("""WITH spread AS (
                          SELECT e.series_id, e.present,
                                 unnest(generate_series(date_trunc('month', e.obs_date),
                                        date_trunc('month', e.obs_date)
                                            + CASE c.frequency WHEN 'Q' THEN INTERVAL 2 MONTH ELSE INTERVAL 0 MONTH END,
                                        INTERVAL 1 MONTH))::DATE AS month
                          FROM expected_dates AS e JOIN series_catalog AS c USING (series_id))
                      SELECT series_id, month, avg(present::INTEGER) AS share_present,
                             count(*) AS expected, count(*) FILTER (WHERE present) AS present
                      FROM spread GROUP BY ALL ORDER BY series_id, month""").pl()


def rows_by_run(con) -> pl.DataFrame:
    """Rows landed per run per load (source/dataset), from the manifest. Kept
    per dataset: FRED's series metadata rows are not observations."""
    return con.sql("""SELECT run_id, source || '/' || dataset AS load, rows_landed
                      FROM run_manifest ORDER BY run_id, load""").pl()


def run_strip(con, last: int = 30) -> pl.DataFrame:
    """The last runs, oldest first, each with the color it ended in: red when
    it blocked or failed, yellow when it published with warnings, else green."""
    return con.sql(f"""SELECT run_id, status, checks_warn, checks_error, checks_broken,
                              CASE WHEN status <> 'published' OR checks_error > 0 OR checks_broken > 0 THEN 'red'
                                   WHEN checks_warn > 0 THEN 'yellow' ELSE 'green' END AS color
                       FROM (SELECT DISTINCT run_id, status, checks_warn, checks_error, checks_broken
                             FROM run_manifest)
                       ORDER BY run_id DESC LIMIT {int(last)}""").pl().reverse()


def spot_checks(con) -> pl.DataFrame:
    """Five served observations per source, chosen by a hash seeded with the
    published run_id: the same five for a run whenever anyone looks, a new five
    with the next publish (D44). Each carries a link to the source's own page."""
    return con.sql(f"""SELECT source, series_id, obs_date, value, vintage, available_date, source_key
                       FROM (SELECT c.source, o.series_id, o.obs_date, o.value, o.vintage, o.available_date,
                                    c.source_key,
                                    row_number() OVER (PARTITION BY c.source ORDER BY
                                        hash((SELECT run_id FROM this_version), o.series_id, o.obs_date, o.vintage)) AS pick
                             FROM observations AS o JOIN series_catalog AS c USING (series_id))
                       WHERE pick <= {SPOT_CHECKS_PER_SOURCE}
                       ORDER BY source, series_id, obs_date""").pl().with_columns(
        pl.struct("source", "source_key", "obs_date", "vintage").map_elements(
            lambda r: source_link(r["source"], r["source_key"], r["obs_date"], r["vintage"]),
            return_dtype=pl.String).alias("link")).drop("source_key")


def source_link(source: str, key: str, obs_date, vintage) -> str:
    if source == "yf":
        # history page narrowed to the week around the date
        start = calendar.timegm(obs_date.timetuple()) - 3 * 86400
        return f"https://finance.yahoo.com/quote/{key}/history/?period1={start}&period2={start + 7 * 86400}"
    if source == "fred":
        return f"https://alfred.stlouisfed.org/series?seid={key}&vintage_date={vintage}"
    raise ValueError(f"no source page known for source {source!r}")


def failing_rows_by_source(result: dict) -> dict[str, list[dict]]:
    """A failing check's rows grouped by the source each row names: its series
    prefix (`yf:`, `fred:`), else its source column, else 'tiingo' for a row that
    names only a ticker, else 'run' for a row about the run itself (D42)."""
    rows = json.loads(result["sample"]) if result.get("sample") else []
    groups: dict[str, list[dict]] = {}
    for row in rows:
        if isinstance(row.get("series_id"), str) and ":" in row["series_id"]:
            source = row["series_id"].split(":", 1)[0]
        elif row.get("source"):
            source = row["source"]
        elif row.get("ticker"):
            source = "tiingo"                     # C12, C18-C20: the reference source
        else:
            source = "run"
        groups.setdefault(source, []).append(row)
    return groups

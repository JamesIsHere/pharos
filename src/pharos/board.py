"""What the dashboard reads: serving/CURRENT and health/, nothing else.

Every query here is DuckDB SQL; results leave as Polars frames for the UI.
Nothing here writes: the app reads health/, it never records (D43).

- connect(): an in-memory connection with the published version, the run
  manifest and the XNYS calendar bound, plus transform/expected_dates.sql,
  the definition C04 uses, so the heatmap's holes are C04's (D44).
- last_published_at() and latest_run_id(): the cache key for what is served
  (D31) and for the manifest, which a blocked run moves without a publish.
- run_failures(): a blocked or failed run's recorded reasons (#38).
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


def latest_run_id() -> str | None:
    """The newest run with a manifest, published or not: the second cache key.
    A blocked run doesn't move last_published_at, but it must reach the run
    strip and the page (#38)."""
    runs = sorted(p.stem for p in (data_root() / "health" / "run_manifest").glob("*.parquet"))
    return runs[-1] if runs else None


def run_failures(run_id: str) -> tuple[str | None, list[dict]]:
    """Why a run didn't publish: its manifest error (a crash) and every check
    that didn't pass in its latest run-time evaluation, as recorded then. Read
    from health/, never re-run: the staged data was refused, not served (#38)."""
    root = data_root() / "health"
    error = duckdb.sql(f"""SELECT any_value(error) FROM read_parquet('{(root / "run_manifest" / f"{run_id}.parquet").as_posix()}')""").fetchone()[0]
    files = sorted((root / "check_results").glob(f"{run_id}__*.parquet"))
    if not files:
        return error, []
    source = f"read_parquet({[f.as_posix() for f in files]}, union_by_name = true)"
    context = "coalesce(context, 'run')" if "context" in duckdb.sql(f"SELECT * FROM {source} LIMIT 0").columns else "'run'"
    rel = duckdb.sql(f"""SELECT * FROM {source} WHERE {context} = 'run' AND status <> 'pass'
                         AND evaluated_at = (SELECT max(evaluated_at) FROM {source} WHERE {context} = 'run')
                         ORDER BY gate DESC, check_id""")
    return error, [dict(zip(rel.columns, row)) for row in rel.fetchall()]


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


# --- Charts (step 9, D47) -------------------------------------------------------

# A first vintage further than this past its period end is a later re-release of
# history, not the release: ALFRED records no release date for that observation
# (GDP/GDPC1 before 1991-07, CPIAUCSL before 1972-04). Measured: recorded releases
# lag at most 84 days, re-released history at least 112 (D47).
RELEASE_RECORDED_WITHIN_DAYS = 90


def series_list(con) -> pl.DataFrame:
    """Every served series, for the picker."""
    return con.sql("""SELECT series_id, name, source, units, frequency FROM series_catalog
                      WHERE series_id IN (SELECT DISTINCT series_id FROM observations)
                      ORDER BY source DESC, series_id""").pl()


def chart_points(con, series_ids: list[str]) -> pl.DataFrame:
    """Each observation's latest-vintage value, placed at the date it became
    public (D47): a price at its trade date; a FRED value at its first vintage,
    or, where ALFRED records no release (first vintage over
    RELEASE_RECORDED_WITHIN_DAYS past period end), at period end plus the series'
    expected_lag_days, flagged `estimated`. A value withdrawn in the latest
    vintage is left out, so the previous value holds until the next release."""
    return con.sql(f"""
        WITH picked AS (SELECT * FROM observations WHERE list_contains(?::VARCHAR[], series_id)),
        latest AS (SELECT series_id, obs_date, value FROM picked
                   QUALIFY row_number() OVER (PARTITION BY series_id, obs_date ORDER BY vintage DESC) = 1),
        released AS (SELECT series_id, obs_date, min(available_date) AS first_available FROM picked GROUP BY ALL),
        dated AS (
            SELECT l.series_id, l.obs_date, l.value, r.first_available, c.expected_lag_days,
                   CASE c.frequency WHEN 'D' THEN l.obs_date
                                    WHEN 'M' THEN (l.obs_date + INTERVAL 1 MONTH)::DATE - 1
                                    WHEN 'Q' THEN (l.obs_date + INTERVAL 3 MONTH)::DATE - 1
                                    ELSE error('no period end for frequency ' || c.frequency) END AS period_end
            FROM latest AS l JOIN released AS r USING (series_id, obs_date) JOIN series_catalog AS c USING (series_id))
        SELECT series_id, obs_date, period_end, value,
               first_available - period_end > {RELEASE_RECORDED_WITHIN_DAYS} AS estimated,
               CASE WHEN first_available - period_end > {RELEASE_RECORDED_WITHIN_DAYS}
                    THEN period_end + expected_lag_days ELSE first_available END AS plot_date
        FROM dated WHERE value IS NOT NULL
        ORDER BY series_id, plot_date, obs_date""", params=[list(series_ids)]).pl()


def chart(con, series_ids: list[str], start, end, rebase: bool) -> pl.DataFrame:
    """chart_points inside (start, end], opened per series by the value in
    effect at start (the last at or before it) drawn at start: a quarterly value
    released before the range still holds on its first day. A series with
    nothing until later opens at its first point. Each series also closes at
    end with its last value, still in effect until the next release. Both added
    points are `carried`. Rebased, each series is 100 at its opening point."""
    points = chart_points(con, series_ids)
    return con.sql("""
        WITH p AS (SELECT * FROM points),
        opening AS (SELECT * REPLACE (?::DATE AS plot_date), plot_date < ?::DATE AS carried FROM p WHERE plot_date <= ?::DATE
                    QUALIFY row_number() OVER (PARTITION BY series_id ORDER BY plot_date DESC, obs_date DESC) = 1),
        windowed AS (SELECT *, false AS carried FROM p WHERE plot_date > ?::DATE AND plot_date <= ?::DATE
                     UNION ALL SELECT * FROM opening),
        closing AS (SELECT * REPLACE (?::DATE AS plot_date, true AS carried) FROM windowed
                    QUALIFY row_number() OVER (PARTITION BY series_id ORDER BY plot_date DESC, obs_date DESC) = 1
                        AND plot_date < ?::DATE),
        drawn AS (SELECT * FROM windowed UNION ALL SELECT * FROM closing),
        based AS (SELECT *, first_value(value) OVER (PARTITION BY series_id ORDER BY plot_date, obs_date) AS base
                  FROM drawn)
        SELECT series_id, obs_date, period_end, plot_date, estimated, carried, value,
               CASE WHEN ? THEN 100 * value / base ELSE value END AS shown
        FROM based ORDER BY series_id, plot_date, obs_date""",
        params=[start, start, start, start, end, end, end, rebase]).pl()


def watchlist_year(con) -> pl.DataFrame:
    """The year to the latest served close of every active watchlist ticker,
    for Home's small multiples."""
    return con.sql("""WITH prices AS (SELECT o.series_id, c.entity_id AS ticker, o.obs_date, o.value, o.vintage
                                      FROM observations AS o JOIN series_catalog AS c USING (series_id)
                                      WHERE c.source = 'yf' AND c.active_to IS NULL)
                      SELECT series_id, ticker, obs_date, value FROM prices
                      WHERE obs_date > (SELECT max(obs_date) FROM prices) - INTERVAL 1 YEAR
                      QUALIFY row_number() OVER (PARTITION BY series_id, obs_date ORDER BY vintage DESC) = 1
                      ORDER BY ticker, obs_date""").pl()

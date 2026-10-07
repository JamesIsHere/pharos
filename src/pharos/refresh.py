"""`pharos refresh`: load -> stage -> audit -> publish, and the run manifest.

refresh() owns the whole run, so it is the one place that can say how a run
ended. The manifest is written in a finally block: a crash in any step still
leaves a record with status `failed` and the error, and the exception is
re-raised so the caller (the nightly task) sees it too (design.md section 6.5).
health/latest.md is rewritten after the manifest, so it reflects every run (D34).

Manifest (D31): one write-once file per run, health/run_manifest/<run_id>.parquet,
one row per raw dataset (yf/prices, fred/observations, fred/series, and the
reference tiingo/prices, whose load error is its own dataset_error, D37) with the
run-level fields repeated. Long, not wide: a new source adds rows, not columns.
- rows_downloaded / rows_landed come from the load records write_raw() left;
  NULL when the run died before that dataset loaded.
- latest_obs_date comes from this run's staged observations, by source; NULL
  for fred/series (metadata has no obs_date) and for an unstaged run.
- Check counts are per run, not per source: a check spans sources.
- published_at is set only when the run published. Its maximum over all
  manifests is last_published_at, the app's cache key.
"""

import traceback
from datetime import datetime, timezone

import duckdb

from pharos import health
from pharos.loaders import fred, tiingo, yahoo
from pharos.paths import data_root
from pharos.publish import Publication, publish
from pharos.runs import new_run_id
from pharos.stage import DATASETS, stage

REFERENCE_DATASETS = [("tiingo", "prices")]   # loaded every run, never required (D36, D37)


def refresh() -> tuple[str, Publication | None]:
    """Run every step for a new run. Returns (run_id, publication); raises on a crash."""
    run_id = new_run_id()
    started_at = datetime.now(timezone.utc)
    publication, error, reference_errors = None, None, {}
    try:
        yahoo.load_prices(run_id)
        fred.load_macro(run_id)
        try:
            tiingo.load_prices(run_id)
        except Exception:
            # reference only: the run goes on, the manifest keeps the error and
            # C18 turns health red until Tiingo is back (D37)
            reference_errors[("tiingo", "prices")] = traceback.format_exc(limit=5)
        stage(run_id)
        publication = publish(run_id)
    except Exception:
        error = traceback.format_exc(limit=5)
        raise
    finally:
        write_manifest(run_id, started_at, publication, error, reference_errors)
        health.evaluate()   # rewrites health/latest.md after every run, crashed ones too
    return run_id, publication


def write_manifest(run_id: str, started_at: datetime, publication: Publication | None,
                   error: str | None, reference_errors: dict | None = None) -> None:
    finished_at = datetime.now(timezone.utc)
    if publication is None:
        status = "failed"
    elif publication.version is not None:
        status = "published"
    else:
        status = publication.evaluation.verdict   # blocked | failed (a broken check)

    counts = {"pass": 0, "warn": 0, "error": 0, "broken": 0}
    for r in publication.evaluation.results if publication else []:
        counts[r["status"]] += 1

    root = data_root()
    target = root / "health" / "run_manifest" / f"{run_id}.parquet"
    target.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("CREATE TABLE ds (source VARCHAR, dataset VARCHAR, dataset_error VARCHAR)")
    con.executemany("INSERT INTO ds VALUES (?, ?, ?)",
                    [(s, d, (reference_errors or {}).get((s, d))) for s, d in DATASETS + REFERENCE_DATASETS])

    loads = sorted((root / "health" / "loads").glob(f"{run_id}__*.parquet"))
    if loads:
        con.execute(f"CREATE VIEW loads AS SELECT * FROM read_parquet({[p.as_posix() for p in loads]})")
    else:
        con.execute("CREATE TABLE loads (source VARCHAR, dataset VARCHAR, "
                    "rows_downloaded BIGINT, rows_written BIGINT)")

    staged = root / "staging" / run_id / "observations.parquet"
    latest = []
    if staged.exists():
        latest.append(f"""SELECT split_part(series_id, ':', 1) AS source, max(obs_date) AS latest_obs_date
                          FROM read_parquet('{staged.as_posix()}') GROUP BY 1""")
    tiingo_raw = root / "raw" / "tiingo" / "prices" / f"{run_id}.parquet"
    if tiingo_raw.exists():   # reference data is not staged: read its raw file
        latest.append(f"SELECT 'tiingo', max(obs_date) FROM read_parquet('{tiingo_raw.as_posix()}')")
    con.execute("CREATE TABLE latest (source VARCHAR, latest_obs_date DATE)")
    for select in latest:
        con.execute(f"INSERT INTO latest {select}")

    con.execute(
        """COPY (
            SELECT ?::VARCHAR AS run_id, ?::TIMESTAMPTZ AS started_at, ?::TIMESTAMPTZ AS finished_at,
                   ?::VARCHAR AS status, ?::TIMESTAMPTZ AS published_at,
                   ds.source, ds.dataset, l.rows_downloaded, l.rows_written AS rows_landed,
                   CASE WHEN ds.dataset <> 'series' THEN t.latest_obs_date END AS latest_obs_date,
                   ?::INTEGER AS checks_pass, ?::INTEGER AS checks_warn,
                   ?::INTEGER AS checks_error, ?::INTEGER AS checks_broken,
                   ?::VARCHAR AS error, ds.dataset_error
            FROM ds
            LEFT JOIN loads AS l USING (source, dataset)
            LEFT JOIN latest AS t ON t.source = ds.source
            ORDER BY ds.source, ds.dataset
        ) TO '""" + target.as_posix() + "' (FORMAT parquet)",
        [run_id, started_at, finished_at, status, finished_at if status == "published" else None,
         counts["pass"], counts["warn"], counts["error"], counts["broken"], error])
    con.close()

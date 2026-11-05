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
- Check counts are per run, not per source: a check spans sources. They count
  after acknowledgment (D46): a warn check whose rows are all reviewed expected
  states counts in checks_acknowledged, not checks_warn, so the run strip agrees
  with health. check_results keep each check's own status.
- published_at is set only when the run published. Its maximum over all
  manifests is last_published_at, the app's cache key.
- git_commit and git_dirty name the code the run ran (D103), read before
  anything loads; a run that can't read them fails without loading.
- Source groups (D81): each group's loaders run on their own, so one group
  failing to load or stage is recorded and the others go on. A run crashes
  (status failed, re-raised) only when no group can stage. Every dataset row
  carries its group's outcome for the run (published, carried_forward,
  never_published, blocked or failed), the reason it wasn't fresh, and the
  run its served tables came from (group_source_run).
- On-demand groups (D135): refresh attempts only the nightly groups;
  run_on_demand(name) is a run of one on-demand group alone, with the same
  manifest, health rewrite and notice. Every group a run didn't attempt is
  recorded not_run.
"""

import traceback
from datetime import datetime, timezone

import duckdb

from pharos import health, notify
from pharos.groups import FAILED, GROUPS, NOT_RUN, PRICES_MACRO, nightly
from pharos.loaders import fred, sec_companyfacts, sec_index, sec_submissions, sec_tickers, tiingo, yahoo
from pharos.paths import data_root
from pharos.publish import Publication, publish
from pharos.runs import new_run_id, provenance
from pharos.stage import stage

REFERENCE_DATASETS = list(PRICES_MACRO.reference)   # loaded every run, never required (D36, D37)


def _load_prices_macro(run_id: str) -> None:
    yahoo.load_prices(run_id)
    fred.load_macro(run_id)


def _load_sec(run_id: str) -> None:
    sec_index.load_index(run_id)
    sec_submissions.load_submissions(run_id)
    sec_tickers.load_tickers(run_id)


# group -> its required loads, and its reference loads (whose failure is only recorded, D37).
# Only groups registered in GROUPS are loaded: a loader here for an unregistered
# group is built but switched off
LOADERS = {"prices_macro": _load_prices_macro, "sec": _load_sec,
           "companyfacts": sec_companyfacts.load_companyfacts}   # on demand only (D135)
REFERENCE_LOADERS = {("tiingo", "prices"): lambda run_id: tiingo.load_prices(run_id)}


def refresh() -> tuple[str, Publication | None]:
    """Run every step for a new run of the nightly groups. Returns (run_id,
    publication); raises on a crash."""
    return _run([g.name for g in nightly()], REFERENCE_LOADERS)


def run_on_demand(name: str) -> tuple[str, Publication | None]:
    """A run of one on-demand group (D135): load, stage, check and publish it
    alone; every other group is served unchanged."""
    if name not in GROUPS or not GROUPS[name].on_demand:
        raise ValueError(f"{name!r} is not a registered on-demand group")
    return _run([name], {})


def _run(names: list[str], reference_loaders: dict) -> tuple[str, Publication | None]:
    run_id = new_run_id()
    started_at = datetime.now(timezone.utc)
    publication, error, reference_errors, code = None, None, {}, (None, None)
    group_errors: dict[str, str] = {}
    try:
        code = provenance()        # no commit, no run: its staging could never be rebuilt (D103)
        health.acknowledgments()   # a bad config/expected_states.csv fails the run before it loads
        first = None
        active = {name: LOADERS[name] for name in names}
        for name, load in active.items():
            try:
                load(run_id)
            except Exception as e:
                # this group can't stage this run; the others go on (D81)
                group_errors[name] = "load failed: " + traceback.format_exc(limit=5)
                first = first or e
        if len(group_errors) == len(active):
            raise first
        for key, load in reference_loaders.items():
            try:
                load(run_id)
            except Exception:
                # reference only: the run goes on, the manifest keeps the error and
                # C18 turns health red until Tiingo is back (D37)
                reference_errors[key] = traceback.format_exc(limit=5)
        staging = stage(run_id)
        for name, why in staging.failed.items():
            group_errors.setdefault(name, "stage failed: " + why)
        publication = publish(run_id, group_errors)
    except Exception:
        error = traceback.format_exc(limit=5)
        raise
    finally:
        write_manifest(run_id, started_at, publication, error, reference_errors, code, group_errors, names)
        status = health.evaluate()   # rewrites health/latest.md after every run, crashed ones too
        notify.send_status(run_id, status)   # never raises (#96)
    return run_id, publication


def write_manifest(run_id: str, started_at: datetime, publication: Publication | None,
                   error: str | None, reference_errors: dict | None = None,
                   code: tuple[str | None, bool | None] = (None, None),
                   group_errors: dict[str, str] | None = None,
                   attempted: list[str] | None = None) -> None:
    finished_at = datetime.now(timezone.utc)
    if publication is None:
        status = "failed"
    elif publication.version is not None:
        status = "published"
    else:
        status = publication.evaluation.verdict   # blocked | failed (a broken gate check, D77)

    counts = {"pass": 0, "warn": 0, "error": 0, "broken": 0, "acknowledged": 0}
    for r in health.acknowledge(publication.evaluation.results) if publication else []:
        counts[r["status"]] += 1

    root = data_root()
    target = root / "health" / "run_manifest" / f"{run_id}.parquet"
    target.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    # one row per raw dataset, each carrying its group's outcome for the run (D81)
    # a crashed run: every group it attempted failed; the rest were not run (D135)
    tried = set(GROUPS) if attempted is None else set(attempted)
    outcomes = publication.groups if publication else {
        g: {"outcome": FAILED, "source_run": None, "reason": (group_errors or {}).get(g) or error}
        if g in tried else {"outcome": NOT_RUN, "source_run": None, "reason": None}
        for g in GROUPS}
    con.execute("CREATE TABLE ds (source VARCHAR, dataset VARCHAR, dataset_error VARCHAR, \"group\" VARCHAR, "
                "group_outcome VARCHAR, group_reason VARCHAR, group_source_run VARCHAR)")
    con.executemany("INSERT INTO ds VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [(s, d, (reference_errors or {}).get((s, d)), g.name, outcomes[g.name]["outcome"],
                      outcomes[g.name]["reason"], outcomes[g.name]["source_run"])
                     for g in GROUPS.values() for s, d in g.datasets])

    loads = sorted((root / "health" / "loads").glob(f"{run_id}__*.parquet"))
    if loads:
        con.execute(f"CREATE VIEW loads AS SELECT * FROM read_parquet({[p.as_posix() for p in loads]}, union_by_name = true)")
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
                   ?::INTEGER AS checks_acknowledged,
                   ?::VARCHAR AS error, ds.dataset_error,
                   ?::VARCHAR AS git_commit, ?::BOOLEAN AS git_dirty,
                   ds."group", ds.group_outcome, ds.group_reason, ds.group_source_run
            FROM ds
            LEFT JOIN loads AS l USING (source, dataset)
            LEFT JOIN latest AS t ON t.source = ds.source
            ORDER BY ds.source, ds.dataset
        ) TO '""" + target.as_posix() + "' (FORMAT parquet)",
        [run_id, started_at, finished_at, status, finished_at if status == "published" else None,
         counts["pass"], counts["warn"], counts["error"], counts["broken"], counts["acknowledged"], error,
         code[0], code[1]])
    con.close()

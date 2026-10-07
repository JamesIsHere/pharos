# Pharos

Pharos (named for the lighthouse of Alexandria) is a local-first data platform: US public-company fundamentals (SEC XBRL), daily prices, and macro series (FRED/ALFRED, later BEA) stored as Parquet, queried with DuckDB, viewed in a Streamlit dashboard. Owner is a CPA with a finance-executive and model-validation background. **Data correctness outranks features.**

## Read first
- `docs/plan.md`: status block and current milestone. Work only on the current milestone.
- `docs/design.md`: data model, time semantics, health system, decision log. Read it before changing any schema, layer, source, or check.

## Session protocol
1. Start: run `uv run pharos health`. If anything is red, fix the data first. Never build features on red.
2. During: one milestone at a time. If a design decision changes, add a row to the decision log in `docs/design.md` in the same commit.
3. End: update the status block at the top of `docs/plan.md`, then commit. The commit message says what changed and why. Git history is the log; don't create separate log or state files.

## Stack
Python 3.12 via uv (lockfile committed) · DuckDB (query engine) · Parquet (storage of record) · Polars (edges only) · Streamlit + Plotly · Windows / PowerShell.

## Commands
```
uv sync                            # install from lockfile
uv run pharos refresh                # load -> transform -> audit -> publish (write-audit-publish)
uv run pharos health                 # pass/warn/fail table; rewrites <data>/health/latest.md
uv run pytest                      # unit tests + fault-injection tests
uv run streamlit run app/Home.py   # dashboard (dev)
scripts\open-dashboard.ps1         # what the desktop icon runs
scripts\register-tasks.ps1         # registers the nightly refresh in Task Scheduler
```

## Layout
```
config/            watchlist.csv (the expected set), sources.yaml
src/pharos/          loaders/, transform/*.sql, publish.py, health/, cli.py
checks/            *.sql, one check per file
app/               Home.py, pages/1_Health.py, pages/2_Charts.py
scripts/           PowerShell launcher + task registration
tests/             incl. test_faults.py
docs/              design.md, plan.md
data/              = PHAROS_DATA_ROOT: raw/ staging/ serving/ health/ catalog.duckdb   (gitignored, never committed)
```

## Hard rules

### Data integrity
- Parquet is the storage of record. The only `.duckdb` file is `catalog.duckdb`. It holds views over Parquet only, and the pipeline regenerates it.
- `raw/` is a snapshot log: one write-once file per source per run (`raw/<source>/<run_id>.parquet`). Never rewrite or delete a raw file. A revision is a new row with a new `vintage`. Deduplicate in staging, never at load.
- Write-audit-publish: write to `staging/`, run checks, and swap into `serving/` only if no error-severity gate check fails. A failed or blocked run must leave `serving/` untouched.
- Every observation carries `obs_date`, `available_date`, `vintage`, `run_id`.
- Transformations are DuckDB SQL. Polars is used only for parsing at the edges and for handing results to the UI. Never write transformation logic in pandas; convert library pandas output at the boundary.
- All data paths derive from `PHAROS_DATA_ROOT` (default: `data/`, gitignored). Never write data anywhere else, and never commit data.

### Time and joins
- Join prices to fundamentals or macro with `ASOF JOIN` on `available_date`, never on period end.
- Never combine split-adjusted prices with as-reported share counts.
- Fundamentals plot as step lines (`line_shape="hv"`). Never interpolate between reported values.

### Sources
- yfinance is unofficial. Always pass `auto_adjust` explicitly (its default has changed between versions), validate the schema on every pull, and fail loudly on drift.
- If a new split event is detected for a ticker, re-pull that ticker's full history as a new vintage.
- SEC (M2+): User-Agent from `SEC_USER_AGENT`, at most 10 requests/s, prefer bulk files (`companyfacts.zip`) over the API.
- FRED: key from `FRED_API_KEY`.
- Secrets live in `.env` (gitignored) locally and in GitHub Secrets in CI. **The repo is public.** Never commit keys or data.

### Health
- A check is a SQL file in `checks/` that returns failing rows. Zero rows means pass. Header comment: `id`, `severity` (error|warn), `gate` (yes|no), `description`. Gate checks test the staged data and can block a publish; monitor checks (`gate: no`) test the world (staleness, lateness, drift) and never block (D17).
- Any new source ships with completeness, uniqueness, and freshness checks in the same change.
- Freshness is evaluated at view time, not only at run time. A pipeline that silently didn't run must show red.
- Any new or changed check needs a fault-injection test in `tests/test_faults.py` proving it fires.
- Never loosen a threshold or delete a check to get green without a decision-log entry explaining why.

### App
- The app reads only `serving/` and `health/`, through one configurable data root (local path now, R2 URL later).
- Cache query results keyed on the manifest's `last_published_at`, so new data appears after a refresh.
- Opening the app never triggers a refresh.
- Every page shows the health status bar.

## Don't
- Don't add a dependency without stating why in the commit message.
- Don't work ahead of the current milestone, however tempting.

# Pharos

Pharos (named for the lighthouse of Alexandria) is a local-first data platform: US public-company fundamentals (SEC XBRL), daily prices, and macro series (FRED/ALFRED, later BEA) stored as Parquet, queried with DuckDB, read through the Scaffold pages. Owner is a CPA with a finance-executive and model-validation background. **Data correctness outranks features.**

## Read first
- `docs/plan.md`: status block and open issues. The milestone plan is retired (D161); the old plan is archived at `docs/archive/plan-2026-11-11.md`.
- `docs/later.md`: the earlier milestones (M1, H, U) as history, and parked issues. Not read by default.
- `docs/design.md`: data model, time semantics, health system. Read it before changing any schema, layer, source, or check.
- `docs/decisions.md`: the decision log (D-numbers). Not read by default; open it when a change touches a past decision.

## How to resume
Run Session protocol step 1 (health) and read the status block in `docs/plan.md`. Run health in this form, so the collapsed tool preview shows the commit and only red rows, never the full table: `git log -1 --format='%h %ci %s'; uv run pharos health 2>&1 | awk -F'|' '$5 ~ /error|fail/ {print "RED:" $2 $3 $5}'`. Then give one screen, in this order: the last commit (hash, time ET, message); state (direction, open issues, next action, anything stale); last line "What do you want to pick up?". Health appears only when something needs fixing (a gate check failing, or red not from age alone, step 1); otherwise it is not mentioned at all, not even as "nothing to fix" (D168). No last-session recap: git log has that.

## Session protocol
1. Start: run `uv run pharos health`. Red from age alone (C01 last publish over 26h, C17 a series 2+ periods behind) means no refresh has run (D160) and does not block. Any other red: fix the data first. Never build features on it.
2. During: if a design decision changes, add a row to `docs/decisions.md` in the same commit: only for a choice between real alternatives, never to describe what was built (D129). A concern not fixed in the turn it comes up gets a row in "Open issues" in `docs/plan.md` only if it blocks current work or could lose or corrupt data; anything else is one line in the reply or the commit message. Fixing a row deletes it and the commit cites its ID.
3. Two lanes (James, 2026-11-07; picture: `scaffold/pipeline.html`). During a session, anything new goes in the quick lane: a page fetches it at build time or queries served data, in seconds. Data worth storing is promoted in `config/sources.yaml`, committed (a FRED series is two lines: its ID, and its expected lag, measured); the next refresh lands it with vintages and checks. Every refresh is on demand (D160): the scheduled nightly is disabled; `pharos refresh` runs prices and FRED, `pharos sec` runs SEC (D159). Never run either mid-session unless James asks.
4. End: update the status block at the top of `docs/plan.md`, then commit. The commit message says what changed and why.

## Stack
Python 3.12 via uv (lockfile committed) · DuckDB (query engine) · Parquet (storage of record) · Polars (edges only) · Windows / PowerShell.

## Commands
```
uv sync                            # install from lockfile
uv run pharos refresh                # load -> transform -> audit -> publish (write-audit-publish)
uv run pharos sec                    # SEC group alone, on demand (D159): index, submissions, tickers, Company Facts
uv run pharos health                 # pass/warn/fail table; rewrites <data>/health/latest.md
uv run pytest                      # unit tests + fault-injection tests
uv run python scaffold/rebuild.py  # rebuild every Scaffold page, in order; stops on the first failure
scripts\register-tasks.ps1         # registers the nightly refresh in Task Scheduler
```

## Layout
```
config/            watchlist.csv (the expected set), sources.yaml, expected_states.csv, corporate_actions.csv, countries.csv + sec_country_codes.csv (D162)
src/pharos/        loaders/, transform/*.sql, cli.py, refresh.py, stage.py, checks.py, publish.py, health.py, ...
checks/            *.sql, one check per file
scripts/           nightly-refresh.ps1, register-tasks.ps1, build_countries.py (writes the two country CSVs)
tests/             incl. test_faults.py
docs/              design.md, plan.md, later.md, decisions.md
scaffold/          James's pages for keeping up with Pharos: Catalog, source sheets, Pipeline, Notes (scaffold/notes.md), Appendix (earlier analysis pages, by topic). Scripts tracked, generated .html gitignored. Run: uv run python scaffold/<page>.py
archive/           retired project folders, history intact (explore-2026-11-12: the pages listed on the Scaffold Appendix, D137, D138; app-2026-11-13: D170)
data/              = PHAROS_DATA_ROOT: raw/ staging/ serving/ health/ catalog.duckdb   (gitignored, never committed)
```

## Hard rules

### Data integrity
- Parquet is the storage of record. The only `.duckdb` file is `catalog.duckdb`. It holds views over Parquet only, and the pipeline regenerates it.
- `raw/` is a snapshot log: one write-once file per source per run (`raw/<source>/<dataset>/<run_id>.parquet`). Never rewrite or delete a raw file. A revision is a new row with a new `vintage`. Deduplicate in staging, never at load.
- Write-audit-publish, per source group (D81, D115): write to `staging/`, run checks, and publish a group's tables only if none of its error-severity gate checks fails; a group that fails is carried forward from the current version. A run where no group passes must leave `serving/` untouched.
- Every observation carries `obs_date`, `available_date`, `vintage`, `run_id`.
- Transformations are DuckDB SQL. Polars is used only for parsing at the edges and for handing results to the UI. Never write transformation logic in pandas; convert library pandas output at the boundary.
- All data paths derive from `PHAROS_DATA_ROOT` (default: `data/`, gitignored). Never write data anywhere else, and never commit data.

### Time and joins
- Join prices to fundamentals or macro with `ASOF JOIN` on `available_date`, never on period end.
- Never combine split-adjusted prices with as-reported share counts.
- Fundamentals plot as step lines. Never interpolate between reported values.

### Sources
- yfinance is unofficial. Always pass `auto_adjust` explicitly (its default has changed between versions), validate the schema on every pull, and fail loudly on drift.
- If a new split event is detected for a ticker, re-pull that ticker's full history as a new vintage.
- SEC (M2+): User-Agent from `SEC_USER_AGENT`, at most 10 requests/s, prefer bulk files (`submissions.zip` from M2, `companyfacts.zip` from M2, D57, D128) over the API.
- FRED: key from `FRED_API_KEY`.
- Secrets live in `.env` (gitignored) locally and in GitHub Secrets in CI. **The repo is public.** Never commit keys or data.

### Health
- A check is a SQL file in `checks/` that returns failing rows. Zero rows means pass. Header comment: `id`, `severity` (error|warn), `gate` (yes|no), `group` (a source group from src/pharos/groups.py, or `run` for a non-gating check about the run itself, D114), `description`. A warn check may add `ack_key` (the columns naming one problem); without it its rows can't be acknowledged (D75). Gate checks test the staged data and can block a publish; monitor checks (`gate: no`) test the world (staleness, lateness, drift) and never block (D17).
- Every source is landed or trusted (D127). Landed, the default: schema validated on every load, one freshness check, a one-row description in design.md section 5; definitions defaulted by Claude and flagged, not sent for sign-off. Trusted (D163) adds six things: (1) counts tie, rows landed equal rows in the source; (2) a completeness gate check against an expected set; (3) a uniqueness gate check on the source's key; (4) one reconciliation against an independent source; (5) James's sign-off on definitions, on the source's Scaffold sheet; (6) one complete run with the checks in place. A source moves up only when an analysis depends on it, by a row in docs/decisions.md. Prices and macro are trusted.
- Freshness is evaluated at view time, not only at run time. A pipeline that silently didn't run must show red.
- A version's `inputs/` folder holds what its view-time checks read; view time never reads `raw/` or `config/` (D76; one exception left, #94).
- Fault-injection tests are not part of the trusted bar (D163). The ones in `tests/test_faults.py` stay and keep running; a new check needs one only when James asks.
- Never loosen a threshold or delete a check to get green without a decision-log entry explaining why.

## Don't
- Don't add a dependency without stating why in the commit message.

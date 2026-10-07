# Design: Pharos

## 1. Purpose and non-goals

**Purpose.** A personal analysis platform. Pull up any stock, macro series, or company-level ratio; compare them over time on one chart; cut across companies and industries. It refreshes daily and tells you plainly whether the data can be trusted.

**Non-goals (v1).**
- No intraday data, no trading or execution.
- No foreign filers (20-F/40-F); scope is 10-K/10-Q filers.
- No fundamentals before XBRL (pre-2009/2011). No HTML parsing of filings.
- No paid data vendors.
- Not a general BI tool. Other projects (e.g. the variance agent) consume `serving/` Parquet as an interface; they don't reach into this repo's internals.

## 2. Architecture

```
Sources (yfinance, FRED/ALFRED; later SEC, BEA)
   |  Python loaders: download, parse, schema-validate
   v
raw/       Parquet, one write-once file per source per run: everything downloaded, untouched
   |  DuckDB SQL: standardize, derive, as-of joins
   v
staging/   candidate serving tables for this run
   |  checks/*.sql  (audit)  --- any gate check at error -> run BLOCKED, serving/ untouched
   v
serving/   series_catalog + observations   <- the contract
health/    run_manifest, check_results, baseline, latest.md
   |
   v
Streamlit app (DuckDB queries -> Plotly charts)
catalog.duckdb: views over raw/, the latest staging/, serving/ and health/, for DBeaver
```

## 3. Data model

### series_catalog
| column | notes |
|---|---|
| series_id | `{source}:{measure}:{entity}`, e.g. `yf:close:AAPL`, `fred:GDP`, `sec:equity_parent:0000320193` |
| name, source, source_key | source_key = Yahoo symbol, FRED id, XBRL concept |
| entity_type | `security` / `company` / `macro` |
| entity_id | ticker (M1), CIK (M2+), null for macro |
| measure, units, frequency | frequency: `D`, `M`, `Q` |
| adjustment | e.g. `split_adjusted`, `none`, `SAAR` |
| kind | `raw` or `derived` |
| active_from, active_to | the expected window. Completeness checks never expect data outside it. |
| expected_lag_days | used by the freshness check |

### observations
| column | notes |
|---|---|
| series_id, obs_date, value | value NULL = the vintage withdrew the value (FRED "."); kept so "latest vintage" can't resurrect a withdrawn number. Serving drops NULLs only after picking the latest vintage |
| units | units in force for this row's vintage (macro: from the FRED metadata window containing the vintage). Can differ from `series_catalog.units`, which is the current units (D19) |
| available_date | date the value became knowable: trade date (prices), vintage release date (macro), filing date (fundamentals) |
| vintage | prices: date of the full-history pull that set the current split adjustment (nightly incremental rows inherit it; a new split starts a new one); macro: ALFRED `realtime_start`; fundamentals: accession filing date |
| run_id, loaded_at | lineage |

Primary key: `(series_id, obs_date, vintage)`. The serving view defaults to the latest vintage per `(series_id, obs_date)`. Point-in-time views filter `available_date <= as_of`.

### events (raw)
Splits and dividends per ticker: `ticker, event_date, type, ratio_or_amount, run_id`.

### health tables
- `run_manifest`: one write-once file per run, one row per raw dataset (D31): run_id, started_at, finished_at, status (`published` / `blocked` / `failed`), published_at, source, dataset, rows_downloaded, rows_landed, latest_obs_date, checks_pass / checks_warn / checks_error / checks_broken (per run), error. `max(published_at)` is `last_published_at`.
- `check_results`: run_id, check_id, severity, status, failing_row_count, every failing row (JSON, in the column named `sample`, D42), evaluated_at. Appended every run.
- `baseline`: per series first_date, last_date, row_count at the opening-balance audit; written once to `health/baseline/<run_id>.parquet` (D39). Every audit attempt's comparisons go to `health/audit/<run_id>__<stamp>.parquet`.

## 4. Time semantics
- **obs_date**: the period the value describes.
- **available_date**: when it became public. All cross-source joins use `ASOF JOIN` on this column.
- **vintage**: which version of the value. Revisions append new vintages; nothing is overwritten.
- Fundamentals are step functions: a value holds from its available_date until the next one arrives.
- Prices: the stored close is split-adjusted as of the pull. A new split triggers a full re-pull for that ticker as a new vintage. Dividends are stored as events; total-return series come later as derived series.

## 5. Sources

| source | milestone | what | notes |
|---|---|---|---|
| yfinance | M1 | daily close (split-adjusted), splits, dividends | Unofficial. Wrapped and schema-checked; `auto_adjust` always explicit |
| second price source | M1 | reconciliation only | Independent of Yahoo. Candidates: Tiingo free tier, Alpha Vantage free tier, Stooq. Pick in M1 and log the decision |
| FRED / ALFRED | M1 | `GDP` (nominal, SAAR), `GDPC1` (real), `CPIAUCSL` (monthly CPI) | Pull all vintages. Freshness uses FRED release dates, not a hard-coded lag (releases slip, e.g. shutdowns) |
| SEC companyfacts.zip | M2 | XBRL facts, all filers, nightly bulk | Keeps accession + filed date per fact = point-in-time is reconstructible |
| SEC Financial Statement Data Sets | M2 | dimensional facts (multi-class shares), `pre` table | companyfacts drops dimensional facts |
| SEC company_tickers_exchange.json | M2/M3 | current ticker to CIK and exchange | Current only: dead or renamed tickers need manual mapping |
| BEA NIPA | M5 | GDP expenditure detail | The GDP tracker, folded in |

## 6. Refresh pipeline rules
1. **Rebuild where you can, increment where you must.** EDGAR and ALFRED are archives, so analysis tables are rebuilt from them. Prices are incremental, using a stored last-pulled watermark.
2. **Revisions append.** New vintage rows, never updates.
3. **Raw is a snapshot log.** Each run writes everything it downloaded to `raw/<source>/<run_id>.parquet` and never touches the file again. FRED/ALFRED lands the full vintage history every run; prices land the incremental window plus a few days of overlap. Deduplication on `(series_id, obs_date, vintage)` happens in staging, never at load.
4. **Write-audit-publish with atomic swap.** A failed run leaves yesterday's serving data intact. `serving/` is one write-once folder per published run plus `CURRENT`, a one-line pointer; the swap is replacing `CURRENT` (D30).
5. **Run manifest every run**, including failed runs.
6. **Idempotent.** Rerunning immediately adds zero rows to `serving/` and stays green. (`raw/` gains that run's snapshot file; that is the evidence trail, not a defect.)
7. The nightly schedule is a Windows Task Scheduler entry with "run as soon as possible after a missed start" enabled. A failure raises a Windows toast via the BurntToast module.

## 7. Health system

### Principle
Completeness requires a declared expectation. `config/watchlist.csv` plus each series' `active_from`/`active_to` IS the expectation, so it's part of the control system, not just config.

### Check catalog (v1)
| id | assertion | severity | gate | rule (query returns violating rows) |
|---|---|---|---|---|
| C01 | cutoff | error | no | last *published* run older than 26h, or none ever, against the run time or the view-time clock (D32) |
| C02 | cutoff | warn | no | active series exactly 1 period behind its expected latest period: periods are XNYS sessions (prices) or months/quarters (FRED), due once period end + `expected_lag_days` is before the clock's UTC date (D33) |
| C17 | cutoff | error | no | the same rule at 2 or more periods behind, or a frequency the rule can't count (D33) |
| C03 | completeness | error | yes | watchlist entity × required series with no series in catalog |
| C04 | completeness | error | yes | expected date with no row, from `active_from` to the earlier of `active_to` and the series' last observation (trading days from XNYS, D24; months/quarters for macro). Prices: current vintage; FRED: any vintage. A withdrawn value (NULL) is a row, not a hole (D25); a series running late is C02's |
| C05 | completeness | error | yes | raw files on disk do not reconcile to the load log: a recorded file missing, its row count changed, or a file with no load record (append-only invariant, D26) |
| C06 | completeness | error | yes | rows landed ≠ rows downloaded (load control total) |
| C07 | accuracy | error | yes | `(series_id, obs_date, vintage)` repeated with the same value: staging's dedup failed. Same key with different values is C14's (D27) |
| C08 | accuracy | error | yes | schema differs from contract (columns, types) |
| C09 | validity | error | yes | price ≤ 0; obs_date in future; obs_date > available_date |
| C10 | validity | warn | yes | abs(daily return) > 40% on split-adjusted close, current vintage per series, no dates excluded (D29) |
| C11 | integrity | error | yes | observations without catalog row, or an active catalog series (`active_to` NULL or on/after the run's UTC date) without observations |
| C15 | completeness | warn | no | ended catalog series (`active_to` before the run's UTC date) without observations: reported as source-missing (D22) |
| C16 | validity | warn | no | value withdrawn by the source (NULL) in the latest vintage, inside the active window (D25) |
| C18 | completeness | error | no | active watchlist ticker with no Tiingo rows in this run (reference source missing; an outage fires every ticker) (D37) |
| C19 | uniqueness | error | no | `(ticker, obs_date)` repeated in this run's Tiingo file (D37) |
| C20 | freshness | warn | no | Tiingo's latest close for an active ticker older than the latest due XNYS session (D37) |
| C12 | reconciliation | warn | no | sampled split-adjusted closes differ from Tiingo by > 0.5%, a ticker both sources have shares no date with Tiingo, or a reviewed reclassification (D40) matches no Tiingo distribution; sample = 5 dates per ticker drawn by hash(run_id) + the latest common date, defined once in `transform/reconcile_sample.sql` (D38) |
| C13 | baseline | warn | no | first_date moved later, row_count below baseline, or a baselined series gone, vs the opening-balance audit; also fires while no baseline is recorded. Metrics defined once in `transform/baseline_metrics.sql` (C04's row rule), shared with the audit (D39) |
| C14 | accuracy | error | yes | same `(series_id, obs_date, vintage)` with different values across raw snapshots (silent source correction). Prices: current vintage per series only (D23); FRED: every key |

### Gate vs monitor
- **Gate** checks assert facts about the staged data. An error-severity gate failure blocks the publish.
- **Monitor** checks assert facts about the world (staleness, source lateness, second-source agreement, baseline drift). They are recorded in `check_results` and drive the status color, but never block a publish: blocking cannot fix them, and C01 at gate would lock the pipeline out after two missed nights.

### Status roll-up
- **Green**: all checks pass and the last successful run is under 26h old.
- **Yellow**: warnings only.
- **Red**: any error, a blocked publish, or staleness.
- One overall status, no per-source roll-up (D42). Which source a failure belongs to is read from its rows, grouped on the health page.

### Three surfaces, one source of truth (`health/` tables)
1. **Health page**: the first page in the app. A status bar on every page.
2. **`uv run pharos health`**: a colored terminal table. Every Claude Code session starts here.
3. **`health/latest.md`**: a plain-text summary rewritten every run and every `pharos health` (D34).

### Health page contents
- Overall traffic light: last successful run, latest obs date, row count, pass/warn/fail counts. Failing rows are grouped by source (the `yf:`/`fred:` series prefix, or the row's source column) under each failing check (D42).
- Coverage heatmap: rows = series, columns = months, color = % of expected observations present.
- Row count by run per source.
- Run strip: last 30 runs as colored squares.
- Failing checks table, drillable to the failing rows.
- Spot-check panel: 5 random observations per load, each linked to its source page (audit sampling).

### Opening-balance audit
Before trusting the first backfill:
- Reconcile every watchlist ticker on ≥5 random dates against the second source.
- Record per-series baseline metrics.
- From then on, C13 watches for drift from that baseline.
- `uv run pharos audit` runs it against `serving/CURRENT` (D39, D41): every common date is compared and written. An isolated day over 0.5% is reported as a print disagreement; consecutive days over, a day at either end of the range, or an active ticker with fewer than 5 dates compared block, and then no baseline is recorded and it exits 1.

### Fault injection (control effectiveness)
`tests/test_design_faults.py` drives each fault end to end through `refresh()` (load -> stage -> checks -> publish -> manifest) and `pharos health`, on a synthetic data root (never real data), injecting it where it would really happen. Each fault must produce the expected color and appear on the health page (step 8). Fault 2 has two halves: a duplicated bar in raw is absorbed by staging's dedup (C07 passes, publishes), and a duplicate that survives staging is injected into the staged table, where C07 guards the dedup. `tests/test_faults.py` keeps the per-check tests on staged tables:
1. Delete one month of one ticker → C04 red, heatmap hole.
2. Duplicate one day → C07 red, publish blocked, serving unchanged.
3. Backdate the last successful run by 2 days → C01 red.
4. Multiply one close by 10 → C10 warn.
5. Change one past close in a later raw snapshot, same vintage → C14 red, publish blocked, serving unchanged.

## 8. Dashboard
- **Pages:** Home (watchlist chart + status bar; never empty on open), Health, Charts.
- **Charts page:**
  - Pick any series and overlay several.
  - Date range with range-selector buttons (1Y/5Y/YTD/Max).
  - Rebase to 100 at range start by default; dual axes opt-in only.
  - Log toggle.
  - Chart state lives in the URL (`st.query_params`), so a bookmark reopens the same chart.
- **Caching:** `st.cache_resource` for the DuckDB connection; `st.cache_data` keyed on `last_published_at`. The status bar and health page evaluation are keyed on (`last_published_at`, wall clock to the minute), and the app evaluates with `record=False` (D43).
- **Desktop icon:**
  - Shortcut runs `powershell -WindowStyle Hidden -File scripts\open-dashboard.ps1`, minimized, with a custom .ico.
  - The script checks whether `localhost:8501` responds, starts Streamlit headless if not, waits, then opens `msedge --app=http://localhost:8501`.
  - No PyInstaller, Electron, or pywebview.

## 9. Known traps for fundamentals (M2+), as rules
- **Shares:**
  - Book value per share uses `CommonStockSharesOutstanding` (balance-sheet date) or `dei:EntityCommonStockSharesOutstanding` (cover page).
  - Never use weighted-average shares; never use `CommonStockSharesIssued` (it includes treasury shares).
  - Flag divergence between the two share counts above 3%.
- **Equity:**
  - Use `StockholdersEquity` (parent only), not the version including noncontrolling interest.
  - Subtract `PreferredStockValue` for book value to common.
  - Tangible book also subtracts `Goodwill` and `IntangibleAssetsNetExcludingGoodwill`.
  - Partnerships tag `PartnersCapital`.
- **Multi-class companies** (GOOGL, META, BRK.B): cover-page shares are dimensional, so they're absent from companyfacts. Use the Financial Statement Data Sets.
- **Negative equity** (e.g. MCD): P/B is undefined. Flag it, never rank it.
- **Scale errors:** XBRL values are full units, but small filers sometimes mis-scale by 1,000x. Treat the P/B distribution tails as the bug list.
- **Flow statements:**
  - 10-Qs report year-to-date; the cash flow statement is year-to-date only.
  - Q4 = full year − 9-month YTD.
  - Handle 52/53-week years (COST, HD).
  - Fiscal-to-calendar alignment needs an explicit rule.
- **Restatements:** keep every fact with its accession number and filed date. Offer as-first-reported and latest views.
- **Financials** (banks, insurers, REITs): generic ratios break. Use sector templates or exclude SIC 6000–6999 from cross-industry comparisons.
- **Ticker history:** CIK is stable and tickers aren't (FB→META). The SEC tickers file is current-only.

## 10. Hosting path (floating milestone)
- GitHub Actions cron runs the refresh and writes `serving/` + `health/` to Cloudflare R2.
- The Streamlit Community Cloud app reads R2 through DuckDB.
- Constraints: about 1 GB memory, sleeps after 12h idle, one private app (the repo is public).
- The desktop icon gets repointed to the hosted URL; the phone uses Add to Home Screen.
- Failure alerts come from GitHub Actions' default email.

## 11. Decision log
| # | date | decision | why | rejected |
|---|---|---|---|---|
| D1 | 2026-10-07 | Parquet is the storage of record; DuckDB is the engine; `catalog.duckdb` holds views only | `.duckdb` single-writer lock conflicts with nightly refresh; open format; clean layer boundaries; DBeaver still works via views | `.duckdb` as storage |
| D2 | 2026-10-07 | SQL-first transforms in DuckDB; Polars at edges only | Auditable in DBeaver; owner thinks in SQL | pandas/Polars-centric transforms |
| D3 | 2026-10-07 | Streamlit + Plotly, local first | Python end to end; same code local and hosted; Plotly step lines, range selectors | Dash, Marimo, Evidence; Observable Framework (maintenance stalled) |
| D4 | 2026-10-07 | uv with committed lockfile | Identical environments locally and in CI | pip/venv, conda |
| D5 | 2026-10-07 | Series model: catalog + long observations with available_date and vintage | Any-vs-any charting; point-in-time correctness | Wide per-source tables |
| D6 | 2026-10-07 | Public GitHub repo | All sources are public; Community Cloud allows one private app | Private repo |
| D7 | 2026-10-07 | Refresh rules (section 6) | EDGAR/ALFRED are archives; a failed run can't corrupt serving | Incremental everything |
| D8 | 2026-10-07 | Milestone order: clean sources and dashboard first, XBRL after; balance sheet before flows | Motivation; proves schema on clean data; instants avoid YTD differencing | P/B snapshot first |
| D9 | 2026-10-07 | Desktop icon = launcher script → Edge `--app` window; opening never refreshes | Must be one double-click or it won't get used | Native wrappers |
| D10 | 2026-10-07 | Health before charts: write-audit-publish gate, checks as SQL, three surfaces, fault injection | Correctness is the owner's top priority | Great Expectations / Soda / dbt in v1 |
| D11 | 2026-10-07 | Separate repo; `serving/` Parquet is the interface for other projects | Keeps this a data product with a contract | Shared monorepo with variance agent |
| D12 | 2026-10-07 | yfinance for M1 prices, wrapped and checked, plus an independent reconciliation source | Free; fragility acknowledged and controlled | Paid vendors in v1 |
| D13 | 2026-10-07 | Macro from FRED/ALFRED with full vintages | Point-in-time macro; GDP gets revised | Latest-only macro |
| D14 | 2026-10-07 | 18-name watchlist chosen to trip known traps | Demo set doubles as test coverage | Random or favorite names |
| D15 | 2026-10-07 | Project name: Pharos (repo, package, CLI `pharos`) | Lighthouse of Alexandria: a light that lets you see far and signals safe/unsafe, like the health light; short and typeable as a CLI | Sentinel, Overwatch, Argus, Tycho, Benchmark |
| D16 | 2026-10-07 | Data root is `data/` inside the repo folder, gitignored; `PHAROS_DATA_ROOT` stays the only path source | The weekly workshop mirror backs it up, and `raw/` is append-only and partly irreplaceable (old yfinance pulls); the real risk, committing data to a public repo, is closed by the ignore rule | Data root outside the repo (`D:\data\pharos`: not mirrored); sibling workshop folder (breaks the one-folder-per-project rule) |
| D17 | 2026-10-07 | Checks are gate or monitor. Only gate checks (C03–C11) can block a publish; C01, C02, C12, C13 are monitor-only | C01 at gate deadlocks: after two missed nights every run is blocked, so the last success never gets newer. World-state checks can't be fixed by blocking; they only withhold valid data | Every error-severity check gates |
| D18 | 2026-10-07 | Raw is a write-once snapshot log per run; dedup in staging; price vintage = adjustment epoch; C14 catches same-key value conflicts | Append-only raw + full FRED re-pull + "rerun adds zero rows" + C06 couldn't all hold. Repeated source testimony is the audit trail; write-once files also protect the mirror backup | Land only new keys (loses evidence of what the source returned; transform logic at load time) |
| D19 | 2026-10-07 | `units` lives on every observation row, set by the vintage's metadata window; `series_catalog.units` is the current units only | Units change across vintages (GDPC1 has 8 base years, CPIAUCSL rebased 1967 -> 1982-84 at the 1988-02-26 vintage). Catalog-only units mislabel point-in-time and revision reads, and fail silently | Separate units-history table joined on vintage (every consumer must remember the join); one series_id per units regime (breaks one series, many vintages, D13) |
| D20 | 2026-10-07 | Raw Yahoo rows carry `pull_start` (inclusive) and `pull_end` (exclusive). A ticker's pull is full-history when `pull_start` <= its first expected date; price vintage = run date of the ticker's latest full pull at or before the row's run. Raw files from before the columns existed (only run 20261007T140923Z) read as NULL = full pull, which every run to date was | Staging must tell full from incremental pulls once incremental exists; the raw row should say how it was obtained | Infer epochs by diffing historical closes across runs (vintage decided by a float tolerance); trust `stock_splits` (carries non-split actions: GOOGL 1.998, O 1.032) |
| D21 | 2026-10-07 | Yahoo pulls are incremental: from the last stored date minus `overlap_days`, full pull when nothing is stored. The overlap's closes are compared with the stored ones (current vintage): all equal within 1e-5 relative = incremental; all off by one common ratio on 2+ dates = re-basing, full re-pull, new vintage; otherwise = correction, landed under the old vintage so C14 blocks; a nonzero `stock_splits` on a not-yet-stored date also forces a full pull. Each row records `pull_reason` | Full pulls every night made every day a new price vintage (D20). A correction must never trigger a full re-pull, because the new vintage key would hide it from C14. 1e-5 is ~100x float32 noise and far below any split ratio; a single shared date can't tell re-basing from correction, so it counts as correction (blocks for review) | Any mismatch = full re-pull (launders corrections into a new vintage); trigger only on `stock_splits` (column carries non-splits and can miss re-basings) |
| D22 | 2026-10-07 | C11 splits by series state. Orphan observations and an active series with zero observations stay error/gate (C11). An ended series (`active_to` in the past) with zero observations is C15, warn, monitor: shown as source-missing, never blocks | ATVI (ended 2023-10-12) returns nothing from Yahoo, so C11 as written blocked every publish, against acceptance #3 (ended, source-missing as warn). A source dropping a dead ticker's history is a fact about the world, not the staged data (D17). Two check files, not one with mixed severity, because a check file declares one severity (open issue #11) | Drop ATVI from the expected set (hides the survivorship finding); per-series exemption list (manual override that grows silently) |
| D23 | 2026-10-07 | A Yahoo correction is accepted by a deliberate full re-pull (`pharos load --full TICKER --reason ...`, rows carry `pull_reason = correction_accepted`), which starts a new price vintage under D20. For prices C14 checks only each series' current vintage; FRED keeps C14 on every key | Raw is write-once, so a correction landed under the old vintage (D21) made C14 fail forever. Re-pulling turns the correction into a revision: revisions append, the old value stays readable in the superseded vintage, and point-in-time reads see when the value changed. Narrowing C14 to the current vintage only stops re-checking a conflict a human already reviewed and superseded. FRED conflicts stay blocking: ALFRED vintages should be immutable, so one is an anomaly to investigate (open issue #24) | Accepted-corrections file that overrides dedup (value changes with no vintage trace; an exemption list); automatic re-pull on any mismatch (launders corrections, rejected in D21) |
| D24 | 2026-10-07 | Expected trading days come from `exchange_calendars` XNYS, built with `start` = `backfill_start` at every run and written to a `trading_days` table at the edge; C04 joins it in SQL | The expectation must not come from the data it checks: a calendar built from the union of observed dates hides a day Yahoo drops for every ticker and expects a bad extra date. Reconciled 2026-10-07: 5,474 sessions 2005-01-03..2026-10-06 = 5,474 observed Yahoo dates, zero differences either way; one-off closures present (2018-12-05, 2025-01-09, 2012-10-29/30). The default calendar starts 20 years back (2006-10-09) and raises DateOutOfBounds before it, hence the explicit start | `pandas_market_calendars` (wraps the same data, extra layer); union-of-observed calendar (circular) |
| D25 | 2026-10-07 | C04 counts a row whose latest-vintage value is withdrawn (FRED ".", stored NULL) as present: it fires only when no row exists. C16 (warn, monitor) lists every withdrawn value inside the active window. C04 ends each series' window at its last observation, so trailing lateness is C02's alone | CPIAUCSL 2025-10-01 has one vintage (2025-12-18) and its value is ".": the source says the value will not exist, so as a C04 hole it would block every publish. "The source says no value" and "we hold no row" are different facts; only the second is a defect in our data. Prices are checked on the current vintage so a full re-pull missing a date blocks instead of letting serving fall back to an older adjustment basis | Known-gaps exception file (exemption list); keep C04 as written (permanent block) |
| D26 | 2026-10-07 | C05 reconciles raw files on disk to the load log (`health/loads`): every recorded file must exist with its recorded `rows_written`, and every file must have a record. The runner binds the files as `raw_files` | The original rule (raw row count lower than previous run) assumed every run was a full pull; under D21 an incremental night lands ~119 Yahoo rows against a full pull's 85,110, so it would fire nightly. The invariant is that a written file stays written: the load log is the ledger, the files are the detail. Row counts miss a rewrite that keeps the count (open issue #28) | Cumulative raw row total never falls (needs evaluation history, same blind spot); drop C05 and rely on C06 (checks a file only when written) |
| D27 | 2026-10-07 | C07 fires only on a key repeated with the same value (NULL counts as a value); same key with different values is C14's alone | Staging collapses rows that agree on everything and keeps same-key value conflicts for C14 (D18). A plain duplicate-key C07 fired on every correction alongside C14, and after a D23 accept it blocked forever on the superseded vintage C14 no longer checks. Split by cause: C07 = our dedup broke (e.g. rows differing only in units or available_date), C14 = the source changed its story | Scope C07 like C14 (one correction still fires under two IDs); drop C07 (nothing catches a broken dedup) |
| D28 | 2026-10-07 | C08's schema contract is a `VALUES` list inside `checks/C08_schema_contract.sql`, compared both ways (missing, unexpected, type) against `information_schema.columns` of the staged `observations` and `series_catalog`. Column order and nullability are not in the contract | A control must not share a source with what it controls: the transform creates the schema, so a contract derived from it (or from a file the transform also reads) compares the schema with itself and cannot fire. Stating it twice is the control, not duplication; a deliberate schema change edits transform/ and C08 in the same commit. One self-describing SQL file per check, no runner change | Contract in a config file (indirection, and the transform may come to read it); contract in Python (same tautology risk if the dict also builds the tables) |
| D29 | 2026-10-07 | C10 has no split-date exclusion: abs(close / previous close - 1) > 40% between consecutive observations of each price series' current vintage, withdrawn values skipped. Yahoo's fractional split factors (GOOGL 2014-04-03 1.998, O 2021-11-15 1.032) are history-rebasing corporate actions and stay in the split re-pull rule | On split-adjusted history a correctly adjusted split date shows no jump, so a jump on a split date is the unadjusted-split signature C10 exists to catch; excluding those dates blinds it (#4). Live run 20261007T153852Z: 0 returns above 40%; GOOGL +0.6% and O +0.9% across their events, so Yahoo did re-base history for both (#15). The series value is split-adjusted, not dividend-adjusted `close`; COST's $15 special dividend moved it -1.2% (#16) | Exclude known split dates (the original rule); filter fractional factors out of the split list; exclude ex-dividend dates |
| D30 | 2026-10-07 | Publish writes `serving/<run_id>/` once (built as `.building-<run_id>`, row counts reconciled to staging, then renamed) and swaps by `os.replace` of `serving/CURRENT`, a one-line pointer, retried briefly then failing loudly. Only verdict `passed` publishes. Readers (catalog views, the app) resolve `CURRENT`; old versions are kept | Windows refuses to rename a folder or replace a file another process holds without delete sharing (open issue #7), so swapping the data itself fails whenever DBeaver or the app has it open. Swapping a pointer no reader holds open keeps the swap atomic and leaves the old version readable for anyone mid-query. Same shape as raw/: write once, never rewrite. Tests pin: a held old-version file does not block the swap; an exclusively held `CURRENT` raises and keeps the old pointer; blocked and failed runs leave serving/ byte-identical | Rename `serving/` aside and the staged folder in (fails on any open file); per-file `os.replace` (not atomic across tables, fails on locks) |
| D31 | 2026-10-07 | `pharos refresh` (load -> stage -> publish) owns the run and writes `health/run_manifest/<run_id>.parquet` in a finally block: a crash anywhere records status `failed` with the traceback and is re-raised. Long format, one row per raw dataset, run-level fields repeated. Check counts are per run. `published_at` is set only on publish; its maximum is the app's cache key | Only the step that owns the whole run can say how it ended, including a crash before any check ran (section 6.5: manifest every run, including failed). Long format keeps a new source as new rows. Checks span sources, so per-source check counts (the original wording) would be invented. Live: run 20261007T170003Z published, and the rerun added 0 rows to serving (96,160 = 96,160, EXCEPT ALL both ways = 0), acceptance #2 | Wide row per run (a column per source); manifest written by publish() (misses load and stage crashes) |
| D32 | 2026-10-07 | C01's "successful" means published: `max(published_at)` over the run manifests with status `published`, measured against a `clock` the runner binds (the run's own time by default; `run_checks(now=...)` for view time). No publish ever also fires | A blocked run leaves serving/ as stale as a run that never happened, and staleness of what the user sees is what C01 guards. Two blocked nights turn C01 red even though the scheduler is alive, which is correct: the data on screen is 48h old. Live: 20261007T170003Z passes at run time; the same run evaluated at 2026-10-08 19:01Z fires (age 26h00m) | Successful = finished without a crash, published or blocked (C01 would only mean the scheduler is alive) |
| D33 | 2026-10-07 | Freshness is one rule split by severity: C02 warns at 1 period behind, C17 errors at 2 or more (a check file declares one severity). Expected dates come from a fixed `expected_lag_days` per series in `sources.yaml` (CPIAUCSL 15, GDP and GDPC1 35 days after period end; prices 0, counted in XNYS sessions), carried into `series_catalog`. No release calendar | Measured first-release lags 2023-2026 from the loaded vintages: CPIAUCSL median 12 days (max 48), GDP and GDPC1 median 30 (max 84); the maxima are the 2025 shutdown. Under a fixed lag a slipped release reads as late, which is true and is what C02 exists to show; a calendar FRED rescheduled after the slip would have stayed green over stale data. A release calendar is a new raw source with its own three checks, to make the signal less informative. Live: 20261007T171613Z passes; with no new data, 2026-10-14 makes all 17 price series C17-stale and 2026-11-05 makes the three macro series C02-late | FRED release-dates endpoint as expected dates (#19's proposal); one C02 file at warn only (2 periods stale never turns red) |
| D34 | 2026-10-07 | `pharos health` judges what `serving/CURRENT` points at: gate checks are read from their latest recorded run-time result for that run (an unrecorded gate check is `broken`); monitor checks are re-run now against the published tables with the wall clock. Red on any error or broken check, nothing published, or a latest run that blocked or failed; yellow on warnings only. Results carry `context` (`run` / `view`). `refresh` rewrites `latest.md` after every run, crashed ones included; exit 1 on red | Gate checks tested staged data that has not changed since publish, so re-running them proves nothing new and would need `staging/`, which is regenerable. Monitors test the world, so only they can turn a silently stopped pipeline red. First live run: yellow, from C15 (ATVI) and C16 (CPI Oct 2025) | Re-run every check at view time (slower, needs staging/); report only the last run's recorded results (C01 can never fire at view time) |
| D35 | 2026-10-07 | The second price source is Tiingo end-of-day (free tier: 1,000 requests/day, 50/hour, 500 symbols/month). Its split-adjusted close is derived in DuckDB from Tiingo's raw close and its own split factors, never from Yahoo's. Key from `TIINGO_API_KEY` in `.env` | C12 compares split-adjusted closes, so the second source must supply its own split basis or the reconciliation compares Yahoo with itself. Tiingo returns raw prices with split factors and dividends; the watchlist's 17 tickers fit the free tier many times over | Alpha Vantage (free daily series is unadjusted with no split factors; adjusted series is paid; 25 requests/day); Stooq (close is adjusted with no raw series, likely including dividends; API key via CAPTCHA since about April 2026) |
| D36 | 2026-10-07 | The Tiingo loader pulls every watchlist ticker's full history every run into `raw/tiingo/prices/<run_id>.parquet`. Tiingo is not part of a run's completeness (`stage.DATASETS`) and never reaches serving/. Key in the Authorization header, never the URL | Full history keeps each night's split-adjusted closes inside one snapshot (no watermark, no stitching split factors across runs) at 17 requests a night against 1,000. Counting Tiingo toward completeness would make every earlier run incomplete, and staging would silently drop their Yahoo history | Incremental pulls like Yahoo (needs step 3b's watermark logic again); Tiingo as a required dataset |
| D37 | 2026-10-07 | A Tiingo load failure does not fail the run: refresh records it as that dataset's `dataset_error` in the manifest and goes on to stage and publish Yahoo and FRED. C18-C20 are Tiingo's completeness, uniqueness and freshness checks, all monitors over this run's Tiingo raw file (bound as `raw_tiingo_prices`, empty when absent); C18 at error is what turns health red on an outage. `complete_runs()` counts only the required datasets | Tiingo never reaches serving (D35), so blocking on it would freeze good data over an outside-world problem blocking can't fix (D17's reasoning for C01). The gap stays loud through health, not by withholding data. Counting only required datasets is forced: the first wiring made every run incomplete (4 load records against an expected 3) and staging found nothing | Fail the whole run on a Tiingo error; Tiingo checks as gates |
| D38 | 2026-10-07 | C12 samples fresh dates every run: per ticker, 5 common dates ranked by hash(run_id, ticker, date) plus the latest common date. Tiingo's split-adjusted close is its raw close over the product of its own later split factors. The sample is one SQL file (`transform/reconcile_sample.sql`) the runner binds as `reconcile_sample`, shared with the opening-balance audit; if it can't be built, checks using it are `broken` with the cause. C12 also fires when both sources have a ticker but no common date. Float32 noise (~1e-7) sits inside 0.5%, closing #14 | Fresh draws build coverage over the whole history, so a bad stretch anywhere is eventually sampled; the latest date checks every new close; hashing the run_id makes any night's draw reproducible from the manifest alone. A shared file keeps audit and nightly check from drifting. The no-common-dates row closes a vacuous pass: a shifted date mapping would otherwise compare nothing and stay green | Re-check the audit's fixed dates nightly (never looks elsewhere); a random draw without a recorded seed (not reproducible) |
| D39 | 2026-10-07 | The opening-balance audit is all or nothing: it records the baseline only when every active watchlist ticker has at least 5 random dates compared (reconcile_sample, seeded by the published run's id) and no compared date, the latest included, differs by more than 0.5%. Every comparison and every coverage gap is written to `health/audit/` either way; a failure records nothing and exits 1. The baseline is written once; an audit when one exists stops before doing anything. Baseline metrics count C04's rows (prices: current vintage; FRED: distinct obs_date, any vintage), in one SQL file shared with C13. C13 warns while no baseline is recorded. reconcile_sample labels a drawn date `random` even when it is also the latest | The audit is the gate on trusting the backfill at all. (Corrected under D41: the baseline holds only per-series dates and row counts, no prices, so a price disagreement cannot become C13's anchor, as first argued here.) A failure is fixed in the data or by a threshold change, which needs its own decision-log entry: the right friction. Counting all price vintages would hide a re-pull that lost early history, since older vintages stay in serving. A missing baseline warns rather than passing vacuously (D38's reasoning for no common dates). The relabel closes a spurious refusal: a ticker whose latest date was also drawn showed 4 random rows, about 1 in 1,000 per ticker on full history | Record with exceptions flagged (James chose all or nothing, 2026-10-07); a fresh audit may replace the baseline (silently moves the anchor); count all vintages |
| D40 | 2026-10-07 | `config/corporate_actions.csv` lists reviewed Tiingo cash distributions that the reconciliation treats as splits, with a required note per row. reconcile_sample turns a listed distribution into a factor from Tiingo's own numbers, previous close / (previous close - div_cash); an unlisted distribution stays a dividend. C12 also warns when a listed row matches no Tiingo distribution. First row: GOOGL 2014-04-03 class C distribution (Tiingo div_cash 567.97, split_factor 1.0; Yahoo split 1.998) | The live audit of 20261007T182814Z refused on GOOGL 2006-03-15, off 49.95% (#35). Economically the distribution was a split: holders kept their value over twice the shares, which is how Yahoo serves it. Deriving the factor from Tiingo keeps D35's independent basis: 1135.10 / (1135.10 - 567.97) = 2.0015 against Yahoo's 1.998, so 0.175% residual, measured live at 0.1745%; every one of GOOGL's 5,474 common dates is now within 0.27%. A named, reviewed list is a documented reconciling item; a stale entry is loud | Any dividend over a share of the prior close becomes a split (silent reclassification on an invented threshold); exclude GOOGL dates before 2014-04-03 (drops 9 of 21 years from reconciliation) |
| D41 | 2026-10-07 | The opening-balance audit compares every common date (`transform/reconcile_all.sql`, which C12's `reconcile_sample` now draws from), not a 5-date sample. A date over 0.5% is classified by shape: isolated (the common dates either side both within tolerance) is a print disagreement, written and reported but not blocking; consecutive dates over, or one at either end of a ticker's common range, block. Coverage is at least 5 common dates per active ticker. Supersedes D39's sampling and 'any date over tolerance blocks' | A full pass of run 20261007T182814Z found 38 of 85,110 dates on 10 tickers at 0.50-1.40% (#36), every one isolated, clustered on shared dates across tickers (2006-01-18 x5, 2006-06-09 x4): two vendors' closing prints, with no third source to say which is right. The 5-date sample hit one about 3.5% of the time, so the gate passed or failed by luck. Adjustment, mapping and identity errors move every date on one side of an event, so they cannot look isolated: the shape rule keeps exactly what the audit exists to catch. Live: 38 reported, 0 blocking, baseline recorded for 20 series (price row counts equal Tiingo's session counts) | A reviewed list of every disagreeing date (38 near-identical notes nobody can verify); keep the 5-date sample (luck-dependent gate). Known cost: one isolated bad print of any size passes the audit; C10 catches only moves over 40% (#37) |
| D42 | 2026-10-07 | Health has one overall status; there is no per-source roll-up. Every failing row of every check is recorded (the 5-row cap is removed, the column keeps its name `sample`), and the health page groups a failing check's rows by the source each row names. Amends section 7, closes #32 | With three sources, an overall light plus failing rows that already name their series (`yf:`, `fred:`) or source answers 'which source?' at a glance. A per-source status needed a source header on checks, row attribution in the runner and a second roll-up to keep consistent: machinery for a presentation question (James, 2026-10-07). The 5-row cap was a real loss: a 21-session hole kept 5 rows, so the page could not drill to the failing rows section 7 requires. Status logic is unchanged. If per-source lights earn a place (M2 adds SEC), the page can compute them from the stored rows | Per-source status computed in health.py from row attribution plus an optional `source:` check header; one owner source per check (a FRED-only hole would paint Yahoo red) |
| D43 | 2026-10-07 | The app caches `health.evaluate()` on (`last_published_at`, the wall clock truncated to the minute) and calls it with `record=False`, which writes neither `health/check_results/` nor `latest.md`. The CLI and `refresh` keep recording. Data queries stay keyed on `last_published_at` alone | A view-time evaluation measured 1.14s cold, 0.88s warm, against acceptance #9's 2s warm budget, so it can't run on every render. Keyed on `last_published_at` alone, the cached status would stay green while the pipeline silently stopped, which breaks view-time freshness (C01, C02, C17, C20); the minute key bounds that lag at 60s. Every view-time evaluation used to append a check_results file and rewrite latest.md, so an open app would have left up to 1,440 files a day, and the app may only read `health/` | Evaluate on every render (0.9s on each page); cache on `last_published_at` and run only C01 live (C02/C17/C20 freeze); a TTL cache (same bound, but the key is invisible in the code) |

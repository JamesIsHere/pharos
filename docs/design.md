# Design: Pharos

## 1. Purpose and non-goals

**Purpose.** A personal analysis platform. Pull up any stock, macro series, or company-level ratio; compare them over time on one chart; cut across companies and industries. It refreshes daily and tells you plainly whether the data can be trusted.

It is also the datacenter for macro and company data: every company that has filed annual reports with the SEC, domestic or foreign, including the ones that have since stopped, kept at the most detailed level each source offers, as a corpus for analysis. Other projects read it rather than gathering their own. The universe (milestone U, parked by D128) is its base: a survivorship-free, point-in-time list of filers per year, with listing status and exits, against which every company dataset is checked for completeness (D51, D54, D65).

**Non-goals (v1).**
- No intraday data, no trading or execution.
- No foreign-filer fundamentals (20-F/40-F); fundamentals scope is 10-K/10-Q filers. Foreign filers are in the universe (D53).
- No fundamentals before XBRL (pre-2009/2011). No HTML parsing of filings.
- No paid data vendors.
- Not a general BI tool. Other projects (e.g. the variance agent) consume `serving/` Parquet as an interface; they don't reach into this repo's internals.

## 2. Architecture

```
Sources (yfinance, FRED/ALFRED, Tiingo as reference; SEC from M2; BEA later)
   |  Python loaders: download, parse, schema-validate
   v
raw/       Parquet, one write-once file per source per run: everything downloaded, untouched
   |  per source group (src/pharos/groups.py): prices_macro; sec_universe from M2
   |  DuckDB SQL: standardize, derive, as-of joins
   v
staging/   candidate tables per group for this run
   |  checks/*.sql (audit): a gate check at error blocks only its own group
   v
publish    one new version: each passing group's fresh tables; each failing group
   |       carried forward from the last version it published in, or left out if
   |       it never has; no group passes -> run BLOCKED, serving/ untouched (D115)
   v
serving/   <run_id>/: series_catalog + observations (from U also universe_filer,
           universe_firm_year, universe_events) <- the contract; groups.parquet
           (each group's outcome and source run); inputs/ (what view-time checks
           read, D76); CURRENT names the served version
health/    run_manifest, loads, check_results, baseline, audit, latest.md
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
| available_date | date the value became knowable: trade date (prices), vintage release date (macro), the next XNYS trading day after the filing date (every SEC row: fundamentals, universe tables, events; D99) |
| vintage | prices: date of the full-history pull that set the current split adjustment (nightly incremental rows inherit it; a new split starts a new one); macro: ALFRED `realtime_start`; fundamentals: accession filing date |
| run_id, loaded_at | lineage |

Primary key: `(series_id, obs_date, vintage)`. The serving view defaults to the latest vintage per `(series_id, obs_date)`. Point-in-time views filter `available_date <= as_of`.

### events (raw)
Splits and dividends per ticker: `ticker, event_date, type, ratio_or_amount, run_id`.

### universe tables (U, D59; parked by D128)
Entity tables beside the series model. Each row carries the four columns every observation needs.

| table | one row per | obs_date | available_date | vintage |
|---|---|---|---|---|
| `universe_filer` | CIK x details snapshot that changed it | snapshot date | snapshot date | snapshot date (a changed name, SIC, ticker or exchange starts a new vintage) |
| `universe_firm_year` | CIK x calendar year in its span (D55) | Jan 1 of the year (D86) | when the row became knowable: a report year on the next trading day after its report's filing date (D99); a gap year on the next trading day after the filing date of the report that closes the gap; an exit at Jan 1 after the full empty year | filing date of the report that set the row |
| `universe_events` | CIK x accession x form (8-A, 25, 15, 15F) | filing date | next trading day after the filing date (D99) | filing date |

`run_id` and `loaded_at` on every row, as in observations. `universe_firm_year` is dated at the start of its year: frequency `A`, obs_date Jan 1, period end Dec 31 computed where needed (D86). Population counts per year (all, domestic, foreign, exchange-listed, analysed set, joined, left) are computed from it for the Universe page and are not served as series (D101); as chartable series they wait on #41 (M1.5 dropped, D137), with D86's count-vintage rule.

### health tables
- `run_manifest`: one write-once file per run, one row per raw dataset (D31): run_id, started_at, finished_at, status (`published` / `blocked` / `failed`), published_at, source, dataset, rows_downloaded, rows_landed, latest_obs_date, checks_pass / checks_warn / checks_error / checks_broken / checks_acknowledged (per run, after acknowledgment, D46), error. `max(published_at)` is `last_published_at`.
- `loads`: one write-once record per download, `health/loads/<run_id>__<source>__<dataset>.parquet`, written by `runs.write_raw`: run_id, source, dataset, path (NULL for an empty download), rows_downloaded, rows_written (C06), loaded_at; from D119 also sha256 of the file as written (C05, D108), last_modified (the download's HTTP Last-Modified) and source_count (the count the source states for the download); from D121 a note (what the loader decided that the rows can't show, e.g. a stale copy skipped); each NULL where a loader has none and in older records. Read with union_by_name (D91).
- `check_results`: run_id, check_id, severity, status, failing_row_count, every failing row (JSON, in the column named `sample`, D42), evaluated_at. Appended every run.
- `baseline`: per series first_date, last_date, row_count at the opening-balance audit; written once to `health/baseline/<run_id>.parquet` (D39). Every audit attempt's comparisons go to `health/audit/<run_id>__<stamp>.parquet`.

## 4. Time semantics
- **obs_date**: the period the value describes.
- **available_date**: when it became public. All cross-source joins use `ASOF JOIN` on this column.
- **vintage**: which version of the value. Revisions append new vintages; nothing is overwritten.
- Fundamentals are step functions: a value holds from its available_date until the next one arrives.
- Prices: the stored close is split-adjusted as of the pull (D107). A full re-pull, and with it a new vintage, is triggered by a nonzero `stock_splits` on a not-yet-stored date or by re-basing found in the overlap window (D21); a correction never triggers one, it blocks on C14. Dividends are stored as events; total-return series come later as derived series.

## 5. Sources

| source | milestone | what | notes | shown online (D106) |
|---|---|---|---|---|
| yfinance | M1 | daily close (split-adjusted), splits, dividends | Unofficial. Wrapped and schema-checked; `auto_adjust` always explicit | No: Yahoo terms 2(h) bar distributing or publicly performing its content without written permission |
| second price source | M1 | reconciliation only | Independent of Yahoo. Candidates: Tiingo free tier, Alpha Vantage free tier, Stooq. Pick in M1 and log the decision | No: Tiingo (chosen, D35) free accounts are 'internal and personal use only', no redistribution 'in any form' |
| FRED / ALFRED | M1 | `GDP` (nominal, SAAR), `GDPC1` (real), `CPIAUCSL` (monthly CPI) | Pull all vintages. Freshness uses FRED release dates, not a hard-coded lag (releases slip, e.g. shutdowns) | Not now (#103): the API terms bar storing its content; the series are public domain at BEA/BLS |
| SEC companyfacts.zip | M2 (D128) | XBRL facts, all filers, landed on demand (D131): `company_facts` (one row per fact, keyed cik, taxonomy, concept, unit, accession, start_date, end_date), `company_concepts` | Keeps accession + filed date per fact = point-in-time is reconstructible. Landed level; freshness C40 | Yes (SEC data is public; unverified, from prior) |
| SEC Financial Statement Data Sets | M3 | dimensional facts (multi-class shares), `pre` table | companyfacts drops dimensional facts | Yes (as above) |
| SEC EDGAR full-index (`master.gz` per quarter) | M2 | every filing: CIK, name, form, date filed: `filings` | Annual reports, events and gap years (D54, D65); current and previous quarter re-landed each run. Landed level; freshness C25 | Yes (as above) |
| SEC submissions.zip | M2 | company details (name, SIC, state, tickers, exchanges) and filing history: `submission_details`, `submission_filings` | Nightly bulk; details snapshot every run, history past the watermark (D58). Landed level; freshness C31 | Yes (as above) |
| SEC company_tickers_exchange.json | M2 | current ticker to CIK and exchange: `tickers` | Current only: dead or renamed tickers need manual mapping. Landed level; freshness C34 | Yes (as above) |
| BEA NIPA | M6 | GDP expenditure detail | The GDP tracker, folded in | Yes, with BEA's notice: its API terms allow building a service to 'display' its data |

## 6. Refresh pipeline rules
1. **Rebuild where you can, increment where you must.** EDGAR and ALFRED are archives, so analysis tables are rebuilt from them. Prices are incremental, using a stored last-pulled watermark.
2. **Revisions append.** New vintage rows, never updates.
3. **Raw is a snapshot log.** Each run writes everything it downloaded to `raw/<source>/<dataset>/<run_id>.parquet` and never touches the file again. FRED/ALFRED lands the full vintage history every run; prices land the incremental window plus a few days of overlap. Deduplication on `(series_id, obs_date, vintage)` happens in staging, never at load.
4. **Write-audit-publish with atomic swap.** A failed run leaves yesterday's serving data intact. `serving/` is one write-once folder per published run plus `CURRENT`, a one-line pointer; the swap is replacing `CURRENT` (D30).
5. **Run manifest every run**, including failed runs.
6. **Idempotent.** Rerunning immediately adds zero rows to `serving/` and stays green. (`raw/` gains that run's snapshot file; that is the evidence trail, not a defect.)
7. The nightly schedule is a Windows Task Scheduler entry with "run as soon as possible after a missed start" enabled. A failure raises no notification; it shows on the dashboard (D50).

## 7. Health system

### Principle
Completeness requires a declared expectation. `config/watchlist.csv` plus each series' `active_from`/`active_to` IS the expectation, so it's part of the control system, not just config.

### Check catalog (v1)
| id | assertion | severity | gate | rule (query returns violating rows) |
|---|---|---|---|---|
| C01 | cutoff | error | no | last *published* run older than 26h, or none ever, against the run time or the view-time clock (D32) |
| C02 | cutoff | warn | no | active series exactly 1 period behind its expected latest period: periods are XNYS sessions (prices) or months/quarters (FRED), due once period end + `expected_lag_days` is before the clock's date: the New York date for prices, the UTC date for FRED (D33, D118) |
| C17 | cutoff | error | no | the same rule at 2 or more periods behind, or a frequency the rule can't count (D33) |
| C03 | completeness | error | yes | watchlist entity × required series with no series in catalog |
| C04 | completeness | error | yes | expected date with no row, from `active_from` to the earlier of `active_to` and the series' last observation (trading days from XNYS, D24; months/quarters for macro). Prices: current vintage; FRED: any vintage. A withdrawn value (NULL) is a row, not a hole (D25); a series running late is C02's |
| C05 | completeness | error | yes | raw files on disk do not reconcile to the load log: a recorded file missing, its row count changed, its content changed (sha256 differs from the record's; records from before D108 have none), or a file with no load record (append-only invariant, D26, D119) |
| C06 | completeness | error | yes | rows landed ≠ rows downloaded (load control total) |
| C07 | accuracy | error | yes | `(series_id, obs_date, vintage)` repeated with the same value: staging's dedup failed. Same key with different values is C14's (D27) |
| C08 | accuracy | error | yes | schema differs from contract (columns, types) |
| C09 | validity | error | yes | price missing (NULL or NaN, D73) or ≤ 0; obs_date in future; obs_date > available_date |
| C10 | validity | warn | yes | abs(daily return) > 40% on split-adjusted close, current vintage per series, no dates excluded (D29) |
| C11 | integrity | error | yes | observations without catalog row, or an active catalog series (`active_to` NULL or on/after the run's UTC date) without observations |
| C15 | completeness | warn | no | ended catalog series (`active_to` before the run's UTC date) without observations: reported as source-missing (D22) |
| C16 | validity | warn | no | value withdrawn by the source (NULL) in the latest vintage, inside the active window (D25) |
| C18 | completeness | error | no | active watchlist ticker with no Tiingo rows in this run (reference source missing; an outage fires every ticker) (D37) |
| C19 | uniqueness | error | no | `(ticker, obs_date)` repeated in this run's Tiingo file (D37) |
| C20 | freshness | warn | no | Tiingo's latest close for an active ticker older than the latest due XNYS session (D37) |
| C35 | carry-forward | warn | no | a source group was carried forward in the latest run: served from an earlier run (`group: run`, D81, D82, D116) |
| C36 | carry-forward | error | no | a source group has not published fresh in 2 or more runs in a row, or has never published (`group: run`, D82, D83, D116). A run that didn't attempt a group (`not_run`) doesn't count (D135) |
| C25 | freshness | error | no | latest full-index filing date older than 3 business days (weekdays) at the clock (`group: sec`, D60) |
| C31 | freshness | warn | no | latest submissions.zip Last-Modified older than 36 hours at the clock (`group: sec`, D60) |
| C34 | freshness | warn | no | latest ticker file Last-Modified older than 14 days at the clock; provisional (`group: sec`, D60) |
| C40 | freshness | warn | no | latest companyfacts landing older than 92 days at the clock; on demand, never red (`group: companyfacts`, D131) |
| C12 | reconciliation | warn | no | sampled split-adjusted closes differ from Tiingo by > 0.5%, a ticker both sources have shares no date with Tiingo, or a reviewed reclassification (D40) matches no Tiingo distribution; sample = 5 dates per ticker drawn by hash(run_id) + the latest common date, defined once in `transform/reconcile_sample.sql` (D38) |
| C13 | baseline | warn | no | first_date moved later, row_count below baseline, or a baselined series gone, vs the opening-balance audit; also fires while no baseline is recorded. Metrics defined once in `transform/baseline_metrics.sql` (C04's row rule), shared with the audit (D39) |
| C14 | accuracy | error | yes | same `(series_id, obs_date, vintage)` with different values across raw snapshots (silent source correction). Prices: current vintage per series only (D23); FRED: every key |
| expected_states | acknowledgment | warn | no | a row of `config/expected_states.csv` that matches no failing row of its check on its `ack_key` columns (D75): a stale acknowledgment. Not a SQL file: health.acknowledge() computes it after the checks run (D46) |

### Gate vs monitor
- **Gate** checks assert facts about the staged data. An error-severity gate failure blocks the publish.
- **Monitor** checks assert facts about the world (staleness, source lateness, second-source agreement, baseline drift). They are recorded in `check_results` and drive the status color, but never block a publish: blocking cannot fix them, and C01 at gate would lock the pipeline out after two missed nights.

### Status roll-up
- **Green**: all checks pass and the last successful run is under 26h old.
- **Yellow**: warnings only. A warn check whose every failing row is acknowledged in `config/expected_states.csv` reports `acknowledged` and doesn't count; its rows stay listed with their notes (D46).
- **Red**: any error, a blocked publish, or staleness.
- One overall status, no per-source roll-up (D42). Which source a failure belongs to is read from its rows, grouped on the health page.

### Three surfaces, one source of truth (`health/` tables)
1. **Health page**: the first page in the app. A status bar on every page.
2. **`uv run pharos health`**: a colored terminal table. Every Claude Code session starts here.
3. **`health/latest.md`**: a plain-text summary rewritten every run and every `pharos health` (D34).

### Health page contents
- Overall traffic light: last successful run, latest obs date, row count, pass/warn/fail counts. Failing rows are grouped by source (the `yf:`/`fred:` series prefix, or the row's source column) under each failing check (D42).
- Coverage heatmap: rows = series, columns = months, color = % of expected observations present, by C04's definition (`transform/expected_dates.sql`, D44). A quarterly date fills its quarter's three months. Starts at `backfill_start`, with a full-history toggle.
- Row count by run per source.
- Run strip: last 30 runs as colored squares.
- Failing checks table, drillable to the failing rows.
- Latest run blocked or failed: that run's own recorded failing checks (and crash error), drillable, above everything that describes what is served (D45).
- Spot-check panel: 5 served observations per source, chosen by a hash seeded with the published run_id, so a run's sample is fixed and re-performable (D44); each linked to its source page (audit sampling).

### Opening-balance audit
Before trusting the first backfill:
- Reconcile every watchlist ticker on ≥5 random dates against the second source.
- Record per-series baseline metrics.
- From then on, C13 watches for drift from that baseline.
- `uv run pharos audit` runs it against `serving/CURRENT` (D39, D41): every common date is compared and written. An isolated day over 0.5% is reported as a print disagreement; consecutive days over, a day at either end of the range, or an active ticker with fewer than 5 dates compared block, and then no baseline is recorded and it exits 1.

### Fault injection (control effectiveness)
`tests/test_design_faults.py` drives each fault end to end through `refresh()` (load -> stage -> checks -> publish -> manifest) and `pharos health`, on a synthetic data root (never real data), injecting it where it would really happen. Each fault must produce the expected color and appear on the health page (step 8). Fault 2 has two halves: a duplicated bar in raw is absorbed by staging's dedup (C07 passes, publishes), and a duplicate that survives staging is injected into the staged table, where C07 guards the dedup. `tests/test_faults.py` keeps the per-check tests on staged tables:
1. Delete one month of one ticker → C04 red, publish blocked, the C04 rows on the health page under the blocked run (D45). The heatmap stays whole: it shows serving/, which the hole never reached.
2. Duplicate one day → C07 red, publish blocked, serving unchanged.
3. Backdate the last successful run by 2 days → C01 red.
4. Multiply one close by 10 → C10 warn.
5. Change one past close in a later raw snapshot, same vintage → C14 red, publish blocked, serving unchanged.

## 8. Dashboard
- **Pages:** Home (watchlist chart + status bar; never empty on open), Health, Charts.
- **Charts page:**
  - A stack of up to 4 panels sharing one date axis (D48). Each panel overlays any series and has its own rebase and log toggles. Up to 8 series across panels, one color slot each; a series keeps its slot in every panel and while shown.
  - Each value is drawn at the date it became public, at its latest vintage: a close at its trading day, a FRED value at its first vintage, or at period end + `expected_lag_days`, flagged estimated, where ALFRED records no release (D47). Macro draws as steps.
  - Date range with range-selector buttons (1Y/5Y/YTD/Max).
  - Rebase to 100 at range start by default: each series' value in effect at the start (D47). No dual axes: series that need their own scale go in their own panel (D48).
  - Log toggle.
  - Chart state lives in the URL (`st.query_params`), so a bookmark reopens the same chart.
- **Caching:** `st.cache_resource` for the DuckDB connection; `st.cache_data` keyed on `last_published_at` and the latest run_id (D45). The status bar and health page evaluation are keyed on (`last_published_at`, wall clock to the minute), and the app evaluates with `record=False` (D43).
- **Desktop icon:**
  - Shortcut runs `pwsh -NoProfile -WindowStyle Hidden -File scripts\open-dashboard.ps1`, with a custom .ico.
  - The script checks whether `localhost:8517` responds, starts Streamlit headless if not, waits, then opens Edge in app mode on it. Port 8517, not Streamlit's default 8501, so another project's app on 8501 is never mistaken for Pharos.
  - No PyInstaller, Electron, or pywebview.

## 9. Known traps for fundamentals (M3+), as rules
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

## 10. Hosting path (milestone H, D104)
- The refresh stays on JAMESTOWN (the nightly task). Publish copies the new version and `health/` to Cloudflare R2, `CURRENT` last. Raw stays local. A refresh on GitHub Actions would need the whole raw history in R2 (staging reads every landing, D18/D84; C05 reconciles raw), so it is a separate, floating step.
- The Streamlit Community Cloud app reads R2 through DuckDB.
- Constraints: about 1 GB memory, sleeps after 12h idle, one private app (the repo is public).
- The desktop icon gets repointed to the hosted URL; the phone uses Add to Home Screen.
- Failure alerts: as today, the dashboard and the status email (#96); GitHub Actions' email applies only if the refresh moves there.

## 11. Decision log
In `docs/decisions.md` (D129).

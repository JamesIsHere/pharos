# Plan: Pharos

## Status
<!-- Claude Code updates this block at the end of every session. Keep it to these lines. -->
- **Milestone:** M1, steps 1-3 and 3b of 10 built (scaffold, loaders, staging, incremental Yahoo loader); step 4 next
- **Health:** n/a (`pharos health` arrives in step 5). `pharos stage` on run 20261007T140923Z: 96,160 observations (11,050 FRED incl. 743 withdrawn-as-NULL, 85,110 Yahoo), 21 catalog rows, 0 duplicate keys; `catalog.duckdb` has 6 views
- **Last session:** 2026-10-07: D19 (units per observation row), D20 (raw Yahoo rows carry pull window; price vintage = latest full pull), D21 (incremental Yahoo pulls with overlap basis check, pull_reason on every row), transform SQL x3, `stage.py` + `pharos stage`, `catalog.py`; 59 tests pass. Live dry run of D21 (no raw written): all 17 stored tickers classify incremental, overlap gap 0.0 on 7 dates; ATVI bootstraps every night (empty response, cheap)
- **Next action:** step 4, checks C01-C14 in `checks/*.sql`, runner, write-audit-publish gate with atomic swap, run manifest. First run the real `pharos load` once to land the first incremental snapshot (exercises D21 on live data and gives step 4 two overlapping runs)
- **Blockers / open questions:** #6 (ATVI blocks every publish via C11) and #17 (a real correction blocks publish forever) block step 4's gate. Full list in Open issues below; cite only IDs that appear there.

## Open issues
<!-- Every concern or finding not fixed in the turn it comes up goes here before that turn ends. IDs never change or get reused. Fixing an issue deletes its row in the same commit, and the commit message cites the ID; git history is the record. -->

| ID  | Issue                                                                                          | Effect if ignored                                                      | Resolves in        |
|-----|------------------------------------------------------------------------------------------------|------------------------------------------------------------------------|--------------------|
| 4   | C10 skips known split dates                                                                    | Hides an unadjusted split, the one failure a split-date jump signals   | step 4 (C10)       |
| 6   | No "ended" or "source-missing" state; ATVI has a catalog row and zero observations             | C11 blocks every publish; acceptance #3 fails                          | step 4 (C11)       |
| 7   | Windows file locks on the serving/ swap (catalog.duckdb half done: fails loudly)               | Swap fails while DBeaver or the app holds the files                    | step 4 (publish)   |
| 9   | Nightly refresh timing vs the Wednesday 06:00 workshop mirror                                  | Backup captures a half-written staging/                                | step 10            |
| 10  | Fault tests run on a copy of real data                                                         | Not repeatable; CI on the public repo can't run them                   | step 6             |
| 11  | C02 is "warn->error" in design.md, but a check file declares one severity                      | The documents contradict each other                                    | step 4 (C02)       |
| 12  | Split-adjusted history uses trade date as available_date                                       | Look-ahead: the adjusted value wasn't knowable then; undocumented      | M1 (decision log)  |
| 14  | Yahoo prices carry float32 precision (~7 significant digits)                                   | Cross-source comparison without a tolerance fails falsely              | step 7 (C12)       |
| 15  | Yahoo stock_splits carries non-splits (GOOGL 2014-04-03 1.998, O 2021-11-15 1.032)             | Split re-pull rule and C10 exclusion fire on non-splits                | step 4 (C10)       |
| 16  | COST $15 special dividend on 2023-12-27                                                        | Looks like a price jump to C10                                         | step 4 (C10)       |
| 17  | Under dedup rule A a real source correction blocks publish forever (old raw file stays)        | One Yahoo correction freezes serving/; needs a deliberate accept path  | step 4 (C14)       |
| 18  | expected_lag_days is NULL in series_catalog                                                    | C02 has nothing to measure freshness against                           | step 4 (C02)       |
| 19  | GDP freshness: drive expected release dates from FRED's release-dates endpoint                 | A hard-coded lag misfires when releases slip                           | step 4 (C02)       |
| 20  | Trading calendar: confirm exchange_calendars or an equivalent                                  | C04 and C02 can't know which trading days to expect                    | step 4 (C04)       |
| 21  | Second price source not chosen (Tiingo / Alpha Vantage / Stooq)                                | C12 and the opening-balance audit can't run                            | step 7             |
| 22  | Watchlist cik column blank                                                                     | Tickers can't map to SEC filers                                        | M2                 |
| 23  | SEC available_date = next trading day after filing                                             | Same-day joins leak filings into that day's close                      | M2                 |

---

## M1: Trusted pipeline + dashboard skeleton (prices + macro, local)

**Goal.** A nightly-refreshing local dashboard over clean sources, with a health system you'd trust before looking at a single chart. It opens from a desktop icon.

### Scope
- **Prices (yfinance):** daily split-adjusted close, plus split and dividend events, for the 18-name watchlist. Backfill from 2005-01-01 (configurable).
- **Macro (FRED/ALFRED):** `GDP`, `GDPC1`, `CPIAUCSL`, all vintages.
- **Second price source:** used for reconciliation only (C12) and the opening-balance audit.
- **Health system:** checks C01–C14, health CLI, `latest.md`, health page, fault-injection tests.
- **Dashboard:** Home, Health, and Charts pages. Desktop launcher. Nightly scheduled task.

### Build order
- [x] 1. Scaffold: uv project, layout, `PHAROS_DATA_ROOT`, `.env.example`, `config/watchlist.csv`
- [x] 2. Loaders: yfinance (wrapped, schema-validated, explicit `auto_adjust`), FRED with vintages
- [x] 3. Transform to staging (DuckDB SQL); series_catalog + observations; regenerate `catalog.duckdb` views
- [x] 3b. Incremental Yahoo loader: watermark + overlap window, full re-pull on re-basing (step-2 scope found missing in step 3)
- [ ] 4. Checks C01–C14 in `checks/*.sql`; runner; write-audit-publish gate with atomic swap; run manifest
- [ ] 5. `pharos health` CLI + `health/latest.md`
- [ ] 6. Fault-injection tests (5 faults, design.md §7)
- [ ] 7. Opening-balance audit against the second source; record baseline
- [ ] 8. Health page (traffic lights, coverage heatmap, row counts by run, run strip, failing checks, spot-check panel)
- [ ] 9. Home + Charts pages (overlay, date range buttons, rebase-to-100 default, log toggle, URL state)
- [ ] 10. Desktop launcher (`open-dashboard.ps1`, shortcut, lighthouse .ico; there is no lighthouse emoji, so draw one) + `register-tasks.ps1` (nightly refresh, run-after-missed-start, BurntToast on failure)

### Acceptance criteria
1. From an empty data root, `uv run pharos refresh` backfills every watchlist series and both macro groups.
2. An immediate rerun adds zero rows to `serving/` and stays green (idempotent; `raw/` gains only that run's snapshot file).
3. `pharos health` is green, apart from documented expected states. ATVI shows as *ended*, not stale. If Yahoo no longer has ATVI history, that's reported explicitly as source-missing (warn). That's the survivorship finding, so it must be visible, not silent.
4. Opening-balance audit: ≥5 random dates per ticker reconcile to the second source within 0.5% on split-adjusted close. Baseline recorded.
5. All 5 fault-injection tests pass. Each fault shows the right color and appears on the health page or heatmap.
6. Health page shows every element listed in design.md §7.
7. Charts page: overlay ≥3 series of mixed frequency (daily price + quarterly GDP + monthly CPI), change the dates, rebase, toggle log. Reloading the URL restores the chart.
8. Home opens to the watchlist chart with the status bar. It's never empty.
9. Desktop icon: cold start to data on screen < 10s; warm < 2s; opens in an Edge app window.
10. A nightly task is registered. A deliberately failed run raises a toast and leaves serving data unchanged.

**Built** when 1–10 pass. **Done** after 7 consecutive green nightly runs. M2 can start once M1 is built.

### Out of scope for M1
SEC/XBRL, CIK mapping (beyond filling the watchlist's CIK column), the derived-series registry, the cross-section page, hosting.

### Watchlist (seed for `config/watchlist.csv`)
Each name is here to trip a known trap. That's why it's the test set.

| ticker | yahoo_symbol | trap it tests | active_from | active_to |
|---|---|---|---|---|
| AAPL | AAPL | 4:1 split 2020; September FY | | |
| NVDA | NVDA | 4:1 split 2021, 10:1 split 2024 | | |
| WMT | WMT | 3:1 split 2024; January FY end | | |
| GOOGL | GOOGL | 20:1 split 2022; dual share classes | | |
| BRK.B | BRK-B | punctuation differs by source; class B shares | | |
| JPM | JPM | bank statement structure | | |
| MSFT | MSFT | June FY end | | |
| MCD | MCD | negative book equity | | |
| AMZN | AMZN | 20:1 split 2022 | | |
| TSLA | TSLA | 5:1 split 2020, 3:1 split 2022; extreme volatility tests jump-detector false positives | | |
| META | META | ticker change FB→META 2022 | | |
| COST | COST | 52/53-week FY ending near Aug 31 | | |
| HD | HD | 52/53-week FY ending near Jan 31 | | |
| O | O | REIT statement structure; one-letter ticker | | |
| XOM | XOM | energy / commodity cycle, macro overlay | | |
| CAT | CAT | industrial cyclical, macro overlay | | |
| CART | CART | IPO Sept 2023: series starts mid-history | 2023-09-19 | |
| ATVI | ATVI | acquired by Microsoft Oct 2023: dead ticker, survivorship test | | 2023-10 (verify last trading day) |

---

## Later milestones (one line each until current)
- **M2: Fundamentals for the watchlist (balance sheet only).** companyfacts → raw facts with accession and filed date. Equity, preferred, both share counts, goodwill and intangibles. Book value per share and P/B as the first derived series via ASOF on available_date. XBRL checks: balance identity, share-count divergence, scale errors. Financial Statement Data Sets for multi-class names. OTC decision.
- **M3: Universe + cross-section page.** Domestic 10-K filers. SIC → Fama-French industries. P/B distribution, industry medians, scatter. Coverage heatmap scales to the universe.
- **M4: Flow statements + quarterly panel.** YTD differencing, Q4 derivation, 52/53-week handling, fiscal→calendar alignment, concept map with fallback chains, ratio library.
- **M5: BEA national accounts breakdown.** The GDP tracker folded in: small-multiples page with drill-down.
- **Hosting (floating, after M1 is done).** Actions cron → R2 → Streamlit Community Cloud; repoint the desktop icon; phone home-screen shortcut.

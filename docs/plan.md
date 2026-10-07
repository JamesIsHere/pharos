# Plan: Pharos

## Status
<!-- Claude Code updates this block at the end of every session. Keep it to these lines. -->
- **Milestone:** M1, steps 1-6 and 3b of 10 built. Step 7 code complete, live run pending: Tiingo loader + wiring, C12, C13, C18-C20, `pharos audit` (all or nothing, D39). The step closes when a live audit records the baseline (acceptance #4)
- **Health:** YELLOW at 2026-10-07 18:30 UTC (`pharos health`): C12 (GOOGL 2006-03-15 off 49.95%, the 2014 share-class distribution, #35), C13 (no baseline recorded), C15 ATVI and C16 CPI Oct 2025 (documented expected states, #31). C18-C20 pass. serving/CURRENT = 20261007T182814Z
- **Last session:** 2026-10-07 (third session): audit baseline rule decided (all or nothing), `pharos audit`, C13, shared transform/baseline_metrics.sql, reconcile_sample relabel (a drawn latest date stays random). D39. 194 tests pass
- **Next action:** decide how the reconciliation treats GOOGL's 2014-04-03 distribution (#35), then rerun `pharos audit` to record the baseline. Live audit of 20261007T182814Z: 16 of 17 tickers within 0.04% (6 comparisons each, 5 random), GOOGL refused, no baseline recorded
- **Blockers / open questions:** OPEN: #35, how the reconciliation treats the 2014 GOOGL distribution. #25 must be settled before the D23 re-pull is built. Full list in Open issues below; cite only IDs that appear there.

## Open issues
<!-- Every concern or finding not fixed in the turn it comes up goes here before that turn ends. IDs never change or get reused. Fixing an issue deletes its row in the same commit, and the commit message cites the ID; git history is the record. -->

| ID  | Issue                                                                                          | Effect if ignored                                                      | Resolves in        |
|-----|------------------------------------------------------------------------------------------------|------------------------------------------------------------------------|--------------------|
| 9   | Nightly refresh timing vs the Wednesday 06:00 workshop mirror                                  | Backup captures a half-written staging/                                | step 10            |
| 12  | Split-adjusted history uses trade date as available_date                                       | Look-ahead: the adjusted value wasn't knowable then; undocumented      | M1 (decision log)  |
| 17  | Under dedup rule A a real source correction blocks publish forever (old raw file stays)        | One Yahoo correction freezes serving/; needs a deliberate accept path  | step 4 (D23)       |
| 22  | Watchlist cik column blank                                                                     | Tickers can't map to SEC filers                                        | M2                 |
| 23  | SEC available_date = next trading day after filing                                             | Same-day joins leak filings into that day's close                      | M2                 |
| 24  | Accept path for a FRED same-key conflict (C14 on every FRED key)                               | A changed ALFRED vintage blocks publish forever                        | only if C14 fires  |
| 25  | Price vintage is a DATE: two full pulls of one ticker on one day share a vintage               | Same-day accept re-pull leaves the conflict in place                   | step 4 (D23 build) |
| 28  | C05 checks raw files by row count only; no content hash is recorded at write time              | A raw file rewritten with the same row count passes C05                | M1 (runs.py)       |
| 31  | Documented expected states (C15 ATVI, C16 CPI Oct 2025) hold health at yellow permanently        | A yellow that never clears trains the eye to ignore yellow; acceptance #3 says green apart from them | step 8 (roll-up)   |
| 32  | Status roll-up is overall only; design.md section 7 rolls up per source, then overall           | Health page traffic light per source has no data behind it             | step 8             |
| 34  | A series added after the audit has no baseline row; C13 never compares it                        | A watchlist addition can lose history with C13 green                   | before any watchlist change |
| 35  | Tiingo books GOOGL's 2014-04-03 class C distribution as a $567.97 cash dividend, Yahoo as a 1.998 split; every GOOGL date before it differs ~50% | The audit refuses the baseline whenever a pre-2014 GOOGL date is drawn; C12 warns the same way | step 7 (before the baseline) |

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
- [x] 4. Checks C01–C16 in `checks/*.sql`; runner; write-audit-publish gate with atomic swap; run manifest
- [x] 5. `pharos health` CLI + `health/latest.md`
- [x] 6. Fault-injection tests (5 faults, design.md §7)
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

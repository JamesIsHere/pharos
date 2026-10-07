# Plan: Pharos

## Status
<!-- Claude Code updates this block at the end of every session. Keep it to these lines. -->
- **Milestone:** M1, steps 1-2 of 10 built (scaffold, loaders)
- **Health:** n/a (`pharos health` arrives in step 5); first raw snapshot run 20261007T140923Z: yf/prices 85,110, fred/observations 11,050, fred/series 19 rows, downloaded = written for all three
- **Last session:** 2026-10-07: FRED loader (all vintages + metadata history), `pharos load`, first real snapshot; 41 tests pass
- **Next action:** step 3, transform to staging. Inputs and findings it must honor: (a) units change across vintages, so join each FRED vintage to the `raw/fred/series` row whose realtime window contains its realtime_start (GDPC1 has 8 units regimes, 1987 dollars -> chained 2017; CPIAUCSL 2, 1967=100 -> 1982-1984=100 at the 1988-02-26 vintage); (b) FRED `value` is raw text, "." = missing, parse in staging; (c) a run is complete only when all its raw files exist (loaders write separately); (d) ATVI absent from prices, recorded only by absence, so step 4 compares watchlist vs tickers present; (e) Yahoo volume is split-adjusted and prices carry float32 precision (~7 significant digits), so cross-source comparisons need a tolerance; (f) for step 4 and review concern 4: Yahoo's stock_splits column also carries non-split corporate actions (GOOGL 2014-04-03 = 1.998 Class C issuance; O 2021-11-15 = 1.032, likely the Orion spin-off, unverified), so "new split -> full re-pull" and the C10 split exclusion must not trust the column blindly; COST $15 special dividend 2023-12-27 is a jump-detector outlier. One-off review of the snapshot: `scratch/data_review.py` -> `scratch/data_review.html` (gitignored; 15 of 16 checks pass, ATVI the expected fail; its trading-day check uses the union calendar, real calendar comes in step 4)
- **Blockers / open questions:** watchlist `cik` left blank until M2 (needs SEC download; plan said M1). Review concerns queued: 4 (C10 split exclusion), 6 (ended/source-missing states), 7 (Windows swap locks), 9 (refresh vs mirror timing), 10 (synthetic fault-test data), 11 (C02 dual severity), 12 (split-adjusted look-ahead), SEC available_date = next trading day after filing (M2)

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
- [ ] 3. Transform to staging (DuckDB SQL); series_catalog + observations; regenerate `catalog.duckdb` views
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

### Open items (resolve during M1, log in design.md)
- Which second price source (Tiingo / Alpha Vantage / Stooq): pick by reliability and free-tier limits.
- GDP freshness rule: drive expected release dates from FRED's release-dates endpoint.
- Confirm the `exchange_calendars` library (or an equivalent) for trading-day expectations.

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

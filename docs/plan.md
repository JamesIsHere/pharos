# Plan: Pharos

## Status
<!-- Claude Code updates this block at the end of every session. Keep it to these lines. -->
- **Milestone:** M1 built: steps 1-10 and 3b. Step 10 closed: desktop icon + Edge app window (cold ~5.7s, warm 0.88s; acceptance #9), nightly task at 01:30 with the mirror guard (D49), failures shown on the dashboard, not toasted (D50; acceptance #10). M1 DONE: 7 consecutive green nightly runs, 20261008T053003Z through 20261014T053008Z (run_manifest: published, 0 errors, 0 warnings each)
- **Health:** GREEN at 2026-11-04 (`pharos health`): C15 ATVI and C16 CPI Oct 2025 acknowledged (D46). serving/CURRENT = 20261104T053016Z
- **Last session:** 2026-10-07/08 (fifth session): step 10 built. Launcher, icon, nightly task, mirror guard (D49, #9); toast dropped because toasts are off machine-wide (D50, #42); Home test pinned to its clock (it broke when the date rolled). 228 tests pass
- **Next action:** M2 scope closed (D52-D56); M3 lands all of companyfacts (D57). Next: write the M2 section (build order, acceptance criteria) for James to red-pen
- **Blockers / open questions:** none. #25 must be settled before the D23 re-pull is built. Full list in Open issues below; cite only IDs that appear there.

## Open issues
<!-- Every concern or finding not fixed in the turn it comes up goes here before that turn ends. IDs never change or get reused. Fixing an issue deletes its row in the same commit, and the commit message cites the ID; git history is the record. -->

| ID  | Issue                                                                                          | Effect if ignored                                                      | Resolves in        |
|-----|------------------------------------------------------------------------------------------------|------------------------------------------------------------------------|--------------------|
| 12  | Split-adjusted history uses trade date as available_date                                       | Look-ahead: the adjusted value wasn't knowable then; undocumented      | M1 (decision log)  |
| 17  | Under dedup rule A a real source correction blocks publish forever (old raw file stays)        | One Yahoo correction freezes serving/; needs a deliberate accept path  | step 4 (D23)       |
| 22  | Watchlist cik column blank                                                                     | Tickers can't map to SEC filers                                        | M2                 |
| 23  | SEC available_date = next trading day after filing                                             | Same-day joins leak filings into that day's close                      | M2                 |
| 24  | Accept path for a FRED same-key conflict (C14 on every FRED key)                               | A changed ALFRED vintage blocks publish forever                        | only if C14 fires  |
| 25  | Price vintage is a DATE: two full pulls of one ticker on one day share a vintage               | Same-day accept re-pull leaves the conflict in place                   | step 4 (D23 build) |
| 28  | C05 checks raw files by row count only; no content hash is recorded at write time              | A raw file rewritten with the same row count passes C05                | M1 (runs.py)       |
| 34  | A series added after the audit has no baseline row; C13 never compares it. Any re-baseline path built for this must also decide a size cap on isolated print disagreements: D41's shape rule passes one of any size, and C10 only catches moves over 40% (folded in from #37) | A watchlist addition can lose history with C13 green; a re-run audit could pass a large isolated bad print | before any watchlist change |
| 41  | Exploration tools beyond overlay (James, 2026-10-07: 'quickly mix and match ... explore trends'): transforms (YoY %, rolling return, drawdown), ratio/spread of two series, rolling correlation, lead/lag shift, resampling to a common frequency. None is in M1 scope; each must be DuckDB SQL computed on obs_date and drawn at plot_date (D47) | Built ad hoc, a transform could compute on the plot timeline and smear revisions, or leak a value before its release | M1.5, after M2 (D51)                   |
| 43  | Handoff from chomper (variance-bridge/handoff-to-pharos-2026-11-04.md): James wants a survivorship-free, point-in-time universe of US public companies over time (entries, exits, exit reason), all of EDGAR at full granularity, and a waterfall from every US-traded company down to the analysed set. Conflicts with non-goals in design.md §1 (foreign filers, pre-XBRL, paid vendors) and with the watchlist-first order of M2/M3 | Fundamentals get built for 18 names and the expectation model is rebuilt at universe scale; the waterfall stays unanswerable | M2 (D51)            |
| 44  | SIC in EDGAR submissions is the current code only (unverified, from prior). A SPAC that merged keeps its CIK and usually takes the target's SIC, so its blank-check years read as an operating company; any SIC-based universe step misclassifies history | The waterfall's shell/fund step undercounts shells in past years | M2 build (shell flag from filings, e.g. the 10-K cover shell-company box) |
| 46  | Listed-company reconciliation gap: chomper's count of company_tickers_exchange.json had 6,069 exchange-listed companies (pulled 2026-11-04, not re-run here); the spike finds 5,424 annual-report filers active in 2025-26 with an exchange ticker in submissions. About 645 exchange-listed CIKs file no 10-K/20-F/40-F, probably ETFs and funds filing N-CSR (unverified) | The waterfall's top and the exchange list disagree with no named step for the difference | M2 build (reconcile and name the step) |
| 47  | companyfacts.zip (2026-11-04) is 126,119,741 rows (one per reported value), growing about 8M a year. Raw is a write-once full snapshot per run (D18); landing the full file nightly would add 126M rows a night (about 46B a year) | Raw storage and the C05 reconcile grow without bound | M3 design (land only new accessions, or snapshot on a slower cadence; decision-log entry) |

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
- [x] 7. Opening-balance audit against the second source; record baseline
- [x] 8. Health page (traffic lights, coverage heatmap, row counts by run, run strip, failing checks, spot-check panel)
- [x] 9. Home + Charts pages (overlay, date range buttons, rebase-to-100 default, log toggle, URL state)
- [x] 10. Desktop launcher (`open-dashboard.ps1`, shortcut, lighthouse .ico; there is no lighthouse emoji, so draw one) + `register-tasks.ps1` (nightly refresh, run-after-missed-start; failures show on the dashboard, no toast, D50)

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
10. A nightly task is registered. A deliberately failed run shows on the dashboard and leaves serving data unchanged (D50; was 'raises a toast').

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

## M2: Universe (DRAFT for James's red-pen, 2026-11-04)

**Goal.** A survivorship-free, year-by-year list of every company that filed an annual report with the SEC: who joined, who left, who was there from the start. A waterfall page narrows that population, one named step at a time, to the set being analysed. It refreshes nightly under the same health system as M1, and it becomes the expected set that M3's fundamentals are checked against.

### Scope (decided: D52-D56)
- **Top of the funnel (D54):** every CIK with an original annual report (form codes from `config/annual_forms.csv`, taken from the codes present in the full-index), 1993-11-29 to as-of. Complete from filing year 1997 (domestic) and 2002 (foreign); earlier years stored and flagged phase-in.
- **Membership (D55):** in for every calendar year from first to last annual report; empty years in between count, flagged; an exit is confirmed once a full calendar year passes with nothing filed.
- **Foreign filers (D53):** in, foreign status per filer-year from that year's form.
- **Events:** listing and exit forms from the full-index, by form type only (no filing text): 8-A12B / 8-A12G (registration), 25 / 25-NSE (delisting), 15-12B / 15-12G / 15-15D (deregistration). Exit reason = the event forms filed around the last annual report, else `unknown`.
- **Sources:** EDGAR full-index (`master.gz` per quarter), bulk `submissions.zip` (name, SIC, state, former names, current tickers and exchanges), `company_tickers_exchange.json` (current ticker to CIK).

### Build order
- [ ] 1. Loaders: full-index (every quarter once; each run re-lands only the current and previous quarter), submissions bulk, tickers file. SEC User-Agent, at most 10 requests/s, one retry on 503, schema validated, raw write-once. submissions.zip nightly: full company details every run, filing history only past the watermark plus overlap (D58)
- [ ] 2. Staging SQL: `filings`, `annual_reports`, `filer` (one row per CIK), `firm_year` (D55, with the gap flag), `events`, exit reason. SIC is labelled current-as-of-pull (#44)
- [ ] 3. Checks C21-C28 in `checks/*.sql`, each with a fault-injection test:
  - C21 completeness (gate): every quarter from 1993Q1 to the current one is in raw
  - C22 control total (gate): staged filings per quarter = lines in that quarter's raw file
  - C23 uniqueness (gate): (cik, year) unique in `firm_year`; accession unique in `filings`
  - C24 integrity (gate): every annual-report CIK has a submissions row (spike: 0 missing)
  - C25 freshness (monitor): latest full-index filing date older than 3 business days
  - C26 form-code drift (monitor, warn): an index form code that looks like an annual report (10-K, KSB, 20-F, 40-F patterns) but is not in `config/annual_forms.csv`. The 10KSB lesson (#45) as a standing control
  - C27 history drift (monitor, warn): a closed year's population count differs from the recorded baseline
  - C28 waterfall foots (gate): each step's count = the previous step minus what it drops, every year
- [ ] 4. Serving: universe tables in `serving/`, views in `catalog.duckdb`, plus population counts per year as derived series in `series_catalog` (all filers, domestic, foreign, joined, left), so the Charts page can overlay the population on GDP
- [ ] 5. Fill the watchlist `cik` column from the tickers file; ATVI mapped by hand (#22). Reconcile the exchange list to the universe and name the difference (#46)
- [ ] 6. Universe page: joined / left / stayed by year, the waterfall for a chosen year with steps from `config/waterfall_steps.csv` (seeded with the measured groups: asset-backed 6189, blank checks 6770, no SIC, foreign, no exchange ticker), each step drillable to its company list. Status bar on the page. No step may use an attribute that disappears when a company dies. Tickers and exchanges do: SEC lists them for live companies only, so applying today's list to a past year drops every company that has since died, which is survivorship bias, the thing M2 removes. The ticker/exchange step reads the nightly details snapshot for the chosen year (D58); for a year before the first snapshot it shows "no data for this year" and drops nothing. Foreign status comes from that year's form (D53). SIC is not a survivorship leak, since a dead company keeps its last code, so the SIC steps run on every year, labelled current-as-of-pull (#44)
- [ ] 7. Re-perform the spike: rebuild the universe through the pipeline and reconcile it to the spike's numbers for filing dates through 2026-11-03

### Acceptance criteria
1. From an empty data root, `uv run pharos refresh` builds the universe and stays green; an immediate rerun adds zero rows to `serving/`.
2. Re-performance: for filing dates through 2026-11-03 the pipeline reproduces the spike exactly: 48,209 CIKs, 319,098 firm-years, 10,568 gap years, and the per-year counts (e.g. 12,233 in 1997, 13,557 in 2002, 7,511 in 2026).
3. Every check C21-C28 has a fault-injection test that shows it firing with the right color.
4. Every confirmed exit has a reason or `unknown`, and the share that is `unknown` is shown on the Universe page.
5. The waterfall foots for every year (C28), and each step names what it drops and how many.
6. The Charts page overlays the population count on GDP.
7. The watchlist `cik` column is filled for all 18 names (#22), and the exchange list's difference from the universe is a named step or a documented expected state (#46).

**Built** when 1-7 pass. **Done** after 7 consecutive green nightly runs with the universe in them.

### Out of scope for M2
Financials of any kind (M3); linking several CIKs into one company (predecessors, re-registrations); reading filing text; paid sources (CRSP); membership on a specific date (Form 25 / 15 dates are stored, not used for membership).

---

## Later milestones (one line each until current)
- **M1.5: Exploration tools (#41).** After M2 (D51).
- **M3: Fundamentals, formerly M2.** companyfacts in full → raw facts with accession and filed date, every filer and concept (D57); curated in staging, balance sheet first. Equity, preferred, both share counts, goodwill and intangibles. Book value per share and P/B as the first derived series via ASOF on available_date. XBRL checks: balance identity, share-count divergence, scale errors. Financial Statement Data Sets for multi-class names. OTC decision.
- **M4: Cross-section page (formerly M3, universe moved to M2).** Domestic 10-K filers. SIC → Fama-French industries. P/B distribution, industry medians, scatter. Coverage heatmap scales to the universe.
- **M5: Flow statements + quarterly panel.** YTD differencing, Q4 derivation, 52/53-week handling, fiscal→calendar alignment, concept map with fallback chains, ratio library.
- **M6: BEA national accounts breakdown.** The GDP tracker folded in: small-multiples page with drill-down.
- **Hosting (floating, after M1 is done).** Actions cron → R2 → Streamlit Community Cloud; repoint the desktop icon; phone home-screen shortcut.

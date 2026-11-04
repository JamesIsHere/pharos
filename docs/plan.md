# Plan: Pharos

## Status
<!-- Claude Code updates this block at the end of every session. Keep it to these lines. -->
- **Milestone:** M1 DONE: 7 consecutive green nightly runs, 20261008T053003Z through 20261014T053008Z. M2 (Universe) drafted and reviewed, awaiting James's red-pen. Nightly task now 07:30 UTC (D63)
- **Health:** GREEN at 2026-11-04 (`pharos health`): C15 ATVI and C16 CPI Oct 2025 acknowledged (D46). serving/CURRENT = 20261104T185655Z (first version with inputs/, D76)
- **Last session:** 2026-11-04 (sixth, resumed after a crash): chomper handoff absorbed (D51-D57); M2 drafted (D58) and reviewed twice (D59-D66); nightly moved after SEC's submissions rebuild (D63); full-project audit logged #56-#93; M1 fixes #56-#70 done (D71-D77)
- **Next action:** #71 (test gaps: no monthly fixture, C16 on FRED only, C20/C17 untested branches, nothing ties discovered check ids to design.md section 7). Then #72 (M1), then the M2 draft rows #73-#91, the docs #92-#93, then the M2 red-pen. #94 waits for hosting
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
| 44  | SIC in EDGAR submissions is the current code only (unverified, from prior). A SPAC that merged keeps its CIK and usually takes the target's SIC, so its blank-check years read as an operating company; any SIC-based universe step misclassifies history | The waterfall's shell/fund step undercounts shells in past years | M2 build (shell flag from filings, e.g. the 10-K cover shell-company box) |
| 46  | Listed-company reconciliation gap: chomper's count of company_tickers_exchange.json had 6,069 exchange-listed companies (pulled 2026-11-04, not re-run here); the spike finds 5,424 annual-report filers active in 2025-26 with an exchange ticker in submissions. About 645 exchange-listed CIKs file no 10-K/20-F/40-F, probably ETFs and funds filing N-CSR (unverified) | The waterfall's top and the exchange list disagree with no named step for the difference | M2 build (reconcile and name the step) |
| 47  | companyfacts.zip (2026-11-04) is 126,119,741 rows (one per reported value), growing about 8M a year. Raw is a write-once full snapshot per run (D18); landing the full file nightly would add 126M rows a night (about 46B a year) | Raw storage and the C05 reconcile grow without bound | M3 design (land only new accessions, or snapshot on a slower cadence; decision-log entry) |
| 54  | Release calendar (James, 2026-11-04): a forward calendar of when each series is due, macro releases (FRED/ALFRED, BEA) and, going forward, company earnings. Not in any milestone yet. Related facts already in Pharos: C02/C17 compute when a series is due from period end + `expected_lag_days`; M2's 10-K/10-Q filing history gives each filer's past filing dates | Releases are known only after they land; no view of what is due next | Unscheduled: James to place it (M1.5 sits beside #41) |
| 55  | Parent-subsidiary links (James, 2026-11-04): the full-index cannot say which CIK on a shared annual report is the parent (the accession's submitter prefix is one of the group's CIKs for 1,913 of 5,774 shared reports; the rest went in through filing agents). Consolidation, equity-method and cost accounting all need real links and ownership shares, which come from somewhere else (XBRL entity tags, filing headers, Exhibit 21; which ones is unverified). M2's co-registrant step (D64) keeps every group intact for this | Fundamentals summed across CIKs double-count consolidated subsidiaries; the waterfall's choice of which CIK stands for a group stays a rule, not a fact | Before any cross-CIK aggregate (M3/M4) |
| 71  | Audit 2026-11-04, M1 code, LOW: test gaps: no monthly fixture (every test series is quarterly, yet CPIAUCSL is monthly); C16 tested on FRED only; C20's ended-ticker filter and C17's unknown-source path untested; nothing enforces C02/C17's shared CTEs, or that discovered check ids match design.md section 7 | Branches running in production have no test; a deleted check vanishes from health without trace | M1 fix |
| 72  | Audit 2026-11-04, M1 code, LOW batch: raw write-once relies on Windows rename refusing to overwrite (runs.py; Linux overwrites, matters for the hosted path); Tiingo end date local and inclusive vs Yahoo's New York and exclusive; yahoo auto_adjust not type-checked; nightly-refresh.ps1 has no catch, so a crash leaves no error line; app caches never evict; Charts panels may be lost on page navigation (inferred, not exercised); a malformed bookmark date shows a traceback; make_icon.py writes into a scratch/ folder a fresh clone lacks; .gitignore lacks .streamlit/secrets.toml (hosting) | Each small; together the edge cases the next host or edit will hit | M1 fix |
| 73  | Audit 2026-11-04, M2 draft, HIGH: the pipeline is built for two tables. stage.py:31,48 requires every run to hold exactly DATASETS (yf/prices, fred/observations, fred/series); publish, stage, catalog (catalog.py:22), checks, board and C08 hard-code observations and series_catalog. Adding SEC as a required dataset makes every M1 run incomplete and staging drops their Yahoo rows | M2 as written cannot be built without an unplanned redesign of staging, publish and the schema contract | M2 draft |
| 74  | Audit 2026-11-04, M2 draft, HIGH: re-landed full-index quarters, nightly details snapshots and the submissions overlap have no rule for which landing staging uses; raw is one file per dataset per run, so 'that quarter's raw file' (C22) does not exist, and C23 fails on night 2 | Every publish blocks from the second night | M2 draft |
| 75  | Audit 2026-11-04, M2 draft, HIGH: yearly series convention undecided. D59 dates counts on Dec 31: the current year fails C09's future-date gate every night; board.py:207 raises 'no period end for frequency'; C02/C17/C04 have no yearly case, and D74 stops staging on A until they do; FRED uses period start; available_date for counts is not stated | The first M2 publish blocks, or the counts go unchecked | M2 draft |
| 76  | Audit 2026-11-04, M2 draft, HIGH: count vintages 'dated by the run that saw it' cannot be rebuilt: staging rebuilds from raw alone, so only today's count exists; two runs in one day share a vintage date and a changed count trips C14 (see #25) | Point-in-time history of the counts depends on run history, not the archive, and can block publish | M2 draft |
| 77  | Audit 2026-11-04, M2 draft, HIGH: monitors C25, C26, C27 read staging-only tables (filings, firm_year), but at view time health binds the served version (health.py run_checks with tables=version, monitors_only) | Those checks come back broken and health is red on every page view | M2 draft |
| 78  | Audit 2026-11-04, M2 draft, HIGH: C23's key (cik, year) in firm_year contradicts D59/D69, which give firm_year rows new vintages | The first second vintage breaks the gate, or dedup to one row loses point-in-time | M2 draft |
| 79  | Audit 2026-11-04, M2 draft, HIGH: D65's listing rule types 15-12B as an exit form, the code that stopped in 2022 (plan scope, D65); exits since then file 15-12G | Deregistrations from 2022 never end listed status; 2022-2025 over-count listings, and C27 locks the error into the baseline | M2 draft |
| 80  | Audit 2026-11-04, M2 draft, HIGH: acceptance #9's calibration compares the current year's stored status, which comes from the details snapshot (SEC's exchange data), with the tickers file (also SEC's exchange data) | Differences come out near zero; the pre-EDGAR hole goes unmeasured | M2 draft |
| 81  | Audit 2026-11-04, M2 draft, MED: the load log (runs.py write_raw) has no field for Last-Modified or the zip's control totals (C29's main-file count and filingCount); its readers lack union_by_name | C29/C31/C34 have no inputs; adding columns may collide with old log files (unverified) | M2 draft |
| 82  | Audit 2026-11-04, M2 draft, MED: C27's baseline in <data root>/health/ beside M1's collides with checks.py:194-196, which raises on more than one file in health/baseline/ | Writing the C27 baseline stops every check evaluation | M2 draft |
| 83  | Audit 2026-11-04, M2 draft, MED: memory and run time unbudgeted: write_raw takes one in-memory frame (about 27M rows on the first run); staging re-reads every run's details snapshot (991,508 rows a night, accumulating) | The first run or a later night can exhaust memory or the 30-minute limit | M2 draft |
| 84  | Audit 2026-11-04, M2 draft, MED: the local-source option has no hook (refresh() takes no arguments; the CLI's refresh has no flags); local files have no Last-Modified for C31/C34; pass-1 raw would become permanent input | Step 7's pass 1 can't run as written | M2 draft |
| 85  | Audit 2026-11-04, M2 draft, MED: the Universe page would read config/waterfall_steps.csv (D70), but the app reads only serving/ and health/; and steps 3-4 need that file, which step 6 writes | Rule break, fails when hosted, and a config edit makes the page disagree with that run's checks; build order broken | M2 draft |
| 86  | Audit 2026-11-04, M2 draft, MED: C29, C32 and C33 test staged tables step 2 never creates (tickers, per-run submissions details); C29 compares details rows to files while `filer` is one row per change | Three checks have nothing to test | M2 draft |
| 87  | Audit 2026-11-04, M2 draft, MED: C26 flags any annual-looking code not in annual_forms.csv, so amendments (10-K/A, 20-F/A) warn forever; the 25 pattern may also match Form 425 (unverified) | C26 is permanently yellow and ignored | M2 draft |
| 88  | Audit 2026-11-04, M2 draft, MED: C21-C25, C28-C30, C32-C33 state gate or monitor but no severity; CLAUDE.md requires one, and only error-severity gates block | Builder guesses which checks can block | M2 draft |
| 89  | Audit 2026-11-04, M2 draft, MED: acceptance #1 ('builds the universe and stays green') cannot pass before step 7 records the C27 baseline, since C27 warns with no baseline; new gate checks also read broken until the first M2 publish (health.py) | Acceptance fails on a documented, expected state | M2 draft |
| 90  | Audit 2026-11-04, M2 draft, MED: SEC available_date has three rules: #23 (next trading day after filing), design.md section 3 (acceptance), design.md observations (filing date) | Same-day joins leak an after-close filing into that day's close | M2 draft (settle #23) |
| 91  | Audit 2026-11-04, M2 draft, MED: smaller fit gaps: acknowledgments can't name a (cik, year) row (health.py key); board.py:152 raises for a source with no source page; board labels any row with a ticker column as Tiingo (C33's rows); #34 (no C13 baseline for new series) hits the count series, unscheduled | Health page errors or mislabels once SEC data is served | M2 draft |
| 92  | Audit 2026-11-04, docs, LOW: open issues whose 'Resolves in' has passed: #12 and #28 (M1, done); #17 and #25 (M1 step 4, checked). #17's accept path (D23) is confirmed still unbuilt in cli.py | The table claims fixes that never happened | Doc pass |
| 93  | Audit 2026-11-04, docs, LOW batch: stale text. Raw path in CLAUDE.md and design.md section 2 (code writes raw/<source>/<dataset>/<run_id>.parquet); CLAUDE.md layout (health/ is a file; config and script files missing); design.md port 8501 and powershell (code: 8517, pwsh); D49 mentions the toast D50 removed; D17 'C03-C11' omits C14; CLAUDE.md cites companyfacts for M2+ (M3, D57); section 5 lacks full-index and submissions rows; CLAUDE.md cache key vs D45; plan exit forms omit 25-NSE; D58's 8-K items vs D69's forms only; D65 cites an undefined 'fix 1'; vintage triggers differ between design.md and the plan; plan.md M1 scope says C01-C14 | A builder trusting the docs trips on each | Doc pass |
| 94  | View-time health still reads config/expected_states.csv (health.acknowledgments), the one config/ read D76 left. Deliberate for now: a newly reviewed acknowledgment takes effect on the next page view, not the next publish. Snapshotting it into inputs/ would make acknowledgments per version and need a publish to take effect | On the hosted path the status bar can't read the file and crashes | Before hosting (decide: snapshot per version, or copy to health/ on edit) |

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

**Goal.** A survivorship-free, year-by-year list of every filer (CIK) that filed an annual report with the SEC: who joined, who left, who was there from the start. A filer is not yet a company: a company that re-registered under a new CIK appears as two filers until CIKs are linked, which is out of scope here (D54). A waterfall page narrows that population, one named step at a time, to the analysed set: what remains after every step, the last being domestic filers whose annual report that year carries XBRL financials. That is the set M4's cross-section page reads (D66). It refreshes nightly under the same health system as M1, and it becomes the expected set that M3's fundamentals are checked against.

### Scope (decided: D52-D70)
- **Top of the funnel (D54):** every CIK with an original annual report (form codes from `config/annual_forms.csv`, taken from the codes present in the full-index), 1993-11-29 to as-of. Complete from filing year 1997 (domestic) and 2002 (foreign); earlier years stored and flagged phase-in.
- **Membership (D55):** in for every calendar year from first to last annual report; empty years in between count, flagged; an exit is confirmed once a full calendar year passes with nothing filed.
- **Foreign filers (D53):** in, foreign status per filer-year from that year's form.
- **Events:** listing and exit forms from the full-index, by form type only (no filing text): 8-A12B / 8-A12G (registration), 25 / 25-NSE (delisting), 15-12B / 15-12G / 15-15D and 15F-12B / 15F-12G / 15F-15D (deregistration, domestic and foreign). Form codes come from the index, as for annual reports: 15-12B falls to 0 during 2022 while 15-12G rises (139 in 2021, 649 in 2023), so a typed list goes stale (C26). Exit reason = the exit forms (25, 15, 15F) filed from 180 days before to 540 days after the last annual report, else `unknown` (D69). A reason that arrives after the exit is confirmed is a new vintage of that firm-year row (D59).
- **Listing status (D65):** exchange-listed in year Y, rebuilt from the forms: an 8-A12B on or before the end of Y with no later Form 25 / 25-NSE / 15-12B / 15F-12B before Y starts. From the first details snapshot on, the snapshot's exchange field decides instead. Labelled approximate before 2006 (Form 25 counts: 0 before 2001, about 400-470 a year 2002-05, 1,400-2,400 from 2006) and blind to listings that began before EDGAR (no 8-A12B on file). The hole is measured, not guessed: acceptance #9.
- **Sources:** EDGAR full-index (`master.gz` per quarter), bulk `submissions.zip` (name, SIC, state, former names, current tickers and exchanges), `company_tickers_exchange.json` (current ticker to CIK).

### Build order
- [ ] 1. Loaders: full-index (every quarter once; each run re-lands only the current and previous quarter), submissions bulk, tickers file. SEC User-Agent, at most 10 requests/s, one retry on 503, schema validated, raw write-once. submissions.zip nightly: full company details every run, filing history only past the watermark plus overlap (D58). The load log records each download's HTTP `Last-Modified` (C31, C34; D60). A local-source option reads already-downloaded files instead of SEC, for step 7's pass 1. `config/annual_forms.csv` and `config/event_forms.csv` are generated once from the form codes present in the full-index and committed (D54, D65); C26 watches them from then on
- [ ] 2. Staging SQL: `filings` (every index row), `submission_filings` (submissions filing history: accession, report date, acceptance time, isXBRL, 8-K items; key measured at build, C30), `annual_reports`, `filer` (one row per CIK per details change: D59's `universe_filer`), `events`, and `firm_year` (D55) carrying each year's attributes: gap flag (D55), foreign (D53), exchange-listed with its basis, snapshot or forms (D65), XBRL on that year's report (D66), co-registrant group (D64), exit reason (D69). SIC is labelled current-as-of-pull (#44)
- [ ] 3. Checks C21-C34 in `checks/*.sql`, each with a fault-injection test. C21-C28 cover the full-index and the universe; C29-C34 give the other two sources their completeness, uniqueness and freshness checks (hard rule; D60):
  - C21 completeness (gate): every quarter from 1993Q1 to the current one is in raw
  - C22 control total (gate): staged filings per quarter = lines in that quarter's raw file
  - C23 uniqueness (gate): (cik, year) unique in `firm_year`; (cik, accession) unique in `annual_reports` and in `events`; (cik, accession, form, name) unique in `filings`, the index's own row key (D61)
  - C24 integrity (gate): every annual-report CIK has a submissions row (spike: 0 missing)
  - C25 freshness (monitor): latest full-index filing date older than 3 business days
  - C26 form-code drift (monitor, warn): an index form code that looks like an annual report (10-K, KSB, 20-F, 40-F patterns) or a listing event (8-A, 25, 15, 15F patterns) but is not in `config/annual_forms.csv` or `config/event_forms.csv`; also a listed code with no filings in the latest full year. The 10KSB lesson (D54) and the 15-12B stop (2022) as a standing control
  - C27 history drift (monitor, warn): a closed year's membership, exchange-listed set or analysed set differs from its accepted baseline (D67). A year is closed once a full calendar year has passed after it (D55's exit rule). The baseline is three CIK sets per closed year (all filers, exchange-listed, analysed), not only counts, so offsetting entries and exits can't cancel out. The warning names the year and the set, both counts, and each CIK that entered or left. A change to `config/waterfall_steps.csv` or a late Form 25 shows up here, since C28 only checks that one run's steps add up. With no baseline recorded it warns, as C13 does. Accept path in step 7 (D62)
  - C28 waterfall integrity (gate): every `firm_year` row lands in exactly one waterfall bucket (a step or the analysed set), none lost or doubled; and every rule in `config/waterfall_steps.csv` names an attribute that exists in `firm_year`. Footing then holds by construction (D70)
  - C29 submissions completeness (gate): details rows staged = main `CIK##########.json` files in the zip (991,508 on 2026-11-04); every overflow file a main record names under `filings.files` is in the zip, and its filings staged = that entry's `filingCount` (spike: 5,412 named, 5,412 present, 0 either way)
  - C30 submissions uniqueness (gate): CIK unique per details snapshot (spike: 991,508 files, 991,508 CIKs). The filing-history key is measured at build, not assumed (D61's lesson)
  - C31 submissions freshness (monitor, warn): the latest download's `Last-Modified` older than 36 hours at view time. SEC rebuilds the file nightly (2026-11-04: 06:21 UTC)
  - C32 tickers completeness (gate): rows staged = rows in the file's `data` array (10,423 on 2026-11-04)
  - C33 tickers uniqueness (gate): (cik, ticker) unique, and no ticker on two CIKs (2026-11-04: 0 and 0)
  - C34 tickers freshness (monitor, warn): `Last-Modified` older than 14 days at view time. The file is not daily (2026-11-04 showed 2026-10-30), so the threshold is provisional until M2 runs record the real cadence (D60)
- [ ] 4. Serving: the three universe tables (D59, design.md section 3) in `serving/`, and the waterfall defined once in `src/pharos/transform/waterfall.sql`: it assigns each firm-year the first step that drops it, or the analysed set, and C27, C28 and the Universe page all run it (D70); views in `catalog.duckdb`, plus population counts per year as derived series in `series_catalog` (all filers, domestic, foreign, exchange-listed, analysed set, joined, left), so the Charts page can overlay the population on GDP
- [ ] 5. Fill the watchlist `cik` column from the tickers file; ATVI mapped by hand (#22). Reconcile the exchange list to the universe and name the difference (#46), and run the listing-status calibration (acceptance #9) against the same file
- [ ] 6. Universe page: joined / left / stayed by year, the waterfall for a chosen year with steps from `config/waterfall_steps.csv` (seeded with the measured groups: asset-backed 6189, blank checks 6770, no SIC, co-registrants, foreign, no exchange ticker, no annual report this year, no XBRL), each step drillable to its company list. This step writes `config/waterfall_steps.csv` from that seed list. The page also shows the share of exits whose reason is `unknown` (acceptance #4) and the listing-status calibration (acceptance #9). The step before last, no annual report this year, drops gap years (D55's flag: a filer inside its span that filed nothing that year, mostly dormant shells) (D68). The last step, no XBRL, then drops only reports that lack it, read from the `isXBRL` flag on that year's annual report in the submissions filing history; what remains is the analysed set (D66). Co-registrant step (D64): an annual report filed under several CIKs counts once; the step drops the other CIKs on it and its drill-down lists the whole group, marking which CIK stands for it. Status bar on the page. No step may use an attribute that disappears when a company dies. Tickers and exchanges do: SEC lists them for live companies only, so applying today's list to a past year drops every company that has since died, which is survivorship bias, the thing M2 removes. The exchange step reads the nightly details snapshot for the chosen year (D58); for a year before the first snapshot it uses the listing status rebuilt from the forms (D65), labelled approximate before 2006, with the Universe page saying what that misses. Foreign status comes from that year's form (D53). SIC is not a survivorship leak, since a dead company keeps its last code, so the SIC steps run on every year, labelled current-as-of-pull (#44)
- [ ] 7. Re-perform the spike in two passes. Pass 1: the loaders read the spike's own downloaded files (`<data root>/spikes/universe/`: the full-index quarters and submissions.zip, pulled 2026-11-04) through a local-source option, and the universe must match the spike exactly. Pass 2: a fresh pull, reconciled to pass 1 for filing dates through 2026-11-03, each difference listed with its cause. After pass 1, record the C27 baseline in `<data root>/health/`, beside M1's opening-balance baseline: each closed year's three CIK sets (all filers, exchange-listed, analysed), stamped with the run_id and dated. Accept path: `pharos universe accept --year YYYY --reason "..."` appends a new baseline for that year, all three sets, with the reason, the run it accepted, and the CIKs that entered and left each set. Earlier baselines are kept, never overwritten. A reason is required. One command accepts one year (D62)

### Acceptance criteria
1. From an empty data root, `uv run pharos refresh` builds the universe and stays green; an immediate rerun adds zero rows to `serving/`.
2. Re-performance (step 7): from the spike's own files the pipeline reproduces the spike exactly: 48,209 CIKs, 319,098 firm-years, 10,568 gap years, and the per-year counts (e.g. 12,233 in 1997, 13,557 in 2002, 7,511 in 2026). On a fresh pull, every difference for filing dates through 2026-11-03 is listed and explained (for example, a filing SEC indexed after the spike's download); an unexplained difference fails.
3. Every check C21-C34 has a fault-injection test that shows it firing with the right color.
   For C27: drop one CIK from a closed year in staging -> warn naming that CIK; flip one CIK's listing status in a closed year -> warn naming the exchange-listed and analysed sets; accept the year -> green; a different change to the same year after the accept -> warns again; accept without a reason -> refused.
4. Every confirmed exit has a reason or `unknown`, and the share that is `unknown` is shown on the Universe page.
5. The waterfall foots for every year (C28: every firm-year in exactly one bucket), and each step names what it drops and how many.
6. The Charts page overlays the population counts (step 4's series) on GDP.
7. The watchlist `cik` column is filled for all 18 names (#22), and the exchange list's difference from the universe is a named step or a documented expected state (#46).
8. Run time, measured from run_manifest: every nightly run with the universe in it finishes within 30 minutes. The first run, which lands the full filing history, is timed and recorded but not held to that limit. Basis (2026-11-04): M1 runs take 18-32 s; parsing all of submissions.zip took 53 s here; the 1.57 GB download is not yet timed.
9. Listing-status calibration (D65): for the current year, the exchange-listed set rebuilt from the forms is compared with the tickers file's exchange-listed set. Both differences are counted and shown on the Universe page: listed today with no 8-A12B on file is the size of the pre-EDGAR hole; rebuilt as listed but absent from the tickers file shows what the rule over-counts (debt-only listings, missed delistings).

**Built** when 1-9 pass. **Done** after 7 consecutive green nightly runs with the universe in them.

### Out of scope for M2
Financials of any kind (M3); linking several CIKs into one company (predecessors, re-registrations); reading filing text; paid sources (CRSP); membership on a specific date (Form 25 / 15 dates are stored; they set the yearly listing status (D65) but not membership).

---

## Later milestones (one line each until current)
- **M1.5: Exploration tools (#41).** After M2 (D51).
- **M3: Fundamentals, formerly M2.** companyfacts in full → raw facts with accession and filed date, every filer and concept (D57); curated in staging, balance sheet first. Equity, preferred, both share counts, goodwill and intangibles. Book value per share and P/B as the first derived series via ASOF on available_date. XBRL checks: balance identity, share-count divergence, scale errors. Financial Statement Data Sets for multi-class names. OTC decision.
- **M4: Cross-section page (formerly M3, universe moved to M2).** Domestic 10-K filers. SIC → Fama-French industries. P/B distribution, industry medians, scatter. Coverage heatmap scales to the universe.
- **M5: Flow statements + quarterly panel.** YTD differencing, Q4 derivation, 52/53-week handling, fiscal→calendar alignment, concept map with fallback chains, ratio library.
- **M6: BEA national accounts breakdown.** The GDP tracker folded in: small-multiples page with drill-down.
- **Hosting (floating, after M1 is done).** Actions cron → R2 → Streamlit Community Cloud; repoint the desktop icon; phone home-screen shortcut.

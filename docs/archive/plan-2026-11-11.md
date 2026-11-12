# Plan: Pharos

## Status
<!-- Claude Code updates this block at the end of every session. Keep it to these lines. -->
- **Milestone:** M3 (Company financials, D148-D151) current since 2026-11-08; W paused at step 1 (D148). M2 (SEC landed) DONE 2026-11-05: acceptance #1-#4 passed; #4 is run time only (D136). M1.5 dropped (D137). M1 DONE (7 green nightlies, 20261008T053003Z-20261014T053008Z). H (Hosting) on hold (D106). Nightly task disabled: every refresh is on demand (D160)
- **Health:** GREEN since run 20261111T134544Z (started by hand 08:45 ET, published 09:37 ET, 52 min; the first run to finish Company Facts staging with real changes: two unpublished nights, 40 min of the 52). From 11-11 evening every refresh is on demand (D160): the scheduled task is disabled; `uv run pharos refresh` runs prices and FRED (13s of the last run), `uv run pharos sec` runs SEC (D159); scripts/nightly-refresh.ps1 -Watch shows a run as it goes. With no scheduled run, the freshness checks (C01, C17 and others) turn red as data ages: the rule doing its job (D157). #117 stays open until more runs pass.
- **Last session:** 2026-11-11 (4): short; no code. James picked trust for the SEC group and asked for a lighter trusted bar (see Next action). Before that, 2026-11-11 (3): started Scaffold (scaffold/), James's pages for keeping up with Pharos: a Catalog of data sources and three source sheets (SEC Company Facts, FRED / ALFRED, Prices: Yahoo and Tiingo), every stored source linked to one. Then: every refresh on demand (D160, nightly task disabled); "reference" dropped as a Catalog state (Tiingo is trusted); the folder-map step left the session protocol (page archived to scratch/archive/). SEC is on demand (D159). Resume from the top of scaffold/notes.md (decisions, findings, open items, next step). Earlier the same day: 2026-11-11 (2): runs are watchable: a step line as each step starts and ends, a 30s heartbeat (memory, CPU), a terminal window on the log (f1f41bd); timing lines inside each Company Facts pass. Scratch pages: scratch/company-facts.html (what the step does), scratch/run-timeline.html (charts from a log, by scratch/run_timeline.py). Earlier the same day: abort hunt (scratch/abort-hunt.html), filings and submission_filings staged by quarter (D155, D156).
- **Next action:** James chose to make the SEC group trusted (2026-11-11 (4)) and said the trusted bar is too heavy: no fault-injection test needed; counts, checks and a run that finished start to finish are what he wants. Open decision, proposed and not yet answered: trusted = the SEC loader guards as they stand (SchemaDriftError, read-back counts, Content-Length) + three SEC gate checks (key uniqueness, counts don't drop sharply against the last published version, Company Facts ties to the filing index) + the 12 Company Facts open questions answered in one red-pen pass on Claude's drafts; fault-injection tests optional everywhere (amends D127 and a CLAUDE.md hard rule, so a D-row). Measured: every SEC check today is a freshness warning (C25, C31, C34, C40, gate: no); nothing can block an SEC publish. Queued behind it: the Company Facts Catalog line (proposed: "Every XBRL fact every company reported: 99.4% financial statements (US GAAP, IFRS), the rest cover page, filing fees and executive pay"; us-gaap + ifrs-full = 125,811,830 of 126,588,444 facts). Health GREEN at 2026-11-11 evening; C01 turns red about 11:37 ET 11-12 without a `uv run pharos refresh` (seconds). #117 waits on a `uv run pharos sec` run James asks for.
- **Blockers / open questions:** #117 (the abort) blocks M3: never build on red. #17 and #25 are built together when C14 first fires on a price. Full list in Open issues below and Parked issues in `docs/later.md`; cite only IDs that appear there.

## Open issues
<!-- A concern not fixed in the turn it comes up goes here only if it blocks the current milestone or could lose or corrupt data (CLAUDE.md session protocol). IDs never change or get reused. Fixing an issue deletes its row in the same commit, and the commit message cites the ID; git history is the record. -->

| ID  | Issue                                                                                          | Effect if ignored                                                      | Resolves in        |
|-----|------------------------------------------------------------------------------------------------|------------------------------------------------------------------------|--------------------|
| 17  | Under dedup rule A a real source correction blocks publish forever (old raw file stays)        | One Yahoo correction freezes serving/; needs a deliberate accept path  | when C14 first fires on a price (build D23 with #25) |
| 24  | Accept path for a FRED same-key conflict (C14 on every FRED key)                               | A changed ALFRED vintage blocks publish forever                        | only if C14 fires  |
| 25  | Price vintage is a DATE: two full pulls of one ticker on one day share a vintage               | Same-day accept re-pull leaves the conflict in place                   | with #17 (D23 build) |
| 34  | A series added after the audit has no baseline row; C13 never compares it. Any re-baseline path built for this must also decide a size cap on isolated print disagreements: D41's shape rule passes one of any size, and C10 only catches moves over 40% (folded in from #37) | A watchlist addition can lose history with C13 green; a re-run audit could pass a large isolated bad print | before any further watchlist change; the five D141 series and W's 56 (D142) run without it |
| 117 | Nightly dies in native abort() (ucrtbase.dll, FAST_FAIL_FATAL_APP_EXIT) at different steps: 11-08 after publish, 11-10 SEC staging step 8, 11-11 Company Facts delta. Every step tested alone passes; cause unknown | Nothing publishes; health stays red; each failed night stacks more work on the next | abort hunt (scratch/abort-hunt.html) |

---

## M3: Company financials (D148-D150, 2026-11-08; DRAFT for James's red pen)

**Goal.** Every domestic 10-K filer's annual financial statements, as each 10-K reported them, served as curated tables with the ratios an analyst reads: margins and returns, working-capital days, capital intensity, leverage and liquidity, each placeable against industry peers at every SIC rung and Pharos sector. No stock price (D148).

### Measured before drafting (FY2024 10-K filers, 2026-11-08)
5,550 filers; 4,049 tag revenue under one of three concepts, 745 of them financials (SIC 6000-6999), leaving 3,304. COGS under one of three concepts: 2,606 (service firms report none, so inventory and payables days cover about half). Receivables 2,756, inventory 2,265, payables 3,260, net PP&E 3,859, capex 3,394. Year-end month: December 73.8%, January 4.8%, June 4.7%, September 4.0%; 321 filers report 52/53-week years.

### Rules
- Annual only: full-year facts from 10-K filings. A flow whose period is far from a year (a 10-KT transition period) is flagged, not used in ratios.
- Fiscal years as reported, dated by the fact's own period end; no calendar alignment (D150). Over time, charts use a real date axis. Against peers, each company's latest 10-K as of a date (ASOF on `available_date`): as of the filing date gives the first-reported figures, as of today the latest.
- Every version kept: a restated year from a later 10-K is a new row with that filing's accession and dates, never an overwrite.
- Per-day ratios (days sales outstanding, inventory and payables days) use the period's actual days and the average of opening and closing balances, both from the same 10-K.
- Peers: every rung of the SIC ladder (division, 2-, 3-, 4-digit) and Pharos sector, each shown with its peer count; no minimum group size (D149). Financials are excluded from peer comparisons; negative equity is flagged, never ranked.
- All transformation in DuckDB SQL. It starts beside the Peers page (quick lane, reading the served facts read-only) and moves to `src/pharos/transform/` only at step 5, with `SECTOR_SQL` and the revenue fallback chain from `explore/lib/base.py` (D151).

### Build order (page first, pipeline last: D151)
- [x] 1. Concept map (DONE 2026-11-08, D152: 35 rows, 14 measures, pure lookup, six FLAG rows kept): `config/concepts.csv`, one row per (measure, priority, concept, unit), with each concept's coverage by year measured 2009-2026. Only the measures the four asks need: revenue, cost of revenue, operating income, net income, depreciation and amortization, capex, receivables, inventory, payables, net PP&E, current assets, current liabilities, total assets, parent equity. Claude drafts it from the measured counts; James signs off the definitions.
- [x] 2. Peers page (DONE 2026-11-08: 3,155 companies, 15 ratios, as of the build date; peers are each company's latest 10-K filed in the 12 months before it) (`explore/peers/`, menu: Companies, after Industry Code Breakdown). Its SQL builds the annual statements by the concept map, the industry rungs and the ratios from the served Company Facts, read-only. A company's ratios against every rung and sector, with peer counts. Each ratio named with its formula.
- [x] 3. (DONE 2026-11-08: 70 cells against the printed 10-Ks, 59 tie, 5 tie with a definition note, 2 from notes, 1 outside the map, 3 not presented; fixes D153, D154 and the year-end rule; four definition items accepted as they stand: XOM revenue incl. other income, XOM trade receivables and payables, XOM inventory untagged as a total, CAT capex excl. leased equipment) Tie-out by hand on that page: WMT (January year-end, inventory), AAPL (September, 52/53-week), XOM (capital intensive), MCD (negative equity), CAT (manufacturing, financing receivables). Every measure in each latest 10-K matches the filed statements.
- [ ] 4. James looks at the page: which measures and ratios earn a place in the pipeline. Anything dropped leaves the concept map.
- [ ] 5. Promote what survived: the SQL moves to `src/pharos/transform/` and is built in the SEC group's stager from the staged facts (Company Facts is nightly there, D139); a warn check on the balance identity (assets vs liabilities plus equity); targeted tests, the full suite once in the background, commit. The page then reads the served tables. Before wiring, a timed run on a throwaway data root measures what it adds to the nightly. No hand refresh: the nightly after the commit is the first real run. Further checks (scale errors, non-annual periods) only for what the page showed going wrong.

### Acceptance criteria
1. The five companies of step 3 tie to their filed 10-Ks on every measure.
2. The curated tables that survived step 4 are in `serving/` and the catalog, and the Peers page reads them.
3. Each measure's coverage is recorded in the concept map.
4. Health is green after the nightly that first publishes them.

**Done** when 1-4 pass.

### Out of scope for M3
Quarterly figures (the panel, later); stock price and P/B; the Financial Statement Data Sets; templates for banks and insurers; goodwill, intangibles and total liabilities until a ratio needs them.

---

## W: World macro (D142, 2026-11-07; PAUSED by D148, step 1 not started)

**Goal.** 56 FRED series for 12 economies and the euro area land in the macro group every night, with every vintage, like the eight US series, and health stays green. No analysis on them; a page on them is exploration (D137).

### The 56 series (each checked current on FRED's API, 2026-11-07)
| Measure            | n  | Series                                                                                                                                                                                  |
|--------------------|----|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Real GDP           | 13 | NGDPRSAXDCCAQ, NGDPRSAXDCGBQ, NGDPRSAXDCDEQ, NGDPRSAXDCFRQ, NGDPRSAXDCITQ, NGDPRSAXDCJPQ, NGDPRSAXDCBRQ, NGDPRSAXDCMXQ, NGDPRSAXDCKRQ, NGDPRSAXDCAUQ, NGDPRNSAXDCINQ, NGDPRXDCCNA, CLVMEURSCAB1GQEA19 |
| CPI (HICP)         | 4  | CP0000DEM086NEST, CP0000FRM086NEST, CP0000ITM086NEST, CP0000EZ19M086NEST                                                                                                                 |
| Unemployment       | 9  | LRHUTTTTCAM156S, LRHUTTTTGBM156S, LRHUTTTTDEM156S, LRHUTTTTFRM156S, LRHUTTTTITM156S, LRHUTTTTJPM156S, LRHUTTTTMXM156S, LRHUTTTTKRM156S, LRHUTTTTAUM156S                                  |
| Short rate         | 10 | IRSTCI01CAM156N, IRSTCI01GBM156N, IRSTCI01JPM156N, IRSTCI01INM156N, IRSTCI01BRM156N, IRSTCI01MXM156N, IRSTCI01KRM156N, IRSTCI01AUM156N, IR3TIB01CNM156N, ECBDFR                          |
| 10-year yield      | 10 | IRLTLT01CAM156N, IRLTLT01GBM156N, IRLTLT01DEM156N, IRLTLT01FRM156N, IRLTLT01ITM156N, IRLTLT01JPM156N, IRLTLT01MXM156N, IRLTLT01KRM156N, IRLTLT01AUM156N, INDIRLTLT01STM                  |
| FX vs USD, monthly | 10 | EXCAUS, EXUSUK, EXUSEU, EXJPUS, EXCHUS, EXINUS, EXBZUS, EXMXUS, EXKOUS, EXUSAL                                                                                                         |

Gaps, not pulled: monthly CPI outside the euro area (FRED's OECD feed stopped in 2025), unemployment for China, India, Brazil and the euro area, 10-year yields for China, Brazil and the euro area. Filling them means IMF or World Bank directly, a new source.

### Build order
- [ ] 1. Release lags. For each series, the median first-release lag 2023-2026 from its ALFRED vintages plus slack (D33's method, as D141), into `expected_lag_days` in `config/sources.yaml`. ECBDFR is daily: its lag is measured the same way, or it is dropped if its vintages make that meaningless; either way D142 says which.
- [ ] 2. Throwaway-root run. With the 56 added, run the prices_macro group alone on a throwaway PHAROS_DATA_ROOT (no SEC; `refresh._run` takes a group list, to confirm before use). Record rows across all vintages, run time, and every check result. Watch C04 (expected dates for OECD and IMF series), C16 (withdrawn values), C02/C17 (the measured lags) and C14. Why it matters: since D144 a failing series no longer carries prices forward, but it is red under C41 every night until fixed, so every series must load cleanly here first. A series that cannot pass is dropped and named in D142, never acknowledged in bulk.
- [ ] 3. Commit `config/sources.yaml` with D142. The nightly lands them (two lanes: no hand refresh).
- [ ] 4. The morning after: health and run time of that nightly, against 20261107T073004Z (about 22 minutes).

### Acceptance criteria
1. Every series kept after step 2 is in `serving_series_catalog` and has observations after one nightly.
2. That nightly publishes prices_macro (not carried forward), and health is green; any acknowledgment it needs is one row in `config/expected_states.csv` with its reason.
3. That nightly's run time is recorded.

**Done** when 1-3 pass.

### Out of scope for W
The gaps above; #34's re-baseline path; any page (exploration, D137); the US (already served).

---

## M2: SEC landed (D128, 2026-11-05; DONE 2026-11-05)

**Goal.** Every SEC bulk dataset Pharos uses sits in Pharos's own data root at the landed level (D127), loaded by the nightly refresh, staged, published and queryable in DuckDB. No analysis on it.

### Build order
- [x] 0. Source groups (D113-D117): see U, step 0, in `docs/later.md`.
- [x] 1. Loaders for the full-index, submissions and the tickers file (D119-D124): see U, step 1, in `docs/later.md`.
- [x] 2. Stage each dataset, newest value per key (D132), and wire the group in. Done 2026-11-05:
  - DONE 2026-11-05: stage_sec.py: replace roll_forward/fold/newest/rebuild_night with one merge: the base is the table in the current serving version (absent: build from every landing); rows from runs after the base's run override base rows by key; keep D126's acceptance-conflict collapse; assert the key unique (a duplicate stops the group). Stage `submission_details` and `tickers` the same way. Delete the `*_versions` tables and their tests.
  - DONE 2026-11-05: groups.py: declare group `sec` (declared as SEC, not yet in GROUPS: it registers with the wiring below, or every nightly records it never_published and C36 is red): required sec/full_index, sec/submission_details, sec/submission_filings, sec/tickers; tables filings, submission_filings, submission_details, tickers; inputs none until step 4.
  - DONE 2026-11-05: stage.STAGERS, checks.BINDERS (a minimal binder: run-level names and the group's tables, at run and view time), refresh.LOADERS (load_index, load_submissions, load_tickers), catalog.py (raw and serving views for the SEC tables, for DBeaver).
  - DONE 2026-11-05: memory cap (#111, closed): stage_sec.connect() sets 8 GB, spilling to <data root>/tmp/duckdb, one connection per dataset. Measured: Python's working set peaked at 11.75 GB (first run) and 11.67 GB (second), during SEC staging: the cap bounds DuckDB's buffers, not the whole process. Left at 8 GB.
  - DONE 2026-11-05: serving keeps two versions (D133). A version with SEC is 2.8 GB (estimate was 1.7).
  - DONE 2026-11-05: first real runs by hand. 20261105T071217Z: 13m52s, lands everything (raw: full-index 479 MB, filings 1.1 GB, details 109 MB, tickers 0.3 MB); sec stopped at staging (D134). 20261105T073034Z: 15m04s, both published; no base yet, so staging read both landings. Served: filings 27,380,030, submission_filings 27,378,552, submission_details 991,698, tickers 10,409; every row keeps the first run's lineage (nothing changed between the runs). data/spikes/ moved to data/archive/spikes-2026-11-04/ (3.6 GB, nothing deleted). Still to record: the first nightly's run time (acceptance #4).
- [x] 3. companyfacts: a loader (bulk zip, D57), staged as landed. Originally on demand (D131); D139 now uses that full landing as the baseline and performs per-CIK nightly refreshes driven by changed submission filings. Done 2026-11-05, lifecycle amended 2026-11-06:
  - Historical D135 behavior: `uv run pharos companyfacts` ran the companyfacts group alone and refresh left it `not_run`. D139 replaces this behavior.
  - First run 20261105T083453Z: landed 126,265,124 facts and 4,837,491 concepts (raw 892 MB + 56 MB, about 10 min), then killed in staging by memory pressure: the merge spilled 60 GB. A first landing now skips the merge (same table, tested). Staged and published that landing with `pharos stage --run` / `pharos publish --run`: 92 s staging (peak 7.66 GB, measured on a throwaway root with the same rows), 8 s publish; the version is 4.3 GB. No run manifest for this run (the kill skipped it; the stage/publish commands write none).
  - The first D139 unified refresh measured the monolithic merge at 27m44s despite 0 changed CIKs. D140 replaces it with per-CIK copy-on-write partitions; the next run performs the one-time conversion, later runs replace only changed CIKs.
  - D139: Company Facts joined the SEC group. A nightly load compares the submissions overlap with the newest prior filing rows, fetches full per-company documents only for changed CIKs, and publishes all six SEC tables together. `pharos companyfacts` now forces a new bulk baseline plus fresh SEC companion datasets.
- [x] 4. One freshness check per dataset (C25, C31, C34 as specified in U, plus one for companyfacts), and a one-row description per dataset in design.md section 5. Done 2026-11-05: C25 (full-index, error, 3 weekdays), C31 (submissions, warn, 36 h), C34 (tickers, warn, 14 days), C40 (originally 92 days on demand; amended by D139 to 36 hours nightly). Section 5 rows name each dataset's tables and check; section 7 has the four rows.

### Acceptance criteria
1. One `uv run pharos refresh` on the real data root lands, stages and publishes every SEC dataset. Originally Company Facts used a separate on-demand command (D131); D139 folds its incremental refresh into this same SEC publication. Each is a table in `catalog.duckdb`.
2. Each dataset has a freshness check and a one-row description.
3. With the SEC download made to fail, a run still publishes prices + macro (U's acceptance #10). PASSED 2026-11-05, run 20261105T102452Z (24 s): `refresh` with SEC_USER_AGENT blanked in-process, so every SEC fetch raised SecFetchError before any request. prices_macro published (yf, fred, tiingo landed); sec carried_forward from 20261105T073034Z with the load error as reason (no SEC raw written, served tables unchanged: filings 27,380,030, lineage 20261105T071217Z); companyfacts not_run from 20261105T083453Z. Health: C35 warn (expected, clears on the next SEC publish), C36 pass.
4. Run time of the first run and of a nightly run is recorded, not held to a limit (memory dropped, D136). PASSED 2026-11-05: first run 15m04s (20261105T073034Z); a nightly-equivalent `refresh` by hand, 239 s (20261105T103527Z): sec and prices_macro published, companyfacts not_run, 0 failing checks. SEC's files were unchanged since the 07:30 UTC run (same Last-Modified and row counts), so no SEC row changed.

**Done** when 1-4 pass and health is green.

### Out of scope for M2
Everything in U (`docs/later.md`); any analysis on the SEC data; new dashboard pages: the SEC tables are browsed in DBeaver over catalog.duckdb, connected read-only (D130).

---

## Later milestones (one line each until current)
- **M4: Market side (reshaped by D148).** Industry comparisons moved into M3. Left here: share counts (both, divergence check), book value per share and P/B via ASOF on available_date, the Financial Statement Data Sets for multi-class names, the OTC decision. P/B across the universe needs a price source for every company, dead ones included (#100).
- **M5: Quarterly panel (annual flows moved into M3 by D148).** YTD differencing, Q4 derivation, quarterly ratios.
- **M6: BEA national accounts breakdown.** The GDP tracker folded in: small-multiples page with drill-down.
- **Refresh on GitHub Actions (floating, D104).** Only on a measured need, e.g. JAMESTOWN off too often. Needs raw in R2, so it follows a decision on what raw keeps (#97, dropped 2026-11-05: harmless at today's growth) and real M2 sizes. App-only hosting is milestone H.

---

Done, on-hold and parked milestones (M1, H, U) and the parked issues are in `docs/later.md`.

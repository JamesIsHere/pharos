# Plan: Pharos

## Status
<!-- Claude Code updates this block at the end of every session. Keep it to these lines. -->
- **Milestone:** M2 (SEC landed) DONE 2026-11-05: acceptance #1-#4 passed; #4 is run time only (D136). Next milestone: M3, when James calls it; M1.5 dropped (D137). M1 DONE (7 green nightlies, 20261008T053003Z-20261014T053008Z). H (Hosting) on hold (D106). Nightly task 07:30 UTC (D63)
- **Health:** run 20261106T090019Z published prices_macro and the unified six-table SEC group; serving/CURRENT points to it. The run completed in 27m44s with 126,265,124 Company Facts rows and 4,837,491 concepts. C15/C16 are acknowledged; C35 was a pre-pointer-swap timing artifact from the prior carried-forward version.
- **Last session:** 2026-11-07: exploration. New explore pages: Appendix (index of every page outside the menu), Reporting Consistency, Companies vs Economy, People and Work. D141 adds five FRED series (population, labor, pay); a refresh started to land them was stopped by James as too slow for a source tweak (it runs every group): #115 parks per-group refresh. Run 20261107T065523Z died by that stop; serving unchanged.
- **Next action:** The next nightly lands the D141 series and performs the one-time D140 conversion; check its run time and health, then M3 when James calls it.
- **Blockers / open questions:** none. #17 and #25 are built together when C14 first fires on a price. Full list in Open issues below and Parked issues in `docs/later.md`; cite only IDs that appear there.

## Open issues
<!-- A concern not fixed in the turn it comes up goes here only if it blocks the current milestone or could lose or corrupt data (CLAUDE.md session protocol). IDs never change or get reused. Fixing an issue deletes its row in the same commit, and the commit message cites the ID; git history is the record. -->

| ID  | Issue                                                                                          | Effect if ignored                                                      | Resolves in        |
|-----|------------------------------------------------------------------------------------------------|------------------------------------------------------------------------|--------------------|
| 17  | Under dedup rule A a real source correction blocks publish forever (old raw file stays)        | One Yahoo correction freezes serving/; needs a deliberate accept path  | when C14 first fires on a price (build D23 with #25) |
| 24  | Accept path for a FRED same-key conflict (C14 on every FRED key)                               | A changed ALFRED vintage blocks publish forever                        | only if C14 fires  |
| 25  | Price vintage is a DATE: two full pulls of one ticker on one day share a vintage               | Same-day accept re-pull leaves the conflict in place                   | with #17 (D23 build) |
| 34  | A series added after the audit has no baseline row; C13 never compares it. Any re-baseline path built for this must also decide a size cap on isolated print disagreements: D41's shape rule passes one of any size, and C10 only catches moves over 40% (folded in from #37) | A watchlist addition can lose history with C13 green; a re-run audit could pass a large isolated bad print | before any further watchlist change; the five D141 series run without it |

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
- **M3: Fundamentals, formerly M2.** companyfacts is landed in M2 (D128); M3 curates it: companyfacts in full → raw facts with accession and filed date, every filer and concept (D57); curated in staging, balance sheet first. Equity, preferred, both share counts, goodwill and intangibles. Book value per share and P/B as the first derived series via ASOF on available_date. XBRL checks: balance identity, share-count divergence, scale errors. Financial Statement Data Sets for multi-class names. OTC decision.
- **M4: Cross-section page (formerly M3, universe moved to M2).** Domestic 10-K filers. SIC → Fama-French industries. P/B distribution, industry medians, scatter. Coverage heatmap scales to the universe.
- **M5: Flow statements + quarterly panel.** YTD differencing, Q4 derivation, 52/53-week handling, fiscal→calendar alignment, concept map with fallback chains, ratio library.
- **M6: BEA national accounts breakdown.** The GDP tracker folded in: small-multiples page with drill-down.
- **Refresh on GitHub Actions (floating, D104).** Only on a measured need, e.g. JAMESTOWN off too often. Needs raw in R2, so it follows a decision on what raw keeps (#97, dropped 2026-11-05: harmless at today's growth) and real M2 sizes. App-only hosting is milestone H.

---

Done, on-hold and parked milestones (M1, H, U) and the parked issues are in `docs/later.md`.

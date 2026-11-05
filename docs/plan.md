# Plan: Pharos

## Status
<!-- Claude Code updates this block at the end of every session. Keep it to these lines. -->
- **Milestone:** M1 DONE: 7 consecutive green nightly runs, 20261008T053003Z through 20261014T053008Z. M2 re-scoped to 'SEC landed' (D127, D128: review session 2026-11-05): steps 0-1 DONE (D113-D124); the universe moved to milestone U. D130-D133: DBeaver read-only as the viewer, companyfacts on demand, SEC staging newest value only, serving keeps two versions. H (Hosting) on hold: Pharos stays private; design.md section 5 records which sources could be shown online (D106). Nightly task now 07:30 UTC (D63)
- **Health:** YELLOW at 2026-11-05 06:04 UTC (`pharos health`): C02 and C20, all 17 tickers one session behind (last run 00:14 UTC was still Nov 4 in New York, D79); clears at the 07:30 nightly. C15 ATVI and C16 CPI Oct 2025 acknowledged (D46). serving/CURRENT = 20261105T001427Z
- **Last session:** 2026-11-05 (thirteenth): review session (scratch/review-2026-11-05.md, all five items). D127 two levels of rigor, landed by default; D128 M2 narrowed to 'SEC landed' (Pharos's data root held no SEC data), the universe moved to milestone U; D129 decision log moved to docs/decisions.md, as-built rows stopped; #114 opened. The uncommitted #113 work in stage_sec.py is left out of this commit. Then D130-D133, open issues pruned (5 open, 10 parked in docs/later.md), plan.md split, #113 work parked on branch wip-113-fold. Health YELLOW at 06:04 UTC (C02/C20: prices one session behind until the 07:30 nightly). Earlier sessions: git log
- **Next action:** M2 step 2, as broken down under M2 below. The nightly task (07:30 UTC) runs from this folder: keep the tree working between edits.
- **Blockers / open questions:** none. #17 and #25 are built together when C14 first fires on a price. Full list in Open issues below and Parked issues in `docs/later.md`; cite only IDs that appear there.

## Open issues
<!-- A concern not fixed in the turn it comes up goes here only if it blocks the current milestone or could lose or corrupt data (CLAUDE.md session protocol). IDs never change or get reused. Fixing an issue deletes its row in the same commit, and the commit message cites the ID; git history is the record. -->

| ID  | Issue                                                                                          | Effect if ignored                                                      | Resolves in        |
|-----|------------------------------------------------------------------------------------------------|------------------------------------------------------------------------|--------------------|
| 17  | Under dedup rule A a real source correction blocks publish forever (old raw file stays)        | One Yahoo correction freezes serving/; needs a deliberate accept path  | when C14 first fires on a price (build D23 with #25) |
| 24  | Accept path for a FRED same-key conflict (C14 on every FRED key)                               | A changed ALFRED vintage blocks publish forever                        | only if C14 fires  |
| 25  | Price vintage is a DATE: two full pulls of one ticker on one day share a vintage               | Same-day accept re-pull leaves the conflict in place                   | with #17 (D23 build) |
| 34  | A series added after the audit has no baseline row; C13 never compares it. Any re-baseline path built for this must also decide a size cap on isolated print disagreements: D41's shape rule passes one of any size, and C10 only catches moves over 40% (folded in from #37) | A watchlist addition can lose history with C13 green; a re-run audit could pass a large isolated bad print | before any watchlist change |
| 111 | DuckDB's default memory_limit is 80% of RAM (25.0 GiB on JAMESTOWN, read 2026-11-05). A scale test holding two 27M-row staging connections in one process ran the machine critically low on memory and was stopped by Claude Code (2026-11-05); one connection capped at 8 GB ran clean but about 3x slower (384 s vs 123 s for one full-index landing). The SEC stager sets no limit today | A nightly run on a machine in use could starve other programs, or spill slowly if capped too low | M2 step 2, when the SEC group is wired into refresh (set a cap from a measurement of run time against it) |

---

## M2: SEC landed (D128, 2026-11-05; steps 0-1 done)

**Goal.** Every SEC bulk dataset Pharos uses sits in Pharos's own data root at the landed level (D127), loaded by the nightly refresh, staged, published and queryable in DuckDB. No analysis on it.

### Build order
- [x] 0. Source groups (D113-D117): see U, step 0, in `docs/later.md`.
- [x] 1. Loaders for the full-index, submissions and the tickers file (D119-D124): see U, step 1, in `docs/later.md`.
- [ ] 2. Stage each dataset, newest value per key (D132), and wire the group in. Found reading the code 2026-11-05:
  - stage_sec.py: replace roll_forward/fold/newest/rebuild_night with one merge: the base is the table in the current serving version (absent: build from every landing); rows from runs after the base's run override base rows by key; keep D126's acceptance-conflict collapse; assert the key unique (a duplicate stops the group). Stage `submission_details` and `tickers` the same way. Delete the `*_versions` tables and their tests.
  - groups.py: declare group `sec` (rename from sec_universe): required sec/full_index, sec/submission_details, sec/submission_filings, sec/tickers; tables filings, submission_filings, submission_details, tickers; inputs none until step 4.
  - stage.STAGERS, checks.BINDERS (a minimal binder: run-level names and the group's tables, at run and view time), refresh.LOADERS (load_index, load_submissions, load_tickers), catalog.py (raw and serving views for the SEC tables, for DBeaver).
  - Memory cap (#111): SET memory_limit in the SEC stager; default 8 GB, flagged, measured on the first run.
  - Serving keeps two versions (D133): publish deletes older version folders.
  - First real run by hand (`uv run pharos refresh`), timed, before leaving it to the nightly task; then archive data/spikes/.
- [ ] 3. companyfacts: a loader (bulk zip, D57), staged as landed. It lands on demand, by its own command, not in the nightly refresh (D131); its freshness check warns on the landing's age, never red.
- [ ] 4. One freshness check per dataset (C25, C31, C34 as specified in U, plus one for companyfacts), and a one-row description per dataset in design.md section 5.

### Acceptance criteria
1. One `uv run pharos refresh` on the real data root lands, stages and publishes the full-index, submissions and tickers; companyfacts' on-demand command does the same for it (D131). Each is a table in `catalog.duckdb`.
2. Each dataset has a freshness check and a one-row description.
3. With the SEC download made to fail, a run still publishes prices + macro (U's acceptance #10).
4. Run time and memory peak of the first run and of a nightly run are measured and recorded, not held to a limit.

**Done** when 1-4 pass and health is green.

### Out of scope for M2
Everything in U (`docs/later.md`); any analysis on the SEC data; new dashboard pages: the SEC tables are browsed in DBeaver over catalog.duckdb, connected read-only (D130).

---

## Later milestones (one line each until current)
- **M1.5: Exploration tools (#41).** After M2 (D51).
- **M3: Fundamentals, formerly M2.** companyfacts is landed in M2 (D128); M3 curates it: companyfacts in full → raw facts with accession and filed date, every filer and concept (D57); curated in staging, balance sheet first. Equity, preferred, both share counts, goodwill and intangibles. Book value per share and P/B as the first derived series via ASOF on available_date. XBRL checks: balance identity, share-count divergence, scale errors. Financial Statement Data Sets for multi-class names. OTC decision.
- **M4: Cross-section page (formerly M3, universe moved to M2).** Domestic 10-K filers. SIC → Fama-French industries. P/B distribution, industry medians, scatter. Coverage heatmap scales to the universe.
- **M5: Flow statements + quarterly panel.** YTD differencing, Q4 derivation, 52/53-week handling, fiscal→calendar alignment, concept map with fallback chains, ratio library.
- **M6: BEA national accounts breakdown.** The GDP tracker folded in: small-multiples page with drill-down.
- **Refresh on GitHub Actions (floating, D104).** Only on a measured need, e.g. JAMESTOWN off too often. Needs raw in R2, so it follows a decision on what raw keeps (#97, dropped 2026-11-05: harmless at today's growth) and real M2 sizes. App-only hosting is milestone H.

---

Done, on-hold and parked milestones (M1, H, U) and the parked issues are in `docs/later.md`.

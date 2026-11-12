# Plan: Pharos

## Status
<!-- Claude Code updates this block at the end of every session. Keep it to these lines. -->
- **Direction:** Scaffold (scaffold/), James's pages for keeping up with Pharos, is the current work; resume from the top of scaffold/notes.md. The milestone plan (M1-M6, W, H, U) is retired (D161): the old plan is archived whole at docs/archive/plan-2026-11-11.md, and docs/later.md keeps the earlier milestones as history. Every refresh is on demand (D160): `uv run pharos refresh` (prices, FRED, Tiingo; seconds), `uv run pharos sec` (SEC; up to an hour, D159).
- **Health:** GREEN at 2026-11-11 evening (run 20261111T134544Z). C01 turns red about 11:37 ET 11-12 without a `uv run pharos refresh`: the freshness rule doing its job.
- **Last session:** 2026-11-11 (5): M3 dropped and the milestone plan retired (D161); Pipeline page built in Scaffold. Before that, (4): James chose trusted for the SEC group and asked for a lighter trusted bar; that bar is not decided.
- **Next action:** James reviews the Pipeline page (scaffold/pipeline.html, built by scaffold/pipeline.py: diagrams generated from the code, structure only, linked from the Catalog's Pipeline column).
- **Blockers / open questions:** #117 (native abort in a run). Full list in Open issues below and Parked issues in `docs/later.md`; cite only IDs that appear there.

## Open issues
<!-- A concern not fixed in the turn it comes up goes here only if it blocks current work or could lose or corrupt data (CLAUDE.md session protocol). IDs never change or get reused. Fixing an issue deletes its row in the same commit, and the commit message cites the ID; git history is the record. -->

| ID  | Issue                                                                                          | Effect if ignored                                                      | Resolves in        |
|-----|------------------------------------------------------------------------------------------------|------------------------------------------------------------------------|--------------------|
| 17  | Under dedup rule A a real source correction blocks publish forever (old raw file stays)        | One Yahoo correction freezes serving/; needs a deliberate accept path  | when C14 first fires on a price (build D23 with #25) |
| 24  | Accept path for a FRED same-key conflict (C14 on every FRED key)                               | A changed ALFRED vintage blocks publish forever                        | only if C14 fires  |
| 25  | Price vintage is a DATE: two full pulls of one ticker on one day share a vintage               | Same-day accept re-pull leaves the conflict in place                   | with #17 (D23 build) |
| 34  | A series added after the audit has no baseline row; C13 never compares it. Any re-baseline path built for this must also decide a size cap on isolated print disagreements: D41's shape rule passes one of any size, and C10 only catches moves over 40% (folded in from #37) | A watchlist addition can lose history with C13 green; a re-run audit could pass a large isolated bad print | before any further watchlist change; the five D141 series and W's 56 (D142) run without it |
| 117 | Nightly dies in native abort() (ucrtbase.dll, FAST_FAIL_FATAL_APP_EXIT) at different steps: 11-08 after publish, 11-10 SEC staging step 8, 11-11 Company Facts delta. Every step tested alone passes; cause unknown | Nothing publishes; health stays red; each failed night stacks more work on the next | abort hunt (scratch/abort-hunt.html) |


# Plan: Pharos

## Status
<!-- Claude Code updates this block at the end of every session. Keep it to these lines. -->
- **Direction:** Scaffold (scaffold/), James's pages for keeping up with Pharos: Catalog, Pipeline, Notes, Appendix (earlier analysis pages by topic; old Explore retired into it). Explore mode: clean up slowly, brainstorm before building. Trusted work parked (D164). Milestone plan retired (D161). Every refresh is on demand (D160): `uv run pharos refresh` (prices, FRED, Tiingo; seconds), `uv run pharos sec` (SEC; up to an hour, D159).
- **Health:** RED at 2026-11-12 15:08 ET: C01, nothing published for over 26 hours (expected with refresh on demand, D160). C34 retired with tickers (D165). Not run: James's call.
- **Last session:** 2026-11-12 (third): Scaffold Archive became Appendix; the old Explore pages listed there by topic, the pages Scaffold replaced left out, each old page's menu swapped for a link back. All 170 links on the shared copy crawled and every page screenshotted: two fixed (countries menu, Country key label). SEC tickers retired (D165): unread, kept 39 dropped tickers, 99.3% duplicated by submission_details. Shared link (https://claude.ai/artifact/SWwbfFj3BTJwrs3o7t6DeX) at version 5: rerun scaffold/share.py and republish to update.
- **Next action:** teardown of overbuilt items, one per call: #34, #117, then this queue. Queued after: health red-by-design vs CLAUDE.md step 1; Catalog shows Company Facts dates to 6016; #117 stale with the nightly off; Appendix scratch reference pages stale (How Pharos Runs, Plan map, Data map); scratch/ and notes.md clutter; Scaffold builds stop on mismatch (warn instead?).
- **Blockers / open questions:** Full list in Open issues below and Parked issues in `docs/later.md`; cite only IDs that appear there.

## Open issues
<!-- A concern not fixed in the turn it comes up goes here only if it blocks current work or could lose or corrupt data (CLAUDE.md session protocol). IDs never change or get reused. Fixing an issue deletes its row in the same commit, and the commit message cites the ID; git history is the record. -->

| ID  | Issue                                                                                          | Effect if ignored                                                      | Resolves in        |
|-----|------------------------------------------------------------------------------------------------|------------------------------------------------------------------------|--------------------|
| 34  | A series added after the audit has no baseline row; C13 never compares it. Any re-baseline path built for this must also decide a size cap on isolated print disagreements: D41's shape rule passes one of any size, and C10 only catches moves over 40% (folded in from #37) | A watchlist addition can lose history with C13 green; a re-run audit could pass a large isolated bad print | before any further watchlist change; the five D141 series and W's 56 (D142) run without it |
| 117 | Nightly dies in native abort() (ucrtbase.dll, FAST_FAIL_FATAL_APP_EXIT) at different steps: 11-08 after publish, 11-10 SEC staging step 8, 11-11 Company Facts delta. Every step tested alone passes; cause unknown | Nothing publishes; health stays red; each failed night stacks more work on the next | abort hunt (scratch/abort-hunt.html) |


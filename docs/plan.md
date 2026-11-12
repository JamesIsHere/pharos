# Plan: Pharos

## Status
<!-- Claude Code updates this block at the end of every session. Keep it to these lines. -->
- **Direction:** Scaffold (scaffold/), James's pages for keeping up with Pharos, is the current work; the trusted work is parked in docs/later.md (D164); resume from the top of scaffold/notes.md. The milestone plan (M1-M6, W, H, U) is retired (D161): the old plan is archived whole at docs/archive/plan-2026-11-11.md, and docs/later.md keeps the earlier milestones as history. Every refresh is on demand (D160): `uv run pharos refresh` (prices, FRED, Tiingo; seconds), `uv run pharos sec` (SEC; up to an hour, D159).
- **Health:** RED at 2026-11-12 ET: C01, nothing published for over 26 hours (last publish 11-11 09:37 ET), expected with refresh on demand (D160); C43 and C44 broken (no recorded result) until the next `uv run pharos sec`. Not run: James's call.
- **Last session:** 2026-11-12 (second): Trusted step 1 done (filing index gates C43, C44; D61's key kept, the archived page's "114 exact repeats" was wrong: they differ in name), then the trusted work parked (D164). Scaffold Pipeline page: click-to-drill panels for the SEC Load, Stage and gate-checks boxes (annotated real code, notes pinned by text). Scaffold shared as a private claude.ai link (https://claude.ai/artifact/SWwbfFj3BTJwrs3o7t6DeX) built by scaffold/share.py, Archive included; rerun the script and republish to update it.
- **Next action:** James: think of more data (new sources). Open: the remaining Pipeline panels (Publish / Keep the last good tables next, one shared panel or two: asked, not answered); whether the published link's diagrams, Pipeline panels and maps render (unverified).
- **Blockers / open questions:** Full list in Open issues below and Parked issues in `docs/later.md`; cite only IDs that appear there.

## Open issues
<!-- A concern not fixed in the turn it comes up goes here only if it blocks current work or could lose or corrupt data (CLAUDE.md session protocol). IDs never change or get reused. Fixing an issue deletes its row in the same commit, and the commit message cites the ID; git history is the record. -->

| ID  | Issue                                                                                          | Effect if ignored                                                      | Resolves in        |
|-----|------------------------------------------------------------------------------------------------|------------------------------------------------------------------------|--------------------|
| 17  | Under dedup rule A a real source correction blocks publish forever (old raw file stays)        | One Yahoo correction freezes serving/; needs a deliberate accept path  | when C14 first fires on a price (build D23 with #25) |
| 24  | Accept path for a FRED same-key conflict (C14 on every FRED key)                               | A changed ALFRED vintage blocks publish forever                        | only if C14 fires  |
| 25  | Price vintage is a DATE: two full pulls of one ticker on one day share a vintage               | Same-day accept re-pull leaves the conflict in place                   | with #17 (D23 build) |
| 34  | A series added after the audit has no baseline row; C13 never compares it. Any re-baseline path built for this must also decide a size cap on isolated print disagreements: D41's shape rule passes one of any size, and C10 only catches moves over 40% (folded in from #37) | A watchlist addition can lose history with C13 green; a re-run audit could pass a large isolated bad print | before any further watchlist change; the five D141 series and W's 56 (D142) run without it |
| 117 | Nightly dies in native abort() (ucrtbase.dll, FAST_FAIL_FATAL_APP_EXIT) at different steps: 11-08 after publish, 11-10 SEC staging step 8, 11-11 Company Facts delta. Every step tested alone passes; cause unknown | Nothing publishes; health stays red; each failed night stacks more work on the next | abort hunt (scratch/abort-hunt.html) |


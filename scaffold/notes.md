# Scaffold notes

Running list for the Scaffold effort: every decision and finding, good or bad. At the end we review it and decide
what becomes a skill, a rule, or a decision-log row. Newest entries go at the bottom of each section.

## Resume here

- Where: Scaffold (scaffold/) is James's set of pages for keeping up with Pharos. Built so far: Catalog (scaffold/catalog.html, the Data Sources list, definitions in scaffold/catalog.csv) and three source sheets: SEC Company Facts (scaffold/company-facts.html, open questions in scaffold/company-facts-questions.md), FRED / ALFRED (scaffold/fred.html, scaffold/fred-questions.md) and Prices (scaffold/prices.html, scaffold/prices-questions.md). Notes page: this file.
- Build: uv run python scaffold/build.py (Catalog), scaffold/company_facts.py (sheet; first build of a served version measures for about 9 min, then cached in data/tmp/scaffold/), scaffold/notes.py (Notes). Open in Chrome; check with a headless screenshot.
- Pace: one small step at a time, built with James on screen. Each change is a line in Decisions here, in the same step. No batches of edits, no extra sections unasked.
- Page rules: data before process; lists and tables, not paragraphs; one font and size (16px body, 13px headers, no monospace); no pills, a state is bold colored text; stamps top right in bold green; open questions first on a sheet.
- Cadence: heavy things run only when needed. SEC is on demand (D159, uv run pharos sec). Full test suite only on schedule or when James says "run the full test suite".
- Next: James looks at the Prices sheet (first cut 2026-11-11). Queued: FRED section 5 one-year-later column (asked, not answered). Then: the sheets' open questions, or back to M3 step 4 / #117.

## Decisions

- 2026-11-11: Scaffold is James's new set of pages for keeping up with Pharos. explore/ stays on disk untouched; we take what worked from it.
- 2026-11-11: Name: Scaffold. Also considered: Trailhead, Watch Room, Foundation, Gantry, Cornerstone, Headframe.
- 2026-11-11: Horizontal menu. First item: Catalog. Source sheets open from a Catalog row.
- 2026-11-11: The Catalog lists every source in any state, not only the ones we like. State is a column (model-inventory style).
- 2026-11-11: States (proposed, not yet confirmed by James): trusted, landed, reference, quick lane, trial, retired.
- 2026-11-11: Catalog definitions live in scaffold/catalog.csv (opens in Excel). Every number is measured from disk at build time. A name in the CSV that is not on disk stops the build.
- 2026-11-11: A source sheet has two halves: Data, then Pipeline. Pipeline is James's word (not plumbing).
- 2026-11-11: First sheet: SEC Company Facts (biggest, slowest nightly step, M3 depends on it).
- 2026-11-11: Sheet sections: 1 schema diagram, 2 tables, 3 columns, 4 joins, 5 shape over time, 6 oddities, 7 one company by hand, 8 pipeline.
- 2026-11-11: Heavy measurements are cached per served version in data/tmp/scaffold/; the first build of a version pays (9 min for Company Facts), rebuilds take seconds.
- 2026-11-11: Pages show data in lists and tables, not paragraphs. A data page opens with what was pulled: which reports, how many, rows per table, one example row.
- 2026-11-11: These notes: scaffold/notes.md, shown on the Notes page.
- 2026-11-11: Removed from Pharos CLAUDE.md: "Git history is the log; don't create separate log or state files." Not accurate (we keep notes, logs, archives); put back if needed.
- 2026-11-11: Catalog page: heading is Data Sources (menu link stays Catalog). Top: a plain list of sources with their state. Health status removed for now (GREEN didn't say what it covered while rows below weren't green); comes back later. Top right, always green: when the data was published and when the page was built. The detail table moves below, titled Details.
- 2026-11-11: Data Sources list: state as bold text, not a colored pill; a third column gives each source's one-line description (from catalog.csv's what column).
- 2026-11-11: No pills anywhere on the page. A state is bold text in its own color (trusted green, landed blue, reference gray, quick lane dark yellow, trial orange, retired light gray); the two stamps top right are bold green text.
- 2026-11-11: One global "Data published" stamp was misleading: sources are in different states and load at different times (Company Facts 02:30 ET, the rest 08:45 ET). Each source now has its own As of: stored = when its newest raw file was loaded; quick lane = when the oldest page that fetches it was built; trial = when its files were downloaded. Page built is the only stamp: when this page measured what it shows.
- 2026-11-11: Tiingo stays "reference" (the pipeline's own word in groups.py: loaded every run, never required). Plainer alternative if needed: cross-check.
- 2026-11-11: SEC is on demand until #117 is closed (D159): `uv run pharos sec` runs it; the nightly runs prices and FRED only. C25 drops to warn so aging SEC data shows as a warning, not red. Considered: SEC weekly, keep nightly.
- 2026-11-11: Full test suite runs only on a scheduled cadence or when James says "run the full test suite"; small targeted tests otherwise. No scheduled run exists today.
- 2026-11-11: Theme: cadence matches need. Heavy things run as often as their use requires, not every chance they get. SEC data: nightly -> on demand. Full test suite: after every change -> on schedule or on request. A large, growing suite is fine; running it constantly is not.
- 2026-11-11: Company Facts sheet opens with the population: facts, share and filings by form (top 10, the rest, total), right under the title. Schema next.
- 2026-11-11: Company Facts sheet lightened: the facts table under the population (what, state, served version, facts, companies, concepts, oddity count) removed; only Built stays, bold green, top right.
- 2026-11-11: One font per page body: the population table's Form column was monospace and smaller, and the schema diagram used Mermaid's own font; both now use the page font at 14px. Headings may differ.
- 2026-11-11: Schema diagram colors by table, and each table is labeled as fact or dimension: company_facts = fact (yellow); submission_details = dimension: company (light blue); filings = dimension: filing (green); company_concepts = dimension: concept (pink). The Tables section carries the same Type and color.
- 2026-11-11: Company Facts sheet opens with its open questions, one-line reminders from scaffold/company-facts-questions.md, above the population table.
- 2026-11-11: All Scaffold tables: 16px body text, 13px headers, no monospace anywhere. Columns section shows up to three example values per column instead of top values.
- 2026-11-11: Company Facts sheet: sections 6 (Oddities), 7 (One company: Apple) and Pipeline deleted; not needed for the catalog. Their findings stay in the open questions.
- 2026-11-11: Catalog page now uses the shared Scaffold style (scaffold/common.py: CSS, menu, esc/num/now_et); its own CSS is gone. Visible change: tables at 16px, headers 13px, Served as no longer monospace, Details heading underlined like the sheet's.
- 2026-11-11: Second source sheet: FRED / ALFRED (scaffold/fred.html, built by scaffold/fred.py, questions in scaffold/fred-questions.md), linked from the Catalog row. Same order as Company Facts; section 5 is Revisions (first print against latest, per series) instead of shape by year. No cache: 32k rows build in seconds. Distinct counts exact here (approx said 7 series for 8).
- 2026-11-11: FRED sheet: a vintages timeline right under the population table, one row per series on a shared year axis, a bar per year = vintages released that year (James: like the Company Facts bar charts).
- 2026-11-11: Third source sheet: Prices, Yahoo with Tiingo on one page (scaffold/prices.html, built by scaffold/prices.py, questions in scaffold/prices-questions.md); the Yahoo and Tiingo Catalog rows both link to it. Section 5 is Yahoo against Tiingo on every common date, through the pipeline's own transform/reconcile_all.sql (not a copy); 5b splits and 5c dividends, both sources. Rows-per-year timeline under the population, as on FRED.
- 2026-11-11: The SEC filing index, submissions and tickers Catalog rows link to the Company Facts sheet (James: Company Facts is the one page for all SEC data).

## Findings

- Explore: each page carries its own copy of the menu, written when it is built; 22 of 23 pages lacked Insiders because they were built before it was added.
- Explore: the seven Data Tests also exist as seven standalone pages (duplicates).
- Insiders (trial): 199,934 filings, 4 quarters, 8 tables, 1,658,416 rows. The first page read everything as text and never showed types; the schema step was missing.
- Insiders: SEC ships a schema file in each zip. 0 values fail their declared type. The declaration doesn't match the files: 4 contact columns declared but absent, 1 footnote column renamed. OWNER_SIGNATURE has 269 duplicate keys. The 10b5-1 flag mixes 0/1, true/false and blank.
- Company Facts: 126,588,444 facts, 0 duplicate keys, 0 facts without a company.
- Company Facts: 92 facts dated after 2100 (typo'd years such as 6016); 6,754 end more than a year after their filing; 38 negative total assets; start dates as early as 0202; fiscal year up to 29,953,000.
- Company Facts: 6 filings not in the filing index; 1,710 company concepts with no facts.
- Company Facts: in 2026, 424B2 bank prospectuses (38,656 filings) and proxy statements (2,014) carry XBRL and appear in Company Facts. Any count of filings or companies needs a form filter.
- Company Facts, filings by form 2019-2026: new in 2026 are 424B2 (38,656), DEF 14A (2,014), S-8 (1,194), and other forms (6,023, against 51-97 a year before). 6-K fell from 666 in 2025 to 124 in 2026. 10-K and 10-Q are steady.
- Sheet build: the first Company Facts build took 9 min against an estimate of 4; the eight oddity checks each read the whole table.
- Company Facts is not just 10-Ks: 10-Q 61.6% of facts, 10-K 30.1%, then 20-F, amendments, 6-K, 8-K, 40-F; 479,983 filings over 99 forms. M3 filters to 10-K annual facts; the store holds every form. Taxonomies: us-gaap 123.2M, ifrs-full 2.6M, dei 0.5M, plus fee (ffd) and pay (ecd) tables.
- Join trap: filings (the EDGAR index) lists a filing once per party: 27,400,901 rows, 18,873,816 distinct accessions, 27,399,372 distinct (cik, accession). Of Company Facts' 480,333 (cik, accession) pairs, 29,647 (6%) have an accession listed more than once. Joining facts to filings on accession alone duplicates those facts; join on (cik, accession). Schema diagram now labels that join cik, accession.
- FRED: 8 series, 32,483 served rows; raw re-lands the full history each run (724,697 rows over 52 runs). Raw newest run and served match both ways (0 unmatched).
- FRED: units change across vintages: real GDP rebased 7 times since 1991 (1987 dollars to chained 2017); CPI from 1967=100 to 1982-84=100 in Feb 1988 (186 periods). Any first-to-latest comparison must hold units equal.
- FRED: first print to latest, median absolute: GDP 4.43% (largest +10.21%, 1992 Q4), COE 1.55%, real GDP 0.53%, CPI 0.02%. Over decades this mixes routine revisions with benchmark revisions.
- Template test: Tables, Columns and Joins ported with small changes (no files or GB columns; joins are source-specific). Section 5 had to be new.
- Prices: ATVI is on the watchlist and in series_catalog but Yahoo has no closes for it; Tiingo has 4,728 rows through 2023-10-13.
- Prices: Yahoo against Tiingo, 85,535 common dates: median difference about 2e-8 (float32 rounding); 38 dates over 0.5%, 2005-2014, all 38 isolated: the same count D41 found on 2026-10-07.
- Prices: splits agree on every date both sources have; one-source-only: ATVI (3, Tiingo) and GOOGL 2014-04-03 (Yahoo's 1.998 split = Tiingo's dividend, D40). Dividends agree date for date except ATVI (14) and that GOOGL date.
- Prices: serving carries the split-adjusted close only; splits and dividends live in raw. Tiingo re-lands full history each run (3.96M rows, 241 MB, 44 runs).

## Open

- Commit: explore/insiders, the explore menu line, and scaffold/ are all uncommitted.
- Confirm the six states.
- Catalog "where" column (which pages use a stored source) is blank for stored sources: lineage.
- Company Facts Pipeline section: the timing lines inside each pass arrive with the first `pharos sec` run (no longer tonight's nightly, D159).
- Promotion checklist: what James needs to see before saying "comfortable", drawn from the Company Facts sheet.
- Shared intake code (explore/lib/intake.py or scaffold equivalent) when the second trial source arrives.
- End-of-effort review: what we learned -> a skill, rules, or decision-log rows.
- Next step, James to pick ((a) shared style on the Catalog: done 2026-11-11): (b) a second source sheet with the same sections, which shows whether the sheet works as a template; (c) work the Company Facts open questions; (d) back to the plan's milestone (M3 step 4, #117).

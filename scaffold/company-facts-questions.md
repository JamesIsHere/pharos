# Company Facts: open questions

- 2026: 424B2 (38,656), DEF 14A (2,014), S-8 (1,194) appear; 6-K falls from 666 to 124. Why?
- Fiscal year (fy) up to 29,953,000. Junk field: ignore fy?
- 38 facts with negative total assets; val ranges from -305,780,046,000,000 to 1e+22.
- 6 filings not in the filing index.
- 1,710 company concepts with no facts.
- submission_details: 992,426 filers vs about 18,287 companies with facts. Fine as a dimension?
- company_concepts: latest label only, not a history of labels?

## Settled

- Dates: 92 facts end after 2100; 6,754 end over a year after filing; start dates as early as 0202. Left as they are (James, 2026-11-12): filer typos, faithful to SEC; the Catalog range stays raw (1927 to 6016).
- filings lists a filing once per party: join on (cik, accession), never accession alone.
- Served values tie to the filed documents: five figures checked on EDGAR on 2026-11-13 (Pfizer's 2018 and 2020 annual reports, Johnson & Johnson's 2021 and 2023, Pernix's 2013 Q1), all five matched. Apple, the company first named for this check, was not one of them.
- The Catalog line said "every XBRL financial fact". Reworded 2026-11-13: 97.3% of facts are US GAAP financial tags, the rest IFRS, cover-page, fee and pay tags.
- Timing a nightly Company Facts pass: dropped. SEC loads are on demand, by `pharos sec` (D159).

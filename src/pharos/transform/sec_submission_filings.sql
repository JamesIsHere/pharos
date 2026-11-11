-- sec_submission_filings: raw submissions.zip filing history -> one staging row per raw row.
--
-- Inputs (bound by stage_sec):
--   raw_sec_submission_filings  cik, accession_number, filing_date, report_date,
--                               acceptance_date_time, act, form, file_number, film_number,
--                               items, core_type, size, is_xbrl, is_inline_xbrl,
--                               is_xbrl_numeric, primary_document, primary_doc_description,
--                               source_file, run_id, loaded_at  (values as sent, D122)
--   xnys_sessions               session DATE, every XNYS trading day from 1980
--
-- One output row per raw row: no dedup here. stage_sec first collapses the
-- copies of one filing inside one landing (D126), then merges landings
-- (D132).
--
-- Casts: the dates are 'YYYY-MM-DD' text and acceptance is
-- 'YYYY-MM-DDTHH:MM:SS.000Z', UTC (measured 2026-11-05: same-day filings
-- peak at 20-21Z, the New York close). A value off those shapes fails the
-- CAST, loudly. An empty report_date is NULL (9,235,574 rows on 2026-11-04).
-- Text fields stay as sent.
--
-- available_date: the next XNYS trading day after the filing date (D99);
-- acceptance is stored, not used for it. obs_date and vintage: the filing date.
-- quarter: the filing date's quarter, 'YYYYQn' as in the full index; the table
-- is staged one folder per quarter (D156). stage_sec.quarter_of is the same
-- expression, for a base published before the column existed.

SELECT
    r.cik,
    r.accession_number                                          AS accession,
    CAST(r.filing_date AS DATE)                                 AS filing_date,
    CAST(NULLIF(r.report_date, '') AS DATE)                     AS report_date,
    CAST(r.acceptance_date_time AS TIMESTAMPTZ)                 AS acceptance,
    r.act,
    r.form,
    r.file_number,
    r.film_number,
    r.items,
    r.core_type,
    r.size,
    r.is_xbrl,
    r.is_inline_xbrl,
    r.is_xbrl_numeric,
    r.primary_document,
    r.primary_doc_description,
    CAST(r.filing_date AS DATE)                                 AS obs_date,
    s.session                                                   AS available_date,
    CAST(r.filing_date AS DATE)                                 AS vintage,
    CAST(year(CAST(r.filing_date AS DATE)) AS VARCHAR) || 'Q'
        || CAST(quarter(CAST(r.filing_date AS DATE)) AS VARCHAR) AS quarter,
    r.source_file,
    r.run_id,
    r.loaded_at
FROM raw_sec_submission_filings AS r
ASOF LEFT JOIN xnys_sessions AS s
       ON s.session > CAST(r.filing_date AS DATE)

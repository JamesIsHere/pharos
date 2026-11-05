-- sec_filings: raw EDGAR full-index rows -> one staging row per raw row.
--
-- Inputs (bound by stage_sec):
--   raw_sec_full_index  cik, company_name, form_type, date_filed, filename, quarter,
--                       last_modified, run_id, loaded_at  (every landing, D84)
--   xnys_sessions       session DATE, every XNYS trading day from 1993
--
-- One output row per raw row: no dedup here. A quarter re-landed nightly comes
-- out once per landing; stage_sec collapses landings (D84, D85).
--
-- accession: the filename's last part, 'edgar/data/<cik>/<accession>.txt'.
-- Measured on the spike index (2026-11-04): every one of 27,374,700 filenames
-- has this shape and its path CIK equals the row's CIK. A row that breaks
-- either comes out with accession '' or path_cik <> cik, and stage_sec stops.
--
-- available_date: the next XNYS trading day after the filing date (D99).
-- obs_date and vintage: the filing date (design.md section 3).

SELECT
    r.cik,
    regexp_extract(r.filename, '^edgar/data/[0-9]+/([0-9]{10}-[0-9]{2}-[0-9]{6})\.txt$', 1) AS accession,
    r.form_type                                                       AS form,
    r.company_name                                                    AS name,
    r.date_filed,
    r.filename,
    r.quarter,
    r.date_filed                                                      AS obs_date,
    s.session                                                         AS available_date,
    r.date_filed                                                      AS vintage,
    TRY_CAST(regexp_extract(r.filename, '^edgar/data/([0-9]+)/', 1) AS BIGINT) AS path_cik,
    r.last_modified,
    r.run_id,
    r.loaded_at
FROM raw_sec_full_index AS r
ASOF LEFT JOIN xnys_sessions AS s
       ON s.session > r.date_filed

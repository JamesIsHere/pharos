-- sec_companyfacts: raw companyfacts.zip facts -> one staging row per raw row.
--
-- Inputs (bound by stage_sec):
--   raw_sec_companyfacts  cik, taxonomy, concept, unit, start, end, val, accn, fy, fp,
--                         form, filed, frame, run_id, loaded_at  (values as sent)
--   xnys_sessions         session DATE, every XNYS trading day from 1980
--
-- One output row per raw row: no dedup here; stage_sec merges landings (D132).
--
-- Casts: start, end and filed are 'YYYY-MM-DD' text (every one on 2026-11-04),
-- renamed start_date / end_date; a value off that shape fails the CAST,
-- loudly. val is text as sent (JSON ints and floats); val is its DOUBLE and
-- val_sent keeps the text, since 46 ints on 2026-11-04 are beyond 2^53,
-- where the DOUBLE differs. fy, fp and frame stay as sent (fp is NULL or ''
-- on about 834k rows).
--
-- obs_date: the period end. vintage: the filed date. available_date: the next
-- XNYS trading day after the filed date, as for filings (D99). Defaulted at
-- the landed level (D127), not signed off. It is looked up once per distinct
-- filed date (a few thousand), then joined: an ASOF join over every fact
-- sorts all 126M rows.

WITH next_session AS (
    SELECT d.filed, s.session
    FROM (SELECT DISTINCT CAST(filed AS DATE) AS filed FROM raw_sec_companyfacts) AS d
    ASOF LEFT JOIN xnys_sessions AS s
           ON s.session > d.filed
)
SELECT
    r.cik,
    r.taxonomy,
    r.concept,
    r.unit,
    CAST(r."start" AS DATE)                        AS start_date,
    CAST(r."end" AS DATE)                          AS end_date,
    CAST(r.val AS DOUBLE)                          AS val,
    r.val                                          AS val_sent,
    r.accn                                         AS accession,
    r.fy,
    r.fp,
    r.form,
    CAST(r.filed AS DATE)                          AS filed,
    r.frame,
    CAST(r."end" AS DATE)                          AS obs_date,
    s.session                                      AS available_date,
    CAST(r.filed AS DATE)                          AS vintage,
    r.run_id,
    r.loaded_at
FROM raw_sec_companyfacts AS r
LEFT JOIN next_session AS s
       ON s.filed = CAST(r.filed AS DATE)

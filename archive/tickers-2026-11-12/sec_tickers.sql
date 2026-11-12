-- sec_tickers: raw company_tickers_exchange.json rows -> one staging row per raw row.
--
-- Inputs (bound by stage_sec):
--   raw_sec_tickers  cik, name, ticker, exchange (values as sent), run_id, loaded_at
--
-- One output row per raw row: no dedup here; stage_sec merges landings (D132).
-- Key ticker: measured unique in a landing (10,409 rows, 8,004 CIKs, 2026-11-05).
--
-- obs_date, available_date and vintage: the run's UTC date, as for
-- sec_submission_details (a snapshot with no date of its own); lineage to
-- stage_sec. Defaulted at the landed level (D127), not signed off.

SELECT
    r.cik,
    r.ticker,
    r.name,
    r.exchange,
    CAST(strptime(r.run_id, '%Y%m%dT%H%M%SZ') AS DATE)  AS obs_date,
    CAST(strptime(r.run_id, '%Y%m%dT%H%M%SZ') AS DATE)  AS available_date,
    CAST(strptime(r.run_id, '%Y%m%dT%H%M%SZ') AS DATE)  AS vintage,
    r.run_id,
    r.loaded_at
FROM raw_sec_tickers AS r

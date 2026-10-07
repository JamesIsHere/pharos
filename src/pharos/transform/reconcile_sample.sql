-- reconcile_sample: C12's nightly draw from reconcile_all (D38, D41).
--
-- Inputs (names the check runner binds):
--   reconcile_all  every common date, Yahoo vs Tiingo (the comparison itself)
--   this_run       run_id, the sampling seed
--
-- Sample per ticker, among dates both sources have: 5 dates ranked by
-- hash(run_id, ticker, date), so each run draws fresh dates and any run's draw
-- can be reproduced from its run_id alone, plus the latest common date always.

WITH ranked AS (
    SELECT b.*,
           row_number() OVER (PARTITION BY b.ticker
                              ORDER BY hash(r.run_id || '|' || b.ticker || '|' || CAST(b.obs_date AS VARCHAR))) AS draw,
           b.obs_date = max(b.obs_date) OVER (PARTITION BY b.ticker) AS is_latest
    FROM reconcile_all AS b, this_run AS r
)
SELECT ticker, obs_date, yahoo_close, tiingo_close, tiingo_raw_close, rel_diff,
       -- a drawn date stays 'random' even when it is also the latest
       CASE WHEN draw <= 5 THEN 'random' ELSE 'latest' END AS pick
FROM ranked
WHERE draw <= 5 OR is_latest

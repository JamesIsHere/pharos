-- reconcile_sample: Yahoo vs Tiingo split-adjusted closes on sampled dates (D38).
--
-- Inputs (names the check runner binds):
--   observations       staged or published observations (Yahoo: yf:close:<ticker>)
--   raw_tiingo_prices  this run's Tiingo snapshot (D36)
--   this_run           run_id, the sampling seed
--   reclassified       (ticker, event_date) from config/corporate_actions.csv (D40)
--
-- Used by C12 (nightly, rows over tolerance) and the opening-balance audit
-- (every sampled row, recorded). One file, so the two can't drift apart.
--
-- Yahoo: each price series' current vintage, withdrawn values skipped.
-- Tiingo: split-adjusted close derived from Tiingo alone (D35): raw close
-- divided by the product of Tiingo's split factors on later dates. A factor
-- applies from its own date onward, so a date's own factor doesn't adjust it.
-- A reviewed distribution (D40) becomes a factor from Tiingo's own numbers:
-- previous close / (previous close - div_cash), so the basis stays Tiingo's.
--
-- Sample per ticker, among dates both sources have: 5 dates ranked by
-- hash(run_id, ticker, date), so each run draws fresh dates and any run's draw
-- can be reproduced from its run_id alone, plus the latest common date always.

WITH yahoo AS (
    SELECT split_part(series_id, ':', 3) AS ticker, obs_date, value AS yahoo_close
    FROM (
        SELECT *
        FROM observations
        WHERE series_id LIKE 'yf:close:%'
        QUALIFY vintage = max(vintage) OVER (PARTITION BY series_id)
    )
    WHERE value IS NOT NULL
),
factors AS (
    SELECT p.ticker, p.obs_date, p.close,
           CASE WHEN r.ticker IS NOT NULL
                THEN lag(p.close) OVER w / (lag(p.close) OVER w - p.div_cash)
                ELSE p.split_factor END AS factor
    FROM raw_tiingo_prices AS p
    LEFT JOIN reclassified AS r ON r.ticker = p.ticker AND r.event_date = p.obs_date
    WINDOW w AS (PARTITION BY p.ticker ORDER BY p.obs_date)
),
tiingo AS (
    SELECT ticker, obs_date, close AS tiingo_raw_close,
           close / coalesce(exp(sum(ln(factor)) OVER (
               PARTITION BY ticker ORDER BY obs_date
               ROWS BETWEEN 1 FOLLOWING AND UNBOUNDED FOLLOWING)), 1.0) AS tiingo_close
    FROM factors
),
both_sources AS (
    SELECT y.ticker, y.obs_date, y.yahoo_close, t.tiingo_close, t.tiingo_raw_close
    FROM yahoo AS y
    JOIN tiingo AS t USING (ticker, obs_date)
),
ranked AS (
    SELECT b.*,
           row_number() OVER (PARTITION BY b.ticker
                              ORDER BY hash(r.run_id || '|' || b.ticker || '|' || CAST(b.obs_date AS VARCHAR))) AS draw,
           b.obs_date = max(b.obs_date) OVER (PARTITION BY b.ticker) AS is_latest
    FROM both_sources AS b, this_run AS r
)
SELECT ticker, obs_date, yahoo_close, tiingo_close, tiingo_raw_close,
       yahoo_close / tiingo_close - 1 AS rel_diff,
       -- a drawn date stays 'random' even when it is also the latest, so a
       -- ticker always shows its 5 random dates (the audit counts them)
       CASE WHEN draw <= 5 THEN 'random' ELSE 'latest' END AS pick
FROM ranked
WHERE draw <= 5 OR is_latest

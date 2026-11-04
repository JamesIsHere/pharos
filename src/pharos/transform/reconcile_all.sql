-- reconcile_all: Yahoo vs Tiingo split-adjusted close on every common date (D41).
--
-- Inputs (names the check runner binds):
--   observations       staged or published observations (Yahoo: yf:close:<ticker>)
--   raw_tiingo_prices  this run's Tiingo snapshot (D36)
--   reclassified       (ticker, event_date) from config/corporate_actions.csv (D40)
--
-- The one definition of the comparison: reconcile_sample (C12's nightly draw)
-- selects from it, and the opening-balance audit reads all of it.
--
-- Yahoo: each price series' current vintage, withdrawn values skipped.
-- Tiingo: split-adjusted close derived from Tiingo alone (D35): raw close
-- divided by the product of Tiingo's split factors on later dates. A factor
-- applies from its own date onward, so a date's own factor doesn't adjust it.
-- A reviewed distribution (D40) becomes a factor from Tiingo's own numbers:
-- previous close / (previous close - div_cash), so the basis stays Tiingo's.
-- A NULL factor (Tiingo sent none, or a distribution on a ticker's first date,
-- with no previous close) leaves every earlier date's tiingo_close NULL: sum()
-- would skip it and read the factor as 1, a guess shown as agreement (#70).

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
           CASE WHEN count(*) OVER later = count(factor) OVER later
                THEN close / coalesce(exp(sum(ln(factor)) OVER later), 1.0) END AS tiingo_close
    FROM factors
    WINDOW later AS (PARTITION BY ticker ORDER BY obs_date
                     ROWS BETWEEN 1 FOLLOWING AND UNBOUNDED FOLLOWING)
),
both_sources AS (
    SELECT y.ticker, y.obs_date, y.yahoo_close, t.tiingo_close, t.tiingo_raw_close
    FROM yahoo AS y
    JOIN tiingo AS t USING (ticker, obs_date)
)
SELECT ticker, obs_date, yahoo_close, tiingo_close, tiingo_raw_close,
       yahoo_close / tiingo_close - 1 AS rel_diff
FROM both_sources

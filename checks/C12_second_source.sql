-- id: C12
-- severity: warn
-- gate: no
-- description: sampled split-adjusted closes differ from Tiingo by more than 0.5% (D38), or a reviewed reclassification matches no Tiingo distribution (D40)
--
-- The sample (5 seeded dates per ticker plus the latest common date) and the
-- Tiingo split adjustment are defined once in transform/reconcile_sample.sql,
-- shared with the opening-balance audit. 0.5% is design.md's tolerance; Yahoo's
-- float32 precision (~1e-7 relative) is far inside it (open issue #14 closed).
-- Split-adjusted close only: dividend adjustment differs by source.
-- A monitor: a disagreement is a question about the world, never a block.

SELECT ticker, obs_date, pick, yahoo_close, tiingo_close, tiingo_raw_close, round(rel_diff, 6) AS rel_diff,
       'over tolerance' AS problem
FROM reconcile_sample
WHERE abs(rel_diff) > 0.005

UNION ALL

-- both sources have the ticker but share no date (e.g. a shifted date mapping):
-- the sample would be empty and C12 would pass comparing nothing
SELECT t.ticker, NULL, NULL, NULL, NULL, NULL, NULL, 'no common dates'
FROM (SELECT DISTINCT ticker FROM raw_tiingo_prices) AS t
WHERE EXISTS (SELECT 1 FROM observations WHERE series_id = 'yf:close:' || t.ticker AND value IS NOT NULL)
  AND NOT EXISTS (SELECT 1 FROM reconcile_sample AS s WHERE s.ticker = t.ticker)

UNION ALL

-- a reviewed reclassification (config/corporate_actions.csv, D40) that matches
-- no Tiingo distribution: the listing is stale or mistyped and adjusts nothing
SELECT r.ticker, r.event_date, NULL, NULL, NULL, NULL, NULL, 'reclassified distribution not in Tiingo'
FROM reclassified AS r
WHERE EXISTS (SELECT 1 FROM raw_tiingo_prices AS t WHERE t.ticker = r.ticker)
  AND NOT EXISTS (SELECT 1 FROM raw_tiingo_prices AS t
                  WHERE t.ticker = r.ticker AND t.obs_date = r.event_date AND t.div_cash > 0)

ORDER BY ticker, obs_date

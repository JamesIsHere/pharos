-- baseline_metrics: per series first_date, last_date, row_count (D39).
--
-- Inputs (names the check runner binds): observations, series_catalog.
--
-- Used by the opening-balance audit (recorded once as the baseline) and C13
-- (measured every run, compared with that record). One file, so the two can't
-- drift apart.
--
-- Which rows count is C04's rule:
-- - Prices: the series' current vintage only. Older vintages stay in serving,
--   so counting them would hide a re-pull that lost early history.
-- - FRED: any vintage, one per obs_date (revisions add vintages, not dates).
-- A withdrawn value (NULL) is a row: the source answered (D25).

WITH counted AS (
    SELECT o.series_id, o.obs_date
    FROM observations AS o
    JOIN series_catalog AS c USING (series_id)
    WHERE c.source <> 'yf'
       OR o.vintage = (SELECT max(vintage) FROM observations AS v WHERE v.series_id = o.series_id)
)
SELECT series_id, min(obs_date) AS first_date, max(obs_date) AS last_date,
       count(DISTINCT obs_date) AS row_count
FROM counted
GROUP BY series_id

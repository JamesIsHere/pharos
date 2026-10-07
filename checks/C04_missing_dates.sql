-- id: C04
-- severity: error
-- gate: yes
-- description: expected date with no row inside the series' window (trading days, months, quarters)
--
-- Window: active_from to the earlier of active_to and the series' last
-- observation. Ending at the last observation means C04 finds holes; a series
-- that is merely late is C02's to report, never both.
--
-- Which rows count:
-- - Prices: the current vintage only (the latest full pull, D20/D23). A full
--   re-pull that lacks a date must block here, or serving's latest-vintage view
--   would fill the date from an older adjustment basis.
-- - FRED: a row in any vintage. A withdrawn value (NULL) is a row: the source
--   answered "no value", which C16 reports (D25).
--
-- Expected dates: XNYS sessions for daily series (D24), first-of-month for
-- monthly, every third month from active_from for quarterly.

WITH counted AS (
    SELECT o.series_id, o.obs_date
    FROM observations AS o
    JOIN series_catalog AS c USING (series_id)
    WHERE c.source <> 'yf'
       OR o.vintage = (SELECT max(vintage) FROM observations AS v WHERE v.series_id = o.series_id)
),
windows AS (
    SELECT c.series_id, c.frequency, c.active_from,
           least(coalesce(c.active_to, DATE '9999-12-31'), max(k.obs_date)) AS window_end
    FROM series_catalog AS c
    JOIN counted AS k USING (series_id)
    GROUP BY c.series_id, c.frequency, c.active_from, c.active_to
),
expected AS (
    SELECT w.series_id, t.session AS obs_date
    FROM windows AS w
    JOIN trading_days AS t ON t.session BETWEEN w.active_from AND w.window_end
    WHERE w.frequency = 'D'

    UNION ALL

    SELECT w.series_id, CAST(g.d AS DATE)
    FROM windows AS w,
         generate_series(CAST(w.active_from AS TIMESTAMP), CAST(w.window_end AS TIMESTAMP),
                         CASE w.frequency WHEN 'M' THEN INTERVAL 1 MONTH ELSE INTERVAL 3 MONTH END) AS g(d)
    WHERE w.frequency IN ('M', 'Q')
)
SELECT e.series_id, e.obs_date
FROM expected AS e
WHERE NOT EXISTS (
    SELECT 1 FROM counted AS k WHERE k.series_id = e.series_id AND k.obs_date = e.obs_date
)
ORDER BY e.series_id, e.obs_date

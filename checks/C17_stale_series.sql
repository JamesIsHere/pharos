-- id: C17
-- severity: error
-- gate: no
-- description: active series 2 or more periods behind its expected latest period: stale (D33)
--
-- C02 (warn, 1 period behind) and C17 (error, 2 or more) are one rule split by
-- severity, because a check file declares one severity (D33, open issue #11
-- closed). The CTEs are identical in both files; change them together.
--
-- A period is due once its end plus the series' expected_lag_days (sources.yaml)
-- is before the clock's UTC date: the run's own time, or the wall clock at view
-- time. Prices count XNYS sessions (lag 0: a session is due from the next day).
-- FRED counts months or quarters; obs_date is the period start. Lateness against
-- the normal lag is the signal, so a slipped release (the 2025 shutdown) shows
-- as late: that is the data being late, not a misfire (D33).
--
-- Scope: active series (active_to NULL or on/after the clock date) with at least
-- one row; a series with none is C11's or C15's. A withdrawn value (NULL) is a
-- row (D25). A monitor: it tests the world, never blocks (D17).

WITH today AS (
    SELECT CAST(timezone('UTC', now) AS DATE) AS d FROM clock
),
latest AS (
    SELECT c.series_id, c.source, c.frequency, c.expected_lag_days, max(o.obs_date) AS latest_obs_date
    FROM series_catalog AS c
    JOIN observations AS o USING (series_id)
    CROSS JOIN today AS t
    WHERE c.active_to IS NULL OR c.active_to >= t.d
    GROUP BY ALL
),
behind AS (
    SELECT l.*,
           (SELECT max(session) FROM trading_days, today
            WHERE session + l.expected_lag_days < today.d) AS expected_obs_date,
           (SELECT count(*) FROM trading_days, today
            WHERE session > l.latest_obs_date
              AND session + l.expected_lag_days < today.d) AS periods_behind
    FROM latest AS l
    WHERE l.source = 'yf'

    UNION ALL

    SELECT l.*, e.expected_obs_date,
           -- greatest() skips NULL, so an uncountable frequency must stay NULL explicitly
           CASE WHEN e.expected_obs_date IS NOT NULL THEN
               greatest(datediff('month', l.latest_obs_date, e.expected_obs_date)
                        // CASE l.frequency WHEN 'Q' THEN 3 ELSE 1 END, 0)
           END
    FROM latest AS l
    CROSS JOIN today AS t
    CROSS JOIN LATERAL (
        -- latest period start P with P_end + lag < today, i.e. P + 1 period <= today - lag
        SELECT CASE l.frequency
                   WHEN 'Q' THEN CAST(date_trunc('quarter', t.d - l.expected_lag_days) - INTERVAL 3 MONTH AS DATE)
                   WHEN 'M' THEN CAST(date_trunc('month', t.d - l.expected_lag_days) - INTERVAL 1 MONTH AS DATE)
               END AS expected_obs_date
    ) AS e
    WHERE l.source = 'fred'
)
SELECT series_id, frequency, expected_lag_days, latest_obs_date, expected_obs_date, periods_behind
FROM behind
WHERE periods_behind >= 2
   OR periods_behind IS NULL   -- a frequency this rule can't count (not D/M/Q): fail loud, never pass silently
ORDER BY series_id

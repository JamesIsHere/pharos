-- id: C10
-- severity: warn
-- gate: yes
-- description: abs(daily return) > 40% on split-adjusted close, current vintage (D29)
--
-- Return = close / previous close - 1, between consecutive observations of each
-- price series' current vintage (the vintage serving shows, as in C04 and C14).
-- Withdrawn values (NULL, D25) are skipped, so a return spans the gap.
--
-- No date is excluded (D29). On split-adjusted history a split date shows no
-- jump, so a jump on a split date is the signature of an unadjusted split: the
-- failure this check exists to catch. Events Yahoo reports as fractional splits
-- (GOOGL 2014-04-03 1.998, O 2021-11-15 1.032) re-base history like any split.
-- A real move this large (a crash, a squeeze) warns, never blocks.

WITH current_vintage AS (
    -- pick the vintage before dropping NULLs, so a withdrawn row can't shift it
    SELECT series_id, obs_date, value
    FROM (
        SELECT *
        FROM observations
        WHERE split_part(series_id, ':', 1) = 'yf'
        QUALIFY vintage = max(vintage) OVER (PARTITION BY series_id)
    )
    WHERE value IS NOT NULL
),
returns AS (
    SELECT series_id, obs_date, value,
           lag(obs_date) OVER w AS prev_date,
           lag(value)    OVER w AS prev_value
    FROM current_vintage
    WINDOW w AS (PARTITION BY series_id ORDER BY obs_date)
)
SELECT series_id, prev_date, obs_date, prev_value, value,
       round(value / prev_value - 1, 4) AS daily_return
FROM returns
WHERE abs(value / prev_value - 1) > 0.40
ORDER BY series_id, obs_date

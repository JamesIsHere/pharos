-- id: C14
-- severity: error
-- gate: yes
-- description: same (series_id, obs_date, vintage) with different values across raw snapshots: silent source correction (D23)
--
-- Staging keeps every distinct value a key arrived with (D18), so a key with
-- more than one value means the source changed its story without a new
-- vintage. A withdrawn value (NULL, D25) against a number is a change too.
--
-- Prices: each series' current vintage only. A correction is accepted by a
-- deliberate full re-pull, which starts a new vintage and leaves the conflict
-- readable in the superseded one (D23). FRED: every key, because an ALFRED
-- vintage should be immutable (open issue #24).

WITH scoped AS (
    SELECT *
    FROM observations
    WHERE split_part(series_id, ':', 1) <> 'yf'

    UNION ALL

    SELECT *
    FROM observations
    WHERE split_part(series_id, ':', 1) = 'yf'
    QUALIFY vintage = max(vintage) OVER (PARTITION BY series_id)
)
SELECT series_id, obs_date, vintage,
       count(*)              AS n_values,
       min(value)            AS min_value,
       max(value)            AS max_value,
       bool_or(value IS NULL) AS has_withdrawn,
       min(run_id)           AS first_run_id,
       max(run_id)           AS last_run_id
FROM scoped
GROUP BY series_id, obs_date, vintage
HAVING min(value) IS DISTINCT FROM max(value)
    OR (bool_or(value IS NULL) AND bool_or(value IS NOT NULL))
ORDER BY series_id, obs_date, vintage

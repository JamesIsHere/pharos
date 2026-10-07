-- id: C09
-- severity: error
-- gate: yes
-- description: impossible observation: price <= 0, obs_date after the run date, or obs_date after available_date
--
-- One row per observation per rule broken, so a row breaking two rules shows twice.
--
-- price <= 0 applies to price series only (source prefix of series_id, so an
-- observation with no catalog row is still checked; that gap is C11's). Macro
-- values can be negative. A withdrawn value (NULL, D25) is not a price.
--
-- "Future" is an obs_date after the run's UTC date. The nightly run lands after
-- the NYSE close, so the day's own trade date is never in the future.
--
-- available_date is when a value became knowable, so it can't precede the date
-- it describes: prices use the trade date itself, FRED the vintage release date
-- against the period-start obs_date.

SELECT o.series_id, o.obs_date, o.vintage, o.value, o.available_date,
       'price <= 0' AS rule
FROM observations AS o
WHERE split_part(o.series_id, ':', 1) = 'yf'
  AND o.value <= 0

UNION ALL

SELECT o.series_id, o.obs_date, o.vintage, o.value, o.available_date,
       'obs_date after run date' AS rule
FROM observations AS o, this_run AS r
WHERE o.obs_date > CAST(timezone('UTC', r.run_at) AS DATE)

UNION ALL

SELECT o.series_id, o.obs_date, o.vintage, o.value, o.available_date,
       'obs_date after available_date' AS rule
FROM observations AS o
WHERE o.obs_date > o.available_date

ORDER BY series_id, obs_date, vintage, rule

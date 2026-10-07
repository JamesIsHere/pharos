-- fred_observations: raw FRED vintages -> staging observation rows.
--
-- Inputs (views the runner binds over raw/ Parquet, every run file):
--   raw_fred_observations  fred_id, obs_date, realtime_start, realtime_end, value (text), run_id, loaded_at
--   raw_fred_series        fred_id, realtime_start, realtime_end, units, ... (metadata history)
--
-- One output row per raw row: no dedup here (D18). The same vintage seen in
-- several runs comes out several times; the runner deduplicates on
-- (series_id, obs_date, vintage) and C14 catches same-key value conflicts.
--
-- value: FRED "." -> NULL. A NULL is the vintage withdrawing the value
-- (GDP 1946Q1: 199.7 in 1992, "." in 1996, 210.4 in 1997, "." in 1999), so it
-- is kept. Any other non-numeric text fails the CAST, loudly.
--
-- units: from the metadata window containing the vintage (D19). The runner
-- asserts the join is exactly one window per row: row count in = row count out,
-- and no NULL units.

WITH units_windows AS (
    -- the same window repeats once per run; collapse to one row per window
    SELECT DISTINCT fred_id, realtime_start, realtime_end, units
    FROM raw_fred_series
)
SELECT
    'fred:' || o.fred_id                                        AS series_id,
    o.obs_date,
    CASE WHEN o.value = '.' THEN NULL
         ELSE CAST(o.value AS DOUBLE) END                       AS value,
    u.units,
    o.realtime_start                                            AS available_date,
    o.realtime_start                                            AS vintage,
    o.run_id,
    o.loaded_at
FROM raw_fred_observations AS o
LEFT JOIN units_windows AS u
       ON u.fred_id = o.fred_id
      AND o.realtime_start BETWEEN u.realtime_start AND u.realtime_end

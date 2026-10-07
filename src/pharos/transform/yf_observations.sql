-- yf_observations: raw Yahoo bars -> staging observation rows (close only).
--
-- Inputs (views the runner binds):
--   raw_yf_prices      every raw/yf/prices file, read with union_by_name, so the
--                      file from before D20 has pull_start / pull_end = NULL
--   watchlist_windows  ticker, first_expected (active_from, else backfill_start)
--
-- One output row per raw row: no dedup here (D18). Overlapping nightly pulls
-- repeat a bar; the runner deduplicates on (series_id, obs_date, vintage) and
-- C14 catches the same key arriving with two different values.
--
-- vintage (D20): the run date of the ticker's latest full-history pull at or
-- before this row's run. A pull is full-history when it starts at or before the
-- ticker's first expected date; NULL pull_start (pre-D20 file) counts as full,
-- which every run before D20 was. An incremental row with no earlier full pull
-- gets NULL vintage, and the runner fails on any NULL vintage.
--
-- available_date = obs_date: a close is knowable at the end of its trading day.
-- The split-adjusted value was not (review concern 12, queued).

WITH runs AS (
    SELECT *, strptime(run_id, '%Y%m%dT%H%M%SZ') AS run_at
    FROM raw_yf_prices
),
full_pulls AS (
    SELECT DISTINCT r.ticker, r.run_at
    FROM runs AS r
    JOIN watchlist_windows AS w USING (ticker)
    WHERE r.pull_start IS NULL OR r.pull_start <= w.first_expected
)
SELECT
    'yf:close:' || r.ticker                                     AS series_id,
    r.obs_date,
    r.close                                                     AS value,
    'USD'                                                       AS units,
    r.obs_date                                                  AS available_date,
    CAST(f.run_at AS DATE)                                      AS vintage,
    r.run_id,
    r.loaded_at
FROM runs AS r
ASOF LEFT JOIN full_pulls AS f
       ON f.ticker = r.ticker
      AND r.run_at >= f.run_at

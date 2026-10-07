-- series_catalog: one row per series Pharos expects, whether or not data arrived.
--
-- Inputs (views the runner binds):
--   watchlist_windows  ticker, yahoo_symbol, first_expected (active_from, else
--                      backfill_start), active_to   <- config/watchlist.csv
--   fred_expected      fred_id                      <- config/sources.yaml fred.series
--   raw_fred_series    FRED metadata history, every run
--
-- This is the expected set, built from config, not from what was downloaded:
-- ATVI gets a row even though Yahoo returned nothing, and a configured FRED
-- series with no current metadata still gets a row (NULL name and units), so
-- completeness checks (step 4) can see the gap. active_from / active_to bound
-- where data is expected.
--
-- units here is the CURRENT units only. Each observation row carries the units
-- of its own vintage (D19).
--
-- expected_lag_days is NULL for now: price freshness needs the trading calendar
-- and GDP freshness needs FRED release dates (M1 open items). Step 4 fills it.

WITH fred_current AS (
    -- the metadata window still open (realtime_end 9999-12-31), from the newest run
    SELECT *
    FROM raw_fred_series
    WHERE realtime_end = DATE '9999-12-31'
    QUALIFY row_number() OVER (PARTITION BY fred_id ORDER BY run_id DESC) = 1
)
SELECT
    'yf:close:' || ticker           AS series_id,
    ticker || ' close'              AS name,
    'yf'                            AS source,
    yahoo_symbol                    AS source_key,
    'security'                      AS entity_type,
    ticker                          AS entity_id,
    'close'                         AS measure,
    'USD'                           AS units,
    'D'                             AS frequency,
    'split_adjusted'                AS adjustment,
    'raw'                           AS kind,
    first_expected                  AS active_from,
    active_to,
    CAST(NULL AS INTEGER)           AS expected_lag_days
FROM watchlist_windows

UNION ALL

SELECT
    'fred:' || e.fred_id,
    m.title,
    'fred',
    e.fred_id,
    'macro',
    NULL,
    e.fred_id,
    m.units,
    m.frequency_short,
    m.seasonal_adjustment_short,
    'raw',
    CAST(m.observation_start AS DATE),
    NULL,
    CAST(NULL AS INTEGER)
FROM fred_expected AS e
LEFT JOIN fred_current AS m USING (fred_id)

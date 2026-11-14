-- imf_weo_observations: raw WEO values -> one staging row per raw row.
--
-- Input (bound by stage_landed):
--   raw_imf_weo_observations  country, indicator, frequency, year, value,
--                             publication_date, update_date, run_id, loaded_at
--
-- obs_date: January 1 of the year the value describes, as every annual row is
-- dated (D86). A fiscal-year country's value sits in the calendar year the IMF
-- files it under; weo_series.latest_actual_annual_data names the fiscal year.
-- vintage: the edition, by its publication date (UTC date of the IMF's stamp).
-- available_date: the same date. A value is knowable from the day its edition
-- is published, forecasts included: a forecast for 2030 is a number the IMF
-- published on that day, so obs_date runs past available_date here.
-- Defaulted at the landed level (D127): the IMF can change a value without
-- changing the publication date; staging then keeps the newest landing's value
-- under the same vintage (D132), and raw keeps both.

SELECT
    country,
    indicator,
    frequency,
    year,
    value,
    make_date(year, 1, 1)                               AS obs_date,
    CAST(publication_date AT TIME ZONE 'UTC' AS DATE)   AS available_date,
    CAST(publication_date AT TIME ZONE 'UTC' AS DATE)   AS vintage,
    update_date,
    run_id,
    loaded_at
FROM raw_imf_weo_observations

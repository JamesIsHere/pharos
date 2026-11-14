-- imf_weo_series: raw WEO series descriptions -> one staging row per raw row.
--
-- Input (bound by stage_landed):
--   raw_imf_weo_series  country, indicator, frequency, scale, decimals_displayed,
--                       overlap, country_update_date, latest_actual_annual_data,
--                       methodology_notes, labor_sector_employment_type,
--                       series_name, unit, notes, indicator_notes,
--                       publication_date, update_date, run_id, loaded_at
--
-- A description of a series as of one edition: obs_date, available_date and
-- vintage are all the edition's publication date (UTC date).
-- country_update_date arrives as m/d/yyyy text (9/24/2026); a value of another
-- shape stops the group here, since strptime raises.

SELECT
    country,
    indicator,
    frequency,
    scale,
    decimals_displayed,
    overlap,
    CAST(strptime(country_update_date, '%m/%d/%Y') AS DATE) AS country_update_date,
    latest_actual_annual_data,
    methodology_notes,
    labor_sector_employment_type,
    series_name,
    unit,
    notes,
    indicator_notes,
    CAST(publication_date AT TIME ZONE 'UTC' AS DATE)       AS obs_date,
    CAST(publication_date AT TIME ZONE 'UTC' AS DATE)       AS available_date,
    CAST(publication_date AT TIME ZONE 'UTC' AS DATE)       AS vintage,
    update_date,
    run_id,
    loaded_at
FROM raw_imf_weo_series

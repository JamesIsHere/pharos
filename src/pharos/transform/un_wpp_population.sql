-- un_wpp_population: raw UN population rows -> one staging row per raw row.
--
-- Input (bound by stage_landed):
--   raw_un_wpp_population  the file's 18 columns as sent (SortOrder ... PopDensity),
--                          revision, last_modified, run_id, loaded_at
--
-- Columns are renamed to snake_case and Time to year; values are untouched.
-- Population is in thousands, density in people per square km, as the UN sends.
--
-- obs_date: July 1 of the year. The file dates every value mid-year
-- (MidPeriod = Time + 0.5); stage_landed stops the group on a row that is not.
-- vintage: the UTC date of the file's HTTP Last-Modified. The UN re-posts a
-- revision's file when it corrects it, and that is a new vintage. Defaulted at
-- the landed level (D127): the revision's own release date is not in the file.
-- available_date: the same date. Projections run to 2100, so obs_date runs
-- past available_date here, as in the WEO tables.

SELECT
    SortOrder                                        AS sort_order,
    LocID                                            AS loc_id,
    Notes                                            AS notes,
    ISO3_code                                        AS iso3_code,
    ISO2_code                                        AS iso2_code,
    SDMX_code                                        AS sdmx_code,
    LocTypeID                                        AS loc_type_id,
    LocTypeName                                      AS loc_type_name,
    ParentID                                         AS parent_id,
    Location                                         AS location,
    VarID                                            AS var_id,
    Variant                                          AS variant,
    "Time"                                           AS year,
    MidPeriod                                        AS mid_period,
    PopMale                                          AS pop_male,
    PopFemale                                        AS pop_female,
    PopTotal                                         AS pop_total,
    PopDensity                                       AS pop_density,
    revision,
    make_date(CAST("Time" AS INTEGER), 7, 1)         AS obs_date,
    CAST(last_modified AT TIME ZONE 'UTC' AS DATE)   AS available_date,
    CAST(last_modified AT TIME ZONE 'UTC' AS DATE)   AS vintage,
    run_id,
    loaded_at
FROM raw_un_wpp_population

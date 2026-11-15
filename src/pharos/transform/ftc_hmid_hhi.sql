-- ftc_hmid_hhi: raw FTC Horizontal Merger Investigation Data HHI cells -> one staging row per raw row.
--
-- Input (bound by stage_landed):
--   raw_ftc_hmid_hhi  edition_end_fy, table, markets, post_merger_hhi, delta_hhi, enforced, closed,
--                     retrieved, file, run_id, loaded_at
--
-- One row per cell of each HHI table in each edition of the report (editions
-- cover fiscal years 1996 through 2003, 2005, 2007 and 2011): the number of
-- markets investigated at that post-merger HHI and change in HHI where the FTC
-- sought relief (enforced) and where it did not (closed). An edition is
-- cumulative from 1996; its industry tables can cover a shorter span, named in
-- `markets`. Values are untouched.
--
-- obs_date: September 30 of the last fiscal year the edition covers. vintage
-- and available_date: the date the file was retrieved (defaulted, D127): the
-- publication dates of the reports are not in the file.

SELECT
    edition_end_fy,
    "table"                                              AS table_no,
    markets,
    post_merger_hhi,
    delta_hhi,
    enforced,
    closed,
    make_date(CAST(edition_end_fy AS INTEGER), 9, 30)    AS obs_date,
    retrieved                                            AS available_date,
    retrieved                                            AS vintage,
    run_id,
    loaded_at
FROM raw_ftc_hmid_hhi

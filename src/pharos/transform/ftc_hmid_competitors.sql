-- ftc_hmid_competitors: raw FTC Horizontal Merger Investigation Data competitor rows -> one staging row per raw row.
--
-- Input (bound by stage_landed):
--   raw_ftc_hmid_competitors  edition_end_fy, table, markets, significant_competitors, enforced, closed,
--                             retrieved, file, run_id, loaded_at
--
-- One row per line of each significant-competitor table in each edition: the
-- number of markets investigated where the merger took the count of
-- significant competitors from one figure to the next ("4 to 3"), split by
-- whether the FTC sought relief. Values are untouched.
--
-- obs_date, vintage, available_date: as in ftc_hmid_hhi.sql.

SELECT
    edition_end_fy,
    "table"                                              AS table_no,
    markets,
    significant_competitors,
    enforced,
    closed,
    make_date(CAST(edition_end_fy AS INTEGER), 9, 30)    AS obs_date,
    retrieved                                            AS available_date,
    retrieved                                            AS vintage,
    run_id,
    loaded_at
FROM raw_ftc_hmid_competitors

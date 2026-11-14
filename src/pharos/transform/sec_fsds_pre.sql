-- sec_fsds_pre: raw Financial Statement Data Sets presentation rows -> one
-- staging row per raw row.
--
-- Input (bound by stage_landed):
--   raw_sec_fsds_pre  adsh, report, line, stmt, inpth, rfile, tag, version,
--                     plabel, negating (all text, as SEC sends them), quarter
--                     ('2026q3'), last_modified, run_id, loaded_at
--
-- SEC's column names are kept (adsh is the accession number). report and line
-- become integers; a value that is not a number comes out NULL and
-- stage_landed stops the group. inpth and negating are SEC's 0/1 flags.
--
-- obs_date: the last day of the quarter the data set covers.
-- vintage: the UTC date of the zip's HTTP Last-Modified.
-- available_date: the same date: when SEC posted the data set, not when each
-- filing was filed (pre.txt carries no filing date; that is in sub.txt, which
-- is not landed). Defaulted at the landed level (D127).

SELECT
    adsh,
    TRY_CAST(report AS INTEGER)                      AS report,
    TRY_CAST(line AS INTEGER)                        AS line,
    stmt,
    inpth = '1'                                      AS inpth,
    rfile,
    tag,
    version,
    plabel,
    negating = '1'                                   AS negating,
    quarter,
    CAST(make_date(CAST(left(quarter, 4) AS INTEGER), CAST(right(quarter, 1) AS INTEGER) * 3, 1)
         + INTERVAL 1 MONTH - INTERVAL 1 DAY AS DATE) AS obs_date,
    CAST(last_modified AT TIME ZONE 'UTC' AS DATE)   AS available_date,
    CAST(last_modified AT TIME ZONE 'UTC' AS DATE)   AS vintage,
    run_id,
    loaded_at
FROM raw_sec_fsds_pre

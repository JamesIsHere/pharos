-- sec_submission_details: raw submissions.zip company details -> one staging row per raw row.
--
-- Inputs (bound by stage_sec):
--   raw_sec_submission_details  one row per main CIK file, values as sent (D122),
--                               plus source_file, run_id, loaded_at
--
-- One output row per raw row: no dedup here; stage_sec merges landings (D132).
-- Key cik: measured unique in a landing (991,508 rows, 2026-11-05).
--
-- obs_date, available_date and vintage: the run's UTC date. The file is a
-- snapshot of what SEC says today, with no date of its own, so a value is
-- known from the run that landed it. They derive from the run, so stage_sec
-- treats them as lineage: an unchanged re-landing keeps its first run's dates.
-- Defaulted at the landed level (D127), not signed off.

SELECT
    r.* EXCLUDE (source_file, run_id, loaded_at),
    CAST(strptime(r.run_id, '%Y%m%dT%H%M%SZ') AS DATE)  AS obs_date,
    CAST(strptime(r.run_id, '%Y%m%dT%H%M%SZ') AS DATE)  AS available_date,
    CAST(strptime(r.run_id, '%Y%m%dT%H%M%SZ') AS DATE)  AS vintage,
    r.source_file,
    r.run_id,
    r.loaded_at
FROM raw_sec_submission_details AS r

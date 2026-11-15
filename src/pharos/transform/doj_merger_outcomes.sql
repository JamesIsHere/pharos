-- doj_merger_outcomes: raw DOJ Antitrust Division merger counts -> one staging row per raw row.
--
-- Input (bound by stage_landed):
--   raw_doj_merger_outcomes  FY, complaints, litigation_complaints, simultaneous_settlement,
--                            abandoned, resolved_concerns, merger_outcomes,
--                            retrieved, file, run_id, loaded_at
--
-- One row a federal fiscal year, from the division workload statistics:
-- complaints filed (for litigation, or with a settlement filed at the same
-- time), and matters that ended without a complaint (transaction abandoned,
-- or the concerns resolved). Values are untouched.
--
-- obs_date: September 30 of the fiscal year. vintage and available_date: the
-- date the file was retrieved (defaulted, D127; see ftc_hsr_fiscal_year.sql).

SELECT
    FY                                       AS fiscal_year,
    complaints,
    litigation_complaints,
    simultaneous_settlement,
    abandoned,
    resolved_concerns,
    merger_outcomes,
    make_date(CAST(FY AS INTEGER), 9, 30)    AS obs_date,
    retrieved                                AS available_date,
    retrieved                                AS vintage,
    run_id,
    loaded_at
FROM raw_doj_merger_outcomes

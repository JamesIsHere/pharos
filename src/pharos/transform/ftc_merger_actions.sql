-- ftc_merger_actions: raw FTC merger enforcement counts -> one staging row per raw row.
--
-- Input (bound by stage_landed):
--   raw_ftc_merger_actions  FY, consents, injunctions, part3, abandoned, total,
--                           retrieved, file, run_id, loaded_at
--
-- One row a federal fiscal year: consent orders, federal court injunctions
-- sought, administrative (Part 3) complaints, and transactions abandoned or
-- restructured after the FTC raised concerns. Values are untouched.
--
-- obs_date: September 30 of the fiscal year. vintage and available_date: the
-- date the file was retrieved (defaulted, D127; see ftc_hsr_fiscal_year.sql).

SELECT
    FY                                       AS fiscal_year,
    consents,
    injunctions,
    part3,
    abandoned,
    total,
    make_date(CAST(FY AS INTEGER), 9, 30)    AS obs_date,
    retrieved                                AS available_date,
    retrieved                                AS vintage,
    run_id,
    loaded_at
FROM raw_ftc_merger_actions

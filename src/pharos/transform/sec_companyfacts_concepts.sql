-- sec_companyfacts_concepts: raw companyfacts.zip concepts -> one staging row per raw row.
--
-- Inputs (bound by stage_sec):
--   raw_sec_companyfacts_concepts  cik, entity_name, taxonomy, concept, label,
--                                  description (values as sent), run_id, loaded_at
--
-- One output row per raw row: no dedup here; stage_sec merges landings (D132).
-- Key (cik, taxonomy, concept): one entry per concept in a filer's file.
--
-- obs_date, available_date and vintage: the run's UTC date, as for
-- sec_submission_details (a snapshot with no date of its own). Defaulted at
-- the landed level (D127), not signed off.

SELECT
    r.cik,
    r.entity_name,
    r.taxonomy,
    r.concept,
    r.label,
    r.description,
    CAST(strptime(r.run_id, '%Y%m%dT%H%M%SZ') AS DATE)  AS obs_date,
    CAST(strptime(r.run_id, '%Y%m%dT%H%M%SZ') AS DATE)  AS available_date,
    CAST(strptime(r.run_id, '%Y%m%dT%H%M%SZ') AS DATE)  AS vintage,
    r.run_id,
    r.loaded_at
FROM raw_sec_companyfacts_concepts AS r

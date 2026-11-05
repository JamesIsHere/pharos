-- id: C35
-- severity: warn
-- gate: no
-- group: run
-- description: a source group was carried forward in the latest run: it is served from an earlier run (D81, D82)
-- ack_key: group, served_from_run
--
-- Reads every run's manifest. A run from before source groups (no group
-- columns) counts as prices_macro, with the run's own status as the outcome.
-- Two runs or more in a row, or a group that has never published, is C36's.
-- A monitor: it tests what is being served, never blocks (D17).

WITH m AS (
    SELECT DISTINCT run_id,
           coalesce("group", 'prices_macro') AS grp,
           coalesce(group_outcome, CASE WHEN status = 'published' THEN 'published' ELSE status END) AS outcome,
           group_source_run, group_reason
    FROM run_manifest
),
latest AS (SELECT max(run_id) AS run_id FROM m)
SELECT m.grp AS "group", m.run_id AS latest_run, m.group_source_run AS served_from_run, m.group_reason AS reason
FROM m
JOIN latest USING (run_id)
WHERE m.outcome = 'carried_forward'
ORDER BY 1

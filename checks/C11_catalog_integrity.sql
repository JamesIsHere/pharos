-- id: C11
-- severity: error
-- gate: yes
-- description: observations without a catalog row, or an active catalog series without observations (D22)
--
-- Active = active_to NULL or on/after the run's UTC date. An ended series with
-- no observations is a fact about the source, not the staged data: C15 reports
-- it as source-missing and never blocks (D22).
--
-- A withdrawn value (NULL, D25) is still an observation row, so it counts.

SELECT o.series_id, count(*) AS n_observations, 'observations without catalog row' AS problem
FROM observations AS o
WHERE NOT EXISTS (SELECT 1 FROM series_catalog AS c WHERE c.series_id = o.series_id)
GROUP BY o.series_id

UNION ALL

SELECT c.series_id, 0, 'active series without observations'
FROM series_catalog AS c, this_run AS r
WHERE (c.active_to IS NULL OR c.active_to >= CAST(timezone('UTC', r.run_at) AS DATE))
  AND NOT EXISTS (SELECT 1 FROM observations AS o WHERE o.series_id = c.series_id)

ORDER BY series_id

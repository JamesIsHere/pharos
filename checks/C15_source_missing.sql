-- id: C15
-- severity: warn
-- gate: no
-- description: ended catalog series (active_to before the run date) without observations: source-missing (D22)
--
-- The survivorship finding (acceptance #3): a dead ticker the source no longer
-- serves, e.g. ATVI, acquired 2023-10-12. Kept in the expected set so the gap
-- stays visible; reported, never blocking (D17, D22). Active series without
-- observations are C11's.

SELECT c.series_id, c.active_from, c.active_to
FROM series_catalog AS c, this_run AS r
WHERE c.active_to < CAST(timezone('UTC', r.run_at) AS DATE)
  AND NOT EXISTS (SELECT 1 FROM observations AS o WHERE o.series_id = c.series_id)
ORDER BY c.series_id

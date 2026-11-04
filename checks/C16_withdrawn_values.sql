-- id: C16
-- severity: warn
-- gate: no
-- description: value withdrawn by the source (NULL) in the latest vintage, inside the active window
-- ack_key: series_id, obs_date
--
-- FRED stores "." when a vintage withdraws a value, and staging keeps it as NULL
-- so a later read can't resurrect the old number. When the latest vintage of a
-- date is NULL, the source currently says the value does not exist (e.g.
-- CPIAUCSL 2025-10-01). That is a fact about the source, not a defect in our
-- data, so it warns and never blocks (D25). Dates before active_from are left
-- out: the source no longer expects them either.

SELECT o.series_id, o.obs_date, o.vintage
FROM observations AS o
JOIN series_catalog AS c USING (series_id)
WHERE o.obs_date >= c.active_from
  AND c.source = 'fred'   -- a withdrawal is FRED's '.'; a missing price is C09's (D73, D78)
QUALIFY row_number() OVER (PARTITION BY o.series_id, o.obs_date ORDER BY o.vintage DESC) = 1
    AND o.value IS NULL
ORDER BY o.series_id, o.obs_date

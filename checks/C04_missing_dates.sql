-- id: C04
-- severity: error
-- gate: yes
-- description: expected date with no row inside the series' window (trading days, months, quarters)
--
-- The expected set, its window and which rows count are defined once in
-- transform/expected_dates.sql, shared with the health page's coverage
-- heatmap so a hole there and a C04 error are the same fact.

SELECT series_id, obs_date
FROM expected_dates
WHERE NOT present
ORDER BY series_id, obs_date

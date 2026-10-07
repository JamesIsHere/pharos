-- id: C07
-- severity: error
-- gate: yes
-- description: (series_id, obs_date, vintage) repeated with the same value: staging's dedup failed
--
-- Staging collapses rows that agree on everything but lineage, so after dedup a
-- key repeated with the same value means the dedup broke, e.g. two rows that
-- differ only in units or available_date. GROUP BY puts NULL values in one
-- group, so two withdrawn rows for one key count too.
--
-- Same key with different values is deliberately kept by staging: it is a
-- source correction, and C14 alone reports it (D27).

SELECT series_id, obs_date, vintage, value, count(*) AS n_rows
FROM observations
GROUP BY series_id, obs_date, vintage, value
HAVING count(*) > 1
ORDER BY series_id, obs_date, vintage

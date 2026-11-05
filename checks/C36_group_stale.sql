-- id: C36
-- severity: error
-- gate: no
-- group: run
-- description: a source group has not published fresh in 2 or more runs in a row, or has never published (D82, D83)
--
-- Counts each group's latest runs back to its last fresh publish: carried
-- forward, blocked and failed all count as not fresh. One run is C35's
-- yellow; two in a row is red here. A group with no published run at all
-- is red from its first run (D83). Runs from before source groups count as
-- prices_macro, with the run's own status as the outcome.
-- A monitor: it tests what is being served, never blocks (D17).

WITH m AS (
    SELECT DISTINCT run_id,
           coalesce("group", 'prices_macro') AS grp,
           coalesce(group_outcome, CASE WHEN status = 'published' THEN 'published' ELSE status END) AS outcome
    FROM run_manifest
),
ranked AS (
    SELECT *, row_number() OVER (PARTITION BY grp ORDER BY run_id DESC) AS k FROM m
),
last_fresh AS (
    SELECT grp, min(k) FILTER (WHERE outcome = 'published') AS k_fresh FROM ranked GROUP BY grp
)
SELECT r.grp AS "group",
       count(*) FILTER (WHERE r.k < coalesce(f.k_fresh, 2147483647)) AS runs_not_fresh,
       max(r.run_id) FILTER (WHERE r.k = f.k_fresh) AS last_fresh_run,
       any_value(r.outcome) FILTER (WHERE r.k = 1) AS latest_outcome
FROM ranked AS r
JOIN last_fresh AS f USING (grp)
GROUP BY r.grp, f.k_fresh
HAVING f.k_fresh IS NULL OR f.k_fresh - 1 >= 2
ORDER BY 1

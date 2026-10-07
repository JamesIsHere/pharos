-- id: C01
-- severity: error
-- gate: no
-- description: last published run is older than 26h, or nothing has ever been published (D32)
--
-- Successful = published (D32). A blocked run leaves serving/ as stale as a run
-- that never happened, so it doesn't reset the clock.
--
-- Measured against `clock`: the run's own time when a run is checked, the wall
-- clock at view time, so a pipeline that silently stopped still goes red. 26h is
-- the nightly cadence plus 2h of slack. A monitor check: it tests the world, not
-- the staged data, and never blocks (D17).

SELECT max(m.published_at)         AS last_published_at,
       any_value(c.now)            AS now,
       any_value(c.now) - max(m.published_at) AS age
FROM clock AS c
LEFT JOIN run_manifest AS m ON m.status = 'published'
HAVING max(m.published_at) IS NULL
    OR any_value(c.now) - max(m.published_at) > INTERVAL 26 HOURS

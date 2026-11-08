-- id: C42
-- severity: error
-- gate: no
-- group: run
-- description: a run loaded data but never wrote its manifest: it crashed or was killed before finishing (D145)
--
-- refresh writes the manifest in a finally block, so any Python error still
-- leaves one; only a hard stop leaves none: a native crash (20261108T073006Z
-- aborted in ucrtbase.dll 4 s after publishing), a kill, or a power loss. Such
-- a run may have published, and nothing else names it: C01 reads publish times
-- from the manifests and only goes red later, for the wrong reason.
--
-- A run with no manifest may still be running, so it counts only once it can't
-- be: a newer run has loaded, or it started more than 5 hours ago (the nightly
-- task's execution limit, scripts/register-tasks.ps1; the longest recorded run
-- took 28 minutes). Only runs after the newest recorded run count: the step
-- commands (pharos load, stage, publish) write no manifest by design, and seven
-- such runs from before C42 would otherwise be red forever. So C42 says "the
-- latest run did not finish" and clears when a later run records its manifest.
-- A monitor: it tests the world, never blocks (D17).

SELECT l.run_id,
       l.first_loaded_at,
       CASE WHEN EXISTS (SELECT 1 FROM run_loads AS n WHERE n.run_id > l.run_id)
            THEN 'a newer run has started' ELSE 'started more than 5 hours ago' END AS why
FROM run_loads AS l, clock AS c
WHERE l.run_id NOT IN (SELECT run_id FROM run_manifest)
  AND l.run_id > (SELECT max(run_id) FROM run_manifest)   -- no manifest at all: NULL, nothing counts (C01 is red then)
  AND (EXISTS (SELECT 1 FROM run_loads AS n WHERE n.run_id > l.run_id)
       OR c.now - timezone('UTC', strptime(l.run_id, '%Y%m%dT%H%M%SZ')) > INTERVAL 5 HOURS)   -- run IDs are UTC
ORDER BY l.run_id

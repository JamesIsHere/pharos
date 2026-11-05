-- id: C31
-- severity: warn
-- gate: no
-- group: sec
-- description: the latest submissions.zip download's Last-Modified older than 36 hours at the clock (D60). SEC rebuilds the file nightly
-- ack_key: last_modified
--
-- Reads the load records' Last-Modified (D91). No record, or none with a
-- Last-Modified, counts as stale. A monitor: never blocks (D17).

SELECT max(l.last_modified) AS last_modified, any_value(c.now) AS now
FROM clock AS c
LEFT JOIN loads AS l ON l.source = 'sec' AND l.dataset = 'submission_details'
HAVING max(l.last_modified) IS NULL
    OR any_value(c.now) - max(l.last_modified) > INTERVAL 36 HOURS

-- id: C40
-- severity: warn
-- gate: no
-- group: sec
-- description: the latest Company Facts landing is older than 36 hours at the clock
-- ack_key: landed_at
--
-- Company Facts is refreshed from CIKs changed in the nightly submissions
-- landing. A monitor: a failed incremental load prevents the SEC group from
-- publishing, while age still explains a scheduler that has stopped.

SELECT max(l.loaded_at) AS landed_at, any_value(c.now) AS now
FROM clock AS c
LEFT JOIN loads AS l ON l.source = 'sec' AND l.dataset = 'companyfacts'
HAVING max(l.loaded_at) IS NULL
    OR any_value(c.now) - max(l.loaded_at) > INTERVAL 36 HOURS

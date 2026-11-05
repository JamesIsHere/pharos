-- id: C34
-- severity: warn
-- gate: no
-- group: sec
-- description: the latest ticker file's Last-Modified older than 14 days at the clock (D60). Provisional: the file is not daily
-- ack_key: last_modified
--
-- Reads the load records' Last-Modified (D91). No record, or none with a
-- Last-Modified, counts as stale. A monitor: never blocks (D17).

SELECT max(l.last_modified) AS last_modified, any_value(c.now) AS now
FROM clock AS c
LEFT JOIN loads AS l ON l.source = 'sec' AND l.dataset = 'tickers'
HAVING max(l.last_modified) IS NULL
    OR any_value(c.now) - max(l.last_modified) > INTERVAL 14 DAYS

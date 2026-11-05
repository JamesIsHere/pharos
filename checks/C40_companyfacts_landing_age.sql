-- id: C40
-- severity: warn
-- gate: no
-- group: companyfacts
-- description: the latest companyfacts landing is older than 92 days at the clock: run `pharos companyfacts` (D131)
-- ack_key: landed_at
--
-- companyfacts lands on demand, never nightly (D131), so its age only warns,
-- never red. 92 days, one quarter of 10-Q filings, is defaulted at the
-- landed level (D127), flagged to James. A monitor: never blocks (D17).

SELECT max(l.loaded_at) AS landed_at, any_value(c.now) AS now
FROM clock AS c
LEFT JOIN loads AS l ON l.source = 'sec' AND l.dataset = 'companyfacts'
HAVING max(l.loaded_at) IS NULL
    OR any_value(c.now) - max(l.loaded_at) > INTERVAL 92 DAYS

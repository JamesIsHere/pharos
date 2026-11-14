-- id: C46
-- severity: warn
-- gate: no
-- group: un
-- description: the newest UN World Population Prospects revision served is 3 or more calendar years older than the clock's year (D174)
-- ack_key: revision
--
-- The UN publishes a revision every two years, mid-year (2022, 2024), so by
-- January of the third year after a revision the next one is out (defaulted at
-- the landed level, D127). The revision's year is read from its name (WPP2024).
-- Reads the served wpp_population table at view time. A monitor: never blocks
-- (D17). Clear it by naming the new revision in config/sources.yaml
-- (un.wpp_revision) and running `pharos un`.

SELECT max(p.revision) AS revision, year(any_value(c.now)) AS this_year
FROM clock AS c
LEFT JOIN wpp_population AS p ON true
HAVING max(p.revision) IS NULL
    OR year(any_value(c.now)) - CAST(substr(max(p.revision), 4) AS INTEGER) >= 3

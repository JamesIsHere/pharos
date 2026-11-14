-- id: C45
-- severity: warn
-- gate: no
-- group: imf
-- description: the newest IMF World Economic Outlook edition served was published more than 7 months before the clock's date (D174)
-- ack_key: published
--
-- The IMF publishes an edition in April and one in September or October, six
-- months apart; 7 months is one edition missed, with a month of slack
-- (defaulted at the landed level, D127). Reads the served weo_observations
-- table at view time. A monitor: never blocks (D17). Clear it with `pharos imf`.

SELECT max(o.vintage) AS published, CAST(any_value(c.now) AS DATE) AS today
FROM clock AS c
LEFT JOIN weo_observations AS o ON true
HAVING max(o.vintage) IS NULL
    OR CAST(any_value(c.now) AS DATE) > max(o.vintage) + INTERVAL 7 MONTH

-- id: C25
-- severity: error
-- gate: no
-- group: sec
-- description: latest full-index filing date older than 3 business days at the clock's date (D60)
--
-- Business days are weekdays after the latest filing date up to the clock's
-- UTC date; SEC holidays are not excluded, so a holiday can make this fire a
-- day early (defaulted at the landed level, D127). Reads the served
-- `filings` table at view time. A monitor: never blocks (D17).

WITH latest AS (SELECT max(date_filed) AS latest_filed FROM filings),
today AS (SELECT CAST(now AS DATE) AS today FROM clock)
SELECT l.latest_filed, t.today,
       (SELECT count(*) FROM generate_series(l.latest_filed + 1, t.today, INTERVAL 1 DAY) AS g(d)
        WHERE dayofweek(g.d) BETWEEN 1 AND 5) AS business_days
FROM latest AS l, today AS t
WHERE l.latest_filed IS NULL
   OR (SELECT count(*) FROM generate_series(l.latest_filed + 1, t.today, INTERVAL 1 DAY) AS g(d)
       WHERE dayofweek(g.d) BETWEEN 1 AND 5) > 3

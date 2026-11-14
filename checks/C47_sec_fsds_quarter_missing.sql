-- id: C47
-- severity: warn
-- gate: no
-- group: fsds
-- description: the newest SEC Financial Statement Data Sets quarter served is older than the last complete quarter at the clock's date (D174)
-- ack_key: last_complete
--
-- SEC posts a quarter's data set shortly after the quarter ends, so this can
-- fire for the first days of a new quarter, before the file exists (defaulted
-- at the landed level, D127). Quarters read as SEC names them, '2026q3', which
-- sort as text. Reads the served fsds_pre table at view time. A monitor: never
-- blocks (D17). Clear it with `pharos fsds`.

WITH last_complete AS (
    SELECT CAST(date_trunc('quarter', CAST(now AS DATE)) - INTERVAL 1 DAY AS DATE) AS d FROM clock
)
SELECT max(p.quarter) AS newest_served,
       CAST(year(any_value(l.d)) AS VARCHAR) || 'q' || CAST(quarter(any_value(l.d)) AS VARCHAR) AS last_complete
FROM last_complete AS l
LEFT JOIN fsds_pre AS p ON true
HAVING max(p.quarter) IS NULL
    OR max(p.quarter) < CAST(year(any_value(l.d)) AS VARCHAR) || 'q' || CAST(quarter(any_value(l.d)) AS VARCHAR)

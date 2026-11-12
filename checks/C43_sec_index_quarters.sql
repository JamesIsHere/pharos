-- id: C43
-- severity: error
-- gate: yes
-- group: sec
-- description: a quarter missing from the filing index between 1993Q1 and its latest quarter (D163 step 1)
--
-- Completeness against an expected set (D163, item 2): EDGAR's full index
-- starts 1993Q1 (D52) and has had a file for every quarter since, so the
-- staged `filings` table must hold every quarter from 1993Q1 through the
-- latest quarter it holds. Returns each missing quarter. Whether that latest
-- quarter is recent enough is C25's question, a monitor; this gate never
-- needs a date window.

WITH quarters AS (
    SELECT DISTINCT quarter FROM filings),
latest AS (
    SELECT max(quarter) AS q FROM quarters),
expected AS (
    SELECT CAST(year(d) AS VARCHAR) || 'Q' || CAST(quarter(d) AS VARCHAR) AS quarter
    FROM latest,
         generate_series(DATE '1993-01-01',
                         CAST(left(q, 4) || '-' || lpad(CAST(3 * CAST(right(q, 1) AS INTEGER) - 2 AS VARCHAR), 2, '0')
                              || '-01' AS DATE),
                         INTERVAL 3 MONTH) AS g(d))
SELECT e.quarter AS missing_quarter
FROM expected AS e ANTI JOIN quarters USING (quarter)
UNION ALL
SELECT 'no quarters at all' FROM latest WHERE q IS NULL
ORDER BY 1

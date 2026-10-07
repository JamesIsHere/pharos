-- id: C03
-- severity: error
-- gate: yes
-- description: expected series missing from series_catalog (watchlist ticker x required series, or a configured FRED series)
--
-- The expectation comes from config, not from the data: every watchlist ticker
-- must have one series per required measure (sources.yaml yahoo.required_series),
-- and every FRED id in sources.yaml must have its series. series_catalog is built
-- from the same config, so this fires only if staging drops a series: it is a
-- control on the transform.

WITH expected AS (
    SELECT 'yf:' || r.measure || ':' || w.ticker AS series_id
    FROM watchlist_windows AS w
    CROSS JOIN required_series AS r

    UNION ALL

    SELECT 'fred:' || fred_id
    FROM fred_expected
)
SELECT e.series_id
FROM expected AS e
WHERE NOT EXISTS (
    SELECT 1 FROM series_catalog AS c WHERE c.series_id = e.series_id
)

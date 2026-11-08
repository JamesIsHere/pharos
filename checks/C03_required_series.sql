-- id: C03
-- severity: error
-- gate: yes
-- group: prices_macro
-- description: expected series missing from series_catalog (watchlist ticker x required series, or a configured FRED series)
--
-- The expectation comes from config, not from the data: every watchlist ticker
-- must have one series per required measure (sources.yaml yahoo.required_series),
-- and every FRED id in sources.yaml must have its series. series_catalog is built
-- from the same config, so this fires only if staging drops a series: it is a
-- control on the transform.
--
-- A FRED series this run's loader left out (D144) is not expected here: a new
-- series that has never loaded has no catalog row, and C41 reports it red
-- instead of this gate freezing the whole group. An existing series left out
-- still has its row from earlier runs, so the exemption only ever spares a
-- series that has never loaded.

WITH expected AS (
    SELECT 'yf:' || r.measure || ':' || w.ticker AS series_id
    FROM watchlist_windows AS w
    CROSS JOIN required_series AS r

    UNION ALL

    SELECT 'fred:' || x.fred_id
    FROM fred_expected AS x
    WHERE NOT EXISTS (SELECT 1 FROM fred_load_errors AS f
                      WHERE f.fred_id = x.fred_id AND f.run_id = (SELECT run_id FROM this_run))
)
SELECT e.series_id
FROM expected AS e
WHERE NOT EXISTS (
    SELECT 1 FROM series_catalog AS c WHERE c.series_id = e.series_id
)

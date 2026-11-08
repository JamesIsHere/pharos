-- id: C41
-- severity: error
-- gate: no
-- group: prices_macro
-- description: configured FRED series failed to load in this run: left out, its earlier vintages still served (D144)
--
-- The FRED loader takes each series on its own: one whose fetch fails, or
-- whose response drifts from the verified shape, is left out of the run and
-- named, with its error, in the observations load record's note. The other
-- series land and publish. A monitor: blocking would freeze every other series
-- and the prices for one broken series (D17). Red every run until the series is
-- fixed or removed from sources.yaml. At view time this_run is the published
-- version's run.

SELECT 'fred:' || f.fred_id AS series_id, f.error
FROM fred_load_errors AS f
WHERE f.run_id = (SELECT run_id FROM this_run)
ORDER BY 1

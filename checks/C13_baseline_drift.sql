-- id: C13
-- severity: warn
-- gate: no
-- description: series drifted from the opening-balance baseline (first_date later, fewer rows, or gone), or no baseline recorded (D39)
--
-- The metrics are defined once in transform/baseline_metrics.sql, shared with
-- the audit that recorded the baseline. History should only ever grow: a later
-- first_date or a lower row_count means the source dropped history we once had.
-- last_date is recorded but not compared; falling behind is C02/C17's.
-- A series added after the audit has no baseline row and is not compared (#34).
-- A monitor: drift is a question about the world, never a block (D17).

SELECT b.series_id, b.first_date AS baseline_first_date, m.first_date,
       b.row_count AS baseline_row_count, m.row_count,
       CASE WHEN m.series_id IS NULL THEN 'series gone'
            WHEN m.first_date > b.first_date THEN 'first_date moved later'
            ELSE 'row_count below baseline' END AS problem
FROM baseline AS b
LEFT JOIN baseline_metrics AS m USING (series_id)
WHERE m.series_id IS NULL OR m.first_date > b.first_date OR m.row_count < b.row_count

UNION ALL

-- before the audit records one there is nothing to compare: say so, never a
-- vacuous pass
SELECT NULL, NULL, NULL, NULL, NULL, 'no baseline recorded'
WHERE NOT EXISTS (SELECT 1 FROM baseline)

ORDER BY series_id

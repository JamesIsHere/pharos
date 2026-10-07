-- id: C05
-- severity: error
-- gate: yes
-- description: raw files on disk do not reconcile to the load log (file missing, row count changed, or no record)
--
-- Raw is write-once (D18): once write_raw() records a file in health/loads, the
-- file must stay exactly as written. The load log is the ledger and the files
-- on disk are the detail; this reconciles the two in both directions (D26).
-- A load record with no path is a download that returned nothing (no file is
-- expected). Row counts can't see a rewrite that keeps the count (#28).

WITH recorded AS (
    SELECT run_id, source, dataset, rows_written
    FROM loads
    WHERE path IS NOT NULL
)
SELECT
    coalesce(r.run_id, f.run_id)    AS run_id,
    coalesce(r.source, f.source)    AS source,
    coalesce(r.dataset, f.dataset)  AS dataset,
    r.rows_written,
    f.rows_on_disk,
    CASE
        WHEN f.run_id IS NULL THEN 'recorded file missing'
        WHEN r.run_id IS NULL THEN 'file has no load record'
        ELSE 'row count changed'
    END                             AS problem
FROM recorded AS r
FULL JOIN raw_files AS f
    ON f.run_id = r.run_id AND f.source = r.source AND f.dataset = r.dataset
WHERE r.run_id IS NULL
   OR f.run_id IS NULL
   OR f.rows_on_disk <> r.rows_written
ORDER BY 1, 2, 3

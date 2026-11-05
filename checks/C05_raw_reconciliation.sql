-- id: C05
-- severity: error
-- gate: yes
-- group: prices_macro
-- description: raw files on disk do not reconcile to the load log (file missing, row count changed, content changed, or no record)
--
-- Raw is write-once (D18): once write_raw() records a file in health/loads, the
-- file must stay exactly as written. The load log is the ledger and the files
-- on disk are the detail; this reconciles the two in both directions (D26).
-- A load record with no path is a download that returned nothing (no file is
-- expected). A record carries the sha256 of the file as write_raw wrote it
-- (D108), so a rewrite that keeps the row count still fails as content
-- changed. Records from before the hash existed have none: those files keep
-- the row-count test only, since a hash taken now would prove nothing about
-- the file as written.

WITH recorded AS (
    SELECT run_id, source, dataset, rows_written, sha256
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
        WHEN f.rows_on_disk IS DISTINCT FROM r.rows_written THEN 'row count changed'
        ELSE 'content changed'
    END                             AS problem
FROM recorded AS r
FULL JOIN raw_files AS f
    ON f.run_id = r.run_id AND f.source = r.source AND f.dataset = r.dataset
WHERE r.run_id IS NULL
   OR f.run_id IS NULL
   OR f.rows_on_disk IS DISTINCT FROM r.rows_written   -- a NULL count is a failure, not a pass (#70)
   OR (r.sha256 IS NOT NULL AND f.sha256_on_disk IS DISTINCT FROM r.sha256)
ORDER BY 1, 2, 3

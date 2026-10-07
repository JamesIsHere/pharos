-- id: C06
-- severity: error
-- gate: yes
-- description: load control total broken: rows written to the raw file differ from rows downloaded
--
-- write_raw() records both numbers for every download: rows_downloaded from the
-- frame it was handed, rows_written read back from the file on disk. A
-- difference means the write lost or invented rows. An empty download records
-- 0 and 0 and passes. C05 then watches the file for as long as it exists.

SELECT run_id, source, dataset, rows_downloaded, rows_written
FROM loads
WHERE rows_written <> rows_downloaded
ORDER BY run_id, source, dataset

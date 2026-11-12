-- id: C44
-- severity: error
-- gate: yes
-- group: sec
-- description: (cik, accession, form, name) repeated in the filing index (D61, D163 step 1)
--
-- Uniqueness on the source's key (D163, item 3). The key is D61's, kept by
-- James on 2026-11-12: a combined filing is listed once per form (1,415
-- (cik, accession) pairs), and 114 SEC staff UPLOAD letters once under a
-- filer's old name and once under its new one. Both are real index rows.
-- GROUP BY treats a NULL name as one value, as staging does (D61).
-- stage_sec refuses a non-unique key before publish; this records it.

SELECT cik, accession, form, name, count(*) AS n_rows
FROM filings
GROUP BY cik, accession, form, name
HAVING count(*) > 1
ORDER BY cik, accession, form, name

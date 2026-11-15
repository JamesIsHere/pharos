-- id: C48
-- severity: warn
-- gate: no
-- group: antitrust
-- description: the newest fiscal year in the served Hart-Scott-Rodino table ended more than 24 months before the clock's date (D175)
-- ack_key: newest_fiscal_year_end
--
-- The FTC adds a fiscal year (ending September 30) to its annual file after the
-- year's report to Congress; FY2025 was in the file on 2026-11-15, 13.5 months
-- after the year ended. 24 months is one year missed (defaulted at the landed
-- level, D127). Reads the served hsr_fiscal_year table at view time. A monitor:
-- never blocks (D17). Clear it by adding the year to
-- <data root>/manual/antitrust/hsr_by_fiscal_year.csv, dating the file in
-- config/sources.yaml, and running `pharos antitrust`.

SELECT max(h.obs_date) AS newest_fiscal_year_end, CAST(any_value(c.now) AS DATE) AS today
FROM clock AS c
LEFT JOIN hsr_fiscal_year AS h ON true
HAVING max(h.obs_date) IS NULL
    OR CAST(any_value(c.now) AS DATE) > max(h.obs_date) + INTERVAL 24 MONTH

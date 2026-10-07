-- id: C19
-- severity: error
-- gate: no
-- description: (ticker, obs_date) repeated in this run's Tiingo file (D37)
--
-- Each run's Tiingo file is one full-history snapshot (D36), so a ticker-day
-- appears once. A repeat would give C12 two reference closes for one date.

SELECT ticker, obs_date, count(*) AS n_rows
FROM raw_tiingo_prices
GROUP BY ticker, obs_date
HAVING count(*) > 1
ORDER BY ticker, obs_date

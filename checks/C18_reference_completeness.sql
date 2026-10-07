-- id: C18
-- severity: error
-- gate: no
-- description: active watchlist ticker with no Tiingo rows in this run: the reference source is missing (D37)
--
-- Tiingo is reference only (D35): it never blocks a publish, so an outage turns
-- health red here instead. A run whose Tiingo load failed has no file and every
-- active ticker fires; a ticker Tiingo doesn't know (HTTP 404) fires alone.
-- Ended tickers (active_to before the clock date) are not expected.

SELECT w.ticker, w.active_to
FROM watchlist_windows AS w, clock AS c
WHERE (w.active_to IS NULL OR w.active_to >= CAST(timezone('UTC', c.now) AS DATE))
  AND NOT EXISTS (SELECT 1 FROM raw_tiingo_prices AS t WHERE t.ticker = w.ticker)
ORDER BY w.ticker

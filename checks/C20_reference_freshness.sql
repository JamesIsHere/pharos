-- id: C20
-- severity: warn
-- gate: no
-- description: Tiingo's latest close for an active ticker is older than the latest due XNYS session (D37)
--
-- Same due rule as C02 for prices: a session is due from the next UTC calendar
-- day. A ticker with no rows at all is C18's.

WITH due AS (
    SELECT max(session) AS session
    FROM trading_days, clock
    WHERE session < CAST(timezone('UTC', clock.now) AS DATE)
)
SELECT t.ticker, max(t.obs_date) AS latest_obs_date, any_value(d.session) AS due_session
FROM raw_tiingo_prices AS t
JOIN watchlist_windows AS w USING (ticker)
CROSS JOIN due AS d
CROSS JOIN clock AS c
WHERE w.active_to IS NULL OR w.active_to >= CAST(timezone('UTC', c.now) AS DATE)
GROUP BY t.ticker
HAVING max(t.obs_date) < any_value(d.session)
ORDER BY t.ticker

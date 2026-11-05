-- id: C20
-- severity: warn
-- gate: no
-- group: prices_macro
-- description: Tiingo's latest close for an active ticker is older than the latest due XNYS session (D37)
-- ack_key: ticker, latest_obs_date
--
-- Same due rule as C02 for prices: a session is due from the next New York
-- calendar day, where the Tiingo pull ends (D79, #104, D118). A ticker with no
-- rows at all is C18's.

WITH due AS (
    SELECT max(session) AS session
    FROM trading_days, clock
    WHERE session < CAST(timezone('America/New_York', clock.now) AS DATE)
)
SELECT t.ticker, max(t.obs_date) AS latest_obs_date, any_value(d.session) AS due_session
FROM raw_tiingo_prices AS t
JOIN watchlist_windows AS w USING (ticker)
CROSS JOIN due AS d
CROSS JOIN clock AS c
WHERE w.active_to IS NULL OR w.active_to >= CAST(timezone('UTC', c.now) AS DATE)
GROUP BY t.ticker
HAVING max(t.obs_date) < any_value(d.session)
    OR max(t.obs_date) IS NULL OR any_value(d.session) IS NULL   -- unknown is a failure (#70)
ORDER BY t.ticker

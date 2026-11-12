# Prices: open questions

- ATVI is on the watchlist (dead ticker, survivorship test) but Yahoo has no closes for it; Tiingo has its history (3 splits, 14 dividends). Its series_catalog row has no observations. Serve ATVI from Tiingo, or accept the gap?
- 38 dates differ from Tiingo by more than 0.5%, all 2005-2014, all isolated: the same 38 D41 found on 2026-10-07 (vendor closing prints, no third source). Still fine to leave reported, not fixed?
- Splits and dividends are only in raw; serving has the split-adjusted close alone. Serve them (needed for total return)?
- Tiingo re-lands its full history every run: 3.96M raw rows, 241 MB over 44 runs, for about 90k rows each. Keep, or land only new dates?
- GOOGL 2014-04-03: Yahoo books the Class C distribution as a 1.998 split, Tiingo as a dividend; config/corporate_actions.csv reconciles it (D40). Fine as is?

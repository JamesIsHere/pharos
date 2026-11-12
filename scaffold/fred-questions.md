# FRED / ALFRED: open questions

- Vintage floor: ALFRED's record for GDP starts 1991-12-04, so 1946 GDP has no vintage before 1991. A point-in-time join before a series' first vintage finds nothing. Show the floor per series wherever it's used?
- Null values are kept on purpose: a null is a vintage withdrawing a value (COE: 697 of 4,580 rows). Fine as is?
- Every run re-lands the full vintage history (724,697 raw rows over 52 runs for 32,483 served). Keep, or land only new vintages?
- realtime_end (when a value was replaced) is in raw, dropped in serving; the next vintage implies it. Fine?

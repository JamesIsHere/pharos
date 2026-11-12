# Scaffold notes

What we have measured about the data, one finding per line. Newest at the bottom.

## Findings

- Insiders (trial): 199,934 filings, 4 quarters, 8 tables, 1,658,416 rows. The first page read everything as text and never showed types; the schema step was missing.
- Insiders: SEC ships a schema file in each zip. 0 values fail their declared type. The declaration doesn't match the files: 4 contact columns declared but absent, 1 footnote column renamed. OWNER_SIGNATURE has 269 duplicate keys. The 10b5-1 flag mixes 0/1, true/false and blank.
- Company Facts: 126,588,444 facts, 0 duplicate keys, 0 facts without a company.
- Company Facts: 92 facts dated after 2100 (typo'd years such as 6016); 6,754 end more than a year after their filing; 38 negative total assets; start dates as early as 0202; fiscal year up to 29,953,000.
- Company Facts: 6 filings not in the filing index; 1,710 company concepts with no facts.
- Company Facts: in 2026, 424B2 bank prospectuses (38,656 filings) and proxy statements (2,014) carry XBRL and appear in Company Facts. Any count of filings or companies needs a form filter.
- Company Facts, filings by form 2019-2026: new in 2026 are 424B2 (38,656), DEF 14A (2,014), S-8 (1,194), and other forms (6,023, against 51-97 a year before). 6-K fell from 666 in 2025 to 124 in 2026. 10-K and 10-Q are steady.
- Company Facts is not just 10-Ks: 10-Q 61.6% of facts, 10-K 30.1%, then 20-F, amendments, 6-K, 8-K, 40-F; 479,983 filings over 99 forms. The store holds every form. Taxonomies: us-gaap 123.2M, ifrs-full 2.6M, dei 0.5M, plus fee (ffd) and pay (ecd) tables.
- Join trap: filings (the EDGAR index) lists a filing once per party: 27,400,901 rows, 18,873,816 distinct accessions, 27,399,372 distinct (cik, accession). Of Company Facts' 480,333 (cik, accession) pairs, 29,647 (6%) have an accession listed more than once. Joining facts to filings on accession alone duplicates those facts; join on (cik, accession). Schema diagram now labels that join cik, accession.
- FRED: 8 series, 32,483 served rows; raw re-lands the full history each run (724,697 rows over 52 runs). Raw newest run and served match both ways (0 unmatched).
- FRED: units change across vintages: real GDP rebased 7 times since 1991 (1987 dollars to chained 2017); CPI from 1967=100 to 1982-84=100 in Feb 1988 (186 periods). Any first-to-latest comparison must hold units equal.
- FRED: first print to latest, median absolute: GDP 4.43% (largest +10.21%, 1992 Q4), COE 1.55%, real GDP 0.53%, CPI 0.02%. Over decades this mixes routine revisions with benchmark revisions.
- Prices: ATVI is on the watchlist and in series_catalog but Yahoo has no closes for it; Tiingo has 4,728 rows through 2023-10-13.
- Prices: Yahoo against Tiingo, 85,535 common dates: median difference about 2e-8 (float32 rounding); 38 dates over 0.5%, 2005-2014, all 38 isolated: the same count D41 found on 2026-10-07.
- Prices: splits agree on every date both sources have; one-source-only: ATVI (3, Tiingo) and GOOGL 2014-04-03 (Yahoo's 1.998 split = Tiingo's dividend, D40). Dividends agree date for date except ATVI (14) and that GOOGL date.
- Prices: serving carries the split-adjusted close only; splits and dividends live in raw. Tiingo re-lands full history each run (3.96M rows, 241 MB, 44 runs).
- WEO: values arrive in units (US GDP 2024 = 29,381,025,000,000); SCALE=9 is a display hint, not a multiplier. 13 of 210 GDP codes are IMF country groups (G001 ... GX123), not countries.
- WEO: the fiscal-to-calendar mapping differs by series: FY(t-1/t) = CY(t) for most, FY(t/t+1) = CY(t) for India GDP and unemployment, Iran and Lesotho, SY(t/t+1) = CY(t) for Afghanistan, none stated for Bhutan. Eritrea and Syria carry no update date.
- Countries: EDGAR uses 272 state/country codes in submissions (upper-cased; lower-case duplicates exist). 14 are retired codes not on the SEC's current list; 12 resolve by their filers' addresses (I8, I9, X9 Germany; U2 Russia; E6, O6 Quebec; L4, L5 Israel; G5 Czechoslovakia; Y2 Yugoslavia; Q9 Northern Mariana Islands; LO a typo for Louisiana), E7 and L1 do not. XX (unknown) covers 1,617 entities.
- Countries: IMF has six three-letter region codes (AFR, APD, EUR, MCD, SDS, WHD), so three letters is not a country test. IMF and World Bank differ on Kosovo (KOS/XKX) and West Bank and Gaza (WBG/PSE); Taiwan is IMF-only; the World Bank names Nauru Naoero.
- Trusted measurements (served version 20261111T134544Z): filing index has 1,529 repeated (cik, accession): 1,415 combined filings under two forms, 114 exact repeats; submission filings unique on (cik, accession, form); Company Facts covers 5,937 of 5,953 XBRL 10-Ks filed in 2024 (match on accession alone: joint filers put facts under one CIK); Company Facts' count control records 0 = 0; WEO US GDP matches FRED GDP within 0.0001% for 2000-2025.

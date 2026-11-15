-- ftc_hsr_fiscal_year: raw Hart-Scott-Rodino annual rows -> one staging row per raw row.
--
-- Input (bound by stage_landed):
--   raw_ftc_hsr_fiscal_year  the file's 10 columns as typed (FY ... EarlyTerminationTransactionsNotGranted),
--                            retrieved, file, run_id, loaded_at
--
-- Columns are renamed to snake_case; values are untouched. Counts are merger
-- transactions, filings or second requests in one federal fiscal year.
--
-- obs_date: September 30 of the fiscal year, its last day.
-- vintage and available_date: the date the file was retrieved (config/sources.yaml).
-- Defaulted at the landed level (D127): the date the agencies published a
-- year's figures is not in the file, so a year reads as knowable later than it was.

SELECT
    FY                                       AS fiscal_year,
    TransactionsReported                     AS transactions_reported,
    FilingsReceived                          AS filings_received,
    AdjustedTransactions                     AS adjusted_transactions,
    SecondRequestTotal                       AS second_requests,
    SecondRequestFTC                         AS second_requests_ftc,
    SecondRequestDOJ                         AS second_requests_doj,
    EarlyTerminationTransactions             AS early_termination_requested,
    EarlyTerminationTransactionsGranted      AS early_termination_granted,
    EarlyTerminationTransactionsNotGranted   AS early_termination_not_granted,
    make_date(CAST(FY AS INTEGER), 9, 30)    AS obs_date,
    retrieved                                AS available_date,
    retrieved                                AS vintage,
    run_id,
    loaded_at
FROM raw_ftc_hsr_fiscal_year

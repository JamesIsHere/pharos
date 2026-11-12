"""Everything the Amazon page uses, from Pharos's database only: SEC companyfacts (annual 10-K values), the AMZN close
(split-adjusted, serving), the split event (raw Yahoo row), share counts from 10-K/10-Q cover pages, and FRED GDP and
CPI (today's vintage). Each function returns a polars frame; build.py draws them."""
from functools import lru_cache

import polars as pl

import facts

SPLIT_DATE = "2022-06-06"   # the one AMZN split since 2005, from raw_yf_prices.stock_splits (20.0)
SPLIT = 20.0

C = dict(
    rev_old="SalesRevenueNet", rev_new="RevenueFromContractWithCustomerExcludingAssessedTax",
    goods="SalesRevenueGoodsNet", services="SalesRevenueServicesNet",
    cogs="CostOfGoodsAndServicesSold", opinc="OperatingIncomeLoss", ni="NetIncomeLoss",
    tax="IncomeTaxExpenseBenefit",
    ocf="NetCashProvidedByUsedInOperatingActivities",
    capex_old="PaymentsToAcquirePropertyPlantAndEquipment", capex_new="PaymentsToAcquireProductiveAssets",
    capsoft="CapitalizedComputerSoftwareAdditions",
    sbc="ShareBasedCompensation", dda="DepreciationDepletionAndAmortization",
    ppe_old="PropertyPlantAndEquipmentNet",
    ppe_new="PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization",
    assets="Assets", equity="StockholdersEquity", cash="CashAndCashEquivalentsAtCarryingValue",
    ltd="LongTermDebtNoncurrent", inv="InventoryNet", ar="AccountsReceivableNetCurrent", ap="AccountsPayableCurrent",
    deferred="ContractWithCustomerLiability", adv_old="MarketingAndAdvertisingExpense", adv_new="AdvertisingExpense",
    shares="WeightedAverageNumberOfDilutedSharesOutstanding", eps="EarningsPerShareDiluted",
    segments="NumberOfOperatingSegments",
)


@lru_cache(maxsize=1)
def pretax_concept() -> str:
    r = facts.con().sql("""
        select concept, count(distinct year(end_date)) n from serving_company_facts
        where cik = 1018724 and form = '10-K' and concept ilike 'IncomeLossFromContinuingOperationsBeforeIncomeTaxes%'
        group by 1 order by n desc limit 1""").fetchone()
    return r[0]


@lru_cache(maxsize=1)
def annual() -> pl.DataFrame:
    """One row per fiscal year 2007-2025 with every figure the page uses, in USD (shares and EPS split-adjusted)."""
    names = list(C.values()) + [pretax_concept()]
    long = facts.annual(names)
    # Split rule (observed event): a per-share figure filed before the split date is pre-split; restate it.
    long = long.with_columns(
        pl.when((pl.col("filed") < pl.lit(SPLIT_DATE).str.to_date()) & (pl.col("concept") == C["shares"]))
          .then(pl.col("val") * SPLIT)
          .when((pl.col("filed") < pl.lit(SPLIT_DATE).str.to_date()) & (pl.col("concept") == C["eps"]))
          .then(pl.col("val") / SPLIT)
          .otherwise(pl.col("val")).alias("val"))
    w = long.pivot(on="concept", index="yr", values="val").sort("yr")
    inv = {v: k for k, v in C.items()}
    inv[pretax_concept()] = "pretax"
    w = w.rename({c: inv[c] for c in w.columns if c in inv})
    for k in list(C) + ["pretax"]:
        if k not in w.columns:
            w = w.with_columns(pl.lit(None, dtype=pl.Float64).alias(k))
    w = w.with_columns(
        pl.coalesce("rev_new", "rev_old").alias("rev"),
        pl.coalesce("capex_new", "capex_old").alias("capex"),
        pl.coalesce("ppe_new", "ppe_old").alias("ppe"),
        pl.coalesce("adv_new", "adv_old").alias("adv"),
    ).with_columns(
        (pl.col("ocf") - pl.col("capex")).alias("fcf"),
        (1 - pl.col("cogs") / pl.col("rev")).alias("gross_margin"),
        (pl.col("opinc") / pl.col("rev")).alias("op_margin"),
        (pl.col("ni") / pl.col("rev")).alias("net_margin"),
        (pl.col("inv") / pl.col("cogs") * 365).alias("dio"),
        (pl.col("ar") / pl.col("rev") * 365).alias("dso"),
        (pl.col("ap") / pl.col("cogs") * 365).alias("dpo"),
        (pl.col("tax") / pl.col("pretax")).alias("tax_rate"),
    ).with_columns((pl.col("dio") + pl.col("dso") - pl.col("dpo")).alias("ccc"))
    return w.filter(pl.col("yr").is_between(2007, 2025))


@lru_cache(maxsize=1)
def macro_annual() -> pl.DataFrame:
    """Calendar-year nominal GDP (mean of the four SAAR quarters), real GDP, and CPI annual average, today's vintage."""
    return facts.con().sql("""
        with l as (
            select series_id, obs_date, value from serving_observations
            where series_id in ('fred:GDP', 'fred:GDPC1', 'fred:CPIAUCSL')
            qualify row_number() over (partition by series_id, obs_date order by vintage desc) = 1
        )
        select year(obs_date) as yr,
               avg(value) filter (where series_id = 'fred:GDP') * 1e9 as gdp,
               avg(value) filter (where series_id = 'fred:GDPC1') * 1e9 as rgdp,
               avg(value) filter (where series_id = 'fred:CPIAUCSL') as cpi,
               count(value) filter (where series_id = 'fred:GDP') as gdp_q,
               count(value) filter (where series_id = 'fred:CPIAUCSL') as cpi_m
        from l where value is not null group by 1 order by 1
    """).pl()


@lru_cache(maxsize=1)
def price() -> pl.DataFrame:
    """Daily AMZN close (split-adjusted) with market cap: close x shares outstanding from the latest cover page on or
    before that day, the share count restated to post-split units when the cover date is before the split."""
    return facts.con().sql(f"""
        with p as (select obs_date, value as close from serving_observations where series_id = 'yf:close:AMZN'
                   qualify row_number() over (partition by obs_date order by vintage desc) = 1),
        s as (select end_date as cover_date,
                     case when end_date < date '{SPLIT_DATE}' then val * {SPLIT} else val end as shares_out
              from serving_company_facts
              where cik = 1018724 and concept = 'EntityCommonStockSharesOutstanding'
              qualify row_number() over (partition by end_date order by filed desc) = 1)
        select p.obs_date, p.close, s.shares_out, s.cover_date, p.close * s.shares_out as mcap
        from p asof left join s on p.obs_date >= s.cover_date
        order by p.obs_date
    """).pl()


@lru_cache(maxsize=1)
def filings() -> pl.DataFrame:
    return facts.con().sql("""
        select form, year(date_filed) as yr, count(*) as n from serving_filings where cik = 1018724 group by all
    """).pl()


@lru_cache(maxsize=1)
def first_reported() -> pl.DataFrame:
    """Revenue, net income and operating income per fiscal year as FIRST reported, with the 10-K filing date that made
    them public (available_date for an ASOF join against prices)."""
    return facts.con().sql("""
        select year(end_date) as yr, min(filed) as filed,
               arg_min(val, filed) filter (where concept in ('SalesRevenueNet', 'RevenueFromContractWithCustomerExcludingAssessedTax')) as rev,
               arg_min(val, filed) filter (where concept = 'NetIncomeLoss') as ni,
               arg_min(val, filed) filter (where concept = 'OperatingIncomeLoss') as opinc
        from serving_company_facts
        where cik = 1018724 and form in ('10-K', '10-K/A') and month(end_date) = 12 and day(end_date) = 31
          and date_diff('day', start_date, end_date) between 350 and 380
          and concept in ('SalesRevenueNet', 'RevenueFromContractWithCustomerExcludingAssessedTax', 'NetIncomeLoss', 'OperatingIncomeLoss')
        group by 1 order by 1
    """).pl()


@lru_cache(maxsize=1)
def valuation() -> pl.DataFrame:
    """Daily market cap over the latest annual revenue and net income public on that day (ASOF on the 10-K's filing
    date, never on period end)."""
    p = price().filter(pl.col("mcap").is_not_null()).sort("obs_date")
    f = first_reported().select("filed", "yr", "rev", "ni").sort("filed")
    return (p.join_asof(f, left_on="obs_date", right_on="filed", strategy="backward")
             .with_columns((pl.col("mcap") / pl.col("rev")).alias("ps"),
                           pl.when(pl.col("ni") > 0).then(pl.col("mcap") / pl.col("ni")).alias("pe")))


@lru_cache(maxsize=1)
def lease_principal() -> pl.DataFrame:
    return facts.annual(["FinanceLeasePrincipalPayments", "PrincipalPaymentsOnCapitalLeases",
                         "RepaymentsOfLongTermCapitalLeaseObligations"]).select("concept", "yr", "val")


QCON = {"RevenueFromContractWithCustomerExcludingAssessedTax": "rev", "SalesRevenueNet": "rev",
        "OperatingIncomeLoss": "opinc", "NetIncomeLoss": "ni"}


@lru_cache(maxsize=1)
def quarterly() -> pl.DataFrame:
    """One row per calendar quarter: rev, opinc, ni. Q1-Q3 from 10-Q three-month values; Q4 = 10-K year minus
    Q1-Q3 (derived), with the 10-K's own tagged Q4 kept alongside where it exists (2009-2020) as a check.
    Revenue: the newer tag where both exist for a quarter."""
    lst = ", ".join(f"'{c}'" for c in QCON)
    raw = facts.con().sql(f"""
        select concept, form, start_date, end_date, val,
               date_diff('day', start_date, end_date) as days
        from serving_company_facts
        where cik = 1018724 and form in ('10-Q', '10-K', '10-K/A') and concept in ({lst})
          and date_diff('day', start_date, end_date) between 80 and 380
        qualify row_number() over (partition by concept, form in ('10-K', '10-K/A'), start_date, end_date
                                   order by filed desc, accession desc) = 1
    """).pl().with_columns(pl.col("concept").replace_strict(QCON).alias("item"),
                           (pl.col("concept") == "RevenueFromContractWithCustomerExcludingAssessedTax").alias("new"))
    # newer revenue tag wins where both exist for the same period and form type
    raw = (raw.with_columns(pl.col("form").is_in(["10-K", "10-K/A"]).alias("k"))
              .sort("new", descending=True).unique(["item", "k", "start_date", "end_date"], keep="first"))
    q3 = raw.filter(~pl.col("k") & pl.col("days").is_between(80, 100))
    q4_tagged = raw.filter(pl.col("k") & pl.col("days").is_between(80, 100) & (pl.col("end_date").dt.month() == 12))
    ytd9 = raw.filter(~pl.col("k") & pl.col("days").is_between(260, 280) & (pl.col("end_date").dt.month() == 9))
    year = raw.filter(pl.col("k") & pl.col("days").is_between(350, 380) & (pl.col("end_date").dt.month() == 12))

    q = (q3.with_columns(pl.col("end_date").dt.year().alias("yr"), pl.col("end_date").dt.quarter().alias("q"))
           .select("item", "yr", "q", "val"))
    s123 = q.group_by("item", "yr").agg(pl.col("val").sum().alias("s123"), pl.len().alias("nq"))
    yr = year.with_columns(pl.col("end_date").dt.year().alias("yr")).select("item", "yr", pl.col("val").alias("year"))
    q4 = (yr.join(s123, on=["item", "yr"]).filter(pl.col("nq") == 3)
            .select("item", "yr", pl.lit(4, dtype=pl.Int8).alias("q"), (pl.col("year") - pl.col("s123")).alias("val")))
    out = pl.concat([q.with_columns(pl.col("q").cast(pl.Int8), pl.lit("10-Q").alias("source")),
                     q4.with_columns(pl.lit("derived").alias("source"))])
    checks = dict(
        q4_tagged=q4_tagged.with_columns(pl.col("end_date").dt.year().alias("yr")).select("item", "yr", pl.col("val").alias("tagged")),
        ytd9=ytd9.with_columns(pl.col("end_date").dt.year().alias("yr")).select("item", "yr", pl.col("val").alias("ytd9")),
        s123=s123, year=yr)
    return out.sort("item", "yr", "q"), checks

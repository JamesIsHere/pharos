"""Amazon's rivals in goods retail, from Pharos's database only: every SEC filer coded to a goods-selling retail SIC,
one row per company and fiscal year (10-K one-year figures), plus each company's filing trail (its last 10-K, and
8-K items that say why it stopped: 1.03 bankruptcy, 5.01 change in control). Each function returns a polars frame."""
from functools import lru_cache

import duckdb
import polars as pl

import facts

AMZN = 1018724
# Goods retail: general merchandise and specialty stores Amazon sells against. Out: food stores (54), auto dealers and
# gas (55), restaurants (58), drug stores (5912), whose sales Amazon barely touched in these years.
GOODS_SQL = "(sic between 5200 and 5399 or sic between 5600 and 5799 or (sic between 5900 and 5999 and sic <> 5912))"
GOODS_LABEL = "SIC 5200-5399, 5600-5799, 5900-5999 except 5912"
# Revenue concepts in order of preference. RevenueFromContractWithCustomerIncludingAssessedTax is TJX's (and other
# retailers') tag since 2018; the shared base in explore/lib omits it, which made TJX look as if it had stopped filing.
REVENUE = ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
           "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet", "SalesRevenueGoodsNet")
ITEMS = ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
         "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet", "SalesRevenueGoodsNet",
         "OperatingIncomeLoss", "NetIncomeLoss", "CostOfGoodsAndServicesSold", "CostOfRevenue", "CostOfGoodsSold",
         "InventoryNet", "AccountsPayableCurrent", "PaymentsToAcquirePropertyPlantAndEquipment",
         "PaymentsToAcquireProductiveAssets")


@lru_cache(maxsize=1)
def panel() -> pl.DataFrame:
    """cik, name, sic, yr, end_date, rev, opinc, ni, cogs, inv, ap, capex: goods retailers (and Amazon), 2009-2025."""
    lst = ", ".join(f"'{c}'" for c in ITEMS)
    rev = ", ".join(f"max(val) filter (where concept = '{c}')" for c in REVENUE)
    # The CIK list is fetched first and inlined: DuckDB raises an internal cast error when the IN-subquery is a CTE here.
    ciks = ", ".join(str(r[0]) for r in facts.con().sql(f"""
        select cik from (select cik, try_cast(sic as int) as sic from serving_submission_details)
        where {GOODS_SQL} or cik = {AMZN}""").fetchall())
    figs = facts.con().sql(f"""
        with f as (select cik, accession, concept, start_date, end_date, val, filed from serving_company_facts
              where taxonomy = 'us-gaap' and unit = 'USD' and form in ('10-K', '10-K/A') and end_date <= filed
                and concept in ({lst}) and cik in ({ciks})
                and (start_date is null or date_diff('day', start_date, end_date) between 350 and 380)),
        -- A company's fiscal year ends are the period ends of its own 10-Ks (the latest year-long end date in each
        -- filing). Some 10-Ks also tag year-long figures ending at quarter ends; anchoring drops those.
        fye as (select distinct cik, max(end_date) as end_date from f where start_date is not null group by cik, accession),
        latest as (select f.* from f join fye using (cik, end_date)
                   qualify row_number() over (partition by cik, concept, end_date order by filed desc, accession desc) = 1),
        fy as (select cik, end_date, year(end_date - interval 182 day) as yr,
                      coalesce({rev}) as rev,
                      max(val) filter (where concept = 'OperatingIncomeLoss') as opinc,
                      max(val) filter (where concept = 'NetIncomeLoss') as ni,
                      coalesce(max(val) filter (where concept = 'CostOfGoodsAndServicesSold'),
                               max(val) filter (where concept = 'CostOfGoodsSold'),
                               max(val) filter (where concept = 'CostOfRevenue')) as cogs,
                      max(val) filter (where concept = 'InventoryNet') as inv,
                      max(val) filter (where concept = 'AccountsPayableCurrent') as ap,
                      coalesce(max(val) filter (where concept = 'PaymentsToAcquireProductiveAssets'),
                               max(val) filter (where concept = 'PaymentsToAcquirePropertyPlantAndEquipment')) as capex
               from latest group by cik, end_date)
        select * from fy
    """).pl()
    # One fiscal year per company and year, run over the fetched frame: in one statement with the CTEs above, this
    # step trips a DuckDB 1.5.6 internal cast error ("Failed to cast expression to type"), though each part runs alone.
    figs = duckdb.sql("""
        select cik, yr, end_date, rev, opinc, ni, cogs, inv, ap, capex from figs
        where rev > 0 and rev <= 1e12 and yr between 2009 and 2025
        qualify row_number() over (partition by cik, yr order by end_date desc) = 1""").pl()
    # Names joined in polars: any SQL join of this result to serving_submission_details trips a DuckDB 1.5.6 internal
    # cast error ("Failed to cast expression to type"), though each part runs alone.
    names = facts.con().sql(f"select cik, name, try_cast(sic as int) as sic from serving_submission_details "
                            f"where cik in ({ciks})").pl()
    return names.join(figs, on="cik").sort("cik", "yr")


@lru_cache(maxsize=1)
def trail() -> pl.DataFrame:
    """Per goods retailer: last 10-K filed, and whether any 8-K carries item 1.03 (bankruptcy) or 5.01 (change in
    control), with the first such date."""
    return facts.con().sql(f"""
        with d as (select cik, try_cast(sic as int) as sic from serving_submission_details),
        g as (select cik from d where {GOODS_SQL} or cik = {AMZN})
        select cik,
               max(filing_date) filter (where form in ('10-K', '10-K405', '10-KT')) as last_10k,
               min(filing_date) filter (where form like '8-K%' and items like '%1.03%') as bankrupt_8k,
               min(filing_date) filter (where form like '8-K%' and items like '%5.01%') as control_8k,
               count(*) as n_filings, min(filing_date) as first_filing
        from serving_submission_filings where cik in (select cik from g)
        group by cik
    """).pl()


@lru_cache(maxsize=1)
def exits() -> pl.DataFrame:
    """One row per goods retailer whose last fiscal year with revenue is before 2024, with the name it traded under at
    its last 10-K (SEC renames shells after a sale or bankruptcy: 'Old COPPER Company' was J C Penney), and why it left
    by its own 8-Ks: item 1.03 (bankruptcy) or 5.01 (change in control) filed from a year before its last fiscal year
    end on. Still filing a 10-K in 2025 or later means the gap is in the tags, not the company."""
    p = panel()
    last = (p.sort("yr").group_by("cik").agg(pl.col("name").last(), pl.col("sic").last(), pl.col("yr").max().alias("last_yr"),
                                              pl.col("end_date").last().alias("last_end"), pl.col("rev").last().alias("last_rev"),
                                              pl.col("rev").max().alias("peak_rev"),
                                              pl.col("yr").filter(pl.col("rev") == pl.col("rev").max()).first().alias("peak_yr")))
    last = last.filter(pl.col("last_yr") < 2024)
    lst = ", ".join(str(c) for c in last["cik"])
    ev = facts.con().sql(rf"""
        with e as (
            select cik, filing_date,
                   regexp_matches(items, '(^|,)1\.03(,|$)') as b, regexp_matches(items, '(^|,)5\.01(,|$)') as c
            from serving_submission_filings where form like '8-K%' and cik in ({lst}))
        select cik, max(filing_date) filter (where b) as b_date, max(filing_date) filter (where c) as c_date
        from e group by cik""").pl()
    tk = facts.con().sql(f"""
        select cik, max(filing_date) as last_10k from serving_submission_filings
        where form in ('10-K', '10-KT') and cik in ({lst}) group by cik""").pl()
    fn = facts.con().sql(f"""
        select cik, unnest(from_json(former_names, '[{{"name":"VARCHAR","from":"VARCHAR","to":"VARCHAR"}}]'), recursive := true)
        from serving_submission_details where cik in ({lst}) and former_names <> '[]'""").pl()
    fn = fn.with_columns(pl.col("from").str.slice(0, 10).str.to_date(), pl.col("to").str.slice(0, 10).str.to_date())
    x = last.join(ev, on="cik", how="left").join(tk, on="cik", how="left")
    was = (x.select("cik", "last_10k").join(fn, on="cik")
            .filter((pl.col("from") <= pl.col("last_10k")) & (pl.col("to") >= pl.col("last_10k")))
            .group_by("cik").agg(pl.col("name").first().alias("was")))
    x = x.join(was, on="cik", how="left").with_columns(pl.coalesce("was", "name").alias("trading_name"))
    since = pl.col("last_end") - pl.duration(days=365)
    return x.with_columns(
        pl.when(pl.col("last_10k") >= pl.date(2025, 1, 1)).then(pl.lit("still files (tag gap)"))
          .when(pl.col("b_date") >= since).then(pl.lit("bankruptcy"))
          .when(pl.col("c_date") >= since).then(pl.lit("taken over"))
          .otherwise(pl.lit("stopped filing, reason not in 8-Ks")).alias("why")).sort("peak_rev", descending=True)


@lru_cache(maxsize=1)
def prices(tickers=("AMZN", "WMT", "COST", "HD")) -> pl.DataFrame:
    """Daily split-adjusted close, latest vintage, for the retail tickers Pharos already loads."""
    ids = ", ".join(f"'yf:close:{t}'" for t in tickers)
    return facts.con().sql(f"""
        select replace(series_id, 'yf:close:', '') as ticker, obs_date, value as close from serving_observations
        where series_id in ({ids})
        qualify row_number() over (partition by series_id, obs_date order by vintage desc) = 1
        order by ticker, obs_date""").pl()

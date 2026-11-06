"""Shared base for the exploratory tests (8+): one row per company and fiscal year from the 10-K's own one-year
figures (the method of test 6), with today's SEC company details. Cached per process; reads catalog.duckdb read-only."""
from functools import lru_cache

import duckdb
import polars as pl

from pharos.paths import PROJECT_ROOT, data_root

ROOT = PROJECT_ROOT.as_posix()
CATALOG = (data_root() / "catalog.duckdb").as_posix()

REVENUE = ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
           "RevenuesNetOfInterestExpense", "InterestAndDividendIncomeOperating")
# Fallback only, when none of REVENUE is tagged for the year (TJX and other retailers since 2018). It includes taxes
# collected for governments, so taking the larger of it and a net figure would inflate excise payers (Philip Morris:
# $82bn gross vs $31bn net, 2021).
REVENUE_GROSS = "RevenueFromContractWithCustomerIncludingAssessedTax"

SECTOR_SQL = """case
    when sic is null then 'No SIC code'
    when sic between 100 and 999 then 'Agriculture, forestry, fishing'
    when sic between 1000 and 1499 then 'Mining, oil and gas'
    when sic between 1500 and 1799 then 'Construction'
    when sic between 2000 and 3999 then 'Manufacturing'
    when sic between 4800 and 4899 then 'Information'
    when sic between 4900 and 4999 then 'Utilities'
    when sic between 4000 and 4799 then 'Transportation and warehousing'
    when sic between 5000 and 5199 then 'Wholesale trade'
    when sic between 5800 and 5899 then 'Arts, recreation, hotels, food services'
    when sic between 5200 and 5999 then 'Retail trade'
    when sic between 6500 and 6599 or sic = 6798 then 'Real estate, rental, leasing'
    when sic between 6000 and 6799 then 'Finance and insurance'
    when sic between 7000 and 7099 or sic between 7900 and 7999 then 'Arts, recreation, hotels, food services'
    when sic in (7370, 7372, 7374) or sic between 7800 and 7899 then 'Information'
    when sic between 7300 and 7399 or sic in (8100, 8111) or sic between 8700 and 8999 then 'Professional and business services'
    when sic between 8000 and 8099 or sic between 8200 and 8399 then 'Education, health care, social assistance'
    when sic between 7000 and 8699 then 'Other services'
    when sic between 9100 and 9799 then 'Government'
    else 'Unclassified'
end"""


@lru_cache(maxsize=1)
def companies() -> pl.DataFrame:
    """cik, yr, revenue, net_income, name, sic, sic_description, sector, exchange, state, category."""
    con = duckdb.connect(CATALOG, read_only=True)
    return con.sql(f"""
        with f as (
            select cik, concept, end_date, val, filed, accession
            from serving_company_facts
            where taxonomy = 'us-gaap' and unit = 'USD' and form in ('10-K', '10-K/A')
              and date_diff('day', start_date, end_date) between 350 and 380 and end_date <= filed
              and concept in {REVENUE + (REVENUE_GROSS, 'NetIncomeLoss')}
        ),
        latest as (
            select * from f
            qualify row_number() over (partition by cik, concept, end_date order by filed desc, accession desc) = 1
        ),
        fy as (
            select cik, end_date, year(end_date - interval 182 day) as yr,
                   coalesce(max(val) filter (where concept in {REVENUE}),
                            max(val) filter (where concept = '{REVENUE_GROSS}')) as revenue,
                   max(val) filter (where concept = 'NetIncomeLoss') as net_income
            from latest group by cik, end_date
        ),
        one as (   -- one fiscal year per company and calendar year (a fiscal-year change can give two)
            select * from fy qualify row_number() over (partition by cik, yr order by end_date desc) = 1
        ),
        d as (
            select cik, name, try_cast(sic as int) as sic, sic_description, nullif(state_of_incorporation, '') as state,
                   category,
                   case when list_has_any(exchanges, ['NYSE', 'NYSE American', 'NYSE Arca']) then 'NYSE'
                        when list_has(exchanges, 'Nasdaq') then 'Nasdaq'
                        when list_has(exchanges, 'OTC') then 'OTC'
                        when len(list_filter(exchanges, x -> x is not null)) > 0 then 'Other exchange'
                        else 'No ticker listed' end as exchange
            from serving_submission_details
        )
        select one.cik, one.yr, one.end_date, one.revenue, one.net_income,
               d.name, d.sic, d.sic_description, {SECTOR_SQL} as sector, d.exchange, d.state, d.category
        from one left join d using (cik)
        where coalesce(d.sic, 0) <> 6770                                       -- SPACs out
          and coalesce(one.revenue, 0) <= 1e12 and abs(coalesce(one.net_income, 0)) <= 2.5e11   -- impossible values out
          and one.yr between 2011 and 2025
    """).pl()


def revenue_year(yr: int = 2024) -> pl.DataFrame:
    """Companies with positive revenue in one calendar year."""
    return companies().filter((pl.col("yr") == yr) & (pl.col("revenue") > 0))


def concentration(df: pl.DataFrame, by: str) -> pl.DataFrame:
    """Per group: companies, revenue, top company and its share, CR4, HHI (0-10,000), companies to reach 50%."""
    ranked = (df.with_columns((pl.col("revenue") / pl.col("revenue").sum().over(by)).alias("share"))
                .sort([by, "share"], descending=[False, True])
                .with_columns(pl.col("share").cum_sum().over(by).alias("cum"),
                              pl.int_range(1, pl.len() + 1).over(by).alias("rank")))
    return ranked.group_by(by).agg(
        pl.len().alias("companies"),
        (pl.col("revenue").sum() / 1e9).alias("revenue_bn"),
        pl.col("name").first().alias("top_company"),
        pl.col("share").first().alias("top1"),
        pl.col("share").filter(pl.col("rank") <= 4).sum().alias("cr4"),
        (pl.col("share").pow(2).sum() * 10_000).alias("hhi"),
        (pl.col("cum") < 0.5).sum().alias("_below") + 1,
    ).rename({"_below": "to_50pct"})


# SIC divisions (1987 SIC manual): letter, first and last 2-digit major group, name.
DIVISIONS = (("A", 1, 9, "Agriculture, forestry, fishing"), ("B", 10, 14, "Mining"), ("C", 15, 17, "Construction"),
             ("D", 20, 39, "Manufacturing"), ("E", 40, 49, "Transportation, communications, utilities"),
             ("F", 50, 51, "Wholesale trade"), ("G", 52, 59, "Retail trade"),
             ("H", 60, 67, "Finance, insurance, real estate"), ("I", 70, 89, "Services"),
             ("J", 91, 97, "Public administration"), ("K", 99, 99, "Nonclassifiable"))


def with_grains(df: pl.DataFrame) -> pl.DataFrame:
    """Add sic3, sic2 and division (letter) from the 4-digit sic, so any test can roll a company up to any level."""
    div = pl.lit(None, dtype=pl.Utf8)
    for letter, lo, hi, _ in reversed(DIVISIONS):
        div = pl.when((pl.col("sic") // 100).is_between(lo, hi)).then(pl.lit(letter)).otherwise(div)
    return df.with_columns((pl.col("sic") // 10).alias("sic3"), (pl.col("sic") // 100).alias("sic2"), div.alias("division"))

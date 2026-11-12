"""Amazon's annual figures from companyfacts: one value per concept and fiscal year (calendar year: FYE 12-31),
the latest filed 10-K value. Flows: 350-380 day periods ending 12-31. Instants: 12-31 balances."""
import duckdb
import polars as pl

from pharos.paths import PROJECT_ROOT, data_root

ROOT = PROJECT_ROOT.as_posix()
CATALOG = (data_root() / "catalog.duckdb").as_posix()
CIK = 1018724


def con():
    return duckdb.connect(CATALOG, read_only=True)


def annual(concepts) -> pl.DataFrame:
    """Long table: concept, yr, val, filed. Latest 10-K value per concept and year."""
    lst = ", ".join(f"'{c}'" for c in concepts)
    return con().sql(f"""
        select concept, year(end_date) as yr, val, filed, unit,
               case when start_date is null then 'instant' else 'flow' end as kind
        from serving_company_facts
        where cik = {CIK} and taxonomy in ('us-gaap', 'dei') and form in ('10-K', '10-K/A') and concept in ({lst})
          and month(end_date) = 12 and day(end_date) = 31
          and (start_date is null or date_diff('day', start_date, end_date) between 350 and 380)
        qualify row_number() over (partition by concept, end_date order by filed desc, accession desc) = 1
        order by concept, yr
    """).pl()


def wide(concepts) -> pl.DataFrame:
    return annual(concepts).pivot(on="concept", index="yr", values="val").sort("yr")

# Pharos

Pharos is a local-first financial data platform built for trustworthy,
point-in-time analysis.

It collects daily prices, revision-aware macroeconomic series, and SEC bulk
source data into Parquet. DuckDB transforms and checks each refresh, and only
approved data is published for analysis.

**Data correctness outranks features.**

## Current status

Available today:

- Daily split-adjusted prices for an 18-company watchlist
- FRED/ALFRED macro series with complete vintage history
- SEC filing indexes, submissions, company details, tickers, and companyfacts
- Versioned Parquet serving data
- Data-health checks and fault-injection tests
- Streamlit health and chart pages

SEC source data is landed and queryable, but it is not yet curated into
standardized financial statements or ratios. That work begins in M3.

## How it works

```text
Sources
   ↓
raw/       immutable snapshots of downloaded data
   ↓
staging/   candidate tables for the current run
   ↓
checks/    quality gates and monitoring checks
   ↓
serving/   versioned, approved Parquet data
   ↓
DuckDB / Streamlit / other projects
```

A failed check never overwrites the last good serving version.

Every observation distinguishes:

- `obs_date`: the period the value describes
- `available_date`: when the value became publicly knowable
- `vintage`: which revision of the value it represents
- `run_id`: the pipeline run that landed it

Cross-source analysis should join on `available_date`, not merely on period end.

## Requirements

- Windows
- Python 3.12
- [uv](https://docs.astral.sh/uv/)

## Setup

Install the locked environment:

```powershell
uv sync
```

Copy `.env.example` to `.env` and fill in:

```dotenv
FRED_API_KEY=
PHAROS_DATA_ROOT=C:\path\to\pharos\data
TIINGO_API_KEY=
SEC_USER_AGENT=Pharos your.email@example.com
```

`SEC_USER_AGENT` identifies the client to SEC EDGAR. It is not an API key or
password.

The data root must be the repository's `data/` directory.

## First run

Check the configured paths:

```powershell
uv run pharos paths
```

Refresh the nightly data sources:

```powershell
uv run pharos refresh
```

Inspect current data health:

```powershell
uv run pharos health
```

Run the dashboard:

```powershell
uv run streamlit run app/Home.py
```

The first SEC refresh establishes the large Company Facts baseline. Later
nightly runs refresh only companies with new or changed submission filings.
To deliberately replace that baseline from SEC's full bulk file, run:

```powershell
uv run pharos companyfacts
```

Submissions, filing indexes, tickers, and Company Facts are checked and
published as one SEC unit. If any part fails, the last good SEC version stays
live.

## Querying the data

Parquet files are the storage of record. `catalog.duckdb` contains views over
those files for convenient exploration. Most logical tables are one Parquet
file. The large Company Facts tables use same-named directories of per-CIK
Parquet partitions so a nightly run replaces only changed companies.

Open `data/catalog.duckdb` in DuckDB or DBeaver and query the approved data:

```sql
SELECT
    series_id,
    obs_date,
    available_date,
    vintage,
    value
FROM serving_observations
WHERE series_id = 'fred:GDP'
ORDER BY obs_date, vintage;
```

Other projects should consume `serving/` Parquet or the serving views. They
should not depend on Pharos's internal Python modules.

## Testing

Run the full suite:

```powershell
uv run pytest
```

The suite includes unit, integration, and fault-injection tests for the
data-quality controls.

## Data-use boundaries

Pharos is designed primarily for personal analysis.

Yahoo and free-tier Tiingo data have redistribution restrictions. Do not
publish or redistribute serving data without reviewing the applicable source
terms.

## Documentation

- [`docs/design.md`](docs/design.md) — architecture, schemas, time semantics,
  and health system
- [`docs/plan.md`](docs/plan.md) — current milestone, status, and open issues
- [`docs/decisions.md`](docs/decisions.md) — design-decision history
- [`CLAUDE.md`](CLAUDE.md) — repository working rules and maintainer commands

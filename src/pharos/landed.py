"""Landed sources (D127), each declared once (D176).

A landed source is one Landed entry in LANDED, below. Everything else that has
to know the source exists reads this list: its group (groups.py), its loader
(refresh.LOADERS), its stager (stage.STAGERS), the names its checks may query
(checks.BINDERS) and its command (`pharos <name>`, cli.py). Adding a source is:
a loader module in loaders/, one transform/<sql>.sql per table, one freshness
check in checks/ whose header names the group, and one entry here.

This module imports nothing from the pipeline, so anything may import it. A
loader is named, not imported: it is imported when its run loads.
"""

import importlib
from dataclasses import dataclass


@dataclass(frozen=True)
class Dataset:
    """One raw dataset and the served table staged from it (stage_landed.stage_table)."""
    source: str
    raw: str                  # raw dataset under raw/<source>/
    sql: str                  # transform/<sql>.sql, one row per raw row
    table: str                # staging table: the newest value per key
    key: tuple[str, ...]
    asserts: tuple[tuple[str, str], ...] = ()   # (condition that must match no row, what it means)


WEO_OBSERVATIONS = Dataset(
    source="imf", raw="weo_observations", sql="imf_weo_observations", table="weo_observations",
    key=("country", "indicator", "year", "vintage"),
    asserts=(("value IS NULL OR isnan(value)", "WEO values that are missing"),
             ("obs_date IS NULL OR vintage IS NULL", "WEO values with no year or no publication date")),
)

WEO_SERIES = Dataset(
    source="imf", raw="weo_series", sql="imf_weo_series", table="weo_series",
    key=("country", "indicator", "vintage"),
    asserts=(("vintage IS NULL", "WEO series with no publication date"),),
)

WPP_POPULATION = Dataset(
    source="un", raw="wpp_population", sql="un_wpp_population", table="wpp_population",
    key=("loc_id", "var_id", "year", "vintage"),
    asserts=(("mid_period IS DISTINCT FROM year + 0.5", "population rows not dated mid-year (July 1)"),
             ("loc_id IS NULL OR var_id IS NULL OR year IS NULL OR vintage IS NULL",
              "population rows with no location, variant, year or file date")),
)

FSDS_PRE = Dataset(
    source="sec", raw="fsds_pre", sql="sec_fsds_pre", table="fsds_pre",
    key=("adsh", "report", "line"),
    asserts=(("NOT regexp_full_match(adsh, '[0-9]{10}-[0-9]{2}-[0-9]{6}')",
              "presentation rows whose accession is not 0000000000-00-000000"),
             ("report IS NULL OR line IS NULL", "presentation rows whose report or line is not a number"),
             ("obs_date IS NULL OR vintage IS NULL", "presentation rows with no quarter or no file date")),
)

# Merger-enforcement statistics (D175). Each file's own arithmetic is asserted
# here. Two rows do not add up in the FTC's own file, read twice on 2026-11-15
# with the same result, and are named: FY1999 (FTC 45 + DOJ 68 = 113 against a
# total of 111) and FY1993 (granted 1,201 + not granted 448 = 1,649 against
# 1,689 requested). Any other row that fails to add up stops the group.
_YEAR = ("fiscal_year IS NULL OR obs_date IS NULL OR vintage IS NULL", "rows with no fiscal year or no retrieval date")
_CELL = ("enforced IS NULL OR closed IS NULL OR enforced < 0 OR closed < 0 OR obs_date IS NULL OR vintage IS NULL",
         "table cells with no count, a negative count, or no edition or retrieval date")

HSR_FISCAL_YEAR = Dataset(
    source="ftc", raw="hsr_fiscal_year", sql="ftc_hsr_fiscal_year", table="hsr_fiscal_year",
    key=("fiscal_year", "vintage"),
    asserts=(_YEAR,
             ("second_requests IS DISTINCT FROM second_requests_ftc + second_requests_doj AND fiscal_year <> 1999",
              "fiscal years whose FTC and DOJ second requests do not add to the total"),
             ("early_termination_requested IS DISTINCT FROM early_termination_granted + early_termination_not_granted "
              "AND fiscal_year <> 1993",
              "fiscal years whose early terminations granted and not granted do not add to those requested"),
             ("adjusted_transactions > transactions_reported OR second_requests > adjusted_transactions",
              "fiscal years with more adjusted transactions than reported, or more second requests than adjusted")),
)

FTC_MERGER_ACTIONS = Dataset(
    source="ftc", raw="merger_actions", sql="ftc_merger_actions", table="ftc_merger_actions",
    key=("fiscal_year", "vintage"),
    asserts=(_YEAR,
             ("total IS DISTINCT FROM consents + injunctions + part3 + abandoned",
              "fiscal years whose FTC actions by type do not add to the total")),
)

DOJ_MERGER_OUTCOMES = Dataset(
    source="doj", raw="merger_outcomes", sql="doj_merger_outcomes", table="doj_merger_outcomes",
    key=("fiscal_year", "vintage"),
    asserts=(_YEAR,
             ("complaints IS DISTINCT FROM litigation_complaints + simultaneous_settlement",
              "fiscal years whose DOJ complaints by kind do not add to complaints filed"),
             ("merger_outcomes IS DISTINCT FROM abandoned + resolved_concerns",
              "fiscal years whose DOJ outcomes without a complaint do not add to their total")),
)

FTC_HMID_HHI = Dataset(
    source="ftc", raw="hmid_hhi", sql="ftc_hmid_hhi", table="ftc_hmid_hhi",
    key=("edition_end_fy", "table_no", "post_merger_hhi", "delta_hhi", "vintage"),
    asserts=(_CELL,),
)

FTC_HMID_COMPETITORS = Dataset(
    source="ftc", raw="hmid_competitors", sql="ftc_hmid_competitors", table="ftc_hmid_competitors",
    key=("edition_end_fy", "table_no", "significant_competitors", "vintage"),
    asserts=(_CELL,),
)


@dataclass(frozen=True)
class Landed:
    name: str                       # the group, and the command: `pharos <name>`
    title: str                      # what the command's last line calls the source
    help: str                       # the command's line in `pharos --help`
    loader: str                     # "<module>:<function>" in pharos.loaders; called with the run_id
    datasets: tuple[Dataset, ...]   # each is one required raw dataset and one served table

    def load(self, run_id: str) -> None:
        module, function = self.loader.split(":")
        getattr(importlib.import_module(f"pharos.loaders.{module}"), function)(run_id)


# Each is its own on-demand group, so one source being down never holds back
# another (D174). Their tables sit beside observations, not in it: annual
# values, forecasts dated in the future and presentation rows are shapes the
# prices_macro checks have no case for (C09, C17).
LANDED: tuple[Landed, ...] = (
    Landed(name="imf", title="IMF World Economic Outlook", loader="imf:load_weo",
           help="land the IMF World Economic Outlook (every country and indicator) and publish it; seconds (D174)",
           datasets=(WEO_OBSERVATIONS, WEO_SERIES)),
    Landed(name="un", title="UN World Population Prospects", loader="un:load_population",
           help="land the UN World Population Prospects population file if it changed and publish it (D174)",
           datasets=(WPP_POPULATION,)),
    Landed(name="fsds", title="SEC Financial Statement Data Sets", loader="sec_fsds:load_pre",
           help="land SEC Financial Statement Data Sets pre.txt for every complete quarter not yet landed "
                "and publish it (D174)",
           datasets=(FSDS_PRE,)),
    # The loader downloads nothing: it lands five files placed by hand in
    # <data root>/manual/antitrust/ (ftc.gov refuses scripted requests, D175).
    Landed(name="antitrust", title="FTC and DOJ merger statistics", loader="antitrust:load",
           help="land the FTC and DOJ merger-enforcement files placed in <data root>/manual/antitrust/ "
                "and publish them; downloads nothing (D175)",
           datasets=(HSR_FISCAL_YEAR, FTC_MERGER_ACTIONS, DOJ_MERGER_OUTCOMES, FTC_HMID_HHI, FTC_HMID_COMPETITORS)),
)

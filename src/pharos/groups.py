"""Source groups (D81, D82): the unit that is loaded, staged, gated and published.

A group declares the raw datasets it needs (required: a run missing one leaves
the group out of staging; reference: loaded every run, never required, D36) and
the tables it stages and serves. Each check's header names its group: a gate
check blocks only its own group, and reads only its own group's tables and raw
data. Checks about the run itself (the age of the last publish, which groups
were carried forward) declare `group: run`; they are monitors and never gate.

M1's data is the one registered group, prices_macro. SEC is declared here
(M2 step 2) but joins GROUPS only in the commit that gives it a stager, a
check binder and its loaders: registered before then, every nightly would
record it never_published, and C36 turns red on that (D83).
"""

from dataclasses import dataclass

RUN = "run"   # the group field of a check about the run itself


@dataclass(frozen=True)
class Group:
    name: str
    required: tuple[tuple[str, str], ...]    # (source, dataset): all needed for the group to stage
    reference: tuple[tuple[str, str], ...]   # loaded every run, never required
    tables: tuple[str, ...]                  # staged, checked and served
    inputs: tuple[str, ...]                  # what its view-time checks read, copied into a version's inputs/ (D76)

    @property
    def datasets(self) -> tuple[tuple[str, str], ...]:
        return self.required + self.reference


PRICES_MACRO = Group(
    name="prices_macro",
    required=(("yf", "prices"), ("fred", "observations"), ("fred", "series")),
    reference=(("tiingo", "prices"),),
    tables=("observations", "series_catalog"),
    inputs=("raw_tiingo_prices", "watchlist_windows", "fred_expected", "required_series",
            "trading_days", "reclassified", "settings"),
)

# SEC bulk data at the landed level (D127, D128). Tables are stage_sec's merges
# (D132); no view-time inputs until its freshness checks (M2 step 4)
SEC = Group(
    name="sec",
    required=(("sec", "full_index"), ("sec", "submission_details"), ("sec", "submission_filings"),
              ("sec", "tickers")),
    reference=(),
    tables=("filings", "submission_filings", "submission_details", "tickers"),
    inputs=(),
)

# registered groups: loaded, staged and published by refresh. SEC joins when wired (above)
GROUPS: dict[str, Group] = {g.name: g for g in (PRICES_MACRO,)}

# What happened to a group in one run (manifest and serving/<run>/groups.parquet):
PUBLISHED = "published"              # this run's staged tables are served
CARRIED_FORWARD = "carried_forward"  # it failed this run; the previous version's tables are served (D81)
NEVER_PUBLISHED = "never_published"  # it failed and has never published: the version has none (D83)
BLOCKED = "blocked"                  # the run published nothing: a gate check failed
FAILED = "failed"                    # the run published nothing: it failed to load, stage or be checked

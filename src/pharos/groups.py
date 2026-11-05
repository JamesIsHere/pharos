"""Source groups (D81, D82): the unit that is loaded, staged, gated and published.

A group declares the raw datasets it needs (required: a run missing one leaves
the group out of staging; reference: loaded every run, never required, D36) and
the tables it stages and serves. Each check's header names its group: a gate
check blocks only its own group, and reads only its own group's tables and raw
data. Checks about the run itself (the age of the last publish, which groups
were carried forward) declare `group: run`; they are monitors and never gate.

Three registered groups: prices_macro (M1), sec (M2) and companyfacts (M2,
on demand). A group joins GROUPS
only with its stager, check binder and loaders: registered without them,
every nightly would record it never_published, and C36 turns red (D83).

A nightly group is attempted by every refresh. An on-demand group (D135) is
attempted only by its own command's run, and a run attempts nothing else: in
any run, a group it didn't attempt is `not_run`, served unchanged from the
current version, and C35/C36 don't count it.
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
    on_demand: bool = False                  # attempted only by its own command, never by refresh (D135)

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

# SEC companyfacts (D57, D128): landed on demand by `pharos companyfacts`,
# never by refresh (D131, D135). No view-time inputs until its freshness check
COMPANYFACTS = Group(
    name="companyfacts",
    required=(("sec", "companyfacts"), ("sec", "companyfacts_concepts")),
    reference=(),
    tables=("company_facts", "company_concepts"),
    inputs=(),
    on_demand=True,
)

# registered groups: loaded, staged and published by their runs
GROUPS: dict[str, Group] = {g.name: g for g in (PRICES_MACRO, SEC, COMPANYFACTS)}

# What happened to a group in one run (manifest and serving/<run>/groups.parquet):
PUBLISHED = "published"              # this run's staged tables are served
CARRIED_FORWARD = "carried_forward"  # it failed this run; the previous version's tables are served (D81)
NEVER_PUBLISHED = "never_published"  # it failed and has never published: the version has none (D83)
BLOCKED = "blocked"                  # the run published nothing: a gate check failed
FAILED = "failed"                    # the run published nothing: it failed to load, stage or be checked
NOT_RUN = "not_run"                  # the run didn't attempt it (D135): served unchanged from the current version
# the outcomes under which a version serves a group's tables (from source_run;
# a not_run group that has never published has none)
HELD = (PUBLISHED, CARRIED_FORWARD, NOT_RUN)


def nightly() -> list[Group]:
    """The registered groups refresh attempts."""
    return [g for g in GROUPS.values() if not g.on_demand]

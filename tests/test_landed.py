"""The landed groups (imf, un, fsds; D174): the loaders' parsing and stage_landed's
newest-value-per-key rule. No test here reaches a network."""

from datetime import date, datetime, timezone

import duckdb
import pandas as pd
import pytest

from pharos.loaders import imf, sec_fsds
from pharos.runs import write_raw
from pharos.stage import StageError
from pharos.landed import WEO_OBSERVATIONS
from pharos.stage_landed import stage_table

RUN_A, RUN_B = "20261101T120000Z", "20261102T120000Z"

WEO = """<?xml version="1.0"?>
<message:StructureSpecificData xmlns:message="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message"
    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <message:DataSet PUBLICATION_DATE="2026-10-13T02:00:00Z" UPDATE_DATE="2026-10-13T02:00:00Z">
    <Group xsi:type="g" INDICATOR="NGDPD" SERIES_NAME="GDP" UNIT="USD"/>
    <Group xsi:type="g" INDICATOR="NGDPD" COUNTRY="USA" LATEST_ACTUAL_ANNUAL_DATA="2025"/>
    <Series COUNTRY="USA" INDICATOR="NGDPD" FREQUENCY="{freq}" SCALE="9" COUNTRY_UPDATE_DATE="9/30/2026"{extra}>
      <Obs TIME_PERIOD="2024" OBS_VALUE="29381025000000"/>
      <Obs TIME_PERIOD="2025" OBS_VALUE="{value}"/>
    </Series>
  </message:DataSet>
</message:StructureSpecificData>"""


def weo(freq="A", extra="", value="30500000000000"):
    return WEO.format(freq=freq, extra=extra, value=value).encode()


def test_weo_parse_reads_values_series_and_the_edition():
    obs, series = imf.parse(weo())
    assert obs[["country", "indicator", "year", "value"]].values.tolist() == [
        ["USA", "NGDPD", 2024, 29381025000000.0], ["USA", "NGDPD", 2025, 30500000000000.0]]
    assert obs["publication_date"].iloc[0] == datetime(2026, 10, 13, 2, tzinfo=timezone.utc)
    row = series.iloc[0]
    assert (row["scale"], row["latest_actual_annual_data"], row["series_name"], row["unit"]) == \
        ("9", "2025", "GDP", "USD")


@pytest.mark.parametrize("kwargs", [{"freq": "Q"}, {"extra": ' NEW_ATTRIBUTE="x"'}, {"value": "n/a"}])
def test_weo_parse_stops_on_drift(kwargs):
    with pytest.raises(imf.SchemaDriftError):
        imf.parse(weo(**kwargs))


def test_fsds_due_lists_complete_quarters_not_yet_landed():
    assert sec_fsds.due(date(2026, 11, 14), "2026q3", set()) == ["2026q3"]
    assert sec_fsds.due(date(2027, 4, 2), "2026q3", {"2026q3"}) == ["2026q4", "2027q1"]
    assert sec_fsds.due(date(2026, 11, 14), "2026q3", {"2026q3"}) == []
    with pytest.raises(sec_fsds.SchemaDriftError):
        sec_fsds.due(date(2026, 11, 14), "2026Q3", set())


def land(run_id, rows, published="2026-10-13"):
    stamp = pd.Timestamp(published, tz="UTC")
    frame = pd.DataFrame(rows, columns=["country", "indicator", "frequency", "year", "value"])
    write_raw(frame.assign(publication_date=stamp, update_date=stamp), "imf", "weo_observations", run_id)


def staged(runs):
    con = duckdb.connect()
    stage_table(con, WEO_OBSERVATIONS, runs)
    return con.execute("SELECT year, value, vintage, run_id FROM weo_observations ORDER BY year, vintage").fetchall()


def test_landing_the_same_edition_twice_changes_nothing(root):
    rows = [("USA", "NGDPD", "A", 2024, 1.0), ("USA", "NGDPD", "A", 2030, 2.0)]
    land(RUN_A, rows)
    land(RUN_B, rows)
    assert staged([RUN_A, RUN_B]) == [(2024, 1.0, date(2026, 10, 13), RUN_A), (2030, 2.0, date(2026, 10, 13), RUN_A)]


def test_a_changed_value_under_one_edition_takes_the_newest_landing(root):
    land(RUN_A, [("USA", "NGDPD", "A", 2024, 1.0)])
    land(RUN_B, [("USA", "NGDPD", "A", 2024, 1.5)])
    assert staged([RUN_A, RUN_B]) == [(2024, 1.5, date(2026, 10, 13), RUN_B)]


def test_a_new_edition_adds_a_vintage_and_keeps_the_old_one(root):
    land(RUN_A, [("USA", "NGDPD", "A", 2024, 1.0)])
    land(RUN_B, [("USA", "NGDPD", "A", 2024, 1.2)], published="2027-04-14")
    assert staged([RUN_A, RUN_B]) == [(2024, 1.0, date(2026, 10, 13), RUN_A), (2024, 1.2, date(2027, 4, 14), RUN_B)]


def test_one_landing_saying_two_things_for_a_key_stops_the_group(root):
    land(RUN_A, [("USA", "NGDPD", "A", 2024, 1.0), ("USA", "NGDPD", "A", 2024, 9.0)])
    with pytest.raises(StageError, match="different values inside one landing"):
        staged([RUN_A])

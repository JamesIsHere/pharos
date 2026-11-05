"""The config files are the declared expectation. These tests stop a bad edit to them
from ever reaching a run: a typo here would otherwise surface as a false alarm
(or a missed one) in the health checks."""

import csv
from datetime import date

import yaml

from pharos.paths import PROJECT_ROOT

CONFIG = PROJECT_ROOT / "config"
WATCHLIST_COLUMNS = ["ticker", "yahoo_symbol", "cik", "trap", "active_from", "active_to"]


def load_watchlist() -> list[dict]:
    with open(CONFIG / "watchlist.csv", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_sources() -> dict:
    with open(CONFIG / "sources.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_watchlist_columns_exact():
    with open(CONFIG / "watchlist.csv", newline="", encoding="utf-8") as f:
        assert next(csv.reader(f)) == WATCHLIST_COLUMNS


def test_watchlist_has_18_unique_names():
    rows = load_watchlist()
    assert len(rows) == 18
    assert len({r["ticker"] for r in rows}) == 18
    assert len({r["yahoo_symbol"] for r in rows}) == 18


def test_every_name_states_its_trap():
    assert all(r["trap"].strip() for r in load_watchlist())


def test_active_windows_are_valid_dates_after_backfill_start():
    start = load_sources()["backfill_start"]
    for r in load_watchlist():
        lo = date.fromisoformat(r["active_from"]) if r["active_from"] else None
        hi = date.fromisoformat(r["active_to"]) if r["active_to"] else None
        if lo:
            assert lo > start, f"{r['ticker']}: active_from before backfill_start is meaningless"
        if lo and hi:
            assert lo < hi, f"{r['ticker']}: window ends before it starts"


def test_sources_yahoo_settings_explicit():
    yahoo = load_sources()["yahoo"]
    assert yahoo["auto_adjust"] is False
    assert isinstance(yahoo["overlap_days"], int) and yahoo["overlap_days"] > 0
    assert yahoo["required_series"] == ["close"]


def test_sources_fred_series_unique():
    series = load_sources()["fred"]["series"]
    assert series and len(series) == len(set(series))


def test_every_fred_series_has_an_expected_lag():
    cfg = load_sources()
    lags = cfg["fred"]["expected_lag_days"]
    assert sorted(lags) == sorted(cfg["fred"]["series"])
    assert all(isinstance(v, int) and v >= 0 for v in lags.values())
    assert isinstance(cfg["yahoo"]["expected_lag_days"], int)


def test_corporate_actions_fail_loudly(tmp_path, monkeypatch):
    import pytest
    from pharos import config
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    (tmp_path / "corporate_actions.csv").write_text("ticker,event_date,note\nGOOGL,2014-04-03,x\nGOOGL,2014-04-03,y\n")
    with pytest.raises(ValueError, match="repeated"):
        config.corporate_actions()
    (tmp_path / "corporate_actions.csv").write_text("ticker,event_date,note\nGOOGL,2014-04-03, \n")
    with pytest.raises(ValueError, match="no note"):
        config.corporate_actions()


def test_live_corporate_actions_parse():
    from datetime import date
    from pharos import config
    assert ("GOOGL", date(2014, 4, 3)) in [(r["ticker"], r["event_date"]) for r in config.corporate_actions()]


def test_expected_states_fail_loudly(tmp_path, monkeypatch):
    import pytest
    from pharos import config
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    head = "check_id,key,note\n"
    (tmp_path / "expected_states.csv").write_text(head + "C16,fred:GDP | 2026-04-01,x\nC16,fred:GDP|2026-04-01,y\n")
    with pytest.raises(ValueError, match="repeated"):
        config.expected_states()
    (tmp_path / "expected_states.csv").write_text(head + "C15,yf:close:ATVI, \n")
    with pytest.raises(ValueError, match="no note"):
        config.expected_states()
    (tmp_path / "expected_states.csv").write_text(head + "C16,fred:GDP | ,x\n")
    with pytest.raises(ValueError, match="empty part"):
        config.expected_states()
    (tmp_path / "expected_states.csv").write_text("check_id,series_id,obs_date,note\nC15,yf:close:ATVI,,x\n")
    with pytest.raises(ValueError, match="columns must be"):
        config.expected_states()


def test_live_expected_states_name_warn_checks():
    from pharos import health
    assert [(s["check_id"], s["key"]) for s in health.acknowledgments()] == [
        ("C15", ("yf:close:ATVI",)), ("C16", ("fred:CPIAUCSL", "2025-10-01"))]


# --- SEC form lists (M2 step 1e, D124) ----------------------------------------

def _form_list(name):
    import csv
    from pharos.config import CONFIG_DIR
    with open(CONFIG_DIR / name, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_form_lists_are_well_formed_and_disjoint():
    annual = _form_list("annual_forms.csv")
    event = _form_list("event_forms.csv")
    ignored = _form_list("ignored_forms.csv")
    assert list(annual[0]) == ["code", "scope", "note"]
    assert list(event[0]) == ["code", "event", "scope", "listing_role", "listing_from", "listing_to", "note"]
    assert list(ignored[0]) == ["code", "reason"]
    codes = [r["code"] for r in annual + event + ignored]
    assert len(codes) == len(set(codes)), "a code is in two lists, or twice in one"
    assert all(r["scope"] in ("domestic", "foreign") and r["note"] for r in annual)
    assert all(r["event"] in ("registration", "delisting", "deregistration") for r in event)
    assert all(r["scope"] in ("domestic", "foreign", "any") and r["note"] for r in event)
    assert all(r["listing_role"] in ("starts", "ends", "none") for r in event)
    assert all(r["reason"] for r in ignored)
    assert not any(r["code"].endswith("/A") for r in annual + event)   # amendments never count (D54)


def test_listing_role_dates_follow_d89():
    from datetime import date
    event = {r["code"]: r for r in _form_list("event_forms.csv")}
    for r in event.values():
        for column in ("listing_from", "listing_to"):
            if r[column]:
                date.fromisoformat(r[column])
                assert r["listing_role"] == "ends", f"{r['code']}: a date on a code that ends no listing"
    assert (event["15-12B"]["listing_to"], event["15-12G"]["listing_from"]) == ("2022-06-16", "2022-06-17")
    assert {c for c, r in event.items() if r["listing_role"] == "starts"} == {"8-A12B"}

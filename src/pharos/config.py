"""Reads the config files. Every module gets config from here, so there is one
parser and one interpretation of each field."""

import csv
from datetime import date

import yaml

from pharos.paths import PROJECT_ROOT

CONFIG_DIR = PROJECT_ROOT / "config"


def sources() -> dict:
    with open(CONFIG_DIR / "sources.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def watchlist() -> list[dict]:
    """Watchlist rows, with active_from / active_to as dates (None when blank).
    A blank active_from means the backfill start; a blank active_to means still trading."""
    with open(CONFIG_DIR / "watchlist.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["active_from"] = date.fromisoformat(r["active_from"]) if r["active_from"] else None
        r["active_to"] = date.fromisoformat(r["active_to"]) if r["active_to"] else None
    return rows


def corporate_actions() -> list[dict]:
    """Reviewed reclassifications for the reference source (D40): each row is a
    Tiingo cash distribution the reconciliation treats as a split. Fails loudly
    on a bad date or a repeated row; an unlisted distribution stays a dividend."""
    with open(CONFIG_DIR / "corporate_actions.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        if not r["note"].strip():
            raise ValueError(f"corporate_actions.csv: {r['ticker']} {r['event_date']} has no note")
        r["event_date"] = date.fromisoformat(r["event_date"])
    keys = [(r["ticker"], r["event_date"]) for r in rows]
    if len(set(keys)) != len(keys):
        raise ValueError(f"corporate_actions.csv: repeated rows {sorted(k for k in keys if keys.count(k) > 1)}")
    return rows


def expected_states() -> list[dict]:
    """Reviewed acknowledgments of warn-check rows (D46): each names one failing
    row by check_id, series_id and obs_date (blank: the row has no obs_date).
    Fails loudly on a missing note, a bad date or a repeated row; whether the
    check exists and only warns is health's to verify, against checks/."""
    with open(CONFIG_DIR / "expected_states.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        if not r["note"].strip():
            raise ValueError(f"expected_states.csv: {r['check_id']} {r['series_id']} has no note")
        r["obs_date"] = date.fromisoformat(r["obs_date"]).isoformat() if r["obs_date"] else None
    keys = [(r["check_id"], r["series_id"], r["obs_date"]) for r in rows]
    if len(set(keys)) != len(keys):
        raise ValueError(f"expected_states.csv: repeated rows {sorted(k for k in keys if keys.count(k) > 1)}")
    return rows

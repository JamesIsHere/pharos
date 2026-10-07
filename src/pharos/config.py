"""Reads the two config files. Every module gets config from here, so there is one
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

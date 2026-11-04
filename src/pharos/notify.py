"""The status email: a few lines after every run, so a silent failure is seen (#96).

Sent at the end of refresh(), after the run manifest and health, crashed runs
included. Built from the run's own manifest (health/run_manifest/<run_id>.parquet)
and the health status, so it says what the dashboard would say.

Never fails a run: any problem sending (no settings, wrong password, no network)
is written to <data root>/logs/notify.log and swallowed. The test suite
(PHAROS_TEST_MODE=1) never sends.

Settings, in .env (gitignored; the repo is public):
  PHAROS_MAIL_FROM          sending Gmail address
  PHAROS_MAIL_TO            where it lands (default: the sender)
  PHAROS_MAIL_APP_PASSWORD  a Google app password for the sender, not its login password
"""

import os
import smtplib
import traceback
from datetime import datetime, timezone
from email.message import EmailMessage

import duckdb

from pharos.paths import data_root

SMTP_HOST, SMTP_PORT = "smtp.gmail.com", 465


def compose(run_id: str, health) -> EmailMessage:
    """Subject and body for one run, from its manifest and the health result."""
    manifest = data_root() / "health" / "run_manifest" / f"{run_id}.parquet"
    rows = []
    if manifest.exists():
        rows = duckdb.connect().execute(
            f"SELECT * FROM read_parquet('{manifest.as_posix()}') ORDER BY source, dataset").pl().to_dicts()
    run_status = rows[0]["status"] if rows else "unknown (no manifest)"
    color = health.status.upper() if health else "UNKNOWN"

    lines = [f"Pharos ran. Health {color}, run {run_status}.", ""]
    if rows:
        r = rows[0]
        minutes = (r["finished_at"] - r["started_at"]).total_seconds() / 60
        lines += [f"Run {run_id}, {minutes:.1f} min.",
                  f"Checks: {r['checks_pass']} pass, {r['checks_warn']} warn, {r['checks_error']} error, "
                  f"{r['checks_broken']} broken, {r['checks_acknowledged']} acknowledged.", ""]
        for d in rows:
            landed = "-" if d["rows_landed"] is None else f"{d['rows_landed']:,} rows"
            latest = f", latest {d['latest_obs_date']}" if d["latest_obs_date"] else ""
            failed = "  FAILED" if d["dataset_error"] else ""
            lines.append(f"  {d['source']}/{d['dataset']}: {landed}{latest}{failed}")
    if health and health.reasons:
        lines += ["", "Why it isn't green:"] + [f"  - {why}" for why in health.reasons]
    if rows and rows[0]["error"]:
        lines += ["", "Error:", rows[0]["error"].strip().splitlines()[-1]]

    msg = EmailMessage()
    msg["Subject"] = f"Pharos {color}: run {run_status}"
    msg.set_content("\n".join(lines) + "\n")
    return msg


def send_status(run_id: str, health, smtp=None) -> bool:
    """Compose and send; True if sent. Never raises. `smtp` replaces the Gmail
    connection in tests; without it the test suite never sends."""
    if smtp is None:
        if os.environ.get("PHAROS_TEST_MODE") == "1":
            return False
        smtp = smtplib.SMTP_SSL
    try:
        sender = os.environ.get("PHAROS_MAIL_FROM")
        password = os.environ.get("PHAROS_MAIL_APP_PASSWORD")
        if not sender or not password:
            raise RuntimeError("PHAROS_MAIL_FROM or PHAROS_MAIL_APP_PASSWORD not set in .env")
        msg = compose(run_id, health)
        msg["From"] = sender
        msg["To"] = os.environ.get("PHAROS_MAIL_TO") or sender
        with smtp(SMTP_HOST, SMTP_PORT, timeout=30) as conn:
            conn.login(sender, password)
            conn.send_message(msg)
        return True
    except Exception:
        _log(run_id, traceback.format_exc(limit=3))
        return False


def _log(run_id: str, text: str) -> None:
    try:
        folder = data_root() / "logs"
        folder.mkdir(parents=True, exist_ok=True)
        with open(folder / "notify.log", "a", encoding="utf-8") as f:
            f.write(f"{datetime.now(timezone.utc).isoformat()} run {run_id}: email not sent\n{text}\n")
    except Exception:
        pass   # the email is a reminder; nothing about it may stop a run

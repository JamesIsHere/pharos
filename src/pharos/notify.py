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
        seconds = (r["finished_at"] - r["started_at"]).total_seconds()
        took = f"{seconds:.0f} s" if seconds < 90 else f"{seconds / 60:.1f} min"
        lines += [f"Run {run_id}, took {took}.",
                  f"Checks: {r['checks_pass']} pass, {r['checks_warn']} warn, {r['checks_error']} error, "
                  f"{r['checks_broken']} broken, {r['checks_acknowledged']} acknowledged.", ""]
        served = _served_rows()
        root = data_root()
        for d in rows:
            source, dataset = d["source"], d["dataset"]
            got = "no rows" if d["rows_landed"] is None else f"{d['rows_landed']:,} rows downloaded this run"
            if dataset == "observations" or (source == "yf" and dataset == "prices"):
                total = f", {served.get(source, 0):,} served"
            elif source == "tiingo":
                total = ", cross-check only, not served"
            else:
                total = ""
            latest = f", latest {d['latest_obs_date']}" if d["latest_obs_date"] else ""
            size = f", {_human(_size(root / 'raw' / source / dataset))} on disk"
            failed = "  FAILED" if d["dataset_error"] else ""
            lines.append(f"  {source}/{dataset}: {got}{total}{latest}{size}{failed}")
        outcomes = sorted({(d["group"], d["group_outcome"], d["group_source_run"], d["group_reason"])
                           for d in rows if d.get("group")}, key=lambda x: x[0])
        if outcomes:
            lines += ["", "Source groups:"]
            for name, outcome, src, why in outcomes:
                frm = f" from {src}" if src and outcome == "carried_forward" else ""
                because = f": {why.strip().splitlines()[-1]}" if why else ""
                lines.append(f"  {name}: {outcome}{frm}{because}")
        parts = [(p.name, _size(p)) for p in sorted(root.iterdir()) if p.is_dir()]
        lines += ["", f"Data folder: {_human(_size(root))} in all ({root}):",
                  "  " + ", ".join(f"{name} {_human(n)}" for name, n in sorted(parts, key=lambda x: -x[1]))]
    if health and health.reasons:
        lines += ["", "Why it isn't green:"] + [f"  - {why}" for why in health.reasons]
    if rows and rows[0]["error"]:
        lines += ["", "Error:", rows[0]["error"].strip().splitlines()[-1]]

    msg = EmailMessage()
    msg["Subject"] = f"Pharos {color}: run {run_status}"
    msg.set_content("\n".join(lines) + "\n")
    return msg


def _served_rows() -> dict[str, int]:
    """Rows in the version the dashboard reads, by source (yf, fred)."""
    from pharos.publish import current_version
    version = current_version()
    if version is None:
        return {}
    path = (version / "observations.parquet").as_posix()
    return dict(duckdb.connect().execute(
        f"SELECT split_part(series_id, ':', 1), count(*) FROM read_parquet('{path}') GROUP BY 1").fetchall())


def _size(path) -> int:
    """Bytes under a folder (0 if it doesn't exist)."""
    total = 0
    for folder, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(folder, f))
            except OSError:
                pass
    return total


def _human(n: int) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:,.0f} {unit}" if unit == "bytes" else f"{n:,.1f} {unit}"
        n /= 1024


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

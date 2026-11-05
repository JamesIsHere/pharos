"""The one door to SEC EDGAR: every SEC loader gets its bytes through fetch().

Rules applied here (CLAUDE.md Sources, M2 step 1):
- User-Agent from SEC_USER_AGENT; unset is an error before any request.
- At most 10 requests a second: fetch() waits MIN_INTERVAL between request
  starts, across every SEC loader in the process.
- One retry on HTTP 503 (SEC's throttle answer), after RETRY_WAIT seconds.
  Every other HTTP or network error fails the load loudly.
- Each response carries its HTTP Last-Modified, for the freshness checks
  (C25, C31, C34; D60, D91).

fetch is a module attribute that loaders call as `sec.fetch(url)`, so step 7's
pass 1 can swap in a reader over the spike's downloaded files without a
production flag (D94). A swapped fetch returns the same Fetched shape.
"""

import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime

from pharos.paths import data_root

MIN_INTERVAL = 0.11   # seconds between request starts: about 9 a second, under SEC's 10
RETRY_WAIT = 2.0      # seconds before the one retry on 503
TIMEOUT = 300         # seconds; submissions.zip is about 1.5 GB


@dataclass(frozen=True)
class Fetched:
    url: str
    content: bytes
    last_modified: datetime | None   # timezone-aware, or None when the server sent none
    origin: str = "http"             # "http", or "local" for a swapped-in file reader (D94)


class SecFetchError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status   # the HTTP status, when there was one


_last_request = 0.0


def user_agent() -> str:
    data_root()   # loads .env, where SEC_USER_AGENT lives locally
    ua = os.environ.get("SEC_USER_AGENT")
    if not ua:
        raise SecFetchError("SEC_USER_AGENT is not set (.env locally, GitHub Secrets in CI)")
    return ua


def _wait_turn() -> None:
    global _last_request
    gap = time.monotonic() - _last_request
    if gap < MIN_INTERVAL:
        time.sleep(MIN_INTERVAL - gap)
    _last_request = time.monotonic()


def _last_modified(value: str | None) -> datetime | None:
    if value is None:
        return None
    stamp = parsedate_to_datetime(value)   # RFC 7231 dates are GMT
    if stamp.tzinfo is None:
        raise SecFetchError(f"Last-Modified {value!r} has no timezone")
    return stamp


def http_fetch(url: str) -> Fetched:
    """GET url from SEC. One retry on 503; any other failure raises."""
    request = urllib.request.Request(url, headers={"User-Agent": user_agent(),
                                                   "Accept-Encoding": "identity"})
    for attempt in (1, 2):
        _wait_turn()
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as resp:
                return Fetched(url, resp.read(), _last_modified(resp.headers.get("Last-Modified")))
        except urllib.error.HTTPError as e:
            if e.code == 503 and attempt == 1:
                time.sleep(RETRY_WAIT)
                continue
            raise SecFetchError(f"GET {url}: HTTP {e.code} {e.reason}", e.code) from e
        except urllib.error.URLError as e:
            raise SecFetchError(f"GET {url}: {e.reason}") from e
    raise AssertionError("unreachable")


fetch = http_fetch

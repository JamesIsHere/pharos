"""One printed line when a run step starts and when it ends, for watching a
long run in its log (2026-11-11). Output only: nothing here changes what a
step computes, and a failing step's line says `failed` before the exception
goes on unchanged.

    07:31:34 +1:29  sec stage submission_filings  done 3.2s
"""

import time
from contextlib import contextmanager
from datetime import datetime

_START = time.monotonic()


def _clock(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def say(text: str) -> None:
    print(f"{datetime.now():%H:%M:%S} +{_clock(time.monotonic() - _START)}  {text}", flush=True)


@contextmanager
def step(label: str):
    say(f"{label}  start")
    t0 = time.monotonic()
    try:
        yield
    except BaseException:
        say(f"{label}  failed after {time.monotonic() - t0:.1f}s")
        raise
    say(f"{label}  done {time.monotonic() - t0:.1f}s")

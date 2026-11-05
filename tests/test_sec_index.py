"""SEC fetch (sec.py) and the full-index loader (sec_index.py), with no network:
sec.fetch is swapped for a fake, as step 7's pass 1 will swap it (D94)."""

import gzip
import io
import urllib.error
from datetime import date, datetime, timezone

import duckdb
import pytest

from pharos.loaders import sec, sec_index
from pharos.loaders.sec_index import SchemaDriftError, due, load_index, parse, quarters
from pharos.paths import data_root

PREAMBLE = ("Description:           Master Index of EDGAR Dissemination Feed\n"
            "Last Data Received:    November 3, 2026\n\n\n")
STAMP = datetime(2026, 11, 4, 6, 0, tzinfo=timezone.utc)


def master(rows: list[str], header: str = sec_index.HEADER, dashes: str = "-" * 80) -> bytes:
    return gzip.compress((PREAMBLE + header + "\n" + dashes + "\n" + "".join(r + "\n" for r in rows))
                         .encode("latin-1"))


def row(cik: int, form: str = "10-K", day: str = "2026-10-06") -> str:
    return f"{cik}|COMPANY {cik}|{form}|{day}|edgar/data/{cik}/0000000000-26-{cik:06d}.txt"


@pytest.fixture
def fake(monkeypatch, root):
    """sec.fetch over a dict {url: bytes}; records every URL asked for."""
    pages, asked = {}, []

    def fetch(url):
        asked.append(url)
        if url not in pages:
            raise sec.SecFetchError(f"GET {url}: HTTP 404 Not Found", 404)
        return sec.Fetched(url, pages[url], STAMP)

    monkeypatch.setattr(sec, "fetch", fetch)
    return pages, asked


@pytest.fixture
def root(monkeypatch, tmp_path):
    monkeypatch.setenv("PHAROS_TEST_MODE", "1")
    monkeypatch.setenv("PHAROS_DATA_ROOT", str(tmp_path))
    return tmp_path


def url(y, q):
    return sec_index.URL.format(year=y, q=q)


def every_quarter(pages, through=(2026, 4), rows_for=lambda yq: [row(1000 + yq[1])]):
    for yq in quarters(through):
        pages[url(*yq)] = master(rows_for(yq))


def raw_rows(run_id):
    path = data_root() / "raw" / "sec" / "full_index" / f"{run_id}.parquet"
    return duckdb.sql(f"SELECT * FROM read_parquet('{path.as_posix()}')")


# --- quarter selection ---------------------------------------------------------

def test_quarters_run_from_1993q1():
    qs = quarters((1994, 2))
    assert qs == [(1993, 1), (1993, 2), (1993, 3), (1993, 4), (1994, 1), (1994, 2)]


def test_first_run_lands_every_quarter(fake):
    pages, asked = fake
    every_quarter(pages)
    counts = load_index("20261105T073000Z", today=date(2026, 11, 5))
    assert len(counts) == len(quarters((2026, 4))) == 136
    assert len(asked) == 136
    assert raw_rows("20261105T073000Z").aggregate("count(*), count(DISTINCT quarter)").fetchone() == (136, 136)


def test_later_run_relands_current_and_previous_only(fake):
    pages, asked = fake
    every_quarter(pages)
    load_index("20261105T073000Z", today=date(2026, 11, 5))
    asked.clear()
    load_index("20261106T073000Z", today=date(2026, 11, 6))
    assert asked == [url(2026, 3), url(2026, 4)]


def test_a_missing_quarter_is_landed_next_run(fake):
    pages, _ = fake
    every_quarter(pages)
    load_index("20261105T073000Z", today=date(2026, 11, 5))
    # a raw file that lost 2001Q2 would be C05's; here 2001Q2 simply never landed
    path = data_root() / "raw" / "sec" / "full_index" / "20261105T073000Z.parquet"
    path.unlink()
    assert len(due(date(2026, 11, 6))) == 136


def test_new_quarter_starts_on_its_first_day(root):
    # load_index's default `today` is New York's date (D118's reasoning), so
    # 2027-01-01 03:00 UTC, still Dec 31 there, does not yet ask for 2027Q1
    assert due(date(2026, 12, 31))[-1] == (2026, 4)
    assert due(date(2027, 1, 1))[-1] == (2027, 1)


# --- the raw file --------------------------------------------------------------

def test_rows_land_with_quarter_and_last_modified(fake):
    pages, _ = fake
    every_quarter(pages, rows_for=lambda yq: [row(1, "10-K"), row(2, "8-A12B")] if yq == (2026, 4) else [])
    load_index("20261105T073000Z", today=date(2026, 11, 5))
    rel = raw_rows("20261105T073000Z")
    assert dict(zip(rel.columns, rel.types))["date_filed"] == "DATE"
    assert rel.order("cik").project("cik, form_type, date_filed, quarter, last_modified").fetchall() == [
        (1, "10-K", date(2026, 10, 6), "2026Q4", STAMP),
        (2, "8-A12B", date(2026, 10, 6), "2026Q4", STAMP)]


def test_load_record_carries_newest_last_modified(fake):
    pages, _ = fake
    every_quarter(pages)
    load_index("20261105T073000Z", today=date(2026, 11, 5))
    rec = data_root() / "health" / "loads" / "20261105T073000Z__sec__full_index.parquet"
    got = duckdb.sql(f"SELECT rows_downloaded, rows_written, last_modified, sha256 IS NOT NULL "
                     f"FROM read_parquet('{rec.as_posix()}')").fetchone()
    assert got == (136, 136, STAMP, True)


def test_temp_files_are_removed(fake):
    pages, _ = fake
    every_quarter(pages)
    load_index("20261105T073000Z", today=date(2026, 11, 5))
    assert not (data_root() / "tmp").exists()


# --- strict parsing: drift stops the load before anything is written ----------

@pytest.mark.parametrize("bad, match", [
    (dict(header="CIK|Company Name|Form Type|Date Filed|File Name"), "no column line"),
    (dict(dashes="=" * 80), "line of dashes"),
    (dict(rows=[row(1), "1|ONE|10-K|2026-10-06"]), "has 4 fields"),
    (dict(rows=[row(1), "1|A|B|10-K|2026-10-06|f.txt"]), "has 6 fields"),
])
def test_drift_stops_the_load(fake, bad, match):
    pages, _ = fake
    every_quarter(pages)
    rows = bad.pop("rows", [row(1)])
    pages[url(2026, 4)] = master(rows, **bad)
    with pytest.raises(SchemaDriftError, match=match):
        load_index("20261105T073000Z", today=date(2026, 11, 5))
    assert not (data_root() / "raw").exists() and not (data_root() / "health" / "loads").exists()
    assert not (data_root() / "tmp").exists()


def test_a_failed_fetch_writes_nothing(fake):
    pages, _ = fake
    every_quarter(pages)
    del pages[url(2015, 2)]
    with pytest.raises(sec.SecFetchError, match="404"):
        load_index("20261105T073000Z", today=date(2026, 11, 5))
    assert not (data_root() / "raw").exists()


def test_parse_reads_the_spike_layout():
    rows = parse(master([row(860585, "13FCONP", "1993-02-11")]), "1993Q1")
    assert rows == ["860585|COMPANY 860585|13FCONP|1993-02-11|edgar/data/860585/0000000000-26-860585.txt"]


# --- sec.http_fetch ------------------------------------------------------------

class Response(io.BytesIO):
    def __init__(self, body, headers):
        super().__init__(body)
        self.headers = headers

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def http(monkeypatch, root):
    """urlopen replaced by a script of responses; sleeps recorded, not slept."""
    script, seen, sleeps = [], [], []

    def urlopen(request, timeout):
        seen.append(request)
        step = script.pop(0)
        if isinstance(step, int):
            raise urllib.error.HTTPError(request.full_url, step, "err", {}, None)
        return step

    monkeypatch.setattr(sec.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(sec.time, "sleep", sleeps.append)
    monkeypatch.setenv("SEC_USER_AGENT", "Pharos test test@example.com")
    return script, seen, sleeps


def test_fetch_sends_user_agent_and_reads_last_modified(http):
    script, seen, _ = http
    script.append(Response(b"body", {"Last-Modified": "Wed, 04 Nov 2026 06:21:00 GMT"}))
    got = sec.http_fetch("https://www.sec.gov/x")
    assert seen[0].get_header("User-agent") == "Pharos test test@example.com"
    assert (got.content, got.last_modified, got.origin) == (
        b"body", datetime(2026, 11, 4, 6, 21, tzinfo=timezone.utc), "http")


def test_fetch_retries_once_on_503(http):
    script, seen, sleeps = http
    script += [503, Response(b"ok", {})]
    assert sec.http_fetch("https://www.sec.gov/x").content == b"ok"
    assert len(seen) == 2 and sec.RETRY_WAIT in sleeps


def test_fetch_fails_on_a_second_503(http):
    script, _, _ = http
    script += [503, 503]
    with pytest.raises(sec.SecFetchError, match="HTTP 503") as e:
        sec.http_fetch("https://www.sec.gov/x")
    assert e.value.status == 503


def test_fetch_does_not_retry_other_errors(http):
    script, seen, _ = http
    script += [404, Response(b"never", {})]
    with pytest.raises(sec.SecFetchError, match="HTTP 404"):
        sec.http_fetch("https://www.sec.gov/x")
    assert len(seen) == 1


def test_fetch_spaces_requests(http, monkeypatch):
    script, _, sleeps = http
    clock = iter([100.0, 100.0, 100.02, 100.13])   # second request 0.02 s after the first
    monkeypatch.setattr(sec.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(sec, "_last_request", 0.0)
    script += [Response(b"a", {}), Response(b"b", {})]
    sec.http_fetch("https://www.sec.gov/a")
    sec.http_fetch("https://www.sec.gov/b")
    assert sleeps == [pytest.approx(sec.MIN_INTERVAL - 0.02)]


def test_missing_user_agent_fails_before_any_request(http, monkeypatch):
    _, seen, _ = http
    monkeypatch.delenv("SEC_USER_AGENT")
    monkeypatch.setattr(sec, "data_root", lambda: None)   # keep .env from refilling it
    with pytest.raises(sec.SecFetchError, match="SEC_USER_AGENT"):
        sec.http_fetch("https://www.sec.gov/x")
    assert seen == []

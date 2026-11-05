"""EDGAR bulk companyfacts.zip: every XBRL fact SEC extracted, for every filer.

Landed on demand by `pharos companyfacts`, never by the nightly refresh (D131,
D135). Each landing is a full snapshot (D57). Two raw files a run:
- raw/sec/companyfacts/<run_id>.parquet: one row per reported fact (cik,
  taxonomy, concept, unit, start, end, val, accn, fy, fp, form, filed, frame),
  values as sent. `val` is text: JSON sends ints and floats, and 46 ints in
  the 2026-11-04 file are beyond 2^53, where a DOUBLE changes them; staging
  casts. `start` and `frame` are NULL where a fact has none.
- raw/sec/companyfacts_concepts/<run_id>.parquet: one row per (cik, taxonomy,
  concept) with the filer's entity name and the concept's label and
  description, so 126M fact rows don't repeat them.

Strict shape, measured on the 2026-11-04 file (20,542 members, 126,119,741
facts): every member is CIK##########.json; a member is either empty ({},
62 of them) or has entityName and facts, and a cik that, when present (int or
digit text, both occur), equals the file name; every concept is exactly
{label, description, units}; every fact has FACT_REQUIRED, may have
FACT_OPTIONAL, and nothing else; values of the types FACT_TYPES names. Any
drift stops the load before anything is written. The cik written is the file
name's (8 members have no cik key).

The zip streams to <data root>/tmp/<run_id>/ through sec.fetch(to=...) and is
deleted after parsing; rows go through temporary Parquet batches to write_raw
as one DuckDB relation (D93). The facts load record's source_count is the
number of members, so the landing can be footed against the zip.
"""

import json
import re
import shutil
import zipfile
from pathlib import Path

import duckdb
import polars as pl

from pharos.loaders import sec
from pharos.paths import data_root
from pharos.runs import write_raw

SOURCE = "sec"
FACTS, CONCEPTS = "companyfacts", "companyfacts_concepts"
URL = "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip"
MEMBER = re.compile(r"CIK(\d{10})\.json")
BATCH = 2_000_000   # fact rows per temporary Parquet file

FACT_REQUIRED = {"accn", "end", "filed", "form", "fp", "fy", "val"}
FACT_OPTIONAL = {"start", "frame"}
CONCEPT_KEYS = {"label", "description", "units"}
FACT_SCHEMA = {"cik": pl.Int64, "taxonomy": pl.String, "concept": pl.String, "unit": pl.String,
               "start": pl.String, "end": pl.String, "val": pl.String, "accn": pl.String, "fy": pl.Int64,
               "fp": pl.String, "form": pl.String, "filed": pl.String, "frame": pl.String}
CONCEPT_SCHEMA = {"cik": pl.Int64, "entity_name": pl.String, "taxonomy": pl.String, "concept": pl.String,
                  "label": pl.String, "description": pl.String}


class SchemaDriftError(RuntimeError):
    pass


def _is_text(x) -> bool:
    return isinstance(x, str)


def _is_text_or_null(x) -> bool:
    return x is None or isinstance(x, str)


def _is_int_or_null(x) -> bool:
    return x is None or (isinstance(x, int) and not isinstance(x, bool))


def _is_number(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


FACT_TYPES = {"accn": _is_text, "end": _is_text, "filed": _is_text, "form": _is_text, "fp": _is_text_or_null,
              "fy": _is_int_or_null, "val": _is_number, "start": _is_text, "frame": _is_text}


class _Facts:
    """Fact rows accumulated column by column, flushed to Parquet in batches."""

    def __init__(self, folder: Path):
        self.folder = folder
        self.columns = {c: [] for c in FACT_SCHEMA}
        self.n_batch, self.files, self.rows = 0, 0, 0

    def add(self, cik: int, taxonomy: str, concept: str, unit: str, fact: dict, where: str) -> None:
        keys = fact.keys()
        if not (FACT_REQUIRED <= keys and keys <= FACT_REQUIRED | FACT_OPTIONAL):
            raise SchemaDriftError(f"{where}: fact keys {sorted(keys)}: missing {sorted(FACT_REQUIRED - keys)}, "
                                   f"unexpected {sorted(keys - FACT_REQUIRED - FACT_OPTIONAL)}")
        for k, v in fact.items():
            if not FACT_TYPES[k](v):
                raise SchemaDriftError(f"{where}: fact {k} = {v!r} ({type(v).__name__})")
        c = self.columns
        c["cik"].append(cik)
        c["taxonomy"].append(taxonomy)
        c["concept"].append(concept)
        c["unit"].append(unit)
        c["start"].append(fact.get("start"))
        c["end"].append(fact["end"])
        c["val"].append(repr(fact["val"]))   # int: its digits; float: the shortest text that round-trips
        c["accn"].append(fact["accn"])
        c["fy"].append(fact["fy"])
        c["fp"].append(fact["fp"])
        c["form"].append(fact["form"])
        c["filed"].append(fact["filed"])
        c["frame"].append(fact.get("frame"))
        self.n_batch += 1
        if self.n_batch >= BATCH:
            self.flush()

    def flush(self) -> None:
        if not self.n_batch:
            return
        pl.DataFrame(self.columns, schema=FACT_SCHEMA, strict=True).write_parquet(
            self.folder / f"facts_{self.files:04d}.parquet")
        self.files += 1
        self.rows += self.n_batch
        self.columns = {c: [] for c in FACT_SCHEMA}
        self.n_batch = 0


def _file_cik(name: str, record: dict) -> int:
    cik = int(MEMBER.fullmatch(name).group(1))
    sent = record.get("cik")
    if sent is not None:
        ok = (isinstance(sent, int) and not isinstance(sent, bool)) or (isinstance(sent, str) and sent.isdigit())
        if not ok or int(sent) != cik:
            raise SchemaDriftError(f"{name}: cik {sent!r} differs from the file name")
    return cik


def parse(zip_path: Path, folder: Path) -> tuple[list[Path], Path, int, int]:
    """Parse the zip into fact Parquet batches and one concepts Parquet under
    folder. Returns (fact files, concepts file, members, fact rows)."""
    concepts = {c: [] for c in CONCEPT_SCHEMA}
    facts = _Facts(folder)
    members = 0
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():
            if not MEMBER.fullmatch(name):
                raise SchemaDriftError(f"companyfacts.zip: unexpected member {name!r}")
            members += 1
            record = json.loads(z.read(name))
            if not isinstance(record, dict):
                raise SchemaDriftError(f"{name}: top level is {type(record).__name__}, expected an object")
            if not record:
                continue   # an empty member: a filer with nothing (62 on 2026-11-04)
            keys = set(record)
            if not ({"entityName", "facts"} <= keys <= {"cik", "entityName", "facts"}):
                raise SchemaDriftError(f"{name}: top-level keys {sorted(keys)}, expected "
                                       "['cik', 'entityName', 'facts'] (cik optional)")
            cik = _file_cik(name, record)
            entity = record["entityName"]
            if not _is_text(entity) or not isinstance(record["facts"], dict):
                raise SchemaDriftError(f"{name}: entityName {type(entity).__name__}, "
                                       f"facts {type(record['facts']).__name__}")
            for taxonomy, body in record["facts"].items():
                if not isinstance(body, dict):
                    raise SchemaDriftError(f"{name}: taxonomy {taxonomy!r} is {type(body).__name__}")
                for concept, c in body.items():
                    where = f"{name} {taxonomy}:{concept}"
                    if not isinstance(c, dict) or set(c) != CONCEPT_KEYS:
                        raise SchemaDriftError(f"{where}: concept keys "
                                               f"{sorted(c) if isinstance(c, dict) else type(c).__name__}, "
                                               f"expected {sorted(CONCEPT_KEYS)}")
                    if not (_is_text_or_null(c["label"]) and _is_text_or_null(c["description"])
                            and isinstance(c["units"], dict)):
                        raise SchemaDriftError(f"{where}: label, description or units of the wrong type")
                    concepts["cik"].append(cik)
                    concepts["entity_name"].append(entity)
                    concepts["taxonomy"].append(taxonomy)
                    concepts["concept"].append(concept)
                    concepts["label"].append(c["label"])
                    concepts["description"].append(c["description"])
                    for unit, values in c["units"].items():
                        if not isinstance(values, list):
                            raise SchemaDriftError(f"{where} [{unit}]: {type(values).__name__}, expected a list")
                        for fact in values:
                            if not isinstance(fact, dict):
                                raise SchemaDriftError(f"{where} [{unit}]: a fact is {type(fact).__name__}")
                            facts.add(cik, taxonomy, concept, unit, fact, f"{where} [{unit}]")
    facts.flush()
    concepts_file = folder / "concepts.parquet"
    pl.DataFrame(concepts, schema=CONCEPT_SCHEMA, strict=True).write_parquet(concepts_file)
    return sorted(folder.glob("facts_*.parquet")), concepts_file, members, facts.rows


def _empty(schema: dict) -> str:
    return "SELECT " + ", ".join(f"NULL::{'BIGINT' if t == pl.Int64 else 'VARCHAR'} AS \"{c}\""
                                 for c, t in schema.items()) + " WHERE false"


def load_companyfacts(run_id: str) -> dict:
    """Download, parse and land both datasets. Returns members and row counts."""
    tmp = data_root() / "tmp" / run_id / "companyfacts"
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        got = sec.fetch(URL, to=tmp / "companyfacts.zip")
        fact_files, concepts_file, members, n_parsed = parse(got.path, tmp)
        got.path.unlink()   # parsed; the rows are what raw keeps
        con = duckdb.connect()
        facts = con.sql(f"SELECT * FROM read_parquet({[f.as_posix() for f in fact_files]})" if fact_files
                        else _empty(FACT_SCHEMA))
        concepts = con.sql(f"SELECT * FROM read_parquet('{concepts_file.as_posix()}')")
        n_facts, n_concepts = len(facts), len(concepts)
        if n_facts != n_parsed:
            raise SchemaDriftError(f"read back {n_facts:,} fact rows, parsed {n_parsed:,}")
        write_raw(facts, SOURCE, FACTS, run_id, last_modified=got.last_modified, source_count=members)
        write_raw(concepts, SOURCE, CONCEPTS, run_id, last_modified=got.last_modified)
        con.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        for folder in (tmp.parent, tmp.parent.parent):   # tmp/<run_id>/, then tmp/, once empty
            if folder.exists() and not any(folder.iterdir()):
                folder.rmdir()
    return {"members": members, "facts": n_facts, "concepts": n_concepts}

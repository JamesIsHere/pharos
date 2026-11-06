"""EDGAR Company Facts: every XBRL fact SEC extracted for a filer.

The first landing (and an explicit ``pharos companyfacts`` run) uses SEC's
bulk companyfacts.zip as the baseline.  Nightly runs read the submissions
landing first, find CIKs whose filing rows are new or changed, and fetch the
per-company Company Facts document only for those CIKs.  Both paths write the
same three append-only raw datasets:
- raw/sec/companyfacts/<run_id>.parquet: one row per reported fact (cik,
  taxonomy, concept, unit, start, end, val, accn, fy, fp, form, filed, frame),
  values as sent. `val` is text: JSON sends ints and floats, and 46 ints in
  the 2026-11-04 file are beyond 2^53, where a DOUBLE changes them; staging
  casts. `start` and `frame` are NULL where a fact has none.
- raw/sec/companyfacts_concepts/<run_id>.parquet: one row per (cik, taxonomy,
  concept) with the filer's entity name and the concept's label and
  description, so 126M fact rows don't repeat them.
- raw/sec/companyfacts_changes/<run_id>.parquet: one row per Company Facts
  document requested in the run, including empty documents and HTTP 404s.
  Staging uses it as the authoritative replacement set for copy-on-write
  partitions; absence of fact rows must be able to delete an older company.

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
FACTS, CONCEPTS, CHANGES = "companyfacts", "companyfacts_concepts", "companyfacts_changes"
BULK_URL = "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip"
COMPANY_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
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
CHANGE_SCHEMA = {"cik": pl.Int64, "document_found": pl.Boolean, "mode": pl.String}


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


def _add_record(name: str, record: object, concepts: dict[str, list], facts: _Facts) -> None:
    """Validate and append one CIK document, from either SEC endpoint."""
    if not isinstance(record, dict):
        raise SchemaDriftError(f"{name}: top level is {type(record).__name__}, expected an object")
    if not record:
        return   # an empty bulk member: a filer with nothing (62 on 2026-11-04)
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


def _parse_documents(documents, folder: Path) -> tuple[list[Path], Path, list[tuple[int, bool]], int]:
    concepts = {c: [] for c in CONCEPT_SCHEMA}
    facts = _Facts(folder)
    members = []
    for name, record in documents:
        if not MEMBER.fullmatch(name):
            raise SchemaDriftError(f"companyfacts: unexpected member {name!r}")
        members.append((int(MEMBER.fullmatch(name).group(1)), bool(record)))
        _add_record(name, record, concepts, facts)
    facts.flush()
    concepts_file = folder / "concepts.parquet"
    pl.DataFrame(concepts, schema=CONCEPT_SCHEMA, strict=True).write_parquet(concepts_file)
    return sorted(folder.glob("facts_*.parquet")), concepts_file, members, facts.rows


def parse(zip_path: Path, folder: Path) -> tuple[list[Path], Path, list[tuple[int, bool]], int]:
    """Parse the bulk zip into fact batches and one concepts Parquet file."""
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        unexpected = [name for name in names if not MEMBER.fullmatch(name)]
        if unexpected:
            raise SchemaDriftError(f"companyfacts.zip: unexpected member {unexpected[0]!r}")
        return _parse_documents(((name, json.loads(z.read(name))) for name in names), folder)


def _empty(schema: dict) -> str:
    return "SELECT " + ", ".join(f"NULL::{'BIGINT' if t == pl.Int64 else 'VARCHAR'} AS \"{c}\""
                                 for c, t in schema.items()) + " WHERE false"


def _land(run_id: str, folder: Path, fact_files: list[Path], concepts_file: Path,
          documents: list[tuple[int, bool]], n_parsed: int, *, mode: str,
          last_modified=None, note: str | None = None) -> dict:
    """Land parsed files from either the bulk or incremental route."""
    con = duckdb.connect()
    try:
        facts = con.sql(f"SELECT * FROM read_parquet({[f.as_posix() for f in fact_files]})" if fact_files
                        else _empty(FACT_SCHEMA))
        concepts = con.sql(f"SELECT * FROM read_parquet('{concepts_file.as_posix()}')")
        n_facts, n_concepts = len(facts), len(concepts)
        if n_facts != n_parsed:
            raise SchemaDriftError(f"read back {n_facts:,} fact rows, parsed {n_parsed:,}")
        members = len(documents)
        write_raw(facts, SOURCE, FACTS, run_id, last_modified=last_modified,
                  source_count=members, note=note)
        write_raw(concepts, SOURCE, CONCEPTS, run_id, last_modified=last_modified,
                  source_count=members, note=note)
        changes = pl.DataFrame(
            {"cik": [cik for cik, _ in documents],
             "document_found": [found for _, found in documents],
             "mode": [mode] * members},
            schema=CHANGE_SCHEMA, strict=True)
        write_raw(changes, SOURCE, CHANGES, run_id, last_modified=last_modified,
                  source_count=members, note=note)
    finally:
        con.close()
    return {"members": members, "facts": n_facts, "concepts": n_concepts}


def load_companyfacts(run_id: str) -> dict:
    """Download the full bulk baseline and land both datasets."""
    tmp = data_root() / "tmp" / run_id / "companyfacts"
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        got = sec.fetch(BULK_URL, to=tmp / "companyfacts.zip")
        fact_files, concepts_file, documents, n_parsed = parse(got.path, tmp)
        got.path.unlink()   # parsed; the rows are what raw keeps
        result = _land(run_id, tmp, fact_files, concepts_file, documents, n_parsed,
                       mode="full", last_modified=got.last_modified, note="full bulk baseline")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        for folder in (tmp.parent, tmp.parent.parent):   # tmp/<run_id>/, then tmp/, once empty
            if folder.exists() and not any(folder.iterdir()):
                folder.rmdir()
    return result


def has_baseline() -> bool:
    """Whether a complete Company Facts landing already exists in raw."""
    root = data_root() / "raw" / SOURCE
    return (any((root / FACTS).glob("*.parquet"))
            and any((root / CONCEPTS).glob("*.parquet")))


def changed_ciks(run_id: str) -> list[int]:
    """CIKs with a new or changed submissions filing in ``run_id``.

    The submissions loader re-lands an overlap window.  Compare each current
    row with the newest earlier value for the same filing so the overlap does
    not cause needless Company Facts requests, while source corrections still
    do.
    """
    folder = data_root() / "raw" / SOURCE / "submission_filings"
    current = folder / f"{run_id}.parquet"
    if not current.exists():
        return []
    earlier = sorted(p for p in folder.glob("*.parquet") if p.stem < run_id)
    con = duckdb.connect()
    try:
        if not earlier:
            return [r[0] for r in con.execute(
                f"SELECT DISTINCT cik FROM read_parquet('{current.as_posix()}') ORDER BY cik").fetchall()]
        values = [c for c in ("cik", "accession_number", "filing_date", "report_date",
                              "acceptance_date_time", "act", "form", "file_number", "film_number",
                              "items", "core_type", "size", "is_xbrl", "is_inline_xbrl",
                              "is_xbrl_numeric", "primary_document", "primary_doc_description")]
        row_current = "row(" + ", ".join(f"c.{c}" for c in values) + ")"
        row_prior = "row(" + ", ".join(f"p.{c}" for c in values) + ")"
        # One submissions landing contains the complete overlap window used by
        # the next landing, so the newest prior file is the comparison base.
        # Reading the whole append-only history here would grow nightly work
        # even though older files cannot change this answer.
        prior_path = earlier[-1].as_posix()
        rows = con.execute(f"""
            WITH current_rows AS (
                SELECT DISTINCT {', '.join(values)}
                FROM read_parquet('{current.as_posix()}')
            ), prior AS (
                SELECT * EXCLUDE (rn) FROM (
                    SELECT *, row_number() OVER (
                        PARTITION BY cik, accession_number, form
                        ORDER BY run_id DESC, loaded_at DESC, source_file DESC) AS rn
                    FROM read_parquet('{prior_path}')
                ) WHERE rn = 1
            )
            SELECT DISTINCT c.cik
            FROM current_rows AS c
            LEFT JOIN prior AS p USING (cik, accession_number, form)
            WHERE p.cik IS NULL OR {row_current} IS DISTINCT FROM {row_prior}
            ORDER BY c.cik
        """).fetchall()
        return [r[0] for r in rows]
    finally:
        con.close()


def load_incremental_companyfacts(run_id: str) -> dict:
    """Land full documents only for CIKs changed by this run's submissions.

    A new data root has nothing to merge partial documents into, so its first
    SEC run establishes the bulk baseline automatically.
    """
    if not has_baseline():
        return load_companyfacts(run_id) | {"mode": "full"}

    ciks = changed_ciks(run_id)
    tmp = data_root() / "tmp" / run_id / "companyfacts"
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        documents = []
        modified = []
        missing = []
        for cik in ciks:
            try:
                got = sec.fetch(COMPANY_URL.format(cik=cik))
            except sec.SecFetchError as e:
                if e.status != 404:
                    raise
                # A submissions filer need not have XBRL facts. SEC expresses
                # that valid absence as a missing per-company document.
                documents.append((f"CIK{cik:010d}.json", {}))
                missing.append(cik)
                continue
            documents.append((f"CIK{cik:010d}.json", json.loads(got.content)))
            if got.last_modified is not None:
                modified.append(got.last_modified)
        fact_files, concepts_file, parsed_documents, n_parsed = _parse_documents(documents, tmp)
        members = len(parsed_documents)
        note = f"incremental Company Facts for {members} CIKs changed in submissions"
        if missing:
            note += f"; {len(missing)} had no SEC Company Facts document (HTTP 404)"
        result = _land(run_id, tmp, fact_files, concepts_file, parsed_documents, n_parsed,
                       mode="incremental", last_modified=max(modified) if modified else None, note=note)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        for folder in (tmp.parent, tmp.parent.parent):
            if folder.exists() and not any(folder.iterdir()):
                folder.rmdir()
    return result | {"mode": "incremental", "ciks": ciks, "missing_ciks": missing}

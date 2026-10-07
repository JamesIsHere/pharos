"""The `pharos` command. Each subcommand is a thin wrapper; the work lives in other modules."""

import argparse
import sys


def cmd_paths(args: argparse.Namespace) -> int:
    from pharos.paths import data_root

    root = data_root()
    print(f"data root: {root}")
    for layer in ("raw", "staging", "serving", "health"):
        print(f"  {layer:<8} {root / layer}")
    return 0


def cmd_load(args: argparse.Namespace) -> int:
    import duckdb

    from pharos.loaders import fred, yahoo
    from pharos.paths import data_root
    from pharos.runs import new_run_id

    run_id = new_run_id()
    print(f"run {run_id}")
    yahoo.load_prices(run_id)
    fred.load_macro(run_id)

    # Report from the load records write_raw() left, not from the loaders' return
    # values: what's on disk is the evidence.
    records = (data_root() / "health" / "loads" / f"{run_id}__*.parquet").as_posix()
    for source, dataset, downloaded, written, path in duckdb.sql(
            f"""SELECT source, dataset, rows_downloaded, rows_written, path
                FROM read_parquet('{records}') ORDER BY source, dataset""").fetchall():
        print(f"  {source}/{dataset:<13} {downloaded:>9,} downloaded {written:>9,} written  {path or '(empty)'}")
    return 0


def cmd_stage(args: argparse.Namespace) -> int:
    import duckdb

    from pharos import catalog
    from pharos.stage import stage

    target = stage(args.run)
    print(f"staged {target}")
    for table in ("series_catalog", "observations"):
        n = duckdb.sql(f"SELECT count(*) FROM read_parquet('{(target / f'{table}.parquet').as_posix()}')").fetchone()[0]
        print(f"  {table:<15} {n:>9,} rows")
    print(f"catalog {catalog.rebuild()}")
    return 0


def cmd_publish(args: argparse.Namespace) -> int:
    from pharos import catalog
    from pharos.publish import publish

    pub = publish(args.run)
    e = pub.evaluation
    print(f"run {pub.run_id}: checks {e.verdict}  ({e.path})")
    for r in e.results:
        if r["status"] != "pass":
            print(f"  {r['check_id']} {r['status']:<6} {r['failing_row_count'] or '':>6}  {r['error'] or r['description']}")
    if pub.version is None:
        print("not published: serving/ is unchanged")
        return 1
    print(f"published {pub.version}")
    print(f"catalog {catalog.rebuild()}")
    return 0


def cmd_refresh(args: argparse.Namespace) -> int:
    from pharos import catalog
    from pharos.refresh import refresh

    run_id, pub = refresh()
    e = pub.evaluation
    print(f"run {run_id}: checks {e.verdict}  ({e.path})")
    for r in e.results:
        if r["status"] != "pass":
            print(f"  {r['check_id']} {r['status']:<6} {r['failing_row_count'] or '':>6}  {r['error'] or r['description']}")
    if pub.version is None:
        print("not published: serving/ is unchanged")
        return 1
    print(f"published {pub.version}")
    print(f"catalog {catalog.rebuild()}")
    return 0


def cmd_health(args: argparse.Namespace) -> int:
    from pharos import health

    h = health.evaluate()
    print(health.render(h, color=sys.stdout.isatty()), end="")
    print(f"wrote {health.write_latest(h)}")
    return 1 if h.status == "red" else 0


def cmd_audit(args: argparse.Namespace) -> int:
    from pharos.audit import audit

    a = audit()
    print(f"audit of published run {a.run_id}: {'passed' if a.passed else 'FAILED'}")
    print(f"comparisons {a.comparisons}")
    for f in a.failures:
        when = f" {f['obs_date']} ({f['pick']}) rel_diff {f['rel_diff']:+.4%}" if f["obs_date"] else ""
        print(f"  {f['ticker']:<6} {f['problem']}{when}")
    if not a.passed:
        print("no baseline recorded")
        return 1
    print(f"baseline {a.baseline}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pharos", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("paths", help="show where Pharos reads and writes data")
    p.set_defaults(func=cmd_paths)

    p = sub.add_parser("load", help="download every source into a new raw snapshot")
    p.set_defaults(func=cmd_load)

    p = sub.add_parser("stage", help="build staging/<run_id>/ from the raw snapshots")
    p.add_argument("--run", help="run ID to stage (default: the latest complete run)")
    p.set_defaults(func=cmd_stage)

    p = sub.add_parser("publish", help="run the checks on a staged run and publish it to serving/ if they pass")
    p.add_argument("--run", help="run ID to publish (default: the latest complete run)")
    p.set_defaults(func=cmd_publish)

    p = sub.add_parser("health", help="judge what is being served, now: recorded gate results + monitors re-run")
    p.set_defaults(func=cmd_health)

    p = sub.add_parser("audit", help="reconcile the published run against the second source; "
                                     "record the baseline only if every ticker passes")
    p.set_defaults(func=cmd_audit)

    p = sub.add_parser("refresh", help="load, stage, check and publish a new run; writes the run manifest")
    p.set_defaults(func=cmd_refresh)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

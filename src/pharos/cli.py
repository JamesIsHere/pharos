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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pharos", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("paths", help="show where Pharos reads and writes data")
    p.set_defaults(func=cmd_paths)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

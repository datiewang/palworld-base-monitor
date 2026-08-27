"""Command line: palmon <update|serve|fetch-data|config>.

--config is handled before anything else is imported, because config.py
reads its file at import time and the other modules bind its values at
theirs. Turning the flag into $PALMON_CONFIG first is what makes a single
import order work for every subcommand.
"""

import argparse
import os
import sys


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="palmon",
        description="Palworld dedicated-server base camp dashboard.")
    parser.add_argument("--config", metavar="FILE",
                        help="config file to use (default: ~/.config/palmon/config.toml)")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("update", help="parse the save once and write status.json")
    sub.add_parser("serve", help="serve the dashboard (this is the long-running one)")
    sub.add_parser("config", help="show where everything is and what is missing")
    fetch = sub.add_parser("fetch-data", help="download the game data the dashboard reads")
    fetch.add_argument("--only", metavar="NAME", action="append",
                       help="fetch just this dataset (repeatable)")

    args = parser.parse_args(argv)
    if args.config:
        os.environ["PALMON_CONFIG"] = os.path.abspath(os.path.expanduser(args.config))

    # Imported here, not at module level: every one of these reads config
    # values at import time.
    from . import config

    if args.command == "config" or args.command is None:
        print(config.describe())
        print()
        ok = config.check(stream=sys.stdout)
        return 0 if ok else 1

    config.ensure_dirs()

    if args.command == "update":
        if not config.check():
            return 1
        from . import status
        result = status.analyze_data()
        bases = result.get("bases", [])
        print(f"{config.OUTPUT_JSON}: {len(bases)} base(s), "
              f"{result.get('total_base_pals', 0)} base pals, "
              f"{result.get('total_all_pals', 0)} pals in the world")
        return 0

    if args.command == "serve":
        config.check()
        from . import server
        return server.serve()

    if args.command == "fetch-data":
        from . import fetchdata
        return fetchdata.fetch_all(only=args.only)

    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())

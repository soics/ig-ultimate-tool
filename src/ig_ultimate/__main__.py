"""Start the tool: `ig-ultimate`, or `python -m ig_ultimate`."""

from __future__ import annotations

import argparse
import os
import sys


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="ig-ultimate", description="IG Ultimate Tool")
    parser.add_argument(
        "--preview",
        action="store_true",
        help="try every screen on a pretend account (no login, nothing real changes)",
    )
    parser.add_argument("--web", action="store_true", help="open the easy version in your web browser")
    args = parser.parse_args(argv)
    if args.web:
        from . import webapp

        sys.exit(webapp.serve(preview=args.preview))
    if args.preview:
        from . import paths

        # Preview keeps its own settings and progress files, so it can never mix with a real account's.
        os.environ["IG_ULTIMATE_HOME"] = str(paths.data_dir() / "preview")
    from . import cli

    sys.exit(cli.run(preview=args.preview))


if __name__ == "__main__":
    main()

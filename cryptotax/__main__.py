"""Usage:
  python -m cryptotax ui                                    # browser app at http://127.0.0.1:8765
  python -m cryptotax wallets --wallets data/wallets.txt    # check how addresses were classified
  python -m cryptotax fetch   --wallets data/wallets.txt [--chains 1,42161,...]
  python -m cryptotax report  --year 2025
"""
from __future__ import annotations

import argparse
import sys
import traceback

from . import pipeline, wallets


def cmd_fetch(args) -> None:
    ws = wallets.load(args.wallets) if args.wallets else wallets.owned()
    chains = [int(c) for c in args.chains.split(",")] if args.chains else None
    failures = pipeline.run_fetch(ws, pipeline.ConsoleProgress(), chains, hyperliquid=not args.skip_hyperliquid)
    if failures:
        print(f"\n{len(failures)} fetch failures (data is INCOMPLETE for these):", file=sys.stderr)
        for f in failures:
            print("  " + f, file=sys.stderr)


def cmd_wallets(args) -> None:
    ws = wallets.load(args.wallets)
    print(f"{len(ws)} unique addresses -> {wallets.summary(ws)}")


def cmd_report(args) -> None:
    pipeline.run_report(args.year, pipeline.ConsoleProgress())


def cmd_ui(args) -> None:
    from .ui.server import serve
    serve(args.port, open_browser=not args.no_browser)


def main() -> None:
    p = argparse.ArgumentParser(prog="cryptotax")
    sub = p.add_subparsers(dest="cmd", required=True)
    u = sub.add_parser("ui")
    u.add_argument("--port", type=int, default=8765)
    u.add_argument("--no-browser", action="store_true")
    f = sub.add_parser("fetch")
    f.add_argument("--wallets", help="address list file (default: the wallet book saved by the UI)")
    f.add_argument("--chains", help="comma-separated chain ids (default: common EVM chains)")
    f.add_argument("--skip-hyperliquid", action="store_true")
    sub.add_parser("wallets").add_argument("--wallets", required=True)
    r = sub.add_parser("report")
    r.add_argument("--year", type=int, default=2025)
    args = p.parse_args()
    try:
        {"fetch": cmd_fetch, "wallets": cmd_wallets, "report": cmd_report, "ui": cmd_ui}[args.cmd](args)
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception:
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

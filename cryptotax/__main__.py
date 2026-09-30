"""Usage:
  python -m cryptotax wallets --wallets data/wallets.txt   # check how addresses were classified
  python -m cryptotax fetch  --wallets data/wallets.txt [--chains 1,42161,...]
  python -m cryptotax report --year 2025
"""
from __future__ import annotations

import argparse
import os
import sys
import traceback
from datetime import datetime, timezone

from . import store, wallets
from .engine import Engine
from .pricing import price_movements
from .report import write

DEFAULT_CHAINS = [1, 42161, 10, 8453, 137, 59144, 534352, 324, 81457, 130, 57073, 480, 56, 43114, 5000, 100, 999]


def cmd_fetch(args) -> None:
    from .sources import etherscan, hyperliquid
    ws = wallets.load(args.wallets)
    evm = [w for w in ws if w.kind == "evm"]
    own = {w.address for w in evm}
    print(f"{len(ws)} unique addresses -> {wallets.summary(ws)}")
    if "ETHERSCAN_API_KEY" not in os.environ:
        sys.exit("Set ETHERSCAN_API_KEY first (free key at https://etherscan.io/myapikey)")
    chains = [int(c) for c in args.chains.split(",")] if args.chains else DEFAULT_CHAINS
    failures = []
    for chain in chains:
        moves = []
        for i, w in enumerate(evm):
            try:
                moves += etherscan.fetch_address(chain, w.address, own)
            except Exception as e:  # keep going; report every gap at the end
                failures.append(f"chain {chain} {w.address}: {e}")
                if "chain" in str(e).lower() and "not supported" in str(e).lower():
                    break
            print(f"\rchain {chain}: {i + 1}/{len(evm)} addresses, {len(moves)} movements", end="", flush=True)
        print()
        store.save(f"evm_{chain}", moves)
    if not args.skip_hyperliquid:
        moves = []
        for w in evm:
            try:
                moves += hyperliquid.fetch_address(w.address, own)
            except Exception as e:
                failures.append(f"hyperliquid {w.address}: {e}")
        print(f"hyperliquid: {len(moves)} movements")
        store.save("hyperliquid", moves)
    if failures:
        print(f"\n{len(failures)} fetch failures (data is INCOMPLETE for these):", file=sys.stderr)
        for f in failures:
            print("  " + f, file=sys.stderr)


def cmd_wallets(args) -> None:
    ws = wallets.load(args.wallets)
    print(f"{len(ws)} unique addresses -> {wallets.summary(ws)}")


def cmd_report(args) -> None:
    moves = store.load_all()
    print(f"{len(moves)} movements loaded; pricing...")
    price_movements(moves)
    snap = datetime(args.year, 1, 1, tzinfo=timezone.utc)
    result = Engine([snap]).run(moves)
    path = write(result, args.year, snapshot=snap)
    print(f"wrote {path} and out/disposals_{args.year}.csv, out/review_{args.year}.csv")


def main() -> None:
    p = argparse.ArgumentParser(prog="cryptotax")
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--wallets", required=True)
    f.add_argument("--chains", help="comma-separated chain ids (default: common EVM chains)")
    f.add_argument("--skip-hyperliquid", action="store_true")
    sub.add_parser("wallets").add_argument("--wallets", required=True)
    r = sub.add_parser("report")
    r.add_argument("--year", type=int, default=2025)
    args = p.parse_args()
    try:
        {"fetch": cmd_fetch, "wallets": cmd_wallets, "report": cmd_report}[args.cmd](args)
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception:
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

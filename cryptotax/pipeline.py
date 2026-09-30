"""Fetch and report steps shared by the CLI and the UI.

`progress` is any object with .log(msg), .step(done, total, label) and a
.cancelled attribute; the CLI and the UI each supply their own.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from . import store
from .engine import Engine
from .pricing import price_movements
from .report import write
from .wallets import Wallet, summary

DEFAULT_CHAINS = [1, 42161, 10, 8453, 137, 59144, 534352, 324, 81457, 130, 57073, 480, 56, 43114, 5000, 100, 999]
CHAIN_NAMES = {
    1: "Ethereum", 42161: "Arbitrum", 10: "Optimism", 8453: "Base", 137: "Polygon", 59144: "Linea",
    534352: "Scroll", 324: "zkSync Era", 81457: "Blast", 130: "Unichain", 57073: "Ink", 480: "World Chain",
    56: "BNB Chain", 43114: "Avalanche", 5000: "Mantle", 100: "Gnosis", 999: "HyperEVM",
}


class Cancelled(Exception):
    pass


def run_fetch(ws: list[Wallet], progress, chains: list[int] | None = None, apikey: str | None = None,
              hyperliquid: bool = True) -> list[str]:
    """Fetch every owned EVM address on every chain. Returns failure messages."""
    from .sources import etherscan, hyperliquid as hl
    apikey = apikey or os.environ.get("ETHERSCAN_API_KEY")
    if not apikey:
        raise ValueError("No Etherscan API key set")
    evm = [w for w in ws if w.kind == "evm"]
    own = {w.address for w in evm}
    chains = chains or DEFAULT_CHAINS
    progress.log(f"{len(ws)} addresses: {summary(ws)}")
    failures: list[str] = []
    total = len(chains) * len(evm) + (len(evm) if hyperliquid else 0)
    done = 0
    for chain in chains:
        name = CHAIN_NAMES.get(chain, str(chain))
        moves, chain_dead = [], False
        for w in evm:
            if progress.cancelled:
                raise Cancelled()
            done += 1
            progress.step(done, total, f"{name}: {w.address[:10]}…")
            if chain_dead:
                continue
            try:
                moves += etherscan.fetch_address(chain, w.address, own, apikey)
            except Exception as e:  # keep going; every gap is reported
                msg = str(e)
                failures.append(f"{name} {w.address}: {msg}")
                if any(s in msg.lower() for s in ("not supported", "invalid chain", "upgrade", "paid")):
                    chain_dead = True
                    progress.log(f"{name}: unavailable on this API key, skipped ({msg[:120]})")
        store.save(f"evm_{chain}", moves)
        progress.log(f"{name}: {len(moves)} movements")
    if hyperliquid:
        moves = []
        for w in evm:
            if progress.cancelled:
                raise Cancelled()
            done += 1
            progress.step(done, total, f"Hyperliquid: {w.address[:10]}…")
            try:
                moves += hl.fetch_address(w.address, own)
            except Exception as e:
                failures.append(f"Hyperliquid {w.address}: {e}")
        store.save("hyperliquid", moves)
        progress.log(f"Hyperliquid: {len(moves)} movements")
    progress.log(f"Done. {len(failures)} failures." if failures else "Done. No failures.")
    return failures


def run_report(year: int, progress) -> dict:
    moves = store.load_all()
    progress.log(f"{len(moves)} movements loaded")
    progress.step(1, 3, "Pricing")
    price_movements(moves)
    progress.step(2, 3, "Computing lots")
    snap = datetime(year, 1, 1, tzinfo=timezone.utc)
    result = Engine([snap]).run(moves)
    progress.step(3, 3, "Writing reports")
    path = write(result, year, snapshot=snap)
    progress.log(f"Wrote {path}")
    return {"disposals": len(result.disposals), "reviews": len(result.reviews)}


class ConsoleProgress:
    cancelled = False

    def log(self, msg: str) -> None:
        print(msg)

    def step(self, done: int, total: int, label: str) -> None:
        print(f"\r[{done}/{total}] {label:<50}", end="", flush=True)
        if done == total:
            print()

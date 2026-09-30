"""Historical USD prices from DefiLlama's free coins API.

Prices are looked up at the hour of each movement (batched per hour) and
cached. Stablecoins are valued at $1. Anything DefiLlama cannot price stays
None and surfaces in the review list; nothing is silently set to $0.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from .assets import STABLES
from .http import fetch
from .models import FEE, INCOME, TRADE, Movement

URL = "https://coins.llama.fi/prices/historical/{ts}/{coins}"

GECKO = {
    "ETH": "coingecko:ethereum", "BTC": "coingecko:bitcoin", "WBTC": "coingecko:bitcoin",
    "SOL": "coingecko:solana", "POL": "coingecko:polygon-ecosystem-token", "BNB": "coingecko:binancecoin",
    "AVAX": "coingecko:avalanche-2", "HYPE": "coingecko:hyperliquid", "MNT": "coingecko:mantle",
    "XDAI": "coingecko:xdai", "FTM": "coingecko:fantom",
}
LLAMA_CHAIN = {
    1: "ethereum", 42161: "arbitrum", 10: "optimism", 8453: "base", 137: "polygon", 56: "bsc",
    43114: "avax", 59144: "linea", 534352: "scroll", 81457: "blast", 324: "era", 5000: "mantle",
    100: "xdai", 250: "fantom", 130: "unichain", 80094: "berachain", 146: "sonic", 999: "hyperliquid",
}


def llama_id(asset: str, symbol: str = "") -> str | None:
    if asset in GECKO:
        return GECKO[asset]
    if ":" in asset:
        chain, addr = asset.split(":", 1)
        if chain.isdigit() and addr.startswith("0x") and int(chain) in LLAMA_CHAIN:
            return f"{LLAMA_CHAIN[int(chain)]}:{addr}"
        if chain == "solana":
            return f"solana:{addr}"
    if symbol.upper() in GECKO:  # e.g. Hyperliquid spot "HYPE"
        return GECKO[symbol.upper()]
    return None


def price_movements(moves: list[Movement], batch: int = 60) -> None:
    need: dict[int, set[str]] = defaultdict(set)
    for m in moves:
        if m.usd is not None or m.kind not in (TRADE, FEE, INCOME):
            continue
        if m.asset in STABLES:
            m.usd = abs(m.qty)
            continue
        lid = llama_id(m.asset, m.symbol)
        if lid:
            need[int(m.ts.timestamp()) // 3600 * 3600].add(lid)

    prices: dict[tuple[int, str], Decimal] = {}
    for hour, ids in need.items():
        ids = sorted(ids)
        for i in range(0, len(ids), batch):
            chunk = ids[i:i + batch]
            data = fetch("GET", URL.format(ts=hour, coins=",".join(chunk)), params={"searchWidth": "6h"},
                         per_sec=5)
            for cid, info in data.get("coins", {}).items():
                prices[(hour, cid)] = Decimal(str(info["price"]))

    for m in moves:
        if m.usd is None and m.kind in (TRADE, FEE, INCOME):
            lid = llama_id(m.asset, m.symbol)
            p = prices.get((int(m.ts.timestamp()) // 3600 * 3600, lid)) if lid else None
            if p is not None:
                m.usd = abs(m.qty) * p

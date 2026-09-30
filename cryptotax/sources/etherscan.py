"""EVM history via the Etherscan V2 multichain API (one key, many chains).

Pulls, per (chain, address): normal txs (native value + gas), internal txs
(native value from contracts), and ERC-20 transfers. NFTs are not handled yet.

Known gaps, reported rather than hidden:
- On L2s (Optimism stack, Arbitrum) the L1 data fee is not always included
  in gasUsed*gasPrice, so gas cost can be slightly understated.
- Etherscan's free tier may not cover every chain; the fetch logs any chain
  that returns an error so you know what is missing.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal

from ..assets import canonical
from ..http import fetch
from ..models import FEE, TRADE, Movement

URL = "https://api.etherscan.io/v2/api"
PAGE = 10000


def _get_all(chainid: int, address: str, action: str, apikey: str) -> list[dict]:
    """Walk the block range; Etherscan caps one query at 10k rows."""
    rows, start, seen = [], 0, set()
    while True:
        params = dict(chainid=chainid, module="account", action=action, address=address,
                      startblock=start, endblock=99999999, page=1, offset=PAGE, sort="asc", apikey=apikey)
        data = fetch("GET", URL, params=params, per_sec=4)
        result = data.get("result")
        if data.get("status") != "1":
            if isinstance(result, list) or (isinstance(result, str) and "No transactions" in data.get("message", "")):
                break
            if data.get("message", "").startswith("No transactions"):
                break
            raise RuntimeError(f"etherscan chain {chainid} {action} {address}: {data.get('message')} {result}")
        new = 0
        for row in result:
            key = (row.get("hash"), row.get("logIndex"), row.get("traceId"), row.get("from"), row.get("to"),
                   row.get("value"), row.get("contractAddress"))
            if key not in seen:
                seen.add(key)
                rows.append(row)
                new += 1
        if len(result) < PAGE or new == 0:
            break
        start = int(result[-1]["blockNumber"])  # re-read last block; dedupe handles overlap
    return rows


def _ts(row) -> datetime:
    return datetime.fromtimestamp(int(row["timeStamp"]), tz=timezone.utc)


def normalize(chainid: int, own: set[str], normal: list[dict], internal: list[dict],
              tokens: list[dict]) -> list[Movement]:
    """Turn raw rows into Movements for any of *our* addresses involved.
    `own` is the set of all your addresses (lowercase), so a transfer between
    two of your wallets yields both legs; uid dedupes when both are fetched."""
    moves: list[Movement] = []
    native = canonical(chainid, None)

    def legs(row, asset, symbol, amount: Decimal, uid: str):
        tx = f"{chainid}:{row['hash']}"
        frm, to = row["from"].lower(), (row.get("to") or "").lower()
        if amount == 0:
            return
        if frm in own:
            moves.append(Movement(_ts(row), f"{chainid}:{frm}", asset, -amount, tx, TRADE,
                                  source="etherscan", symbol=symbol, uid=f"{uid}:out"))
        if to in own:
            moves.append(Movement(_ts(row), f"{chainid}:{to}", asset, amount, tx, TRADE,
                                  source="etherscan", symbol=symbol, uid=f"{uid}:in"))

    for row in normal:
        tx = f"{chainid}:{row['hash']}"
        frm = row["from"].lower()
        if row.get("isError") != "1":
            legs(row, native, native, Decimal(row["value"]) / Decimal(10) ** 18, f"{tx}:n")
        if frm in own:  # sender pays gas even on failed txs
            gas = Decimal(row["gasUsed"]) * Decimal(row["gasPrice"]) / Decimal(10) ** 18
            if gas > 0:
                moves.append(Movement(_ts(row), f"{chainid}:{frm}", native, -gas, tx, FEE,
                                      source="etherscan", symbol=native, uid=f"{tx}:gas"))
    for row in internal:
        if row.get("isError") == "1":
            continue
        tx = f"{chainid}:{row['hash']}"
        legs(row, native, native, Decimal(row["value"]) / Decimal(10) ** 18,
             f"{tx}:i:{row.get('traceId', '')}:{row['from']}:{row['to']}:{row['value']}")
    for row in tokens:
        tx = f"{chainid}:{row['hash']}"
        dec = int(row.get("tokenDecimal") or 0)
        asset = canonical(chainid, row["contractAddress"])
        sym = row.get("tokenSymbol") or asset
        if asset == "ETH":
            sym = "ETH"
        legs(row, asset, sym, Decimal(row["value"]) / Decimal(10) ** dec, f"{tx}:t:{row['logIndex']}")
    return moves


def fetch_address(chainid: int, address: str, own: set[str], apikey: str | None = None) -> list[Movement]:
    apikey = apikey or os.environ["ETHERSCAN_API_KEY"]
    a = address.lower()
    return normalize(chainid, own,
                     _get_all(chainid, a, "txlist", apikey),
                     _get_all(chainid, a, "txlistinternal", apikey),
                     _get_all(chainid, a, "tokentx", apikey))

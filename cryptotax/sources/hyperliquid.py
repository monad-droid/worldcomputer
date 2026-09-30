"""Hyperliquid via its public info API (no key needed).

Pulls fills (perp + spot), funding, and non-funding ledger updates
(deposits, withdrawals, transfers). Each Hyperliquid address is one account.

Known limits, reported rather than hidden:
- Hyperliquid documents that only the most recent 10,000 fills are
  retrievable through userFillsByTime. If an address hit that cap, older
  fills must come from Hyperliquid's own trade-history CSV export.
  fetch_address() raises a warning when it sees a full window.
- Perp tax treatment: Hyperliquid perps are not regulated futures, so the
  Section 1256 60/40 rule should not apply; realized PnL is reported here as
  short-term capital gain/loss. Confirm with a CPA.
- Genesis HYPE airdrop and other spot balances that did not arrive via a
  ledger update will show up as missing_basis when sold. That is on purpose.
"""
from __future__ import annotations

import warnings
from datetime import datetime, timezone
from decimal import Decimal

from ..http import fetch
from ..models import FEE, FUNDING, PERP_PNL, TRADE, Movement

URL = "https://api.hyperliquid.xyz/info"
START_MS = 1672531200000  # 2023-01-01; Hyperliquid mainnet launched in 2023


def _post(body):
    return fetch("POST", URL, body=body, per_sec=2)


def _paged(kind: str, user: str, end_ms: int, key) -> list[dict]:
    rows, start, seen = [], START_MS, set()
    while True:
        batch = _post({"type": kind, "user": user, "startTime": start, "endTime": end_ms})
        new = [r for r in batch if key(r) not in seen]
        for r in new:
            seen.add(key(r))
        rows.extend(new)
        if not batch or not new:
            break
        start = max(r["time"] for r in batch)
    return rows


def _ts(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def spot_names() -> dict[str, tuple[str, str]]:
    """Map fill coin ('@107' or 'PURR/USDC') -> (base, quote) symbols."""
    meta = _post({"type": "spotMeta"})
    tokens = {t["index"]: t["name"] for t in meta["tokens"]}
    out = {}
    for u in meta["universe"]:
        pair = (tokens[u["tokens"][0]], tokens[u["tokens"][1]])
        out[u["name"]] = pair
        out[f"@{u['index']}"] = pair
    return out


def normalize(user: str, own: set[str], fills, funding, ledger, spot: dict) -> list[Movement]:
    acct = f"hyperliquid:{user.lower()}"
    moves: list[Movement] = []
    D = Decimal

    for f in fills:
        ts, tx = _ts(f["time"]), f"hl:{f['hash']}:{f['tid']}"
        px, sz, fee = D(f["px"]), D(f["sz"]), D(f.get("fee", "0"))
        coin = f["coin"]
        if coin in spot or coin.startswith("@"):
            base, quote = spot.get(coin, (coin, "USDC"))
            notional = px * sz
            usd = notional if quote == "USDC" else None
            sign = 1 if f["side"] == "B" else -1
            moves.append(Movement(ts, acct, base, sign * sz, tx, TRADE, usd, "hyperliquid", base))
            moves.append(Movement(ts, acct, quote, -sign * notional, tx, TRADE, usd, "hyperliquid", quote))
            if fee:
                ftok = f.get("feeToken", "USDC")
                fusd = fee if ftok == "USDC" else (fee * px if ftok == base and usd is not None else None)
                moves.append(Movement(ts, acct, ftok, -fee, tx, FEE, fusd, "hyperliquid", ftok))
        else:
            pnl = D(f.get("closedPnl", "0")) - fee
            if pnl:
                moves.append(Movement(ts, acct, "USDC", pnl, tx, PERP_PNL, pnl, "hyperliquid", f"{coin}-PERP"))

    for r in funding:
        d = r["delta"]
        amt = D(d["usdc"])
        if amt:
            moves.append(Movement(_ts(r["time"]), acct, "USDC", amt, f"hl:fund:{r['hash']}:{d['coin']}:{r['time']}",
                                  FUNDING, amt, "hyperliquid", f"{d['coin']}-FUNDING"))

    for r in ledger:
        d, ts, tx = r["delta"], _ts(r["time"]), f"hl:{r['hash']}"
        t = d.get("type")
        if t == "deposit":
            moves.append(Movement(ts, acct, "USDC", D(d["usdc"]), tx, TRADE, D(d["usdc"]), "hyperliquid", "USDC",
                                  uid=f"{tx}:dep"))
        elif t == "withdraw":
            amt, fee = D(d["usdc"]), D(d.get("fee", "0"))
            moves.append(Movement(ts, acct, "USDC", -amt, tx, TRADE, amt, "hyperliquid", "USDC", uid=f"{tx}:wd"))
            if fee:
                moves.append(Movement(ts, acct, "USDC", -fee, tx, FEE, fee, "hyperliquid", "USDC", uid=f"{tx}:wdfee"))
        elif t in ("internalTransfer", "spotTransfer", "send"):
            token = d.get("token", "USDC")
            amt = D(d.get("usdc") or d.get("amount") or "0")
            usd = D(d["usdcValue"]) if d.get("usdcValue") else (amt if token == "USDC" else None)
            src = (d.get("user") or user).lower()
            dst = (d.get("destination") or "").lower()
            if src in own:
                moves.append(Movement(ts, f"hyperliquid:{src}", token, -amt, tx, TRADE, usd, "hyperliquid", token,
                                      uid=f"{tx}:{t}:out"))
            if dst in own:
                moves.append(Movement(ts, f"hyperliquid:{dst}", token, amt, tx, TRADE, usd, "hyperliquid", token,
                                      uid=f"{tx}:{t}:in"))
        elif t in ("accountClassTransfer", "subAccountTransfer", "vaultDeposit", "vaultWithdraw",
                   "vaultCreate", "vaultDistribution", "liquidation", "rewardsClaim"):
            # Perp<->spot moves are within one account (no-op). The rest need
            # handling once we see them in real data.
            if t != "accountClassTransfer":
                moves.append(Movement(ts, acct, "USDC", D("0"), tx, TRADE, None, "hyperliquid",
                                      f"UNHANDLED:{t}", uid=f"{tx}:{t}"))
    return moves


def fetch_address(user: str, own: set[str], end: datetime | None = None) -> list[Movement]:
    end_ms = int((end or datetime.now(timezone.utc)).timestamp() * 1000)
    fills = _paged("userFillsByTime", user, end_ms, key=lambda r: r["tid"])
    if len(fills) >= 10000:
        warnings.warn(f"{user}: {len(fills)} fills returned; Hyperliquid may be truncating older history. "
                      "Use Hyperliquid's CSV export for earlier fills.")
    funding = _paged("userFunding", user, end_ms, key=lambda r: (r["hash"], r["delta"]["coin"], r["time"]))
    ledger = _paged("userNonFundingLedgerUpdates", user, end_ms, key=lambda r: (r["hash"], r["time"]))
    return normalize(user, own, fills, funding, ledger, spot_names())

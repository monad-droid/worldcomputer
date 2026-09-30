"""Core data types.

Every source (chain, exchange, perp venue) is normalized into Movements: one
signed quantity change of one asset in one account. Legs of the same event
share a tx_id, so a swap is two Movements (one negative, one positive).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

# Movement kinds
TRADE = "trade"          # leg of a swap/buy/sell, or a plain send/receive
FEE = "fee"              # gas / network / trading fee paid in-kind
PERP_PNL = "perp_pnl"    # realized perp PnL (already net), qty in USDC
FUNDING = "funding"      # perp funding payment, qty in USDC
INCOME = "income"        # explicitly known income (staking reward, airdrop)


@dataclass
class Movement:
    ts: datetime                # timezone-aware UTC
    account: str                # e.g. "1:0xabc..." (chainid:address), "coinbase", "hyperliquid:0x..."
    asset: str                  # canonical asset id, e.g. "ETH", "USDC", "8453:0xtoken"
    qty: Decimal                # signed
    tx_id: str                  # groups legs of one event
    kind: str = TRADE
    usd: Decimal | None = None  # USD value of abs(qty) at ts, if known
    source: str = ""
    symbol: str = ""            # human-readable symbol for reports
    uid: str = ""               # dedupe key (same on-chain leg seen via two addresses)


@dataclass
class Lot:
    qty: Decimal
    basis: Decimal              # total USD basis of qty
    acquired: datetime
    origin: str                 # tx_id that created the lot
    flags: set[str] = field(default_factory=set)


@dataclass
class Disposal:
    account: str
    asset: str
    symbol: str
    qty: Decimal
    acquired: datetime | None   # None = various / unknown
    disposed: datetime
    proceeds: Decimal
    basis: Decimal
    tx_id: str
    kind: str = TRADE
    flags: set[str] = field(default_factory=set)

    @property
    def gain(self) -> Decimal:
        return self.proceeds - self.basis

    @property
    def long_term(self) -> bool:
        if self.acquired is None:
            return False
        a = self.acquired.date()
        try:
            anniversary = a.replace(year=a.year + 1)
        except ValueError:  # Feb 29
            anniversary = a.replace(year=a.year + 1, day=28)
        return self.disposed.date() > anniversary


@dataclass
class ReviewItem:
    """Something the engine had to guess about. These are where tax software
    usually goes wrong, so they are reported with their USD magnitude."""
    ts: datetime
    account: str
    asset: str
    symbol: str
    qty: Decimal
    usd: Decimal | None
    tx_id: str
    reason: str

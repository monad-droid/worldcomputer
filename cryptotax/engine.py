"""Per-wallet FIFO lot engine.

Rules implemented (see README for the reasoning and open questions):
- Every account (chain+address, exchange, perp venue) keeps its own lots.
  This is the 2025+ wallet-by-wallet requirement (Rev. Proc. 2024-28), and
  replaying all history per wallet yields the "actual" position at 1/1/2025.
- Transfers between your own accounts move lots (original date and basis
  kept); they are never gains or losses.
- A swap disposes the outgoing asset at the event's USD value and acquires
  the incoming asset with that same value as basis.
- Anything the engine cannot classify with confidence becomes a ReviewItem
  with its USD size, rather than being silently assigned $0 basis.
"""
from __future__ import annotations

import bisect
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from .models import (FEE, FUNDING, INCOME, PERP_PNL, TRADE, Disposal, Lot,
                     Movement, ReviewItem)

ZERO = Decimal(0)
FIAT = {"USD"}
# Assets whose USD price we trust most when two sides of a swap disagree.
REFERENCE_ASSETS = {"USD", "USDC", "USDT", "DAI", "USDE", "PYUSD", "ETH", "BTC", "SOL"}

# Transfer matching windows for transfers that are not in the same tx
# (exchange withdrawals, bridges). Receipt may lag the send.
MATCH_BEFORE = timedelta(minutes=30)
MATCH_AFTER = timedelta(days=3)
MATCH_TOLERANCE = Decimal("0.03")  # received may be up to 3% less (fees)


def _dust(qty: Decimal, ref: Decimal) -> bool:
    return abs(qty) <= max(Decimal("1e-12"), abs(ref) * Decimal("1e-8"))


@dataclass
class TransferPair:
    out: Movement
    inn: Movement


@dataclass
class Result:
    disposals: list[Disposal] = field(default_factory=list)
    reviews: list[ReviewItem] = field(default_factory=list)
    transfers: list[TransferPair] = field(default_factory=list)
    income: list[Movement] = field(default_factory=list)
    nondeductible_fees: list[tuple[Movement, Decimal]] = field(default_factory=list)
    lots: dict = field(default_factory=dict)          # final (account, asset) -> [Lot]
    snapshots: dict = field(default_factory=dict)     # datetime -> {(account, asset): (qty, basis)}


# ---------------------------------------------------------------- preprocessing

def dedupe(moves: list[Movement]) -> list[Movement]:
    seen, out = set(), []
    for m in moves:
        if m.uid:
            if m.uid in seen:
                continue
            seen.add(m.uid)
        out.append(m)
    return out


def net_legs(moves: list[Movement]) -> list[Movement]:
    """Combine legs with the same (tx, account, asset, kind). Wrapping ETH
    into WETH (both canonicalized to ETH) nets to zero and disappears."""
    groups: dict[tuple, list[Movement]] = defaultdict(list)
    for m in moves:
        groups[(m.tx_id, m.account, m.asset, m.kind)].append(m)
    out = []
    for legs in groups.values():
        if len(legs) == 1:
            out.append(legs[0])
            continue
        qty = sum((l.qty for l in legs), ZERO)
        if _dust(qty, max(abs(l.qty) for l in legs)):
            continue
        priced = all(l.usd is not None for l in legs)
        usd = abs(sum((l.usd * (1 if l.qty > 0 else -1) for l in legs), ZERO)) if priced else None
        first = legs[0]
        out.append(Movement(ts=min(l.ts for l in legs), account=first.account, asset=first.asset,
                            qty=qty, tx_id=first.tx_id, kind=first.kind, usd=usd,
                            source=first.source, symbol=first.symbol, uid=first.uid))
    return out


def match_transfers(moves: list[Movement]) -> list[TransferPair]:
    """Pair sends with receipts between your own accounts."""
    pairs: list[TransferPair] = []
    used: set[int] = set()

    # Legs by (tx, account) so we can tell one-sided events from swaps.
    by_tx_acct: dict[tuple, list[Movement]] = defaultdict(list)
    for m in moves:
        if m.kind == TRADE:
            by_tx_acct[(m.tx_id, m.account)].append(m)

    def one_sided(m: Movement) -> bool:
        legs = by_tx_acct[(m.tx_id, m.account)]
        return all((l.qty > 0) == (m.qty > 0) for l in legs)

    # 1) Same transaction: A sends, B receives (on-chain between own addresses).
    by_tx: dict[str, list[Movement]] = defaultdict(list)
    for m in moves:
        if m.kind == TRADE:
            by_tx[m.tx_id].append(m)
    for legs in by_tx.values():
        outs = [l for l in legs if l.qty < 0]
        ins = [l for l in legs if l.qty > 0]
        for o in outs:
            for i in ins:
                if id(i) in used or i.account == o.account or i.asset != o.asset:
                    continue
                if i.qty <= -o.qty and i.qty >= -o.qty * (1 - MATCH_TOLERANCE):
                    pairs.append(TransferPair(o, i))
                    used.update((id(o), id(i)))
                    break

    # 2) Across transactions (exchange <-> wallet, bridges). Greedy, time-ordered.
    outs = sorted((m for m in moves if m.kind == TRADE and m.qty < 0 and id(m) not in used and one_sided(m)),
                  key=lambda m: m.ts)
    ins_by_asset: dict[str, list[Movement]] = defaultdict(list)
    for m in sorted(moves, key=lambda m: m.ts):
        if m.kind == TRADE and m.qty > 0 and id(m) not in used and one_sided(m):
            ins_by_asset[m.asset].append(m)
    for o in outs:
        sent = -o.qty
        best = None
        for i in ins_by_asset.get(o.asset, []):
            if id(i) in used or i.account == o.account:
                continue
            if i.ts < o.ts - MATCH_BEFORE:
                continue
            if i.ts > o.ts + MATCH_AFTER:
                break
            if sent * (1 - MATCH_TOLERANCE) <= i.qty <= sent:
                best = i
                break
        if best is not None:
            pairs.append(TransferPair(o, best))
            used.update((id(o), id(best)))
    return pairs


# ---------------------------------------------------------------- engine

class Engine:
    def __init__(self, snapshot_times: list[datetime] | None = None):
        self.lots: dict[tuple[str, str], list[Lot]] = defaultdict(list)
        self.r = Result()
        self._snapshot_times = sorted(snapshot_times or [])

    # --- lot primitives
    def _add(self, account: str, asset: str, lot: Lot) -> None:
        if asset in FIAT:
            return
        lots = self.lots[(account, asset)]
        keys = [l.acquired for l in lots]
        lots.insert(bisect.bisect_right(keys, lot.acquired), lot)

    def _take(self, account: str, asset: str, qty: Decimal) -> tuple[list[Lot], Decimal]:
        """Remove qty FIFO. Returns (pieces, shortfall)."""
        lots = self.lots[(account, asset)]
        pieces, need = [], qty
        while need > 0 and lots:
            lot = lots[0]
            if lot.qty <= need or _dust(lot.qty - need, need):
                pieces.append(lots.pop(0))
                need -= lot.qty
            else:
                frac = need / lot.qty
                part = Lot(need, lot.basis * frac, lot.acquired, lot.origin, set(lot.flags))
                lot.qty -= need
                lot.basis -= part.basis
                pieces.append(part)
                need = ZERO
        if need < 0 or _dust(need, qty):
            need = ZERO
        return pieces, need

    def _review(self, m: Movement, reason: str, usd: Decimal | None = None) -> None:
        self.r.reviews.append(ReviewItem(m.ts, m.account, m.asset, m.symbol, m.qty,
                                         m.usd if usd is None else usd, m.tx_id, reason))

    def _dispose(self, m: Movement, qty: Decimal, proceeds: Decimal, kind: str = TRADE) -> None:
        if m.asset in FIAT:  # spending dollars is not a disposal
            return
        pieces, short = self._take(m.account, m.asset, qty)
        for p in pieces:
            self.r.disposals.append(Disposal(m.account, m.asset, m.symbol, p.qty, p.acquired, m.ts,
                                             proceeds * p.qty / qty, p.basis, m.tx_id, kind, set(p.flags)))
        if short > 0:
            self.r.disposals.append(Disposal(m.account, m.asset, m.symbol, short, None, m.ts,
                                             proceeds * short / qty, ZERO, m.tx_id, kind, {"missing_basis"}))
            self._review(m, "missing_basis: sold more than this account held; basis set to $0",
                         proceeds * short / qty)

    # --- event handlers
    def _transfer(self, p: TransferPair) -> None:
        sent, got = -p.out.qty, p.inn.qty
        pieces, short = self._take(p.out.account, p.out.asset, sent)
        if short > 0:
            self._review(p.out, "missing_basis: transferred more than this account held; $0 basis carried")
            pieces.append(Lot(short, ZERO, p.out.ts, p.out.tx_id, {"missing_basis"}))
        # Anything lost in transit is a transfer fee (not a sale).
        ratio = got / sent
        for piece in pieces:
            moved = Lot(piece.qty * ratio, piece.basis * ratio, piece.acquired, piece.origin, piece.flags)
            self._add(p.inn.account, p.inn.asset, moved)
        lost_basis = sum((pc.basis for pc in pieces), ZERO) * (1 - ratio)
        if lost_basis > 0:
            self.r.nondeductible_fees.append((p.out, lost_basis))

    def _group(self, legs: list[Movement]) -> None:
        trades = [l for l in legs if l.kind == TRADE]
        outs = [l for l in trades if l.qty < 0]
        ins = [l for l in trades if l.qty > 0]
        fees = [l for l in legs if l.kind == FEE]
        for l in trades:
            if l.qty == 0 and l.symbol.startswith("UNHANDLED"):
                self._review(l, f"unhandled event type {l.symbol}; not included in totals")

        fee_usd = ZERO
        for f in fees:
            v = f.usd if f.usd is not None else ZERO
            if f.usd is None:
                self._review(f, "unpriced_fee")
            self._dispose(f, -f.qty, v, FEE)
            fee_usd += v

        for l in legs:
            if l.kind in (PERP_PNL, FUNDING):
                self._perp(l)
            elif l.kind == INCOME:
                self._income(l)

        if outs and ins:
            value = self._swap_value(outs, ins)
            if value is None:
                for l in outs + ins:
                    self._review(l, "unpriced_swap: neither side has a USD price; $0 used")
                value = ZERO
            out_w = self._weights(outs, value)
            in_w = self._weights(ins, value)
            # Fees on a swap increase the basis of what was acquired.
            for o, v in zip(outs, out_w):
                self._dispose(o, -o.qty, v)
            for i, v in zip(ins, in_w):
                extra = fee_usd * v / value if value else fee_usd / len(ins)
                self._add(i.account, i.asset, Lot(i.qty, v + extra, i.ts, i.tx_id))
        elif outs:
            for o in outs:
                if o.asset in FIAT:  # fiat withdrawal to bank
                    continue
                # A send to an address that is not yours and not matched to a
                # receipt: could be a payment, gift, DeFi deposit, or a wallet
                # missing from your list. Lots leave; no gain/loss is booked.
                pieces, short = self._take(o.account, o.asset, -o.qty)
                basis = sum((p.basis for p in pieces), ZERO)
                self._review(o, f"unmatched_out: sent to an unknown address; basis ${basis:.2f} left the books "
                                f"without a gain/loss", basis)
            if fee_usd:
                self.r.nondeductible_fees.append((fees[0], fee_usd))
        elif ins:
            for i in ins:
                if i.asset in FIAT:  # fiat deposit from bank
                    continue
                if i.usd is None:
                    self._review(i, "unpriced_in: no USD price (often spam); basis $0")
                    self._add(i.account, i.asset, Lot(i.qty, ZERO, i.ts, i.tx_id, {"unpriced"}))
                else:
                    self._review(i, "unmatched_in: received from an unknown address; basis = market value. "
                                    "If this was income it is taxable; if from your own wallet, add that wallet")
                    self._add(i.account, i.asset, Lot(i.qty, i.usd, i.ts, i.tx_id, {"unmatched_in"}))
            if fee_usd:
                self.r.nondeductible_fees.append((fees[0], fee_usd))
        elif fees:
            self.r.nondeductible_fees.append((fees[0], fee_usd))

    @staticmethod
    def _swap_value(outs: list[Movement], ins: list[Movement]) -> Decimal | None:
        v_out = sum((l.usd for l in outs), ZERO) if all(l.usd is not None for l in outs) else None
        v_in = sum((l.usd for l in ins), ZERO) if all(l.usd is not None for l in ins) else None
        if v_out is not None and v_in is not None:
            if any(l.asset in REFERENCE_ASSETS for l in ins) and not any(l.asset in REFERENCE_ASSETS for l in outs):
                return v_in
            return v_out
        return v_out if v_out is not None else v_in

    @staticmethod
    def _weights(legs: list[Movement], value: Decimal) -> list[Decimal]:
        if len(legs) == 1:
            return [value]
        known = [l.usd for l in legs]
        if all(k is not None for k in known) and sum(known) > 0:
            total = sum(known)
            return [value * k / total for k in known]
        return [value / len(legs)] * len(legs)

    def _perp(self, m: Movement) -> None:
        pnl = m.qty  # USDC, signed
        self.r.disposals.append(Disposal(m.account, m.asset, m.symbol, abs(pnl), None, m.ts,
                                         max(pnl, ZERO), max(-pnl, ZERO), m.tx_id, m.kind))
        if pnl > 0:
            self._add(m.account, "USDC", Lot(pnl, pnl, m.ts, m.tx_id))
        else:
            self._take(m.account, "USDC", -pnl)

    def _income(self, m: Movement) -> None:
        self.r.income.append(m)
        if m.usd is None:
            self._review(m, "unpriced_income")
        self._add(m.account, m.asset, Lot(m.qty, m.usd or ZERO, m.ts, m.tx_id, {"income"}))

    def _snapshot(self, t: datetime) -> None:
        snap = {}
        for key, lots in self.lots.items():
            if lots:
                snap[key] = (sum((l.qty for l in lots), ZERO), sum((l.basis for l in lots), ZERO))
        self.r.snapshots[t] = snap

    # --- driver
    def run(self, moves: list[Movement]) -> Result:
        moves = net_legs(dedupe(moves))
        pairs = match_transfers(moves)
        self.r.transfers = pairs
        paired = {id(p.out) for p in pairs} | {id(p.inn) for p in pairs}

        events: list[tuple[datetime, int, object]] = []
        for p in pairs:
            events.append((p.out.ts, 0, p))
        groups: dict[tuple, list[Movement]] = defaultdict(list)
        for m in moves:
            if id(m) not in paired:
                groups[(m.tx_id, m.account)].append(m)
        for legs in groups.values():
            events.append((min(l.ts for l in legs), 1, legs))
        events.sort(key=lambda e: (e[0], e[1]))

        snaps = list(self._snapshot_times)
        for ts, _, ev in events:
            while snaps and ts >= snaps[0]:
                self._snapshot(snaps.pop(0))
            if isinstance(ev, TransferPair):
                self._transfer(ev)
            else:
                self._group(ev)
        for t in snaps:
            self._snapshot(t)
        self.r.lots = dict(self.lots)
        return self.r

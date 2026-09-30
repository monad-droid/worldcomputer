"""Write the year's outputs: Form 8949-style disposals, the review list, and a
summary that breaks out where gains/losses and uncertainty come from.
The summary is written as JSON (read by the UI) and Markdown."""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from .engine import Result
from .models import FEE, FUNDING, PERP_PNL

ZERO = Decimal(0)


def _money(x) -> str:
    return f"{Decimal(str(x)):,.2f}"


def summarize(result: Result, year: int, snapshot: datetime | None = None) -> dict:
    disp = [d for d in result.disposals if d.disposed.year == year]
    reviews = [r for r in result.reviews if r.ts.year == year]

    buckets: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for d in disp:
        term = "long" if d.long_term else "short"
        cat = {PERP_PNL: "perp PnL", FUNDING: "perp funding", FEE: "gas/fee disposals"}.get(d.kind, f"spot {term}-term")
        buckets[f"{cat} gains"] += max(d.gain, ZERO)
        buckets[f"{cat} losses"] += min(d.gain, ZERO)
    by_reason: dict[str, list] = defaultdict(lambda: [0, ZERO])
    for r in reviews:
        k = r.reason.split(":")[0]
        by_reason[k][0] += 1
        by_reason[k][1] += abs(r.usd) if r.usd is not None else ZERO
    by_account: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for d in disp:
        by_account[d.account] += d.gain

    def total(pred):
        return sum((d.gain for d in disp if pred(d)), ZERO)

    holdings = []
    if snapshot and snapshot in result.snapshots:
        for (acct, asset), (q, b) in sorted(result.snapshots[snapshot].items()):
            if q > 0:
                holdings.append({"account": acct, "asset": asset, "qty": f"{q.normalize():f}", "basis": float(b)})

    return {
        "year": year,
        "generated": datetime.now().isoformat(timespec="seconds"),
        "net": float(total(lambda d: True)),
        "short_term": float(total(lambda d: d.kind not in (PERP_PNL, FUNDING) and not d.long_term)),
        "long_term": float(total(lambda d: d.kind not in (PERP_PNL, FUNDING) and d.long_term)),
        "perp": float(total(lambda d: d.kind in (PERP_PNL, FUNDING))),
        "missing_basis_proceeds": float(sum((d.proceeds for d in disp if "missing_basis" in d.flags), ZERO)),
        "disposal_count": len(disp),
        "review_count": len(reviews),
        "buckets": {k: float(v) for k, v in sorted(buckets.items())},
        "review_reasons": [{"reason": k, "count": n, "usd": float(u)}
                           for k, (n, u) in sorted(by_reason.items(), key=lambda kv: -kv[1][1])],
        "by_account": [{"account": a, "net": float(g)}
                       for a, g in sorted(by_account.items(), key=lambda kv: -abs(kv[1]))],
        "holdings_snapshot": {"at": snapshot.isoformat() if snapshot else None, "rows": holdings},
        "transfers_matched": len(result.transfers),
    }


def _markdown(s: dict) -> str:
    lines = [f"# {s['year']} capital gains summary", "",
             "All dates are UTC. Figures are computed, not advice; see README for assumptions.", "",
             "## Realized gain/loss", "", "| bucket | USD |", "|---|---:|"]
    lines += [f"| {k} | {_money(v)} |" for k, v in s["buckets"].items()]
    lines += [f"| **net** | **{_money(s['net'])}** |", "",
              f"Proceeds booked with $0 basis (missing_basis): **${_money(s['missing_basis_proceeds'])}**. "
              "Each dollar here is gain that may really be a loss.", "",
              "## Needs review", "", "| reason | count | USD |", "|---|---:|---:|"]
    lines += [f"| {r['reason']} | {r['count']} | {_money(r['usd'])} |" for r in s["review_reasons"]]
    lines += ["", "## Net by account (top 25 by size)", "", "| account | net USD |", "|---|---:|"]
    lines += [f"| {a['account']} | {_money(a['net'])} |" for a in s["by_account"][:25]]
    h = s["holdings_snapshot"]
    if h["rows"]:
        lines += ["", f"## Holdings per account at {h['at']} (documentation for Rev. Proc. 2024-28)", "",
                  "| account | asset | qty | basis USD |", "|---|---|---:|---:|"]
        lines += [f"| {r['account']} | {r['asset']} | {r['qty']} | {_money(r['basis'])} |" for r in h["rows"]]
    return "\n".join(lines) + "\n"


def write(result: Result, year: int, out_dir: str | Path = "out", snapshot: datetime | None = None) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    disp = [d for d in result.disposals if d.disposed.year == year]
    reviews = [r for r in result.reviews if r.ts.year == year]

    with open(out / f"disposals_{year}.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["description", "date_acquired", "date_sold", "proceeds", "cost_basis", "gain_loss",
                    "term", "kind", "account", "flags", "tx"])
        for d in sorted(disp, key=lambda d: d.disposed):
            w.writerow([f"{d.qty.normalize():f} {d.symbol or d.asset}",
                        d.acquired.date().isoformat() if d.acquired else "VARIOUS", d.disposed.date().isoformat(),
                        f"{d.proceeds:.2f}", f"{d.basis:.2f}", f"{d.gain:.2f}",
                        "long" if d.long_term else "short", d.kind, d.account, ";".join(sorted(d.flags)), d.tx_id])

    with open(out / f"review_{year}.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "account", "asset", "qty", "usd", "reason", "tx"])
        for r in sorted(reviews, key=lambda r: -(abs(r.usd) if r.usd is not None else ZERO)):
            w.writerow([r.ts.isoformat(), r.account, r.symbol or r.asset, f"{r.qty.normalize():f}",
                        "" if r.usd is None else f"{r.usd:.2f}", r.reason, r.tx_id])

    s = summarize(result, year, snapshot)
    (out / f"summary_{year}.json").write_text(json.dumps(s, indent=1))
    path = out / f"summary_{year}.md"
    path.write_text(_markdown(s))
    return path

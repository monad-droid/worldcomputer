"""Load your address list. Accepts a plain list (one address per line) or a CSV
whose first column is the address; anything after is treated as a label.

Every line is classified by chain family. Only families with a fetcher are
scanned; the rest are counted and reported so nothing is silently dropped.
0x addresses (20 bytes) are scanned on EVM chains and on Hyperliquid.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

B58 = "[1-9A-HJ-NP-Za-km-z]"
PATTERNS = [  # order matters: specific before generic
    ("evm", re.compile(r"^0x[0-9a-fA-F]{40}$")),
    ("aptos_or_sui", re.compile(r"^0x[0-9a-fA-F]{64}$")),
    ("bitcoin", re.compile(r"^(bc1[02-9ac-hj-np-z]{11,71}|[13]" + B58 + r"{25,34})$")),
    ("dogecoin", re.compile(r"^D" + B58 + r"{33}$")),
    ("tron", re.compile(r"^T" + B58 + r"{33}$")),
    ("xrp", re.compile(r"^r[1-9A-HJ-NP-Za-km-z]{24,34}$")),
    ("zcash", re.compile(r"^t1" + B58 + r"{33}$")),
    ("tezos", re.compile(r"^tz[123]" + B58 + r"{33}$")),
    ("mina", re.compile(r"^B62q" + B58 + r"{51}$")),
    ("cosmos", re.compile(r"^(cosmos|celestia|dym|sei|terra|init|osmo|inj|tia)1[02-9ac-hj-np-z]{38,58}$")),
    ("solana", re.compile(r"^" + B58 + r"{43,44}$")),
    ("substrate", re.compile(r"^" + B58 + r"{46,48}$")),
]
SUPPORTED = {"evm"}


@dataclass
class Wallet:
    address: str
    kind: str
    label: str = ""


def classify(addr: str) -> str:
    for kind, rx in PATTERNS:
        if rx.match(addr):
            return kind
    return "unknown"


def load(path: str | Path) -> list[Wallet]:
    return parse_text(Path(path).read_text())


def parse_text(text: str) -> list[Wallet]:
    out, seen = [], set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in re.split(r"[,\t]", line)]
        addr, label = parts[0], " ".join(p for p in parts[1:] if p)
        kind = classify(addr)
        if kind == "unknown" and not any(c.isdigit() for c in addr):
            continue  # header row like "to_address"
        if kind in ("evm", "aptos_or_sui"):
            addr = addr.lower()
        if addr not in seen:
            seen.add(addr)
            out.append(Wallet(addr, kind, label))
    return out


def summary(ws: list[Wallet]) -> str:
    c = Counter(w.kind for w in ws)
    return ", ".join(f"{k}: {n}{'' if k in SUPPORTED else ' (not scanned yet)'}" for k, n in c.most_common())


# --- persistent wallet book used by the UI (data/wallets.json)
BOOK = Path("data/wallets.json")
# mine: confirmed yours, gets fetched. unsure: not fetched until confirmed.
# not_mine: exchange deposit address, someone else's wallet, etc.
ROLES = ("mine", "unsure", "not_mine")


def read_book() -> list[dict]:
    if BOOK.exists():
        return json.loads(BOOK.read_text())
    return []


def write_book(rows: list[dict]) -> None:
    BOOK.parent.mkdir(parents=True, exist_ok=True)
    BOOK.write_text(json.dumps(rows, indent=1))


def merge_into_book(ws: list[Wallet], role: str = "mine") -> tuple[int, int, int]:
    """Add new addresses with `role`; addresses already present get their role
    set to `role` too, so pasting a confirmed list upgrades existing entries."""
    role = role if role in ROLES else "mine"
    rows = read_book()
    by_addr = {r["address"]: r for r in rows}
    added = changed = 0
    for w in ws:
        r = by_addr.get(w.address)
        if r is None:
            r = {"address": w.address, "kind": w.kind, "label": w.label, "role": role}
            rows.append(r)
            by_addr[w.address] = r
            added += 1
        else:
            if r.get("role") != role:
                r["role"] = role
                changed += 1
            if w.label and not r.get("label"):
                r["label"] = w.label
    write_book(rows)
    return added, changed, len(rows)


def owned(rows: list[dict] | None = None) -> list[Wallet]:
    rows = read_book() if rows is None else rows
    return [Wallet(r["address"], r["kind"], r.get("label", "")) for r in rows if r.get("role", "mine") == "mine"]

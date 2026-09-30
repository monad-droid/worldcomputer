"""Load your address list. Accepts a plain list (one address per line) or a CSV
whose first column is the address; anything after is treated as a label.
0x addresses are scanned on EVM chains and on Hyperliquid; base58 addresses
are treated as Solana."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

EVM_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
SOL_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


@dataclass
class Wallet:
    address: str
    kind: str  # "evm" | "solana"
    label: str = ""


def load(path: str | Path) -> list[Wallet]:
    out, seen = [], set()
    for raw in Path(path).read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in re.split(r"[,\t]", line)]
        addr, label = parts[0], " ".join(p for p in parts[1:] if p)
        if EVM_RE.match(addr):
            w = Wallet(addr.lower(), "evm", label)
        elif SOL_RE.match(addr):
            w = Wallet(addr, "solana", label)
        else:
            continue  # header row or unrecognized
        if w.address not in seen:
            seen.add(w.address)
            out.append(w)
    return out

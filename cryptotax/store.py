"""Persist normalized movements as JSON lines (data/movements/<source>.jsonl)."""
from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from .models import Movement

DIR = Path("data/movements")


def save(name: str, moves: list[Movement]) -> Path:
    DIR.mkdir(parents=True, exist_ok=True)
    path = DIR / f"{name}.jsonl"
    with open(path, "w") as f:
        for m in moves:
            d = m.__dict__.copy()
            d["ts"] = m.ts.isoformat()
            d["qty"] = str(m.qty)
            d["usd"] = None if m.usd is None else str(m.usd)
            f.write(json.dumps(d) + "\n")
    return path


def load_all() -> list[Movement]:
    out = []
    for path in sorted(DIR.glob("*.jsonl")):
        for line in path.read_text().splitlines():
            d = json.loads(line)
            d["ts"] = datetime.fromisoformat(d["ts"])
            d["qty"] = Decimal(d["qty"])
            d["usd"] = None if d["usd"] is None else Decimal(d["usd"])
            out.append(Movement(**d))
    return out

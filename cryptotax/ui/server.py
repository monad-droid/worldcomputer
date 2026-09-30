"""Local web UI. Standard library only; binds to 127.0.0.1.

Safety for a local app holding an API key:
- listens on loopback only and rejects requests whose Host header is not
  localhost/127.0.0.1 (blocks DNS-rebinding pages from reaching it);
- state-changing calls require a JSON body, which a cross-site form cannot
  send without a CORS preflight that this server never approves.
"""
from __future__ import annotations

import csv
import json
import re
import threading
import traceback
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .. import pipeline, store, wallets

HERE = Path(__file__).parent
OUT = Path("out")
SETTINGS = Path("data/settings.json")


# ---------------------------------------------------------------- settings

def read_settings() -> dict:
    return json.loads(SETTINGS.read_text()) if SETTINGS.exists() else {}


def write_settings(s: dict) -> None:
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(json.dumps(s, indent=1))
    SETTINGS.chmod(0o600)


# ---------------------------------------------------------------- background job

class Job:
    def __init__(self):
        self.lock = threading.Lock()
        self.kind = None
        self.status = "idle"      # idle | running | done | error | cancelled
        self.done = 0
        self.total = 0
        self.label = ""
        self.lines: list[str] = []
        self.failures: list[str] = []
        self.cancelled = False
        self.started = None

    # progress protocol used by pipeline
    def log(self, msg: str) -> None:
        with self.lock:
            self.lines.append(f"{datetime.now():%H:%M:%S}  {msg}")
            self.lines = self.lines[-400:]

    def step(self, done: int, total: int, label: str) -> None:
        with self.lock:
            self.done, self.total, self.label = done, total, label

    def snapshot(self) -> dict:
        with self.lock:
            return {"kind": self.kind, "status": self.status, "done": self.done, "total": self.total,
                    "label": self.label, "log": list(self.lines), "failures": list(self.failures),
                    "started": self.started}

    def start(self, kind: str, fn) -> bool:
        with self.lock:
            if self.status == "running":
                return False
            self.kind, self.status, self.done, self.total, self.label = kind, "running", 0, 0, ""
            self.lines, self.failures, self.cancelled = [], [], False
            self.started = datetime.now().isoformat(timespec="seconds")

        def run():
            try:
                result = fn(self)
                if isinstance(result, list):
                    self.failures = result
                self.status = "done"
            except pipeline.Cancelled:
                self.log("Cancelled.")
                self.status = "cancelled"
            except Exception as e:
                self.log(f"ERROR: {e}")
                self.log(traceback.format_exc(limit=3))
                self.status = "error"

        threading.Thread(target=run, daemon=True).start()
        return True


JOB = Job()


# ---------------------------------------------------------------- table reads

_cache: dict[Path, tuple[float, list[dict]]] = {}


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    mtime = path.stat().st_mtime
    hit = _cache.get(path)
    if hit and hit[0] == mtime:
        return hit[1]
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    _cache[path] = (mtime, rows)
    return rows


NUMERIC = {"proceeds", "cost_basis", "gain_loss", "usd", "qty"}


def query_table(year: int, table: str, q: str, reason: str, sort: str, desc: bool,
                offset: int, limit: int) -> dict:
    path = OUT / (f"disposals_{year}.csv" if table == "disposals" else f"review_{year}.csv")
    rows = _rows(path)
    if reason:
        key = "reason" if table == "review" else "flags"
        rows = [r for r in rows if r.get(key, "").startswith(reason) or (table == "disposals" and reason in r.get(key, ""))]
    if q:
        ql = q.lower()
        rows = [r for r in rows if any(ql in str(v).lower() for v in r.values())]
    if sort and rows and sort in rows[0]:
        if sort in NUMERIC:
            def k(r):
                try:
                    return float(r[sort] or 0)
                except ValueError:
                    return 0.0
        else:
            def k(r):
                return r[sort]
        rows = sorted(rows, key=k, reverse=desc)
    total = len(rows)
    sums = {}
    if table == "disposals":
        sums = {c: round(sum(float(r[c] or 0) for r in rows), 2) for c in ("proceeds", "cost_basis", "gain_loss")}
    return {"total": total, "rows": rows[offset:offset + limit], "sums": sums,
            "columns": list(_rows(path)[0].keys()) if _rows(path) else []}


def data_status() -> dict:
    files = {}
    for p in sorted(store.DIR.glob("*.jsonl")) if store.DIR.exists() else []:
        with open(p) as f:
            files[p.stem] = sum(1 for _ in f)
    years = sorted({int(m.group(1)) for p in OUT.glob("summary_*.json")
                    if (m := re.match(r"summary_(\d{4})\.json", p.name))}) if OUT.exists() else []
    return {"movement_files": files, "result_years": years}


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "cryptotax"

    def log_message(self, fmt, *args):  # keep the terminal quiet
        pass

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost")

    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json")

    def _err(self, msg: str, code: int = 400) -> None:
        self._json({"error": msg}, code)

    def do_GET(self):
        if not self._host_ok():
            return self._err("bad host", 403)
        u = urlparse(self.path)
        qs = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path == "/":
                return self._send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
            if u.path == "/api/state":
                s = read_settings()
                key = s.get("etherscan_key", "")
                book = wallets.read_book()
                return self._json({
                    "settings": {"has_key": bool(key), "key_hint": f"…{key[-4:]}" if key else ""},
                    "wallets": book,
                    "supported": sorted(wallets.SUPPORTED),
                    "chains": [{"id": c, "name": pipeline.CHAIN_NAMES.get(c, str(c))} for c in pipeline.DEFAULT_CHAINS],
                    "job": JOB.snapshot(),
                    "data": data_status(),
                })
            if u.path == "/api/job":
                return self._json(JOB.snapshot())
            if u.path == "/api/results":
                p = OUT / f"summary_{int(qs.get('year', 2025))}.json"
                return self._json(json.loads(p.read_text()) if p.exists() else None)
            if u.path == "/api/rows":
                return self._json(query_table(
                    int(qs.get("year", 2025)), qs.get("table", "disposals"), qs.get("q", ""),
                    qs.get("reason", ""), qs.get("sort", ""), qs.get("desc") == "1",
                    max(0, int(qs.get("offset", 0))), min(500, int(qs.get("limit", 100)))))
            m = re.fullmatch(r"/download/((?:disposals|review|summary)_\d{4}\.(?:csv|md|json))", u.path)
            if m and (OUT / m.group(1)).exists():
                return self._send(200, (OUT / m.group(1)).read_bytes(), "application/octet-stream",
                                  {"Content-Disposition": f'attachment; filename="{m.group(1)}"'})
            return self._err("not found", 404)
        except Exception as e:
            return self._err(str(e), 500)

    def do_POST(self):
        if not self._host_ok():
            return self._err("bad host", 403)
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self._err("json required", 415)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._err("bad json")
        path = urlparse(self.path).path
        try:
            if path == "/api/settings":
                s = read_settings()
                key = (body.get("etherscan_key") or "").strip()
                if key:
                    s["etherscan_key"] = key
                write_settings(s)
                return self._json({"ok": True})
            if path == "/api/wallets/import":
                ws = wallets.parse_text(body.get("text", ""))
                added, changed, total = wallets.merge_into_book(ws, body.get("role", "mine"))
                return self._json({"added": added, "changed": changed, "total": total, "parsed": len(ws)})
            if path == "/api/wallets/update":
                addrs = set(body.get("addresses") or [body.get("address")])
                rows = wallets.read_book()
                for r in rows:
                    if r["address"] in addrs:
                        if body.get("role") in wallets.ROLES:
                            r["role"] = body["role"]
                        if "label" in body:
                            r["label"] = str(body["label"])[:80]
                wallets.write_book(rows)
                return self._json({"ok": True})
            if path == "/api/wallets/delete":
                addrs = set(body.get("addresses") or [])
                wallets.write_book([r for r in wallets.read_book() if r["address"] not in addrs])
                return self._json({"ok": True})
            if path == "/api/fetch":
                key = read_settings().get("etherscan_key")
                if not key:
                    return self._err("Add your Etherscan API key first.")
                ws = wallets.owned()
                if not ws:
                    return self._err("Add wallets first.")
                chains = [int(c) for c in body.get("chains") or []] or None
                hl = bool(body.get("hyperliquid", True))
                ok = JOB.start("fetch", lambda p: pipeline.run_fetch(ws, p, chains, key, hl))
                return self._json({"ok": ok} if ok else {"error": "A job is already running."})
            if path == "/api/report":
                year = int(body.get("year", 2025))
                ok = JOB.start("report", lambda p: pipeline.run_report(year, p))
                return self._json({"ok": ok} if ok else {"error": "A job is already running."})
            if path == "/api/cancel":
                JOB.cancelled = True
                return self._json({"ok": True})
            return self._err("not found", 404)
        except Exception as e:
            return self._err(str(e), 500)


def serve(port: int = 8765, open_browser: bool = True) -> None:
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"cryptotax UI running at {url}  (Ctrl+C to stop)")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass

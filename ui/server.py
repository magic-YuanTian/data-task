"""DataTaskBench explorer: tasks with their ground-truth reasoning paths, the data, sources and a SQL workbench.

    .venv/bin/python ui/server.py                    # http://127.0.0.1:8765
    .venv/bin/python ui/server.py --warm             # precompute table profiles, charts and maps, then exit
    .venv/bin/python ui/server.py --public --port 3550 --embed-origin https://example.org   # for visitors, framed

The server only reads the built environments in out/ (databases are opened read-only, without file access).
"""
from __future__ import annotations

import argparse
import gzip
import json
import mimetypes
import re
import sys
import threading
import time
import traceback
from collections import defaultdict, deque
from contextlib import contextmanager
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from store import LEVEL_NAMES, LEVEL_ORDER, TASK_TYPES, UI, Store  # noqa: E402

STATIC = UI / "static"
ROUTES = []


def route(method, pattern, gated=False):
    """gated: limited per visitor in public mode."""
    def deco(fn):
        ROUTES.append((method, re.compile(f"^{pattern}$"), fn, gated))
        return fn
    return deco


class ApiError(Exception):
    def __init__(self, status, msg):
        super().__init__(msg)
        self.status = status


class Gate:
    """Public mode: a few visitor queries at a time and a rate per visitor, so that no visitor can hold the machine."""

    def __init__(self, slots=3, per_minute=30, wait=20):
        self.slots = threading.BoundedSemaphore(slots)
        self.per_minute, self.wait = per_minute, wait
        self.recent = defaultdict(deque)
        self.lock = threading.Lock()

    @contextmanager
    def admit(self, client):
        now = time.monotonic()
        with self.lock:
            if len(self.recent) > 10000:
                for k in [k for k, q in self.recent.items() if not q or now - q[-1] > 60]:
                    del self.recent[k]
            q = self.recent[client]
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= self.per_minute:
                raise ApiError(429, "Too many queries in the last minute. Wait a moment and run it again.")
            q.append(now)
        if not self.slots.acquire(timeout=self.wait):
            raise ApiError(503, "The demo is busy with other queries. Try again in a moment.")
        try:
            yield
        finally:
            self.slots.release()


def _world(store, world):
    if world not in store.builds():
        raise ApiError(404, f"unknown environment {world}")
    return world


# ---------------------------------------------------------------- API

@route("GET", "/api/meta")
def meta(store, q, body):
    return {"mode": "demo" if store.public else "local", "embed_origins": Handler.embed_origins,
            "levels": LEVEL_NAMES, "level_order": LEVEL_ORDER, "task_types": TASK_TYPES,
            "worlds": [store.world_summary(w) for w in store.builds()]}


@route("GET", r"/api/world/(?P<world>[\w-]+)")
def world(store, q, body, world):
    w = _world(store, world)
    out = store.world_summary(w)
    out.update(tables=store.tables(w), dictionary_note=store.dictionary(w)["note"], metrics=store.metrics(w),
               dictionary_md=store.dictionary_md(w),
               views=store.views(w), sources=store.documents(w))
    return out


@route("GET", r"/api/world/(?P<world>[\w-]+)/profile/(?P<table>\w+)")
def profile(store, q, body, world, table):
    return store.profile(_world(store, world), table)


@route("GET", r"/api/world/(?P<world>[\w-]+)/view/(?P<view>[\w:.-]+)")
def view(store, q, body, world, view):
    return store.view_data(_world(store, world), view)


@route("GET", r"/api/world/(?P<world>[\w-]+)/geo/(?P<layer>\w+)")
def geo(store, q, body, world, layer):
    return store.geo(_world(store, world), layer)


@route("GET", r"/api/tasks/(?P<world>[\w-]+)")
def tasks(store, q, body, world):
    return store.tasks(_world(store, world))


@route("POST", r"/api/sql", gated=True)
def sql(store, q, body):
    w = _world(store, body.get("world"))
    text = (body.get("sql") or "").strip()
    if not text:
        raise ApiError(400, "empty query")
    limit = max(1, min(int(body.get("limit", 2000)), 5000))
    try:
        return store.run_sql(w, text, limit=limit, timeout=30 if store.public else 120)
    except TimeoutError as e:
        raise ApiError(408, str(e))
    except Exception as e:  # database errors go back to the editor
        raise ApiError(400, str(e).split("\n\nLINE")[0][:2000] if "LINE" in str(e) else str(e)[:2000])


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    store: Store = None
    gate: Gate | None = None          # set in public mode
    embed_origins: list[str] = []     # pages allowed to show the explorer in a frame
    server_version = "DataTaskBenchExplorer/1.0"

    def log_message(self, fmt, *args):
        if "/api/" in (args[0] if args else "") or (len(args) > 1 and str(args[1])[0] in "45"):
            sys.stderr.write("%s %s\n" % (self.log_date_time_string(), fmt % args))

    def _send(self, status, data: bytes, ctype, extra=None):
        gz = len(data) > 2048 and "gzip" in self.headers.get("Accept-Encoding", "") and \
            ctype.split(";")[0] in ("application/json", "text/html", "text/css", "application/javascript",
                                    "text/javascript", "text/plain", "text/csv")
        if gz:
            data = gzip.compress(data, 5)
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        if self.embed_origins:
            self.send_header("Content-Security-Policy", "frame-ancestors 'self' " + " ".join(self.embed_origins))
        if gz:
            self.send_header("Content-Encoding", "gzip")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _json(self, status, obj):
        self._send(status, json.dumps(obj, ensure_ascii=False, default=str).encode(), "application/json; charset=utf-8")

    def do_GET(self):
        self._dispatch("GET")

    def do_HEAD(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def _dispatch(self, method):
        url = urlparse(self.path)
        path = unquote(url.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            if path.startswith("/api/"):
                return self._api(method, path, q)
            return self._static(path)
        except ApiError as e:
            self._json(e.status, {"error": str(e)})
        except PermissionError as e:
            self._json(403, {"error": str(e)})
        except KeyError as e:
            self._json(404, {"error": f"not found: {e}"})
        except Exception as e:
            traceback.print_exc()
            self._json(500, {"error": "internal error" if self.store.public else f"{type(e).__name__}: {e}"})

    def _api(self, method, path, q):
        body = {}
        if method == "POST":
            n = int(self.headers.get("Content-Length") or 0)
            if n > 2_000_000:
                raise ApiError(413, "request too large")
            body = json.loads(self.rfile.read(n) or b"{}")
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise ApiError(415, "expected application/json")
        for m, rx, fn, gated in ROUTES:
            mt = rx.match(path)
            if mt and m == method:
                if gated and self.gate:
                    with self.gate.admit(self._client()):
                        return self._json(200, fn(self.store, q, body, **mt.groupdict()))
                return self._json(200, fn(self.store, q, body, **mt.groupdict()))
        raise ApiError(404, f"no route {method} {path}")

    def _client(self):
        # behind Cloudflare every request comes from the local tunnel; the visitor's address is in this header
        return self.headers.get("CF-Connecting-IP") or self.client_address[0]

    def _static(self, path):
        rel = path.lstrip("/") or "index.html"
        f = (STATIC / rel).resolve()
        if STATIC.resolve() not in f.parents or not f.is_file():
            f = STATIC / "index.html"            # client-side routes
        ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        if f.suffix == ".js":
            ctype = "text/javascript; charset=utf-8"
        elif f.suffix in (".html", ".css"):
            ctype += "; charset=utf-8"
        self._send(200, f.read_bytes(), ctype)


def warm(store: Store):
    """Precompute table profiles, charts and maps of every environment."""
    for w in store.builds():
        for t in store.tables(w):
            print(f"profile {w} {t['name']}", flush=True)
            store.profile(w, t["name"])
        for m in store.metrics(w):
            print(f"metric {w} {m}", flush=True)
            store.view_data(w, f"metric:{m}")
        for v in store.views(w):
            print(f"view {w} {v['id']}", flush=True)
            if v.get("type") == "map":
                for layer in v.get("layers", []):
                    store.geo(w, layer)
            if v.get("sql"):
                store.view_data(w, v["id"])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--public", action="store_true", help="limit queries per visitor and hide server error details")
    ap.add_argument("--worlds", default="", help="serve only these worlds (comma-separated)")
    ap.add_argument("--warm", action="store_true", help="precompute profiles and charts, then exit")
    ap.add_argument("--embed-origin", action="append", default=[], metavar="ORIGIN",
                    help="a site allowed to show the explorer in a frame, such as https://example.org (repeatable)")
    a = ap.parse_args()
    mimetypes.add_type("text/javascript", ".js")
    store = Store(public=a.public, worlds=[w for w in a.worlds.split(",") if w])
    if a.warm:
        return warm(store)
    Handler.store = store
    Handler.embed_origins = [o.rstrip("/") for o in a.embed_origin]
    if a.public:
        Handler.gate = Gate()
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    srv.daemon_threads = True
    print(f"DataTaskBench explorer ({'public' if a.public else 'local'}) on http://{a.host}:{a.port}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""RustDesk address book web app: browse, connect, live online status, and
add / edit / delete machines — all in the browser.

    python3 serve.py            # http://127.0.0.1:8765
    python3 serve.py 9000       # custom port

Data lives in book.json (see book.py). Mutations are saved immediately and
re-exported to rustdesk_address_book.json/.csv.
"""
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import book as bookmod
import status

HERE = Path(__file__).resolve().parent
NAT_PORT = 21115                 # online queries hit rendezvous_port - 1
STATUS_TTL = 8.0                 # cache window for status polls
_lock = threading.Lock()         # serialize book mutations
_status_cache = {"t": 0.0, "data": {}}


def collect_status():
    now = time.monotonic()
    if now - _status_cache["t"] < STATUS_TTL and _status_cache["data"]:
        return _status_cache["data"]
    smap = bookmod.status_map(bookmod.load())
    merged = {}
    if smap:
        with ThreadPoolExecutor(max_workers=len(smap)) as ex:
            futs = [ex.submit(status.query, host, NAT_PORT, ids)
                    for host, ids in smap.items()]
            for fut in futs:
                merged.update(fut.result())
    _status_cache.update(t=now, data=merged)
    return merged


def _validate(data, book, *, existing_id=None):
    pid = (data.get("id") or "").strip()
    if not pid:
        return "id is required"
    if pid != existing_id and any(p["id"] == pid for p in book["peers"]):
        return f"id {pid} already exists"
    server = (data.get("server") or book["default_server"]).strip()
    if server not in book["servers"]:
        if not (data.get("key") or "").strip():
            return f"unknown server {server}: provide a key"
    return None


def _apply(data, book):
    server = (data.get("server") or book["default_server"]).strip()
    if server not in book["servers"]:
        book["servers"][server] = (data.get("key") or "").strip()
    tags = data.get("tags")
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    if not tags:
        tags = [bookmod.classify(data.get("hostname", ""))]
    return {
        "id": data["id"].strip(),
        "alias": (data.get("alias") or "").strip() or data.get("hostname") or data["id"].strip(),
        "hostname": (data.get("hostname") or "").strip(),
        "username": (data.get("username") or "").strip(),
        "platform": (data.get("platform") or "").strip(),
        "server": server,
        "tags": tags,
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    # ---- routes ----
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            return self._file("app.html", "text/html; charset=utf-8")
        if path == "/manifest.webmanifest":
            return self._file("manifest.webmanifest", "application/manifest+json")
        if path == "/icon.svg":
            return self._file("icon.svg", "image/svg+xml")
        if path == "/api/book":
            return self._json(200, bookmod.view(bookmod.load()))
        if path == "/status":
            try:
                return self._json(200, collect_status())
            except Exception as e:
                return self._json(500, {"error": str(e)})
        self._json(404, {"error": "not found"})

    def do_POST(self):
        segs = self._segs()
        if segs == ["api", "resync"]:
            with _lock:
                book = bookmod.load()
                added = bookmod.merge(book)
            return self._json(200, {"added": [{"id": p["id"], "alias": p["alias"]}
                                              for p in added],
                                    "book": bookmod.view(bookmod.load())})
        if segs == ["api", "tags"]:                       # create tag
            d = self._body()
            return self._tag_op(lambda b: bookmod.add_tag(b, d.get("name"), d.get("color")))
        if len(segs) == 4 and segs[:2] == ["api", "peers"] and segs[3] == "tags":
            d = self._body()                              # change membership (drag/drop)
            pid = urllib_unquote(segs[2])
            return self._tag_op(lambda b: bookmod.set_membership(
                b, pid, d.get("add", []), d.get("remove", [])))
        if segs == ["api", "peers"]:                      # add machine
            with _lock:
                book = bookmod.load()
                data = self._body()
                err = _validate(data, book)
                if err:
                    return self._json(400, {"error": err})
                book["peers"].append(_apply(data, book))
                bookmod.ensure_tags(book)
                bookmod.save(book)
            return self._json(201, bookmod.view(bookmod.load()))
        self._json(404, {"error": "not found"})

    def do_PUT(self):
        segs = self._segs()
        if segs == ["api", "tags"]:                       # rename tag
            d = self._body()
            return self._tag_op(lambda b: bookmod.rename_tag(b, d.get("old"), d.get("new")))
        if segs == ["api", "tags", "color"]:              # recolor tag
            d = self._body()
            return self._tag_op(lambda b: bookmod.set_tag_color(b, d.get("name"), d.get("color")))
        pid = self._peer_id()
        if pid is None:
            return self._json(404, {"error": "not found"})
        with _lock:
            book = bookmod.load()
            idx = next((i for i, p in enumerate(book["peers"]) if p["id"] == pid), None)
            if idx is None:
                return self._json(404, {"error": f"no peer {pid}"})
            data = self._body()
            err = _validate(data, book, existing_id=pid)
            if err:
                return self._json(400, {"error": err})
            book["peers"][idx] = _apply(data, book)
            bookmod.ensure_tags(book)
            bookmod.save(book)
        self._json(200, bookmod.view(bookmod.load()))

    def do_DELETE(self):
        segs = self._segs()
        if segs == ["api", "tags"]:                       # delete tag (+ children)
            d = self._body()
            return self._tag_op(lambda b: bookmod.delete_tag(b, d.get("name")))
        pid = self._peer_id()
        if pid is None:
            return self._json(404, {"error": "not found"})
        with _lock:
            book = bookmod.load()
            before = len(book["peers"])
            book["peers"] = [p for p in book["peers"] if p["id"] != pid]
            if len(book["peers"]) == before:
                return self._json(404, {"error": f"no peer {pid}"})
            bookmod.save(book)
        self._json(200, bookmod.view(bookmod.load()))

    def _tag_op(self, fn):
        try:
            with _lock:
                fn(bookmod.load())
        except ValueError as e:
            return self._json(400, {"error": str(e)})
        self._json(200, bookmod.view(bookmod.load()))

    def _segs(self):
        return self.path.split("?", 1)[0].strip("/").split("/")

    def _peer_id(self):
        segs = self._segs()
        if len(segs) == 3 and segs[:2] == ["api", "peers"]:
            return urllib_unquote(segs[2])
        return None

    def _file(self, name, ctype):
        p = HERE / name
        if not p.exists():
            return self._json(404, {"error": f"{name} missing"})
        body = p.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def urllib_unquote(s):
    import urllib.parse
    return urllib.parse.unquote(s)


def main():
    args = sys.argv[1:]
    no_open = "--no-open" in args                 # used by the login agent
    ports = [a for a in args if a.isdigit()]
    port = int(ports[0]) if ports else 8765
    bookmod.load()  # ensure book.json exists (seed if needed)
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"RustDesk address book at {url}  (Ctrl-C to stop)")
    if not no_open:
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:
            pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()

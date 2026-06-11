#!/usr/bin/env python3
"""Editable address-book data layer for the RustDesk launcher.

book.json is the source of truth (created/edited via the web app). This module
seeds it from the local RustDesk peer store the first time, and provides helpers
to compute connect links, group ids per server for status, and export to the
RustDesk address-book JSON/CSV format.

CLI:
    python3 book.py seed [--force]   # (re)build book.json from the peer store
    python3 book.py export           # write rustdesk_address_book.json + .csv
"""
import csv
import json
import os
import re
import sys
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOOK = HERE / "book.json"               # local data store (gitignored)
TAG_RULES_FILE = HERE / "tag_rules.json"  # optional hostname->tag rules (gitignored)

# palette used to auto-color new tags; TAG_ORDER/TAG_COLORS stay empty by default
# (the persisted book.json keeps whatever order/colors you set in the UI)
TAG_ORDER = []
TAG_COLORS = {}
PALETTE = ["#1976D2", "#388E3C", "#F57C00", "#C2185B", "#7B1FA2", "#0097A7",
           "#C0392B", "#8E44AD", "#16A085", "#D4AC0D", "#2C3E50", "#E67E22"]


def rustdesk_dir():
    """Location of the RustDesk config/peer store for this OS."""
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library/Preferences/com.carriez.RustDesk"
    if sys.platform.startswith("win"):
        return Path(os.environ.get("APPDATA", home)) / "RustDesk" / "config"
    return home / ".config/rustdesk"


PEERS_DIR = rustdesk_dir() / "peers"


def rustdesk_default():
    """(server, key) for this client's configured rendezvous server, read from
    RustDesk2.toml — so the tool works against whatever server you use."""
    f = rustdesk_dir() / "RustDesk2.toml"
    server = key = ""
    if f.exists():
        t = f.read_text(errors="replace")
        m = (re.search(r"custom-rendezvous-server\s*=\s*'([^']*)'", t)
             or re.search(r"rendezvous_server\s*=\s*'([^']*)'", t))
        if m:
            server = m.group(1).split(":")[0]
        k = re.search(r"^\s*key\s*=\s*'([^']*)'", t, re.M)
        if k:
            key = k.group(1)
    return server, key


def _rules():
    """Optional [[regex, tag], ...] from tag_rules.json for auto-classifying new
    machines by hostname. Absent -> machines start untagged."""
    if TAG_RULES_FILE.exists():
        try:
            return [(re.compile(p, re.I), t) for p, t in json.loads(TAG_RULES_FILE.read_text())]
        except Exception:
            return []
    return []


def classify(hostname):
    for rx, tag in _rules():
        if rx.search(hostname or ""):
            return tag
    return ""    # untagged by default


# ---------- tag registry (ordered, hierarchical, colored) ----------
def ancestors(path):
    parts = path.split("/")
    return ["/".join(parts[:i]) for i in range(1, len(parts))]


def _hier_sort(tags, base):
    """Order tags so each parent precedes its children, grouped by branch."""
    idx = {t: i for i, t in enumerate(base)}
    def key(path):
        parts = path.split("/")
        return [idx.get("/".join(parts[:i + 1]), 1e9) for i in range(len(parts))]
    return sorted(tags, key=key)


def ensure_tags(book):
    """Make sure book['tags'] (registry) and book['tag_colors'] cover every tag
    used by peers, plus all ancestor paths. Returns True if anything changed."""
    used = set()
    for p in book["peers"]:
        for t in p.get("tags", []):
            used.add(t)
            used.update(ancestors(t))
    existing = list(book.get("tags", []))
    all_tags = set(existing) | used
    for t in list(all_tags):                   # parents always get a header/section
        all_tags.update(ancestors(t))
    # base order: keep existing registry order, then new tags (known order, then sorted)
    new = [t for t in all_tags if t not in existing]
    new.sort(key=lambda t: (TAG_ORDER.index(t) if t in TAG_ORDER else len(TAG_ORDER), t))
    base = existing + new
    ordered = _hier_sort(all_tags, base)
    colors = dict(book.get("tag_colors", {}))
    for i, t in enumerate(ordered):
        if t not in colors:
            leaf = t.split("/")[-1]
            colors[t] = TAG_COLORS.get(t) or TAG_COLORS.get(leaf) or PALETTE[i % len(PALETTE)]
    changed = ordered != existing or colors != book.get("tag_colors")
    book["tags"] = ordered
    book["tag_colors"] = colors
    return changed


# ---------- persistence ----------
def load():
    if not BOOK.exists():
        seed()
    book = json.loads(BOOK.read_text())
    if ensure_tags(book):          # migrate older book.json / keep registry in sync
        BOOK.write_text(json.dumps(book, indent=2))
    return book


def save(book):
    book["peers"].sort(key=lambda p: (p.get("tags") or [""])[0:1] + [p["alias"].lower()])
    BOOK.write_text(json.dumps(book, indent=2))
    export(book)
    return book


# ---------- derived helpers ----------
def connect_link(peer, book):
    pid, server = peer["id"], peer.get("server") or book["default_server"]
    if server == book["default_server"]:
        return "rustdesk://" + pid              # default server needs no key
    key = book["servers"].get(server, "")
    if not key:
        return "rustdesk://" + pid
    return f"rustdesk://{pid}@{server}?key={urllib.parse.quote(key, safe='')}"


def connect_string(peer, book):
    """Full id@server?key= form used for the RustDesk export 'id' field."""
    pid, server = peer["id"], peer.get("server") or book["default_server"]
    key = book["servers"].get(server, "")
    return f"{pid}@{server}?key={key}" if key else f"{pid}@{server}"


def status_map(book):
    m = {}
    for p in book["peers"]:
        m.setdefault(p.get("server") or book["default_server"], []).append(p["id"])
    return m


def access_times():
    """{pid: last-connection epoch} from peer-store file mtimes (RustDesk bumps
    a peer's .toml each time you connect)."""
    t = {}
    for f in PEERS_DIR.glob("*.toml"):
        pid = f.stem.split("@")[0]
        try:
            m = f.stat().st_mtime
        except OSError:
            continue
        if m > t.get(pid, 0):
            t[pid] = m
    return t


def view(book):
    """Shape sent to the browser (keys not exposed; links precomputed)."""
    atimes = access_times()
    return {
        "default_server": book["default_server"],
        "servers": sorted(book["servers"].keys()),
        "tags": book.get("tags", []),
        "tag_colors": book.get("tag_colors", {}),
        "peers": [
            {**{k: p.get(k, "") for k in
                ("id", "alias", "hostname", "username", "platform", "server")},
             "tags": p.get("tags", []),
             "last": atimes.get(p["id"], 0),
             "link": connect_link(p, book)}
            for p in book["peers"]
        ],
    }


# ---------- tag operations ----------
def add_tag(book, name, color=None):
    name = (name or "").strip().strip("/")
    if not name:
        raise ValueError("tag name required")
    if name not in book.get("tags", []):
        book.setdefault("tags", []).append(name)
        if color:
            book.setdefault("tag_colors", {})[name] = color
    ensure_tags(book)
    return save(book)


def rename_tag(book, old, new):
    old, new = (old or "").strip().strip("/"), (new or "").strip().strip("/")
    if not old or not new:
        raise ValueError("old and new names required")

    def remap(t):  # rename old itself and any descendant prefix
        if t == old:
            return new
        if t.startswith(old + "/"):
            return new + t[len(old):]
        return t

    book["tags"] = list(dict.fromkeys(remap(t) for t in book.get("tags", [])))
    book["tag_colors"] = {remap(k): v for k, v in book.get("tag_colors", {}).items()}
    for p in book["peers"]:
        p["tags"] = list(dict.fromkeys(remap(t) for t in p.get("tags", [])))
    ensure_tags(book)
    return save(book)


def delete_tag(book, name):
    name = (name or "").strip().strip("/")

    def gone(t):
        return t == name or t.startswith(name + "/")

    book["tags"] = [t for t in book.get("tags", []) if not gone(t)]
    book["tag_colors"] = {k: v for k, v in book.get("tag_colors", {}).items() if not gone(k)}
    for p in book["peers"]:
        p["tags"] = [t for t in p.get("tags", []) if not gone(t)]
    ensure_tags(book)
    return save(book)


def set_tag_color(book, name, color):
    book.setdefault("tag_colors", {})[name] = color
    return save(book)


def set_membership(book, pid, add=(), remove=()):
    peer = next((p for p in book["peers"] if p["id"] == pid), None)
    if peer is None:
        raise ValueError(f"no peer {pid}")
    tags = list(peer.get("tags", []))
    for t in remove:
        if t in tags:
            tags.remove(t)
    for t in add:
        t = (t or "").strip().strip("/")
        if t and t not in tags:
            tags.append(t)
    peer["tags"] = tags
    ensure_tags(book)
    return save(book)


# ---------- seeding from the RustDesk peer store ----------
def _field(text, key):
    m = re.search(rf"^{re.escape(key)}\s*=\s*'([^']*)'", text, re.M)
    return m.group(1) if m else ""


def scan_peers():
    """Read the RustDesk peer store -> deduped candidate peers.

    Returns (peers, server_keys) where server_keys maps each rendezvous server
    to its key, discovered from each peer's `other-server-key` field plus the
    client's configured default server (RustDesk2.toml)."""
    def_server, def_key = rustdesk_default()
    server_keys = {def_server: def_key} if def_server else {}
    peers, by_pid = [], {}
    for f in sorted(PEERS_DIR.glob("*.toml")):
        pid, sep, server = f.stem.partition("@")
        text = f.read_text(errors="replace")
        host = _field(text, "hostname")
        server = server or def_server
        key = _field(text, "other-server-key")
        if server and key:
            server_keys.setdefault(server, key)
        elif server:
            server_keys.setdefault(server, "")
        t = classify(host)
        entry = {
            "id": pid,
            "alias": _field(text, "alias") or host or pid,
            "hostname": host,
            "username": _field(text, "username"),
            "platform": _field(text, "platform"),
            "server": server,
            "tags": [t] if t else [],
            "_explicit": bool(sep),
        }
        if pid in by_pid:                      # dedup bare vs @server duplicates
            cur = peers[by_pid[pid]]
            if (entry["_explicit"] and not cur.get("_explicit")) or \
               (host and not cur["hostname"]):
                peers[by_pid[pid]] = entry
        else:
            by_pid[pid] = len(peers)
            peers.append(entry)
    for p in peers:
        p.pop("_explicit", None)
    return peers, server_keys


def seed(force=False):
    if BOOK.exists() and not force:
        return json.loads(BOOK.read_text())
    peers, server_keys = scan_peers()
    def_server = rustdesk_default()[0] or (peers[0]["server"] if peers else "")
    book = {"default_server": def_server, "servers": server_keys, "peers": peers}
    ensure_tags(book)
    return save(book)


def merge(book):
    """Add machines from the peer store that aren't in the book yet.

    Existing entries (with the user's edits) are left untouched. Returns the
    list of newly-added peers.
    """
    have = {p["id"] for p in book["peers"]}
    peers, server_keys = scan_peers()
    added = []
    for cand in peers:
        if cand["id"] in have:
            continue
        book["servers"].setdefault(cand["server"], server_keys.get(cand["server"], ""))
        book["peers"].append(cand)
        added.append(cand)
    if added:
        ensure_tags(book)
        save(book)
    return added


# ---------- export to RustDesk address-book format ----------
def export(book):
    tags = []
    for p in book["peers"]:
        for t in p.get("tags", []):
            if t not in tags:
                tags.append(t)
    ab = {
        "tags": tags,
        "peers": [{
            "id": connect_string(p, book), "hash": "",
            "username": p.get("username", ""), "hostname": p.get("hostname", ""),
            "platform": p.get("platform", ""), "alias": p.get("alias", ""),
            "tags": p.get("tags", []),
        } for p in book["peers"]],
    }
    (HERE / "rustdesk_address_book.json").write_text(json.dumps(ab, indent=2))
    with open(HERE / "rustdesk_address_book.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "username", "hostname", "alias", "platform", "tags", "server"])
        for p in book["peers"]:
            w.writerow([p["id"], p.get("username", ""), p.get("hostname", ""),
                        p.get("alias", ""), p.get("platform", ""),
                        ";".join(p.get("tags", [])), p.get("server", "")])


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "seed"
    if cmd == "seed":
        b = seed(force="--force" in sys.argv)
        print(f"book.json: {len(b['peers'])} peers, servers: {', '.join(b['servers'])}")
    elif cmd == "resync":
        added = merge(load())
        print(f"added {len(added)} new: {', '.join(p['alias'] for p in added) or '(none)'}")
    elif cmd == "export":
        export(load())
        print("exported rustdesk_address_book.json + .csv")
    else:
        print(__doc__)

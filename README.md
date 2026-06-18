# RustDesk Address Book

A self-hosted, single-file web app that turns your RustDesk **recent
connections** into a browsable address book — with **live online status**, tags,
search, and one-click connect — including when your machines are spread across
**multiple rendezvous servers**.

Works without RustDesk Pro or an account. Pure Python standard library + one HTML
file.

## Why

[RustDesk](https://rustdesk.com) is an excellent open-source remote-desktop tool.
Its hosted address book is a Pro feature, so on the free, self-hosted path the
usual way back into a machine is the *recent connections* list — which is hard to
browse, label, or scan for who's online once you're past a handful of machines.

This tool fills that gap for two common situations:

- **Free / self-hosted, no Pro or API server** — it builds a browsable, taggable
  address book from the peer list RustDesk already keeps on disk.
- **Machines spread across multiple rendezvous servers** (connected with the
  `id@server?key=...` form) — it queries every server directly, so live online
  status for all of them shows up in one place.

It reads the local RustDesk peer store, organizes it, and never modifies RustDesk
itself.

## Features

- **Click-to-connect** via the `rustdesk://` scheme (or the `id@server?key=` form for non-default servers)
- **Live online status** — green dots refreshed every few seconds, across *all* your servers; online/offline notifications (online alerts immediately, offline only after a grace period so brief blips stay quiet)
- **Tags as labels** — a machine can have several; **hierarchical** via `Parent/Child` names
- **Drag & drop** — drag a card onto a tag to move it; hold ⌘/Ctrl/Alt to add it (multi-tag)
- **Collapsible** sections, **search**, and **sort** by display name / hostname / recently accessed
- **Add / edit / delete** machines and tags in the browser
- **Re-sync** — pull in machines you've connected to since, without touching your edits
- **Auto-start on login** (macOS LaunchAgent installer included)
- **Exports** a RustDesk-format `address_book.json` + `.csv` for portability

## Requirements

- **Python 3.8+** (standard library only — no `pip install`)
- The **RustDesk desktop client** installed, with some recent connections
- macOS, Linux, or Windows (config auto-located per-OS)

## Quick start

```bash
git clone https://github.com/Cybis320/rustdesk-addressbook.git
cd rustdesk-addressbook
python3 serve.py
```

It opens `http://127.0.0.1:8765`, seeding `book.json` from your RustDesk peer
store on first run. That's it.

### Auto-start on login (macOS)

```bash
./install-login-agent.sh        # server runs at login, always on 127.0.0.1:8765
./install-login-agent.sh uninstall
```

On Linux, create a `systemd --user` service running `python3 serve.py 8765 --no-open`.

## How it works

| File | Role |
|------|------|
| `serve.py` | Local HTTP server: serves the app + a REST API (`/api/book`, `/api/peers`, `/api/tags`, `/api/resync`, `/status`) |
| `app.html` | The entire UI (vanilla JS, no build step, no dependencies) |
| `book.py`  | Data layer: seeds/merges from the RustDesk peer store, computes connect links, exports |
| `status.py`| Speaks RustDesk's native rendezvous protocol to read online status |
| `book.json`| Your editable data store (gitignored) |

### Online status — the interesting part

RustDesk clients learn online status by asking the **rendezvous server**, not the
machines. This tool does the same:

- Connects over **TCP to `rendezvous_port - 1`** (the NAT-test port, e.g. `21115`)
- Sends a framed `OnlineRequest{ id, peers[] }` protobuf
- Reads back `OnlineResponse{ states }` — a bitmap, **MSB-first** (`states[i//8] & (0x80 >> i%8)`)

Each server in your address book is queried in parallel and the results merged,
so machines across several servers all show correct status.

### Configuration & secrets

Nothing is hardcoded. On first run the tool reads your RustDesk config:

- **Default server + key** from `RustDesk2.toml`
- **Per-server keys** from each peer's `other-server-key` field

Your machine list, hostnames, and **server keys live only in `book.json`** (and the
exports), which are **gitignored** — they never enter the repo.

### Tag auto-classification (optional)

Drop a `tag_rules.json` next to the scripts to auto-tag new machines by hostname:

```json
[["^web", "Servers"], ["(raspberrypi|^pi\\d)", "Raspberry Pi"]]
```

First match wins; no match ⇒ machine starts **Untagged**. See `tag_rules.example.json`.
This file is gitignored (it can encode your naming conventions).

## CLI

```bash
python3 book.py seed [--force]   # build book.json from the peer store
python3 book.py resync           # add newly-connected machines
python3 book.py export           # write address_book.json + .csv
python3 status.py <host> <id>... # query online status directly
```

## Security notes

- The server binds to **`127.0.0.1` only** — not exposed to your network.
- It can launch RustDesk connections; treat the machine running it as trusted.
- Don't commit `book.json` / exports / `tag_rules.json` (already gitignored).

## Acknowledgements

Built on top of the open-source [RustDesk](https://github.com/rustdesk/rustdesk)
project and its rendezvous protocol — thanks to the RustDesk authors and
community. This is an independent companion tool, not affiliated with or endorsed
by RustDesk.

## License

[MIT](LICENSE)

#!/usr/bin/env python3
"""Query a RustDesk rendezvous (hbbs) server for peer online status.

Speaks the native RustDesk rendezvous protocol over TCP/21116:
  send RendezvousMessage{ online_request{ id, peers[] } }
  recv RendezvousMessage{ online_response{ states: bitmap } }
The states bitmap is LSB-first: peer i is online iff states[i//8] & (1 << (i%8)).
"""
import socket
import struct

MY_ID = "000000000"  # requester id; hbbs only uses it for bookkeeping


# ---- minimal protobuf encoding ----
def _varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _len_field(field, payload):  # wire type 2 (length-delimited)
    return _varint((field << 3) | 2) + _varint(len(payload)) + payload


def _str_field(field, s):
    return _len_field(field, s.encode())


def build_online_request(peer_ids):
    body = _str_field(1, MY_ID)
    for pid in peer_ids:
        body += _str_field(2, pid)            # repeated string peers = 2
    return _len_field(23, body)               # online_request = 23


# ---- RustDesk BytesCodec length framing ----
def frame(data):
    n = len(data)
    if n <= 0x3F:
        head = struct.pack("<B", n << 2)
    elif n <= 0x3FFF:
        head = struct.pack("<H", (n << 2) | 0x1)
    elif n <= 0x3FFFFF:
        h = (n << 2) | 0x2
        head = struct.pack("<H", h & 0xFFFF) + struct.pack("<B", h >> 16)
    else:
        head = struct.pack("<I", (n << 2) | 0x3)
    return head + data


def _recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("socket closed")
        buf += chunk
    return buf


def read_frame(sock):
    b0 = _recv_exact(sock, 1)[0]
    head_len = (b0 & 0x3) + 1
    extra = _recv_exact(sock, head_len - 1) if head_len > 1 else b""
    raw = bytes([b0]) + extra + b"\x00" * (4 - head_len)
    n = struct.unpack("<I", raw)[0] >> 2
    return _recv_exact(sock, n)


# ---- minimal protobuf field walker ----
def _fields(buf):
    i = 0
    while i < len(buf):
        key = 0
        shift = 0
        while True:
            b = buf[i]; i += 1
            key |= (b & 0x7F) << shift
            if not (b & 0x80):
                break
            shift += 7
        field, wire = key >> 3, key & 0x7
        if wire == 2:
            ln = 0; shift = 0
            while True:
                b = buf[i]; i += 1
                ln |= (b & 0x7F) << shift
                if not (b & 0x80):
                    break
                shift += 7
            yield field, buf[i:i + ln]; i += ln
        elif wire == 0:
            while buf[i] & 0x80:
                i += 1
            i += 1
        else:
            raise ValueError(f"unsupported wire type {wire}")


def parse_online_response(msg):
    for field, val in _fields(msg):
        if field == 24:  # online_response
            for f2, v2 in _fields(val):
                if f2 == 1:  # states bytes
                    return v2
    return None


def query(host, port, peer_ids, timeout=6.0):
    """Return {peer_id: bool} on success, or None if the server couldn't be
    reached / gave no valid response. None means *unknown* (e.g. our own machine
    lost network) — callers must NOT treat that as 'everything offline'."""
    states = None
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall(frame(build_online_request(peer_ids)))
            for _ in range(8):  # skip any unrelated frames the server sends first
                states = parse_online_response(read_frame(s))
                if states is not None:
                    break
    except (OSError, ValueError, ConnectionError):
        return None
    if states is None:
        return None
    # hbbs packs bits left-to-right: peer i -> states[i//8] bit (7 - i%8)
    return {pid: bool(i >> 3 < len(states) and states[i >> 3] & (0x80 >> (i & 7)))
            for i, pid in enumerate(peer_ids)}


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("usage: python3 status.py <rendezvous-host> <id> [id ...]")
        sys.exit(1)
    host, ids = sys.argv[1], sys.argv[2:]
    res = query(host, 21115, ids)  # online queries go to nat_port = rendezvous - 1
    if res is None:
        print(f"{host}: unreachable (status unknown)")
        sys.exit(2)
    online = sum(res.values())
    print(f"{host}: {online}/{len(ids)} online")
    for pid, up in res.items():
        print(f"  {'ONLINE ' if up else 'offline'}  {pid}")

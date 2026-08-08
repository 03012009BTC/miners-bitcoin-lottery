# MINERS — Bitcoin Lottery: USB ASIC sticks (Blue Fury BF1), your CPU and even
# your phone's browser, playing the solo mining "lottery" on public-pool.io.
#
# Stratum V1: TCP + JSON lines. subscribe -> authorize -> notify (jobs) / set_difficulty.
# From each pool job we build coinbase + merkle root + the 80-byte header; every stick
# gets its own job (midstate + header tail), CPU workers scan nonce stripes of a shared
# job, browser players fetch stripes over HTTP. Found nonces are verified locally and
# submitted to the pool.
#
# Sticks hot-plug at RUNTIME. CPU + browsers use their OWN pool connection (worker
# ".cpu") so the pool can give them a much lower share difficulty than the sticks get.
#
# Configuration lives in config.json (created with defaults on first run).
#
# Run:  python miner.py   (or START_MINING.bat)
# Stop: Ctrl+C
import json
import multiprocessing
import os
import queue
import socket
import struct
import threading
import time
from collections import deque
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import serial
from serial.tools import list_ports

from bitfury import (
    BF1_VIDPID, OFFSETS, _M, bitfury_decnonce, sha256_midstate,
)

# ---------------------------- configuration ----------------------------
_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(_DIR, "config.json")
STATE_FILE = os.path.join(_DIR, "lottery_best.json")

DEFAULTS = {
    "btc_address": "",                # YOUR bitcoin address (bc1...) — the miner refuses to start without it
    "worker_name": "sticks",          # worker suffix for the USB sticks
    "pool_host": "public-pool.io",
    "pool_port": 21496,
    "dashboard_port": 8888,
    "suggest_difficulty": 256,        # for the sticks (~2.6 GH/s each)
    "cpu_mining": True,               # no stick? no problem — your CPU plays too
    "cpu_workers": 0,                 # 0 = automatic (CPU cores - 1, at least 1)
    "cpu_suggest_difficulty": 1,      # CPUs are slow; ask the pool for the easiest shares
    "browser_mining": True,           # let phones/tablets/PCs join by pressing PLAY in the dashboard
}


def _bech32_ok(address: str) -> bool:
    """BIP-173 checksum check for bc1... addresses (typo protection)."""
    charset = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
    gen = [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]
    try:
        hrp, data = address.lower().rsplit("1", 1)
        vals = [charset.index(c) for c in data]
    except ValueError:
        return False
    chk = 1
    for v in [ord(x) >> 5 for x in hrp] + [0] + [ord(x) & 31 for x in hrp] + vals:
        b = chk >> 25
        chk = (chk & 0x1FFFFFF) << 5 ^ v
        for i in range(5):
            chk ^= gen[i] if ((b >> i) & 1) else 0
    return chk == 1


def load_config() -> dict:
    """Load config.json; create it with defaults on first run."""
    cfg = dict(DEFAULTS)
    try:
        # utf-8-sig: Windows Notepad saves JSON with a BOM — accept it silently
        with open(CONFIG_FILE, encoding="utf-8-sig") as f:
            cfg.update(json.load(f))
    except FileNotFoundError:
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(DEFAULTS, f, indent=2)
        except OSError:
            pass
    except (OSError, json.JSONDecodeError) as e:
        print(f"[config] cannot read config.json ({e}) — using defaults")
    address = cfg["btc_address"]
    if not address:
        raise SystemExit(
            "\nWelcome to MINERS — Bitcoin Lottery!\n"
            "One thing before you play: open config.json (right-click -> Notepad),\n"
            "put YOUR OWN bitcoin address into \"btc_address\" and start again.\n"
            "The winnings (if lightning ever strikes) go to that address.\n")
    if address.lower().startswith("bc1") and not _bech32_ok(address):
        raise SystemExit(f"config.json: btc_address '{address}' has a BAD checksum (typo?) — refusing to mine")
    return cfg


CFG = load_config()
POOL_HOST = CFG["pool_host"]
POOL_PORT = int(CFG["pool_port"])
BTC_ADDRESS = CFG["btc_address"]
WORKER = f"{BTC_ADDRESS}.{CFG['worker_name']}"
WORKER_CPU = f"{BTC_ADDRESS}.cpu"
DASHBOARD_PORT = int(CFG["dashboard_port"])
DIFF1_TARGET = 0xFFFF * 2**208


def _lan_ip() -> str:
    """Best-effort local LAN address (for the 'open this on your phone' hint)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))          # no packet is sent; just picks the route
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return ""


# the miner thread writes STATS, the dashboard threads read it — serialising a
# dict while another thread edits it can raise or hand out a half-built list
STATS_LOCK = threading.Lock()
STATS = {
    "start": time.time(),
    "lan": _lan_ip(), "port": DASHBOARD_PORT,
    "pool": {"host": POOL_HOST, "connected": False, "difficulty": 0},
    "address": BTC_ADDRESS,
    "accepted": 0, "rejected": 0,
    "best_alltime": 0.0, "best_session": 0.0,
    "devices": [],   # [{name, port, hs, shares}]  (hs = hashes per second)
    "draws": [],     # recently submitted "tickets": {t, nonce, diff, who}
}

# ---------------------------- browser players ----------------------------
# Any device that opens the dashboard and presses PLAY becomes a hashing player:
# it asks for a nonce stripe (/work), hashes it in JavaScript and reports
# candidates back (/submit). No install, works on phones too.
WEB_LOCK = threading.Lock()
WEB = {
    "job": None,        # (uid, header76 bytes, en2, ntime, job_id, target)
    "stripe": 0,        # next nonce stripe to hand out
    "players": {},      # client id -> {name, hashes, shares, last, start}
    "submits": queue.Queue(),   # (uid, nonce, client) candidates waiting for the pool
}
WEB_STRIPE = 1 << 22    # 4M nonces per request (~10-20 s of browser work)


def web_live_players() -> list[dict]:
    """Browser players seen in the last 30 s, as dashboard device entries."""
    now = time.time()
    out = []
    with WEB_LOCK:
        for cid, p in list(WEB["players"].items()):
            if now - p["last"] > 120:
                del WEB["players"][cid]
                continue
            if now - p["last"] <= 30:
                out.append({"name": p["name"], "port": "browser",
                            "hs": round(p["hashes"] / max(1.0, now - p["start"]), 1),
                            "shares": p["shares"]})
    return out


def load_best() -> None:
    """Load the persistent all-time best "ticket" difficulty."""
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            STATS["best_alltime"] = float(json.load(f).get("best", 0))
    except (OSError, ValueError):
        pass


def save_best() -> None:
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"best": STATS["best_alltime"]}, f)
    except OSError:
        pass


# app icon + manifest so "Add to Home Screen" on a phone looks the part
ICON_SVG = ("""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
<rect width="512" height="512" fill="#070a07"/>
<circle cx="256" cy="256" r="152" fill="#f7931a"/>
<circle cx="256" cy="256" r="152" fill="none" stroke="#ffc832" stroke-width="12" opacity="0.6"/>
<text x="256" y="334" font-family="Consolas,monospace" font-size="216" font-weight="bold"
 fill="#ffffff" text-anchor="middle">&#8383;</text>
</svg>""").encode()

MANIFEST = json.dumps({
    "name": "MINERS — Bitcoin Lottery",
    "short_name": "MINERS",
    "display": "standalone",
    "background_color": "#070a07",
    "theme_color": "#070a07",
    "start_url": "/",
    "icons": [{"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml",
               "purpose": "any maskable"}],
}).encode()


class _DashHandler(BaseHTTPRequestHandler):
    def _send(self, body: bytes, ctype: str, code: int = 200) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):  # noqa: N802 — CORS preflight for /submit
        self._send(b"", "text/plain")

    def do_POST(self):  # noqa: N802 — browser players report hashes and candidates
        if self.path.split("?")[0] != "/submit":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            msg = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._send(b'{"ok":false}', "application/json", 400)
            return
        cid = str(msg.get("client", ""))[:64]
        with WEB_LOCK:
            player = WEB["players"].get(cid)
            if player is not None:
                player["hashes"] += int(msg.get("hashes", 0) or 0)
                player["last"] = time.time()
        if msg.get("nonce") is not None:
            try:
                WEB["submits"].put_nowait((int(msg["uid"]), int(msg["nonce"]) & _M, cid))
            except queue.Full:
                pass
        self._send(b'{"ok":true}', "application/json")

    def do_GET(self):  # noqa: N802 (name required by http.server)
        path = self.path.split("?")[0]
        if path == "/work":
            self._send(json.dumps(_web_work(self.path)).encode(), "application/json")
            return
        if path == "/stats.json":
            with STATS_LOCK:
                body = json.dumps(STATS).encode()
            ctype = "application/json"
        elif path == "/icon.svg":
            body, ctype = ICON_SVG, "image/svg+xml"
        elif path == "/manifest.json":
            body, ctype = MANIFEST, "application/manifest+json"
        elif path in ("/", "/index.html", "/dashboard.html"):
            try:
                with open(os.path.join(_DIR, "dashboard.html"), "rb") as f:
                    body, ctype = f.read(), "text/html; charset=utf-8"
            except OSError:
                self.send_error(404, "dashboard.html missing next to the miner")
                return
        else:
            self.send_error(404)
            return
        self._send(body, ctype)   # CORS header lets the double-clicked file:// page read it too

    def log_message(self, *args):  # silent — keep the miner output clean
        pass


def _web_work(path: str) -> dict:
    """Hand out one nonce stripe of the current low-difficulty job to a browser player."""
    from urllib.parse import parse_qs, urlparse
    q = parse_qs(urlparse(path).query)
    cid = (q.get("client", [""])[0])[:64]
    name = (q.get("name", ["Browser"])[0])[:40] or "Browser"
    with WEB_LOCK:
        job = WEB["job"]
        if job is None:
            return {"wait": True}
        uid, header, _en2, _ntime, _job_id, target = job
        start = WEB["stripe"]
        WEB["stripe"] = (start + WEB_STRIPE) & _M
        if cid:
            player = WEB["players"].get(cid)
            if player is None:
                WEB["players"][cid] = {"name": f"Browser ({name})", "hashes": 0,
                                       "shares": 0, "last": time.time(), "start": time.time()}
                print(f"[+] Browser ({name}) joined the game")
            else:
                player["last"] = time.time()
                player["name"] = f"Browser ({name})"
    return {"uid": uid, "header": header.hex(), "target": f"{target:064x}",
            "start": start, "count": WEB_STRIPE}


def start_dashboard_server() -> None:
    try:
        srv = ThreadingHTTPServer(("0.0.0.0", DASHBOARD_PORT), _DashHandler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        print(f"[dashboard] running at http://localhost:{DASHBOARD_PORT} (from your phone: http://PC-IP:{DASHBOARD_PORT})")
    except OSError as e:
        print(f"[dashboard] server failed to start ({e}) — mining continues without it")


def dsha(b: bytes) -> bytes:
    return sha256(sha256(b).digest()).digest()


class Stratum:
    """Minimal Stratum V1 client (TCP + JSON lines)."""

    def __init__(self, host: str, port: int) -> None:
        self.sock = socket.create_connection((host, port), timeout=30)
        self.sock.settimeout(0.05)
        self.buf = b""
        self.msg_id = 0
        self.extranonce1 = ""
        self.extranonce2_size = 4
        self.difficulty = 1.0
        self.job = None          # latest job from the pool
        self.clean = False       # clean_jobs — drop work in progress
        self.pending: dict[int, str] = {}  # request id -> method name

    def send(self, method: str, params: list) -> int:
        self.msg_id += 1
        line = json.dumps({"id": self.msg_id, "method": method, "params": params}) + "\n"
        self.sock.sendall(line.encode())
        self.pending[self.msg_id] = method
        return self.msg_id

    def read(self) -> list[dict]:
        """Return every complete JSON message that has arrived (short poll, no long block)."""
        try:
            data = self.sock.recv(4096)
            if not data:
                raise ConnectionError("pool closed the connection")
            self.buf += data
        except socket.timeout:
            pass
        messages = []
        while b"\n" in self.buf:
            line, self.buf = self.buf.split(b"\n", 1)
            if line.strip():
                try:
                    messages.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return messages

    def handle(self, msg: dict, name: str = "pool") -> None:
        method = msg.get("method")
        if method == "mining.notify":
            p = msg["params"]
            self.job = {
                "job_id": p[0], "prevhash": p[1], "coinb1": p[2], "coinb2": p[3],
                "merkle_branches": p[4], "version": p[5], "nbits": p[6], "ntime": p[7],
            }
            self.clean = bool(p[8])
            if self.clean:
                print(f"[{name}] new job {p[0]} (new block — clean start)")
        elif method == "mining.set_difficulty":
            self.difficulty = float(msg["params"][0])
            print(f"[{name}] share difficulty: {self.difficulty}")


def connect_pool(worker: str, diff: float, name: str) -> Stratum:
    """Connect + subscribe + authorize one Stratum session; wait for the first job."""
    st = Stratum(POOL_HOST, POOL_PORT)
    st.send("mining.subscribe", ["miners-bitcoin-lottery/1.0"])
    st.send("mining.authorize", [worker, "x"])
    st.send("mining.suggest_difficulty", [diff])
    start = time.time()
    while (not st.extranonce1 or st.job is None) and time.time() - start < 30:
        for m in st.read():
            if m.get("id") in st.pending:
                method = st.pending.pop(m["id"])
                if method == "mining.subscribe" and m.get("result"):
                    st.extranonce1 = m["result"][1]
                    st.extranonce2_size = m["result"][2]
                    print(f"[{name}] subscribed (extranonce1={st.extranonce1})")
                elif method == "mining.authorize":
                    print(f"[{name}] authorization: {'OK' if m.get('result') else m.get('error')}")
            else:
                st.handle(m, name)
        time.sleep(0.02)
    if st.job is None:
        raise ConnectionError(f"{name} sent no job within 30 s")
    return st


def build_work(st: Stratum, extranonce2: int) -> tuple[bytes, str, str]:
    """Build the 80-byte header (sans nonce) from the pool job. Returns (header76, en2hex, ntime)."""
    j = st.job
    en2 = extranonce2.to_bytes(st.extranonce2_size, "big").hex()
    coinbase = bytes.fromhex(j["coinb1"] + st.extranonce1 + en2 + j["coinb2"])
    merkle = dsha(coinbase)
    for branch in j["merkle_branches"]:
        merkle = dsha(merkle + bytes.fromhex(branch))
    # stratum prevhash quirk: 8 x 4-byte words, each word byte-reversed
    prev = b"".join(
        struct.pack("<I", int(j["prevhash"][i:i + 8], 16)) for i in range(0, 64, 8)
    )
    header76 = (
        struct.pack("<I", int(j["version"], 16))
        + prev
        + merkle
        + struct.pack("<I", int(j["ntime"], 16))
        + struct.pack("<I", int(j["nbits"], 16))
    )
    return header76, en2, j["ntime"]


def bf1_packet(header76: bytes) -> bytes:
    """BF1 work packet: 'W' + 32 B midstate + 12 B header tail (getwork word order)."""
    midstate = struct.pack("<8I", *sha256_midstate(header76[:64]))
    tail = b"".join(header76[i:i + 4][::-1] for i in range(64, 76, 4))
    return b"W" + midstate + tail


# cgminer's BF1WAIT: the chip needs ~1.6 s for a full nonce-range scan; feeding
# it faster truncates scans (measured live: at a 0.8 s pace most jobs found nothing)
BF1_JOB_SECONDS = 1.6


def find_bf1_ports() -> list[str]:
    return [p.device for p in list_ports.comports() if (p.vid, p.pid) == BF1_VIDPID]


def rolling_hs(hits: deque, start: float) -> float:
    """Effective hashrate (H/s) from unique diff-1 hits over the last 15 minutes."""
    now = time.time()
    while hits and now - hits[0] > 900:
        hits.popleft()
    return len(hits) * 2**32 / max(1.0, min(now - start, 900.0))


class Stick:
    """One connected Blue Fury: serial port, jobs in flight, counters."""

    def __init__(self, port: str) -> None:
        self.port = port
        self.name = f"Blue Fury ({port})"
        self.s = serial.Serial(port, 115200, timeout=0.05, write_timeout=2)
        self.s.reset_input_buffer()
        self.s.write(b"R")
        time.sleep(0.5)
        self.s.read(7)
        self.in_flight: list[tuple[bytes, str, str, str]] = []  # last 3 jobs (chip reports old work too)
        self.pending = b""
        self.send_next = True
        self.last_job_t = 0.0
        self.last_frame_t = time.time()
        self.reset_sent = False
        self.hits = deque()      # timestamps of unique diff-1 hits (rolling window)
        self.shares = 0
        self.start = time.time()

    def hs(self) -> float:
        return rolling_hs(self.hits, self.start)

    def close(self) -> None:
        try:
            self.s.close()
        except (OSError, serial.SerialException):
            pass


# ---------------------------- Butterfly Labs Jalapeno (BFLSC) ----------------------------
# Text protocol per cgminer driver-bflsc.c/h: ZGX identity, ZNX -> "OK" -> 46-byte
# job [45|midstate 32|header tail 12|0xAA] -> "OK:QUEUED", ZOX result poll, ZQX queue
# flush, ZLX temperature. The device queues up to 20 jobs and scans the FULL nonce
# range of each, reporting every diff-1 nonce it finds.
BFL_VIDPID = (0x0403, 0x6014)      # FTDI bridge on BitForce SC devices
BFL_QUEUE_TARGET = 5               # jobs we keep queued on the device (max 20; deeper = staler shares)
BFL_TEMP_PAUSE = 80                # deg C: stop feeding above this, resume 5 below


def _bswap32(n: int) -> int:
    return struct.unpack("<I", struct.pack(">I", n))[0]


class Jalapeno:
    """One BitForce SC device. A dedicated thread speaks the (slow, line-based)
    serial protocol so the main loop never blocks on it: the loop only feeds
    prepared work into work_q and drains nonce candidates from out_q."""

    def __init__(self, port: str) -> None:
        self.port = port
        self.s = serial.Serial(port, 115200, timeout=1, write_timeout=2)
        # unjam a half-finished binary job upload (e.g. after a hard kill): pad it
        # to completion with zeros, let the device spit its error, then start clean
        self.s.write(bytes(64))
        time.sleep(0.3)
        self.s.reset_input_buffer()
        self.s.write(b"ZGX")
        identity = self.s.readline().decode("ascii", "replace").strip()
        if "SHA256" not in identity.upper():
            self.s.close()
            raise ValueError(f"{port} is not a BitForce SC device ({identity!r})")
        self.s.write(b"ZQX")               # empty any leftover device queue
        self.s.readline()
        self.name = f"Jalapeno ({port})"
        self.work_q: queue.Queue = queue.Queue()   # (en2, header76, ntime, job_id)
        self.out_q: queue.Queue = queue.Queue()    # (en2, header76, ntime, job_id, nonce)
        self.jobs: dict[str, tuple] = {}           # midstate hex -> work item in device queue
        self.queued = 0                            # our estimate of the device queue depth
        self.temp: int | None = None               # hotter of the two board sensors (deg C)
        self.hot = False
        self.hits = deque()
        self.shares = 0
        self.start = time.time()
        self.alive = True
        self.flush = False                         # new block: empty the device queue
        self._shutdown = False
        self._last_result_t = time.time()
        self._last_temp_t = 0.0
        self._last_poll_t = 0.0
        threading.Thread(target=self._run, daemon=True).start()

    def hs(self) -> float:
        return rolling_hs(self.hits, self.start)

    def close(self) -> None:
        self._shutdown = True
        try:
            self.s.close()
        except (OSError, serial.SerialException):
            pass

    # --- everything below runs in the device thread ---
    def _line(self) -> str:
        return self.s.readline().decode("ascii", "replace").strip()

    def _queue_job(self, item: tuple) -> bool:
        _en2, header76, _ntime, _job_id = item
        midstate = struct.pack("<8I", *sha256_midstate(header76[:64]))
        tail = b"".join(header76[i:i + 4][::-1] for i in range(64, 76, 4))
        self.s.write(b"ZNX")
        if "OK" not in self._line():
            return False
        self.s.write(bytes([45]) + midstate + tail + bytes([0xAA]))
        reply = self._line()                       # "OK:QUEUED n" or "ERR:QUEUE FULL"
        if "QUEUED" in reply:
            self.jobs[midstate.hex()] = item
            while len(self.jobs) > 40:             # drop the oldest if results went missing
                del self.jobs[next(iter(self.jobs))]
            self.queued += 1
            return True
        if "FULL" in reply:
            self.queued = 20                       # decays as result lines come back
        return False

    def _poll_results(self) -> None:
        self.s.write(b"ZOX")
        lines = []
        while True:
            line = self._line()
            if not line or line == "OK":
                break
            lines.append(line)
        for line in lines:
            # NOTE: "INPROCESS:n" is how many jobs are being HASHED right now (1),
            # NOT the queue depth — we track the depth ourselves (measured live;
            # trusting INPROCESS made us hammer a full queue with ZNX retries)
            if line.startswith(("INPROCESS", "COUNT")):
                continue
            fields = [f.strip() for f in line.split(",")]
            if len(fields) < 3 or len(fields[0]) != 64:
                continue                           # not a result line
            self._last_result_t = time.time()
            item = self.jobs.pop(fields[0].lower(), None)
            self.queued = max(0, self.queued - 1)
            if item is None:
                continue                           # job was flushed / unknown
            en2, header76, ntime, job_id = item
            for f in fields[2:]:
                if len(f) == 8:
                    try:
                        raw = int(f, 16)
                    except ValueError:
                        continue
                    # the nonce hex is in raw header byte order -> swap for the RPC value
                    self.out_q.put((en2, header76, ntime, job_id, _bswap32(raw)))

    def _read_temp(self) -> None:
        self.s.write(b"ZLX")
        line = self._line()                        # e.g. "Temp1: 43, Temp2: 45"
        vals = []
        for part in line.replace(",", " ").split():
            tail = part.split(":")[-1]
            if tail.isdigit():
                vals.append(int(tail))
        if vals:
            self.temp = max(vals)
            if self.temp >= BFL_TEMP_PAUSE and not self.hot:
                self.hot = True
                print(f"[!] {self.name} is hot ({self.temp} degC) — pausing work until it cools")
            elif self.hot and self.temp <= BFL_TEMP_PAUSE - 5:
                self.hot = False
                print(f"[ok] {self.name} cooled down ({self.temp} degC) — resuming")

    def _run(self) -> None:
        try:
            while not self._shutdown:
                if self.flush:
                    self.flush = False
                    try:                           # drop work prepared for the old block
                        while True:
                            self.work_q.get_nowait()
                    except queue.Empty:
                        pass
                    self.s.write(b"ZQX")
                    self._line()
                    self.jobs.clear()
                    self.queued = 0
                while not self.hot and self.queued < BFL_QUEUE_TARGET:
                    try:
                        item = self.work_q.get_nowait()
                    except queue.Empty:
                        break
                    if not self._queue_job(item):
                        self.work_q.put(item)      # keep the work for the next free slot
                        break
                # polling too often disturbs the device mid-scan (it loses valid
                # nonces!) — cgminer waits ~954 ms per job (BAJ_WORK_TIME), so do we
                if time.time() - self._last_poll_t >= 1.0:
                    self._last_poll_t = time.time()
                    self._poll_results()
                if time.time() - self._last_temp_t > 30:
                    self._last_temp_t = time.time()
                    self._read_temp()
                if time.time() - self._last_result_t > 60:
                    raise serial.SerialException("no results for 60 s")
                time.sleep(0.1)
        except (OSError, serial.SerialException):
            pass
        self.alive = False


def find_bfl_ports() -> list[str]:
    return [p.device for p in list_ports.comports() if (p.vid, p.pid) == BFL_VIDPID]


# ---------------------------- CPU mining ----------------------------
def cpu_worker(idx: int, n_workers: int, in_q, out_q) -> None:
    """One CPU worker process: scans its stripe of the nonce space for the current job.

    Every hash is a genuine lottery "ticket" — Python is slow, but the math is real."""
    sha = sha256
    pack = struct.Struct("<I").pack
    job = None          # (uid, header76, target)
    nonce = end = 0
    while True:
        try:
            while True:                       # drain the queue, keep only the newest job
                item = in_q.get_nowait()
                if item is None:
                    return
                job = item
                stripe = (1 << 32) // n_workers
                nonce = idx * stripe
                end = nonce + stripe
        except queue.Empty:
            pass
        if job is None:
            time.sleep(0.2)
            continue
        uid, header, target = job
        batch_end = min(nonce + 20000, end)
        count = 0
        while nonce < batch_end:
            d = sha(sha(header + pack(nonce)).digest()).digest()
            if int.from_bytes(d, "little") <= target:
                out_q.put(("share", uid, nonce))
            nonce += 1
            count += 1
        out_q.put(("hashes", count))
        if nonce >= end:
            job = None                        # stripe exhausted — wait for fresh work


class CpuRig:
    """Manages the CPU worker processes and their jobs."""

    def __init__(self, n_workers: int) -> None:
        self.n = n_workers
        self.name = f"CPU ({n_workers} core{'s' if n_workers > 1 else ''})"
        self.in_qs = [multiprocessing.Queue() for _ in range(n_workers)]
        self.out_q = multiprocessing.Queue()
        self.procs = [
            multiprocessing.Process(target=cpu_worker, args=(i, n_workers, self.in_qs[i], self.out_q),
                                    daemon=True)
            for i in range(n_workers)
        ]
        for p in self.procs:
            p.start()
        self.jobs: dict[int, tuple[bytes, str, str, str, int]] = {}  # uid -> (hdr76, en2, ntime, job_id, target)
        self.uid = 0
        self.hashes = 0
        self.shares = 0
        self.start = time.time()

    def new_job(self, header76: bytes, en2: str, ntime: str, job_id: str, target: int) -> None:
        self.uid += 1
        self.jobs[self.uid] = (header76, en2, ntime, job_id, target)
        for uid in list(self.jobs):
            if uid <= self.uid - 4:           # keep only recent jobs (late shares still valid)
                del self.jobs[uid]
        for q in self.in_qs:
            q.put((self.uid, header76, target))

    def hs(self) -> float:
        return self.hashes / max(1.0, time.time() - self.start)

    def close(self) -> None:
        for q in self.in_qs:
            try:
                q.put(None)
            except (OSError, ValueError):
                pass
        for p in self.procs:
            p.terminate()


def run_session(rig: CpuRig | None) -> None:
    """One pool session: connect, subscribe, then serve sticks + CPU + browsers."""
    STATS["pool"]["connected"] = False
    print(f"Connecting to {POOL_HOST}:{POOL_PORT} ...")
    st = connect_pool(WORKER, float(CFG["suggest_difficulty"]), "pool")
    STATS["pool"]["connected"] = True
    st_cpu = None
    if rig is not None or CFG.get("browser_mining"):
        # CPUs and browsers share one low-difficulty connection, separate from the sticks
        st_cpu = connect_pool(WORKER_CPU, float(CFG["cpu_suggest_difficulty"]), "pool/cpu")

    target = int(DIFF1_TARGET / st.difficulty)
    extranonce2 = 0
    en2_cpu = 0
    cpu_diff = cpu_job_id = None
    web_uid = 0
    web_jobs: dict[int, tuple[bytes, str, str, str, int]] = {}
    accepted = rejected = 0
    seen: set[tuple[str, int]] = set()
    sticks: dict[str, Stick] = {}
    jals: dict[str, Jalapeno] = {}
    bfl_bad: set[str] = set()   # FTDI ports that answered ZGX with something else
    last_scan = 0.0
    last_status = time.time()
    last_job = st.job["job_id"]
    warned_empty = False

    def record_ticket(sharediff: float, n: int, who: str) -> None:
        """Common bookkeeping for a submitted share ("ticket")."""
        with STATS_LOCK:
            STATS["best_session"] = max(STATS["best_session"], sharediff)
            new_record = sharediff > STATS["best_alltime"]
            if new_record:
                STATS["best_alltime"] = sharediff
            STATS["draws"].insert(0, {
                "t": int(time.time()), "nonce": f"{n:08X}",
                "diff": round(sharediff, 3), "who": who,
            })
            del STATS["draws"][30:]
        if new_record:
            save_best()
            print(f"[RECORD] best \"ticket\" so far: diff {sharediff:,.0f}")

    print(f"Mining to address {BTC_ADDRESS}. Ctrl+C = quit.")
    try:
        while True:
            # 1) pool messages (both connections)
            for conn, conn_name in ((st, "pool"), (st_cpu, "pool/cpu")):
                if conn is None:
                    continue
                for m in conn.read():
                    if m.get("id") in conn.pending:
                        method = conn.pending.pop(m["id"])
                        if method == "mining.submit":
                            if m.get("result"):
                                accepted += 1
                                print(f"[POOL ACCEPTED share #{accepted}] OK")
                            else:
                                rejected += 1
                                print(f"[pool rejected share: {m.get('error')}]")
                            # STATS gets these under the lock further down the loop
                    else:
                        conn.handle(m, conn_name)
            target = int(DIFF1_TARGET / st.difficulty)

            # new block / new job -> sticks get fresh work right away
            if st.clean or st.job["job_id"] != last_job:
                for stick in sticks.values():
                    stick.send_next = True
                if st.clean:
                    for jal in jals.values():
                        jal.flush = True   # old block's jobs are worthless now
                last_job = st.job["job_id"]
                st.clean = False

            # CPU + browsers: push a fresh job on job change or difficulty change
            if st_cpu is not None:
                if st_cpu.job["job_id"] != cpu_job_id or st_cpu.difficulty != cpu_diff or st_cpu.clean:
                    cpu_job_id = st_cpu.job["job_id"]
                    cpu_diff = st_cpu.difficulty
                    st_cpu.clean = False
                    cpu_target = int(DIFF1_TARGET / cpu_diff)
                    header76, en2, ntime = build_work(st_cpu, en2_cpu)
                    en2_cpu += 1
                    web_uid += 1
                    if rig is not None:
                        rig.new_job(header76, en2, ntime, cpu_job_id, cpu_target)
                    if CFG.get("browser_mining"):
                        with WEB_LOCK:
                            WEB["job"] = (web_uid, header76, en2, ntime, cpu_job_id, cpu_target)
                            WEB["stripe"] = 0
                            web_jobs[web_uid] = (header76, en2, ntime, cpu_job_id, cpu_target)
                            for old in [u for u in web_jobs if u <= web_uid - 4]:
                                del web_jobs[old]

                # candidates reported by browser players
                try:
                    while True:
                        uid, n, cid = WEB["submits"].get_nowait()
                        stored = web_jobs.get(uid)
                        if stored is None:
                            continue                   # job already too old
                        header76, en2, ntime, job_id, web_target = stored
                        h = dsha(header76 + struct.pack("<I", n))
                        h_int = int.from_bytes(h, "little")
                        if h_int > web_target:
                            continue                   # not a real share, ignore quietly
                        with WEB_LOCK:
                            player = WEB["players"].get(cid)
                            player_name = player["name"] if player else "Browser"
                            if player:
                                player["shares"] += 1
                        st_cpu.send("mining.submit",
                                    [WORKER_CPU, job_id, en2, ntime, f"{n:08x}"])
                        print(f"[share] nonce {n:#010x} sent to pool "
                              f"({player_name}, hash ...{h[::-1].hex()[:16]})")
                        record_ticket(DIFF1_TARGET / h_int, n, player_name)
                except queue.Empty:
                    pass

            if st_cpu is not None and rig is not None:
                # CPU worker results
                try:
                    while True:
                        kind, *rest = rig.out_q.get_nowait()
                        if kind == "hashes":
                            rig.hashes += rest[0]
                        elif kind == "share":
                            uid, n = rest
                            stored = rig.jobs.get(uid)
                            if stored is None:
                                continue          # too old, job long gone
                            header76, en2, ntime, job_id, cpu_target = stored
                            h = dsha(header76 + struct.pack("<I", n))
                            h_int = int.from_bytes(h, "little")
                            if h_int > cpu_target:
                                continue          # safety re-check
                            st_cpu.send("mining.submit",
                                        [WORKER_CPU, job_id, en2, ntime, f"{n:08x}"])
                            rig.shares += 1
                            print(f"[share] nonce {n:#010x} sent to pool "
                                  f"({rig.name}, hash ...{h[::-1].hex()[:16]})")
                            record_ticket(DIFF1_TARGET / h_int, n, rig.name)
                except queue.Empty:
                    pass

            # 2) hotplug: every 5 s look for newly plugged devices
            if time.time() - last_scan > 5:
                last_scan = time.time()
                for port in find_bf1_ports():
                    if port not in sticks:
                        try:
                            sticks[port] = Stick(port)
                            print(f"[+] {sticks[port].name} joined the game")
                            warned_empty = False
                        except (OSError, serial.SerialException):
                            pass  # port not ready yet — retry on the next scan
                bfl_present = find_bfl_ports()
                bfl_bad.intersection_update(bfl_present)   # unplugged -> forget the verdict
                for port in bfl_present:
                    if port not in jals and port not in bfl_bad:
                        try:
                            jals[port] = Jalapeno(port)
                            print(f"[+] {jals[port].name} joined the game")
                            warned_empty = False
                        except ValueError as e:
                            print(f"[?] {e} — ignoring this port")
                            bfl_bad.add(port)
                        except (OSError, serial.SerialException):
                            pass
                if not sticks and not jals and not warned_empty:
                    if rig is None:
                        print("No miner connected — plug a Blue Fury or a Jalapeno in, it will join automatically.")
                    else:
                        print("No miner connected — the CPU keeps playing; a device joins automatically when plugged.")
                    warned_empty = True

            # 3) serve each stick
            for port, stick in list(sticks.items()):
                try:
                    # a fresh job (every ~1.6 s, or after a reset / new block)
                    if stick.send_next or time.time() - stick.last_job_t > BF1_JOB_SECONDS:
                        header76, en2, ntime = build_work(st, extranonce2)
                        extranonce2 += 1
                        stick.s.write(bf1_packet(header76))
                        stick.in_flight.append((header76, en2, ntime, last_job))
                        del stick.in_flight[:-3]
                        stick.send_next = False
                        stick.last_job_t = time.time()

                    # stick replies
                    data = stick.pending + stick.s.read(7 * 8)
                    if len(data) > len(stick.pending):
                        stick.last_frame_t = time.time()
                        stick.reset_sent = False
                    else:
                        silence = time.time() - stick.last_frame_t
                        if silence > 20 and not stick.reset_sent:
                            print(f"[watchdog] {stick.name} silent for 20 s — sending reset")
                            stick.s.write(b"R")
                            stick.reset_sent = True
                            stick.send_next = True
                        elif silence > 40:
                            raise serial.SerialException("stick not responding")

                    while len(data) >= 7:
                        # frame alignment: a frame starts with 'RR', 'WW' or 'SD';
                        # otherwise shift one byte (late/truncated reply)
                        if data[:2] not in (b"RR", b"WW", b"SD"):
                            data = data[1:]
                            continue
                        frame, data = data[:7], data[7:]
                        if frame[:2] != b"SD" or not stick.in_flight:
                            continue
                        candidate = bitfury_decnonce(struct.unpack("<I", frame[3:7])[0])
                        # the chip double-buffers jobs, so results may belong to older
                        # work — try the candidate against the last 3 jobs
                        for header76, en2, ntime, job_id in reversed(stick.in_flight):
                            hit = False
                            for off in OFFSETS:
                                # chip byte order -> bitcoin nonce = byte-swapped value
                                n = struct.unpack("<I", struct.pack(">I", (candidate + off) & _M))[0]
                                h = dsha(header76 + struct.pack("<I", n))
                                h_int = int.from_bytes(h, "little")
                                if h_int <= DIFF1_TARGET:
                                    hit = True
                                    key = (en2, n)
                                    if key in seen:
                                        continue    # chip repeats result frames — count each hit once
                                    seen.add(key)
                                    stick.hits.append(time.time())
                                    if h_int <= target:
                                        st.send("mining.submit",
                                                [WORKER, job_id, en2, ntime, f"{n:08x}"])
                                        stick.shares += 1
                                        print(f"[share] nonce {n:#010x} sent to pool "
                                              f"({stick.name}, hash ...{h[::-1].hex()[:16]})")
                                        record_ticket(DIFF1_TARGET / h_int, n, stick.name)
                            if hit:
                                break
                    stick.pending = data

                except (OSError, serial.SerialException):
                    print(f"[-] {stick.name} lost — unplug & replug it to rejoin")
                    stick.close()
                    del sticks[port]

            # 3b) serve each Jalapeno: keep its thread fed, collect its candidates
            for port, jal in list(jals.items()):
                if not jal.alive:
                    print(f"[-] {jal.name} lost — check power & USB, it rejoins automatically")
                    jal.close()
                    del jals[port]
                    continue
                while jal.work_q.qsize() < 6:
                    header76, en2, ntime = build_work(st, extranonce2)
                    extranonce2 += 1
                    jal.work_q.put((en2, header76, ntime, last_job))
                try:
                    while True:
                        en2, header76, ntime, job_id, n = jal.out_q.get_nowait()
                        key = (en2, n)
                        if key in seen:
                            continue    # device may repeat results — count each once
                        h = dsha(header76 + struct.pack("<I", n))
                        h_int = int.from_bytes(h, "little")
                        if h_int > DIFF1_TARGET:
                            continue    # noise / hardware error, not a real find
                        seen.add(key)
                        jal.hits.append(time.time())
                        if h_int <= target:
                            st.send("mining.submit",
                                    [WORKER, job_id, en2, ntime, f"{n:08x}"])
                            jal.shares += 1
                            print(f"[share] nonce {n:#010x} sent to pool "
                                  f"({jal.name}, hash ...{h[::-1].hex()[:16]})")
                            record_ticket(DIFF1_TARGET / h_int, n, jal.name)
                except queue.Empty:
                    pass

            # 4) dashboard stats + a status line once per minute
            devices = [
                {"name": v.name, "port": v.port, "hs": round(v.hs(), 1), "shares": v.shares}
                for v in sticks.values()
            ] + [
                {"name": v.name, "port": v.port, "hs": round(v.hs(), 1),
                 "shares": v.shares, "temp": v.temp}
                for v in jals.values()
            ]
            if rig is not None:
                devices.append({"name": rig.name, "port": "—",
                                "hs": round(rig.hs(), 1), "shares": rig.shares})
            if CFG.get("browser_mining"):
                devices.extend(web_live_players())
            with STATS_LOCK:
                STATS["pool"]["difficulty"] = st.difficulty
                STATS["devices"] = devices
                STATS["accepted"] = accepted
                STATS["rejected"] = rejected
            if len(seen) > 4000:
                seen.clear()   # old jobs never come back; no need to keep the memory
            if time.time() - last_status > 60:
                last_status = time.time()
                total = sum(d["hs"] for d in devices)
                print(f"[status] sticks: {len(sticks)}"
                      + (f" + {len(jals)} Jalapeno" if jals else "")
                      + (f" + {rig.name}" if rig is not None else "")
                      + f", ~{total / 1e9:.3f} GH/s total, "
                        f"accepted: {accepted}, rejected: {rejected}, diff: {st.difficulty}")

            time.sleep(0.02)
    finally:
        for stick in sticks.values():
            stick.close()
        for jal in jals.values():
            jal.close()
        with WEB_LOCK:
            WEB["job"] = None      # browsers wait until the new session hands out work


def main() -> None:
    """Run forever with self-healing: a pool outage costs a 10 s pause and a fresh
    session; sticks attach/detach on their own at runtime (hotplug)."""
    load_best()
    start_dashboard_server()
    rig = None
    if CFG.get("cpu_mining"):
        n = int(CFG.get("cpu_workers") or 0)
        if n <= 0:
            n = max(1, (os.cpu_count() or 2) - 1)   # leave one core for the human
        rig = CpuRig(n)
        print(f"[cpu] {rig.name} joined the game — every hash is a \"ticket\"")
    if CFG.get("browser_mining"):
        print(f"[web] browser players welcome — open the dashboard and press PLAY "
              f"(phones: http://PC-IP:{DASHBOARD_PORT})")
    try:
        while True:
            try:
                run_session(rig)
            except KeyboardInterrupt:
                raise
            except (ConnectionError, OSError, serial.SerialException) as e:
                print(f"[watchdog] outage: {e} — retrying in 10 s")
                time.sleep(10)
    finally:
        if rig is not None:
            rig.close()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    try:
        main()
    except KeyboardInterrupt:
        print("\nDone — mining stopped.")

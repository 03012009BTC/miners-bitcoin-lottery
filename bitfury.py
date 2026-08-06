# Bitfury BF1 (Blue Fury / Red Fury) protocol core + a self-test on a known block.
#
# Running this file directly sends historical block 125552 (May 2011) to a
# connected stick, which must find the known winning nonce 0x9546A142.
#
# BF1 protocol per the official bfgminer/cgminer sources:
#   'W' + 32 B midstate + 12 B header tail -> the stick scans the whole nonce space
#   replies come as 7-byte frames: [0] type, [1] state, [2] switch flag, [3:7] nonce (LE)
#   the nonce is decoded with bitfury_decnonce and tried with 6 offsets.
import struct
import time
from hashlib import sha256

import serial
from serial.tools import list_ports

BF1_VIDPID = (0x03EB, 0x204B)

# ---- Block 125552 (the textbook example from Mastering Bitcoin) ----
VERSION = 1
PREV_HASH = "00000000000008a3a41b85b8b29ad444def299fee21793cd8b9e567eab02cd81"
MERKLE_ROOT = "2b12fcf1b09288fcaff797d71e950e71ae42b91e8bdb2304758dfcffc2b620e3"
NTIME = 1305998791
NBITS = 0x1A44B9F2
KNOWN_NONCE = 0x9546A142  # 2,504,433,986
KNOWN_HASH = "00000000000000001e8d6829a8a21adc5d38d0a473b144b6765798e61f98bd1d"

DIFF1_TARGET = 0xFFFF * 2**208  # the "share difficulty 1" bound — the chip reports everything below it


def build_header(nonce: int) -> bytes:
    """The 80-byte block-125552 header in raw (network) byte order."""
    return (
        struct.pack("<I", VERSION)
        + bytes.fromhex(PREV_HASH)[::-1]
        + bytes.fromhex(MERKLE_ROOT)[::-1]
        + struct.pack("<I", NTIME)
        + struct.pack("<I", NBITS)
        + struct.pack("<I", nonce)
    )


# ---- our own SHA-256 compression, needed only for the midstate (hashlib cannot export it) ----
_K = [
    0x428A2F98, 0x71374491, 0xB5C0FBCF, 0xE9B5DBA5, 0x3956C25B, 0x59F111F1, 0x923F82A4, 0xAB1C5ED5,
    0xD807AA98, 0x12835B01, 0x243185BE, 0x550C7DC3, 0x72BE5D74, 0x80DEB1FE, 0x9BDC06A7, 0xC19BF174,
    0xE49B69C1, 0xEFBE4786, 0x0FC19DC6, 0x240CA1CC, 0x2DE92C6F, 0x4A7484AA, 0x5CB0A9DC, 0x76F988DA,
    0x983E5152, 0xA831C66D, 0xB00327C8, 0xBF597FC7, 0xC6E00BF3, 0xD5A79147, 0x06CA6351, 0x14292967,
    0x27B70A85, 0x2E1B2138, 0x4D2C6DFC, 0x53380D13, 0x650A7354, 0x766A0ABB, 0x81C2C92E, 0x92722C85,
    0xA2BFE8A1, 0xA81A664B, 0xC24B8B70, 0xC76C51A3, 0xD192E819, 0xD6990624, 0xF40E3585, 0x106AA070,
    0x19A4C116, 0x1E376C08, 0x2748774C, 0x34B0BCB5, 0x391C0CB3, 0x4ED8AA4A, 0x5B9CCA4F, 0x682E6FF3,
    0x748F82EE, 0x78A5636F, 0x84C87814, 0x8CC70208, 0x90BEFFFA, 0xA4506CEB, 0xBEF9A3F7, 0xC67178F2,
]
_M = 0xFFFFFFFF


def _rotr(x: int, n: int) -> int:
    return ((x >> n) | (x << (32 - n))) & _M


def sha256_midstate(block64: bytes) -> list[int]:
    """SHA-256 state (8 x 32 bit) after processing the first 64 bytes — exactly what the chip needs."""
    h = [0x6A09E667, 0xBB67AE85, 0x3C6EF372, 0xA54FF53A, 0x510E527F, 0x9B05688C, 0x1F83D9AB, 0x5BE0CD19]
    w = list(struct.unpack(">16I", block64))
    for i in range(16, 64):
        s0 = _rotr(w[i - 15], 7) ^ _rotr(w[i - 15], 18) ^ (w[i - 15] >> 3)
        s1 = _rotr(w[i - 2], 17) ^ _rotr(w[i - 2], 19) ^ (w[i - 2] >> 10)
        w.append((w[i - 16] + s0 + w[i - 7] + s1) & _M)
    a, b, c, d, e, f, g, hh = h
    for i in range(64):
        s1 = _rotr(e, 6) ^ _rotr(e, 11) ^ _rotr(e, 25)
        ch = (e & f) ^ (~e & g)
        t1 = (hh + s1 + ch + _K[i] + w[i]) & _M
        s0 = _rotr(a, 2) ^ _rotr(a, 13) ^ _rotr(a, 22)
        maj = (a & b) ^ (a & c) ^ (b & c)
        t2 = (s0 + maj) & _M
        hh, g, f, e, d, c, b, a = g, f, e, (d + t1) & _M, c, b, a, (t1 + t2) & _M
    return [(x + y) & _M for x, y in zip(h, [a, b, c, d, e, f, g, hh])]


def bitfury_decnonce(n: int) -> int:
    """Faithful port of bitfury_decnonce() from bfgminer/libbitfury.c."""
    out = (n & 0xFF) << 24
    n >>= 8
    n = (((n & 0xAAAAAAAA) >> 1) | ((n & 0x55555555) << 1)) & _M
    n = (((n & 0xCCCCCCCC) >> 2) | ((n & 0x33333333) << 2)) & _M
    n = (((n & 0xF0F0F0F0) >> 4) | ((n & 0x0F0F0F0F) << 4)) & _M
    out |= (n >> 2) & 0x3FFFFF
    if n & 1:
        out |= 1 << 23
    if n & 2:
        out |= 1 << 22
    return (out - 0x800004) & _M


OFFSETS = [0, 0xFFC00000, 0xFF800000, 0x02800000, 0x02C00000, 0x00400000]  # from libbitfury.c


def check_nonce(nonce: int) -> bool:
    """Double SHA-256 of the known-block header with the given nonce — below the share bound?"""
    d = sha256(sha256(build_header(nonce)).digest()).digest()
    return int.from_bytes(d, "little") <= DIFF1_TARGET


def main() -> None:
    # 1) self-check: the header with the known nonce must produce exactly the known block hash
    header = build_header(KNOWN_NONCE)
    hash_hex = sha256(sha256(header).digest()).digest()[::-1].hex()
    assert hash_hex == KNOWN_HASH, f"header assembled wrong: {hash_hex}"
    print("Self-check OK — the block 125552 header is assembled correctly.")

    # 2) work packet: midstate of the first 64 B + last 12 B of the header (words reversed)
    state = sha256_midstate(header[:64])
    midstate = struct.pack("<8I", *state)
    tail = b"".join(header[i:i + 4][::-1] for i in range(64, 76, 4))
    packet = b"W" + midstate + tail
    assert len(packet) == 45

    # 3) find the stick
    port = None
    for p in list_ports.comports():
        if (p.vid, p.pid) == BF1_VIDPID:
            port = p.device
            break
    if port is None:
        print("Blue Fury (BF1) is not connected.")
        raise SystemExit(1)
    print(f"Stick on {port}, sending reset and the job (block 125552)...")

    found: set[int] = set()
    raw_frames = 0
    with serial.Serial(port, 115200, timeout=1) as s:
        s.reset_input_buffer()
        s.write(b"R")
        time.sleep(0.5)
        s.read(7)
        # send the job TWICE — the Bitfury chip double-buffers jobs and reliably
        # starts only once a second job is queued (verified on live hardware)
        pending = b""
        for wait in (3, 15):
            s.write(packet)
            deadline = time.time() + wait
            while time.time() < deadline:
                data = pending + s.read(7 * 8)
                while len(data) >= 7:
                    # frame alignment: frames start with 'RR', 'WW' or 'SD';
                    # otherwise shift one byte (late/truncated reply)
                    if data[:2] not in (b"RR", b"WW", b"SD"):
                        data = data[1:]
                        continue
                    frame, data = data[:7], data[7:]
                    raw_frames += 1
                    candidate = bitfury_decnonce(struct.unpack("<I", frame[3:7])[0])
                    for off in OFFSETS:
                        # chip byte order -> bitcoin nonce = byte-swapped value
                        n = struct.unpack("<I", struct.pack(">I", (candidate + off) & _M))[0]
                        if check_nonce(n):
                            found.add(n)
                            print(f"  valid share: nonce {n:#010x} (frame {frame.hex()})")
                pending = data
            if KNOWN_NONCE in found:
                break  # proof obtained, no need to wait out the full window

    # 4) verdict
    print(f"Frames received: {raw_frames}, valid nonces: {len(found)}")
    if KNOWN_NONCE in found:
        d = sha256(sha256(build_header(KNOWN_NONCE)).digest()).digest()[::-1].hex()
        print(f"SUCCESS: the stick found the correct nonce {KNOWN_NONCE:#010x} ({KNOWN_NONCE})")
        print(f"   block hash: {d}")
        print("   Matches historical block 125552 of 21 May 2011. The stick hashes correctly!")
    else:
        print("FAILURE: the known nonce was not found. Check packet format / endianness.")
        raise SystemExit(2)


if __name__ == "__main__":
    main()

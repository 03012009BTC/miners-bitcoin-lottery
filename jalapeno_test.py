# Self-test for the Butterfly Labs Jalapeno (BFLSC protocol, cgminer driver-bflsc.c/h):
# send block 125552 and verify it returns the known nonce 0x9546A142.
#
#   ZNX -> "OK" -> [length 45][midstate 32 B][header tail 12 B][0xAA] -> "OK:QUEUED"
#   ZOX -> lines "midstate,blockdata,count,nonce..." terminated by "OK"; nonce = 8 hex chars
#   (the nonce hex comes in RAW header byte order -> byte-swap it for the RPC value)
import struct
import time
from hashlib import sha256

import serial
from serial.tools import list_ports

from bitfury import (
    KNOWN_NONCE, KNOWN_HASH, _M, sha256_midstate, build_header, check_nonce,
)

BFL_VIDPID = (0x0403, 0x6014)


def bswap(n: int) -> int:
    return struct.unpack("<I", struct.pack(">I", n))[0]


def main() -> None:
    header = build_header(KNOWN_NONCE)
    hash_hex = sha256(sha256(header).digest()).digest()[::-1].hex()
    assert hash_hex == KNOWN_HASH, "header assembled wrong"
    print("Self-check OK — the block 125552 header is correct.")

    midstate = struct.pack("<8I", *sha256_midstate(header[:64]))
    tail = b"".join(header[i:i + 4][::-1] for i in range(64, 76, 4))
    packet = bytes([45]) + midstate + tail + bytes([0xAA])

    port = None
    for p in list_ports.comports():
        if (p.vid, p.pid) == BFL_VIDPID:
            port = p.device
            break
    if port is None:
        print("Jalapeno is not connected.")
        raise SystemExit(1)
    print(f"Jalapeno on {port}, sending the job (block 125552)...")

    found: set[int] = set()
    with serial.Serial(port, 115200, timeout=2, write_timeout=2) as s:
        s.reset_input_buffer()
        s.write(b"ZNX")
        reply = s.readline().decode("ascii", errors="replace").strip()
        print(f"  ZNX -> '{reply}'")
        if "OK" not in reply:
            print("The device did not accept the job request.")
            raise SystemExit(2)
        s.write(packet)
        reply = s.readline().decode("ascii", errors="replace").strip()
        print(f"  job -> '{reply}'")

        deadline = time.time() + 15
        while time.time() < deadline:
            time.sleep(1.0)
            s.write(b"ZOX")
            while True:
                line = s.readline().decode("ascii", errors="replace").strip()
                if not line:
                    break
                print(f"  ZOX: {line}")
                for field in line.split(","):
                    field = field.strip()
                    if len(field) == 8 and all(c in "0123456789abcdefABCDEF" for c in field):
                        raw = int(field, 16)
                        # try both byte orders (RPC first, raw as fallback)
                        for n in (bswap(raw), raw):
                            if check_nonce(n):
                                found.add(n)
                if line == "OK" or line.startswith("INPROCESS"):
                    if line == "OK":
                        break
            if KNOWN_NONCE in found:
                break

    if KNOWN_NONCE in found:
        d = sha256(sha256(build_header(KNOWN_NONCE)).digest()).digest()[::-1].hex()
        print(f"SUCCESS: the Jalapeno found the correct nonce {KNOWN_NONCE:#010x} ({KNOWN_NONCE})")
        print(f"   block hash: {d}")
        print("   Matches historical block 125552. The Jalapeno hashes correctly!")
    else:
        print(f"FAILURE: the known nonce was not found (valid nonces: {len(found)}).")
        raise SystemExit(2)


if __name__ == "__main__":
    main()

# Identification of USB ASIC sticks (Blue Fury BF1, Bi-Fury BXF, BFL Jalapeno).
# Protocols taken from the official sources: bfgminer driver-bigpic.c / driver-bifury.c,
# cgminer driver-bflsc.c.
import sys

import serial
from serial.tools import list_ports

BF1_VIDPID = (0x03EB, 0x204B)  # Atmel — Blue Fury / Bitfury BF1
BFL_VIDPID = (0x0403, 0x6014)  # FTDI — Butterfly Labs Jalapeno (BFLSC protocol)


def identify_bf1(port: str) -> None:
    """BF1: byte 'I' -> 14 bytes: [0] state, [1] version, [2:10] product, [10:14] serial (LE)."""
    with serial.Serial(port, 115200, timeout=2) as s:
        s.reset_input_buffer()
        s.write(b"I")
        resp = s.read(14)
    print(f"  reply ({len(resp)} B): {resp.hex()}")
    if len(resp) == 14:
        product = resp[2:10].decode("ascii", errors="replace").strip("\x00 ")
        serial_no = int.from_bytes(resp[10:14], "little")
        print(f"  -> state={resp[0]}, version={resp[1]}, product='{product}', serial={serial_no:#010x}")
    else:
        print("  -> incomplete reply, stick did not answer as expected")


def identify_bxf(port: str) -> None:
    """BXF (Bi-Fury): text command 'version\\n' -> line 'version <maj>.<min> rev <r> chips <n>'."""
    with serial.Serial(port, 115200, timeout=2, write_timeout=2) as s:
        s.reset_input_buffer()
        s.write(b"version\n")
        line = s.readline().decode("ascii", errors="replace").strip()
    print(f"  reply: '{line}'")


def identify_bfl(port: str) -> None:
    """Butterfly Labs (BFLSC): 'ZGX' -> identity line, 'ZCX' -> details terminated by 'OK'."""
    with serial.Serial(port, 115200, timeout=3, write_timeout=2) as s:
        s.reset_input_buffer()
        s.write(b"ZGX")
        ident = s.readline().decode("ascii", errors="replace").strip()
        print(f"  identity: '{ident}'")
        s.write(b"ZCX")
        while True:
            line = s.readline().decode("ascii", errors="replace").strip()
            if not line:
                break
            print(f"  {line}")
            if line == "OK":
                break


def main() -> None:
    found = False
    for p in list_ports.comports():
        if p.vid is None:
            continue
        print(f"{p.device}: VID={p.vid:#06x} PID={p.pid:#06x} SN={p.serial_number}")
        if (p.vid, p.pid) == BF1_VIDPID:
            found = True
            print("  type: Blue Fury (BF1) — sending 'I'")
            identify_bf1(p.device)
        elif (p.vid, p.pid) == BFL_VIDPID:
            found = True
            print("  type: Butterfly Labs (BFLSC) — sending 'ZGX'")
            identify_bfl(p.device)
        else:
            found = True
            print("  unknown type — trying text command 'version' (Bi-Fury)")
            identify_bxf(p.device)
    if not found:
        print("No USB serial mining stick found.")
        sys.exit(1)


if __name__ == "__main__":
    main()

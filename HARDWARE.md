# Does my old USB Bitcoin miner still work?

Short answer: **probably yes.** Most of these devices were not thrown away
because they broke — they were thrown away because the software died. The
download links from 2013 are gone, the forum threads are archived, and the
binaries that are still floating around are not something you should run.

This project is a from-scratch, readable replacement. If your device is in the
first table below, plug it in and it will mine. If it is in the second table,
the hardware is understood but nobody has written the driver yet — and that is
usually because nobody who owns one has asked.

---

## Supported today

| Device | Also sold as | Chip | USB ID | Speed (measured) | Notes |
|---|---|---|---|---|---|
| **Bitfury BF1** | Blue Fury, Red Fury | Bitfury BF1 | `03EB:204B` | ~1.7–2.0 GH/s each | Multiple sticks at once, hot-plug, per-stick watchdog. Sold as "2.6 GH/s"; that number was optimistic even in 2013. |
| **Butterfly Labs Jalapeño** | BFL SC, "BitFORCE SHA256 SC" | 2× BFL SC (65 nm) | `0403:6014` (FTDI) | ~5.2–6.4 GH/s | Needs its own 12 V PSU. Temperature is shown on the dashboard; the miner pauses it above 80 °C. |

Both drivers were written by reading the `bfgminer` and `cgminer` sources and
then fixing them against live hardware, which is where most of the surprises
were. They are verified against historical block 125552 — the device has to
find the known winning nonce before the code is trusted.

## Understood, but not supported yet

Nobody here owns one of these. If you do and you want it to work, open an issue
— that is genuinely all it takes to move something up to the table above.

| Device | Chip | Why it is not done |
|---|---|---|
| **Bi•Fury (BXF)** | 2× Bitfury | Identification code exists; our only unit is faulty (overheats, dead serial), so the driver could never be tested. |
| **GekkoScience Compac / Compac F / NewPac** | BM1384 / BM1387 | The most common USB stick still in circulation. Protocol is documented in cgminer. No hardware here to test on. |
| **ASICMiner Block Erupter USB** | BE200 | The classic 336 MH/s blue stick. Simple protocol, would be a nice addition. |
| **Antminer U1 / U2 / U3** | BM1380 / BM1384 | Same family as the GekkoScience sticks. |
| **NanoFury / HexFury / Rockminer** | Bitfury | Close relatives of the BF1 — likely the smallest amount of work of anything on this list. |

## Devices that mine on their own

These do **not** need this software: they run their own firmware and talk to a
pool by themselves. Point them at your own address and they play the same
lottery.

| Device | What this project can do with it |
|---|---|
| **Bitaxe** (Ultra / Supra / Gamma, BM1366–BM1370) | Runs AxeOS, which has an HTTP API — showing it on the dashboard alongside the old sticks is planned. |
| **NerdMiner / NMMiner** (ESP32) | No local API to read; it will show up as a worker on your pool, but not on the dashboard. |
| **Antminer S9 and friends** | Has the cgminer API on port 4028 — readable in principle, same idea as AxeOS. |

---

## Which one do I even have?

Plug it in and run:

```
python identify.py
```

It lists every serial device with its USB vendor/product ID and, where the
device supports it, asks the device what it is. If the ID is not in the table
above, paste the output into an issue — that is the first thing anyone needs in
order to help you.

## It is plugged in and nothing happens

These are the failures we actually hit, in the order they are worth checking.

**The stick appears in Device Manager but has no COM port.**
It is probably still bound to a Zadig/libwdi WinUSB driver from an older mining
tool. Remove it as Administrator and replug:

```
pnputil /delete-driver oemXX.inf /uninstall /force
```

where `oemXX.inf` is the driver shown in the device's properties. Windows then
binds its own `usbser` driver and a COM port appears.

**A Jalapeño shows up as "BitFORCE SHA256 SC" with a yellow warning mark.**
Windows has no FTDI driver for it, so it never gets a COM port. Right-click the
device → *Update driver* → *Search automatically*; Windows Update has the
signed FTDI driver. Do not download FTDI drivers from random sites.

**The device enumerates, gets a COM port, and returns nothing at all.**
Try a different USB cable, and try a direct port instead of a hub. A bad cable
will happily enumerate a device and pass no data — this cost us a day.

**It works for a while, then goes silent until you unplug it.**
On laptops this is USB power saving. Run `NOTEBOOK_FIX_USB.bat` as
Administrator once and reboot. On a desktop, suspect the powered hub or the
cable instead.

**A Jalapeño does nothing even though the COM port is there.**
It needs its own 12 V supply of at least 5 A — USB power alone runs the serial
bridge but not the chips. Do not "make do" with a 13 V adapter.

## Getting your hardware supported

Open an issue with:

1. what the device is (photo of the label is ideal),
2. the output of `identify.py`,
3. whether you are willing to run a test script and paste what it prints.

Point 3 is the one that matters. Writing a driver blind against a 13-year-old
protocol description produces something that looks right and silently loses
shares — we know, because that is exactly what happened with the Jalapeño
before it was fixed against a real device.

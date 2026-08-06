=====================================================
 MINERS — BITCOIN LOTTERY  (portable package)
 Simple step-by-step guide — no experience needed
=====================================================

WHAT THIS IS
------------
A little Bitcoin "lottery machine". It mines Bitcoin in SOLO mode:
you will (almost certainly) never hit a block — but every attempt
is a real lottery ticket, and somebody wins every ~10 minutes.
It works with retro USB ASIC sticks (Blue Fury), with your CPU,
and even with your phone's browser. Nothing to install.


1. START PLAYING (2 clicks)
---------------------------
1. Copy this WHOLE folder onto the computer (e.g. the Desktop).
2. Double-click  START_MINING.bat
   - a black window opens and the game begins
   - closing that window = stop playing
   - if a blue "Windows protected your PC" box appears the first
     time: click "More info" -> "Run anyway" (it only means the
     file has no paid publisher certificate)
   - if Windows Firewall asks about "python.exe": click ALLOW
     (needed so your phone can see the dashboard later)
3. Double-click  dashboard.html
   - the lottery screen opens in your browser: jackpot, burning
     fuse, your machines, your best "ticket"...

Have a Blue Fury USB stick? Just plug it in - it joins the game
by itself within 5 seconds, any time, even while mining runs.
No stick? Your CPU is already playing (see section 3).


2. PLAY FROM YOUR PHONE OR TABLET
---------------------------------
The dashboard (and the PLAY button) works on any device in your
HOME network:

1. Find the address of the mining PC:
   - press the Windows key, type  cmd  and press Enter
   - in the black window type  ipconfig  and press Enter
   - find the line "IPv4 Address", e.g.  192.168.1.23
2. On the phone: connect to the SAME Wi-Fi as the PC
   (home Wi-Fi, NOT mobile data - this is important!)
3. In the phone's browser open:  http://THAT-ADDRESS:8888
   example:                      http://192.168.1.23:8888
   (type http://  - not https://)
4. Tap the green  ▶ PLAY  button. Your phone is now drawing
   "tickets" too and appears under "Machines in the game".
   Tap ■ STOP (or close the tab) to leave the game.

Tips:
- while playing, the page keeps the phone's screen AWAKE on purpose:
  locking the screen or leaving the browser pauses the game
- phone mining is for fun - do it while charging, phones get warm
- do NOT open port 8888 to the internet (router port forwarding):
  the dashboard has no password; it belongs in your home network
- if PC works but the phone can't connect: your router may have
  "AP isolation" enabled - turn it off in the router settings


3. SETTINGS  (config.json)
--------------------------
Right-click config.json -> Open with -> Notepad. Change what you
need, save, then close the miner window and start it again.

  "btc_address":  WHERE the winnings would go. PUT YOUR OWN
                  Bitcoin address here (starts with bc1...).
                  A typo is detected and the miner refuses to
                  start - so you cannot mine to a broken address.
  "cpu_mining":   true  = your CPU plays too (default)
                  false = CPU stays idle, only sticks/phones play
  "cpu_workers":  0 = automatic (all cores minus one)
                  or a number, e.g. 2 = use only 2 cores (quieter)
  "browser_mining": true/false = allow the PLAY button for phones
  "worker_name":  name of your sticks on the pool website
  "dashboard_port": 8888 = the number after the ":" in the address

Everything else is fine at default.


4. FREQUENTLY ASKED QUESTIONS
-----------------------------
Q: Do I have to turn off my antivirus, like in the old days?
A: NO - and please don't. Old mining tools were downloaded EXE
   files, which antiviruses (rightly) hated. This project is
   readable Python text files + the official Python from
   python.org, so Windows Defender has no reason to complain.
   If some antivirus still flags "mining behaviour", add an
   EXCEPTION for this folder - never disable your protection.

Q: What happens if I press PLAY on the same PC that is mining?
A: Nothing bad - the browser joins as one more player. But it
   steals CPU from the miner, so it's pointless there. PLAY is
   meant for OTHER devices (phone, tablet, another computer).

Q: How do I stop mining?
A: Close the black window. That's it.

Q: Is my Bitcoin wallet at risk?
A: No. The miner only knows your PUBLIC address (like a bank
   account number for receiving). No keys, no seed, no wallet
   files - it cannot spend anything, only (theoretically) win.

Q: Mining on two computers at once?
A: Yes - copy the folder to both. Optionally set a different
   "worker_name" on each so you can tell them apart on
   https://web.public-pool.io (paste your address there).

Q: Where do I see my results?
A: dashboard.html (local, pretty) and https://web.public-pool.io
   (the pool's own site - paste your address).

Q: How big is the chance, really?
A: A stick: about 1 : 10 billion per block. A phone: much worse.
   Think of it as a lottery ticket that never expires and
   redraws every 10 minutes. While you play, you live. :-)


5. WHAT IS IN THE FOLDER
------------------------
  START_MINING.bat     the two-click launcher (logs to miner.log)
  dashboard.html       the lottery screen (open any time)
  config.json          your settings (created on first run)
  miner.py             the miner itself (readable Python)
  bitfury.py           BF1 protocol core + self-test on block 125552
  jalapeno_test.py     self-test for BFL Jalapeno cubes
  identify.py          "who is plugged in?" diagnostic
  test_js_hasher.js    checks the browser hasher (node test_js_hasher.js)
  lottery_best.json    your best "ticket" ever - your trophy!
  miner.log            everything the miner printed (for debugging)
  python\              official embedded Python 3.13 (python.org)
  serial\              the pyserial library (pure Python)
  README.md, LICENSE   full documentation (EN/SK), GPLv3

License: GPLv3 - free to use, study, improve and share.
Donating bitcoin: bc1qrw38lsml6anen4np6k5j0fdrjf4fa2jqhm8qw5
Powered by inspiration from https://proofofforge.com/

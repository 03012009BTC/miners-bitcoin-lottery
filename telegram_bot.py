# Remote control over Telegram: check on the miner and start/stop it from
# anywhere, without exposing anything to the internet — the bot dials out, so
# no port forwarding, no VPN, and the dashboard stays on your home network.
#
# Deliberately limited: it answers ONLY the chat id in config.json, and it can
# only run the handful of commands below. There is no "run any command" here,
# on purpose — a bot token is not a good front door to a computer.
#
#   /status   what the daily report says, on demand
#   /start    start mining
#   /stop     stop mining
#   /restart  stop, then start
#   /log      last lines of miner.log
#   /help     this list
#
# Setup: same config.json keys as telegram_report.py (telegram_token,
# telegram_chat_id). Run it in the background:  python telegram_bot.py
# Only the standard library is used.
import json
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from telegram_report import (
    CONFIG_FILE, build_report, get_json, load_config, send,
)

TASK_NAME = "MINERS - Bitcoin Lottery"
POLL_TIMEOUT = 30            # long-poll: Telegram holds the request open
MINER_MATCH = "miner.py"


def _run(args: list[str]) -> str:
    """Run a fixed command line (never anything the user typed) and return its output."""
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=60,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return (p.stdout or "") + (p.stderr or "")
    except Exception as e:
        return f"({e})"


def miner_pids() -> list[int]:
    out = _run(["powershell", "-NoProfile", "-Command",
                "Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and "
                f"$_.CommandLine -like '*{MINER_MATCH}*' " + "} | ForEach-Object { $_.ProcessId }"])
    return [int(x) for x in out.split() if x.strip().isdigit()]


def cmd_status(cfg: dict) -> str:
    return build_report(cfg)


def cmd_start(cfg: dict) -> str:
    if miner_pids():
        return "Already mining. Nothing to do."
    _run(["schtasks", "/run", "/tn", TASK_NAME])
    for _ in range(20):                       # give it time to open the ports
        time.sleep(3)
        if miner_pids():
            return "Started. Give it a minute, then ask for /status."
    return "Tried to start it, but no miner process appeared — worth a look in person."


def cmd_stop(cfg: dict) -> str:
    pids = miner_pids()
    if not pids:
        return "It is not running."
    _run(["schtasks", "/end", "/tn", TASK_NAME])
    for pid in pids:
        _run(["taskkill", "/F", "/PID", str(pid)])
    time.sleep(2)
    return "Stopped." if not miner_pids() else "Asked it to stop, but something is still running."


def cmd_restart(cfg: dict) -> str:
    return cmd_stop(cfg) + "\n" + cmd_start(cfg)


def cmd_log(cfg: dict) -> str:
    path = CONFIG_FILE.replace("config.json", "miner.log")
    for enc in ("utf-16", "utf-8", "cp1250"):
        try:
            with open(path, encoding=enc) as f:
                lines = [l.rstrip() for l in f.readlines() if l.strip()]
            break
        except (UnicodeError, UnicodeDecodeError):
            continue
        except OSError:
            return "No miner.log yet."
    else:
        return "Cannot read miner.log."
    tail = lines[-12:]
    return "<pre>" + "\n".join(l.replace("<", "&lt;") for l in tail) + "</pre>"


def cmd_help(cfg: dict) -> str:
    return ("/status — how the fleet is doing\n"
            "/start — start mining\n"
            "/stop — stop mining\n"
            "/restart — stop, then start\n"
            "/log — last lines of the miner log\n"
            "/help — this list")


COMMANDS = {
    "/status": cmd_status, "/start": cmd_start, "/stop": cmd_stop,
    "/restart": cmd_restart, "/log": cmd_log, "/help": cmd_help,
}


def register_menu(token: str) -> None:
    """Put the commands into Telegram's own '/' menu, with descriptions."""
    cmds = [
        {"command": "status", "description": "how the fleet is doing"},
        {"command": "start", "description": "start mining"},
        {"command": "stop", "description": "stop mining"},
        {"command": "restart", "description": "stop, then start"},
        {"command": "log", "description": "last lines of the miner log"},
        {"command": "help", "description": "list the commands"},
    ]
    data = urllib.parse.urlencode({"commands": json.dumps(cmds)}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/setMyCommands", data=data)
    try:
        urllib.request.urlopen(req, timeout=20).read()
    except Exception as e:
        print(f"could not register the command menu: {e}")


def main() -> None:
    cfg = load_config()
    token, owner = cfg.get("telegram_token", ""), str(cfg.get("telegram_chat_id", ""))
    if not token or not owner:
        raise SystemExit("Set telegram_token and telegram_chat_id in config.json first.")
    register_menu(token)
    print(f"listening for commands from chat {owner} — Ctrl+C to quit")
    send(token, owner, "\U0001f916 Remote control is up. Buttons are below, "
                       "or tap the ☰ menu next to the text field.")

    offset = 0
    while True:
        try:
            upd = get_json(
                f"https://api.telegram.org/bot{token}/getUpdates"
                f"?timeout={POLL_TIMEOUT}&offset={offset}",
                timeout=POLL_TIMEOUT + 15)
        except Exception:
            time.sleep(10)                     # network hiccup: just try again
            continue
        for u in upd.get("result", []):
            offset = u["update_id"] + 1
            msg = u.get("message") or {}
            chat = str((msg.get("chat") or {}).get("id", ""))
            text = (msg.get("text") or "").strip().split()[:1]
            if not text:
                continue
            if chat != owner:                  # someone else found the bot
                print(f"ignoring {text[0]} from chat {chat}")
                continue
            handler = COMMANDS.get(text[0].lower().split("@")[0])
            reply = handler(cfg) if handler else "Unknown command. /help"
            try:
                send(token, owner, reply)
            except Exception as e:
                print(f"could not reply: {e}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nstopped")

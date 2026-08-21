# Daily mining report over Telegram.
#
# Run it once a day (Task Scheduler / cron). It reads the miner's own
# /stats.json, asks the pool what it has credited to your address, and sends
# one short message. If the miner is not running, that is exactly what the
# message says — which is the report you most want to receive.
#
# Setup:
#   1. Talk to @BotFather in Telegram, /newbot, copy the token.
#   2. Send any message to your new bot.
#   3. Put the token into config.json as "telegram_token", then run:
#          python telegram_report.py --chat-id
#      It prints the chat id; put that into config.json as "telegram_chat_id".
#   4. Test it with:  python telegram_report.py
#
# Only the standard library is used — no extra dependencies.
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(_DIR, "config.json")
POOL_API = "https://public-pool.io:40557/api/client/{address}"
TIMEOUT = 20


def load_config() -> dict:
    with open(CONFIG_FILE, encoding="utf-8-sig") as f:
        return json.load(f)


def get_json(url: str, timeout: int = TIMEOUT):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def rate(hs: float) -> str:
    for unit, div in (("TH/s", 1e12), ("GH/s", 1e9), ("MH/s", 1e6), ("kH/s", 1e3)):
        if hs >= div:
            return f"{hs / div:.2f} {unit}"
    return f"{hs:.0f} H/s"


def short(n: float) -> str:
    for unit, div in (("T", 1e12), ("G", 1e9), ("M", 1e6), ("k", 1e3)):
        if n >= div:
            return f"{n / div:.2f} {unit}"
    return f"{n:.0f}"


def build_report(cfg: dict) -> str:
    port = int(cfg.get("dashboard_port", 8888))
    address = cfg.get("btc_address", "")
    lines = ["\U0001f3b0 <b>MINERS — Bitcoin Lottery</b>"]

    # 1) the miner itself
    try:
        st = get_json(f"http://127.0.0.1:{port}/stats.json", timeout=10)
        devices = st.get("devices", [])
        total = sum(d.get("hs", 0) for d in devices)
        hours = max(0.0, time.time() - st.get("start", time.time())) / 3600
        lines.append(f"⛏️ <b>{rate(total)}</b> · running {hours:.1f} h")
        for d in devices:
            temp = f" · {d['temp']} °C" if d.get("temp") else ""
            lines.append(f"   • {d['name']}: {rate(d.get('hs', 0))}{temp}")
        lines.append(
            f"\U0001f3ab session: {st.get('accepted', 0)} accepted"
            f" / {st.get('rejected', 0)} rejected"
        )
        best = max(st.get("best_session", 0), 0)
        if best:
            lines.append(f"⭐ best ticket this session: {short(best)}")
    except Exception:
        lines.append("⚠️ <b>THE MINER IS NOT RUNNING</b> — no answer from the dashboard.")

    # 2) what the pool has actually credited (survives miner restarts)
    if address:
        try:
            pool = get_json(POOL_API.format(address=address))
            acc = pool.get("accounting", {})
            lines.append(
                f"\U0001f4ca pool 24 h: {acc.get('acceptedSharesLastDay', 0):,} tickets"
                f" · total {acc.get('totalAcceptedShares', 0):,}"
            )
            lines.append(
                f"\U0001f3c6 best ticket ever: {short(float(pool.get('bestDifficulty', 0) or 0))}"
                f" · workers online: {pool.get('workersCount', 0)}"
            )
        except Exception:
            lines.append("\U0001f4ca pool: unreachable right now")

    # 3) where the network stands
    try:
        height = int(urllib.request.urlopen(
            "https://mempool.space/api/blocks/tip/height", timeout=TIMEOUT).read())
        lines.append(f"\U0001f9f1 block {height:,}")
    except Exception:
        pass

    lines.append("")
    lines.append("Good luck \U0001f340")
    return "\n".join(lines)


# Buttons that stay under the text field, so the controls are always one tap
# away instead of something you have to remember or scroll back for.
KEYBOARD = {
    "keyboard": [["/status", "/log"], ["/restart", "/stop"]],
    "resize_keyboard": True,
    "is_persistent": True,
}


def send(token: str, chat_id: str, text: str, keyboard: bool = True) -> None:
    fields = {
        "chat_id": chat_id, "text": text, "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }
    if keyboard:
        fields["reply_markup"] = json.dumps(KEYBOARD)
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        if not json.loads(r.read().decode("utf-8")).get("ok"):
            raise RuntimeError("Telegram refused the message")


def print_chat_id(token: str) -> None:
    """Find the chat id of whoever last messaged the bot."""
    upd = get_json(f"https://api.telegram.org/bot{token}/getUpdates")
    chats = {
        str(u["message"]["chat"]["id"]): u["message"]["chat"].get("first_name", "")
        for u in upd.get("result", []) if "message" in u
    }
    if not chats:
        print("No messages yet — open Telegram, send any message to your bot, then run this again.")
        return
    for cid, name in chats.items():
        print(f"chat id: {cid}   ({name})")


def main() -> None:
    cfg = load_config()
    token = cfg.get("telegram_token", "")
    if not token:
        raise SystemExit('Put your bot token into config.json as "telegram_token" first.')
    if "--chat-id" in sys.argv:
        print_chat_id(token)
        return
    chat_id = cfg.get("telegram_chat_id", "")
    if not chat_id:
        raise SystemExit('Put your chat id into config.json as "telegram_chat_id" '
                         '(run this with --chat-id to find it).')
    # The laptop's Wi-Fi is not always up at 08:00, and one failed request
    # should not cost the whole daily report — keep trying for ten minutes.
    last = None
    for attempt in range(10):
        try:
            send(token, str(chat_id), build_report(cfg))
            print("report sent" + (f" (attempt {attempt + 1})" if attempt else ""))
            return
        except Exception as e:
            last = e
            print(f"attempt {attempt + 1} failed: {e}")
            time.sleep(60)
    raise SystemExit(f"gave up after 10 attempts: {last}")


if __name__ == "__main__":
    main()

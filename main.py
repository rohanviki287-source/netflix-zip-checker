import os
import time
import logging
import threading
import requests
from flask import Flask
from telegram.ext import (
    Updater,
    MessageHandler,
    CommandHandler,
    CallbackQueryHandler,
    Filters,
)
from telegram import ParseMode, InlineKeyboardButton, InlineKeyboardMarkup

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Flask app — keeps this bot's own Render port open (unrelated to waking
# the other service). Render pings this to know the bot service is alive.
# ---------------------------------------------------------------------------
flask_app = Flask(__name__)


@flask_app.route("/")
def home():
    return "Bot is running!"


def run_flask():
    flask_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))


threading.Thread(target=run_flask, daemon=True).start()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BOT_TOKEN = os.environ["BOT_TOKEN"]

BASE_URL = "https://netflix-checker-3pev.onrender.com"
WAKE_URL = f"{BASE_URL}/"
SCRAPE_URL = f"{BASE_URL}/scrape"

# A browser-like header. Some proxies / spin-up pages treat plain
# python-requests traffic differently from a real browser request.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}

pending_sessions = {}

# ---------------------------------------------------------------------------
# Waking the other Render service
#
# Render free instances can take anywhere from ~15s to 60s+ to boot from
# cold. During that window the edge/proxy layer can return odd statuses
# (429, 404, 502, 503) that do NOT mean the real app is up yet. The only
# reliable way to know the app itself is ready is to keep polling until
# you get a stable, repeatable "good" response — not just stop at the
# first response that isn't a 429.
# ---------------------------------------------------------------------------
STATUS_STILL_BOOTING = {429, 502, 503, 504}


def _single_check():
    """One GET to WAKE_URL. Returns (ok: bool, status_or_error)."""
    try:
        r = requests.get(WAKE_URL, headers=HEADERS, timeout=30)
        return r.status_code, None
    except Exception as e:
        return None, str(e)


def wake_up_server(max_wait_seconds=180, poll_interval=8, confirm_count=2):
    """
    Poll WAKE_URL until the service responds consistently.

    Strategy:
      - Treat 429 / 502 / 503 / 504 as "still booting" -> keep polling.
      - Treat any other status (200, 404, etc.) as "app is responding".
      - Require `confirm_count` consecutive non-booting responses in a row
        before declaring success, so a one-off response from the edge
        layer (before the container is really ready) doesn't fool us.
    """
    start = time.time()
    consecutive_ok = 0
    attempt = 0

    while time.time() - start < max_wait_seconds:
        attempt += 1
        status, error = _single_check()

        if error:
            log.warning(f"Wake attempt {attempt}: request failed: {error}")
            consecutive_ok = 0
        elif status in STATUS_STILL_BOOTING:
            log.info(f"Wake attempt {attempt}: status {status} (still booting)")
            consecutive_ok = 0
        else:
            consecutive_ok += 1
            log.info(
                f"Wake attempt {attempt}: status {status} "
                f"({consecutive_ok}/{confirm_count} consecutive good responses)"
            )
            if consecutive_ok >= confirm_count:
                log.info(f"Service appears awake after {attempt} attempt(s).")
                return True

        time.sleep(poll_interval)

    log.error(f"Gave up waking the server after {max_wait_seconds}s.")
    return False


def wake_up_server_async():
    threading.Thread(target=wake_up_server, daemon=True).start()


# Wake it once when this bot process starts.
wake_up_server_async()


# ---------------------------------------------------------------------------
# Example: call wake_up_server() (blocking) right before you actually need
# the other service, e.g. inside your /scrape command handler, instead of
# only at startup. This matters because Render free services spin back
# down after ~15 minutes of no traffic, so a startup-only wake won't help
# if the bot is used hours later.
#
# def scrape_command(update, context):
#     update.message.reply_text("Waking the scraper, one moment...")
#     if not wake_up_server(max_wait_seconds=90):
#         update.message.reply_text("Couldn't wake the server, try again shortly.")
#         return
#     r = requests.get(SCRAPE_URL, headers=HEADERS, timeout=60)
#     update.message.reply_text(f"Scrape status: {r.status_code}")
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Telegram bot setup
# ---------------------------------------------------------------------------
updater = Updater(BOT_TOKEN, use_context=True)
dp = updater.dispatcher


def start_command(update, context):
    """Test handler: reports whether net-3pev is awake, with a status
    message sent immediately so the user isn't staring at a blank chat
    during the (possibly 1-2 minute) wait."""
    update.message.reply_text("Checking if the server is awake, please wait...")

    is_awake = wake_up_server(max_wait_seconds=90)

    if is_awake:
        update.message.reply_text("✅ Server is awake and ready.")
    else:
        update.message.reply_text(
            "⚠️ Server didn't wake up in time. Try /start again in a bit."
        )


dp.add_handler(CommandHandler("start", start_command))

# NOTE: paste your other existing command/message/callback handlers below —
# they weren't included in what you shared earlier, so only /start is wired
# up here for testing the wake-up logic.
# dp.add_handler(CommandHandler("scrape", scrape_command))
# dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))
# dp.add_handler(CallbackQueryHandler(handle_callback))

if __name__ == "__main__":
    log.info("Starting Telegram bot polling...")
    updater.start_polling()
    updater.idle()

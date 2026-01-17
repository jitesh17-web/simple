import os
import json
import requests
from io import BytesIO
import logging
import re
import base64
from bs4 import BeautifulSoup

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)
from telegram.error import Conflict

# ---------------- LOGGING ----------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------- STATES ----------------
ASK_NID, ASK_PDF_NAME = range(2)

# ---------------- AUTHORIZED USERS ----------------
AUTHORIZED_USER_IDS = [7927314662, 7686927258]

# ---------------- BOT TOKEN ----------------
BOT_TOKEN = "7569082224:AAHKhpg_MfaMbdXtLYiD2nVlVWXPN9kz4JU"


# ==================================================
# IMAGE HANDLING
# ==================================================
def download_and_encode_image(img_url: str) -> str:
    try:
        response = requests.get(img_url, timeout=15)
        response.raise_for_status()

        content_type = response.headers.get("Content-Type", "image/png")
        encoded = base64.b64encode(response.content).decode()

        return f"data:{content_type};base64,{encoded}"

    except Exception as e:
        logger.error(f"Image error: {e}")
        return img_url


def process_html_content(html_string: str) -> str:
    if not html_string:
        return ""

    soup = BeautifulSoup(html_string, "html.parser")

    for img in soup.find_all("img"):
        src = img.get("src")
        if not src:
            continue

        if src.startswith("//"):
            src = "https:" + src

        if src.startswith("http"):
            img["src"] = download_and_encode_image(src)

    return str(soup)


# ==================================================
# API FUNCTIONS
# ==================================================
def fetch_locale_json_from_api(nid: str):
    url = f"https://learn.aakashitutor.com/quiz/{nid}/getlocalequestions"

    try:
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        raw = r.json()

        questions = []

        for _, lang_data in raw.items():
            if not isinstance(lang_data, dict):
                continue

            eng = lang_data.get("843")
            if not eng:
                continue

            if "body" in eng and "alternatives" in eng:
                questions.append({
                    "body": eng.get("body", ""),
                    "alternatives": eng.get("alternatives", [])
                })

        return questions

    except Exception as e:
        logger.error(e)
        return None


def fetch_test_title_and_description(nid: str):
    url = f"https://learn.aakashitutor.com/api/getquizfromid?nid={nid}"

    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        data = r.json()

        if isinstance(data, list) and data:
            return data[0].get("title", ""), data[0].get("description", "")

        return f"Test {nid}", ""

    except Exception:
        return f"Test {nid}", ""


# ==================================================
# HTML GENERATOR
# ==================================================
def generate_html_with_answers(data, title, syllabus):
    html = """
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>Question Paper</title>
<style>
body{font-family:Arial;padding:40px;max-width:900px;margin:auto}
.question{border:2px solid #000;padding:25px;margin-bottom:40px}
.option{border:1px solid #000;padding:12px;margin:8px 0}
.correct{background:#e0e0e0;font-weight:bold}
.option span{font-weight:bold;margin-right:10px}
img{max-width:100%}
</style>
</head>
<body>
"""

    labels = ["A", "B", "C", "D"]

    for i, q in enumerate(data, 1):
        html += f"<div class='question'><h3>Question {i}</h3>"
        html += process_html_content(q["body"])

        for idx, opt in enumerate(q["alternatives"][:4]):
            correct = str(opt.get("score_if_chosen")) == "1"
            cls = "option correct" if correct else "option"

            html += f"<div class='{cls}'><span>{labels[idx]}</span>"
            html += process_html_content(opt.get("answer", ""))
            html += "</div>"

        html += "</div>"

    html += "</body></html>"
    return html


# ==================================================
# BOT HANDLERS
# ==================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in AUTHORIZED_USER_IDS:
        await update.message.reply_text("❌ Access denied")
        return ConversationHandler.END

    await update.message.reply_text("📌 Send NID:")
    return ASK_NID


async def handle_nid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nid = update.message.text.strip()

    if not nid.isdigit():
        await update.message.reply_text("❌ Invalid NID")
        return ASK_NID

    context.user_data["nid"] = nid
    await update.message.reply_text("📄 Enter file name:")
    return ASK_PDF_NAME


async def handle_pdf_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nid = context.user_data["nid"]

    name = re.sub(r'[\\/*?:"<>|]', "_", update.message.text.strip())
    if not name:
        name = f"Extracted_{nid}"

    await update.message.reply_text("⏳ Processing...")

    title, desc = fetch_test_title_and_description(nid)
    data = fetch_locale_json_from_api(nid)

    if not data:
        await update.message.reply_text("❌ No data found")
        return ConversationHandler.END

    html = generate_html_with_answers(data, title, desc)

    file = BytesIO(html.encode("utf-8"))
    file.name = f"{name}.html"

    await update.message.reply_document(
        document=file,
        caption="✅ Question paper with answer key"
    )

    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled")
    return ConversationHandler.END


# ==================================================
# MAIN
# ==================================================
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ASK_NID: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_nid)],
            ASK_PDF_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_pdf_name)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(conv)

    logger.info("✅ BOT STARTED SUCCESSFULLY")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

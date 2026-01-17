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

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Define conversation states
ASK_NID, ASK_PDF_NAME = range(2)

# IMPORTANT: Replace with your actual authorized user ID(s)
AUTHORIZED_USER_IDS = [7927314662, 7686927258]

def download_and_encode_image(img_url: str) -> str:
    """
    Downloads an image from a URL and returns it as a base64 data URI.
    """
    try:
        response = requests.get(img_url, timeout=10)
        response.raise_for_status()
        
        # Determine content type
        content_type = response.headers.get('Content-Type', 'image/png')
        
        # Encode image to base64
        image_data = base64.b64encode(response.content).decode('utf-8')
        
        # Return as data URI
        return f"data:{content_type};base64,{image_data}"
    except Exception as e:
        logger.error(f"Failed to download image from {img_url}: {e}")
        return img_url  # Return original URL if download fails

def process_html_content(html_string: str) -> str:
    """
    Processes HTML content, converting all images to base64 data URIs.
    """
    if not html_string:
        return ""
    
    soup = BeautifulSoup(html_string, 'html.parser')
    
    for img_tag in soup.find_all('img'):
        src = img_tag.get('src')
        if src:
            # Fix protocol-relative URLs
            if src.startswith('//'):
                src = f"https:{src}"
                
            # Download and convert to base64 if it's an HTTP(S) URL
            if src.startswith('http://') or src.startswith('https://'):
                logger.info(f"Downloading and encoding image: {src}")
                data_uri = download_and_encode_image(src)
                img_tag['src'] = data_uri
            
    return str(soup)

def fetch_locale_json_from_api(nid: str):
    """
    Fetches question data from the API for a given NID.
    """
    url = f"https://learn.aakashitutor.com/quiz/{nid}/getlocalequestions"
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        raw_data = response.json()
        logger.info(f"Raw API response for NID {nid}: {json.dumps(raw_data, indent=2)}")

        processed_questions = []

        def is_valid_question_object(data_obj):
            return isinstance(data_obj, dict) and \
                   "body" in data_obj and \
                   "alternatives" in data_obj and \
                   isinstance(data_obj.get("alternatives"), list)

        if isinstance(raw_data, dict):
            for question_nid_key, question_data_by_language in raw_data.items():
                if isinstance(question_data_by_language, dict):
                    english_version = question_data_by_language.get("843")
                    
                    if is_valid_question_object(english_version):
                        languages = english_version.get("language", [])
                        language_names = english_version.get("language_names", [])
                        
                        if "843" in languages or "English" in language_names:
                            processed_questions.append({
                                "body": english_version.get("body", ""),
                                "alternatives": english_version.get("alternatives", [])
                            })
                            logger.info(f"NID {nid}: Successfully extracted English question for internal NID {question_nid_key}.")
                        else:
                            logger.warning(f"NID {nid}: Found data under {question_nid_key} -> '843', but it's not explicitly marked as English.")
                    else:
                        logger.warning(f"NID {nid}: No valid question object found under {question_nid_key} -> '843'.")
                else:
                    logger.warning(f"NID {nid}: Content under top-level key '{question_nid_key}' is not a dictionary.")
        else:
            logger.error(f"NID {nid}: Raw API response is not a dictionary. Type: {type(raw_data)}")
            return None

        if not processed_questions:
            logger.error(f"NID {nid}: No English questions extracted from the API response.")

        return processed_questions
    except requests.exceptions.RequestException as req_err:
        logger.error(f"Network error fetching questions for NID {nid}: {req_err}")
        return None
    except json.JSONDecodeError as json_err:
        logger.error(f"JSON decode error for NID {nid}: {json_err}")
        return None
    except Exception as e:
        logger.error(f"An unexpected error occurred for NID {nid}: {e}", exc_info=True)
        return None

def fetch_test_title_and_description(nid: str):
    """
    Fetches the test title and description from the API for a given NID.
    """
    url = f"https://learn.aakashitutor.com/api/getquizfromid?nid={nid}"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, list) and data:
            item = data[0]
            title = item.get("title", f"Test {nid}").strip()
            description = item.get("description", "").strip()
            return title, description
        return f"Test {nid}", ""
    except requests.exceptions.RequestException as e:
        logger.error(f"Network error fetching title for NID {nid}: {e}")
        return f"Test {nid}", ""
    except json.JSONDecodeError:
        logger.error(f"JSON decode error for title for NID {nid}")
        return f"Test {nid}", ""
    except Exception as e:
        logger.error(f"An unexpected error occurred fetching title for NID {nid}: {e}")
        return f"Test {nid}", ""

def generate_html_with_answers(data, test_title, syllabus):
    """Generate HTML with questions and highlighted correct answers - Simple Black & White Theme"""
    html = f"""
<!DOCTYPE html>
<html>
<head>
<meta charset='UTF-8'>
<title>Question Paper</title>
<style>
    * {{
        margin: 0;
        padding: 0;
        box-sizing: border-box;
    }}
    
    body {{
        font-family: Arial, sans-serif;
        background: #ffffff;
        color: #000000;
        padding: 40px;
        line-height: 1.6;
        max-width: 900px;
        margin: 0 auto;
    }}
    
    .question-block {{
        margin-bottom: 40px;
        padding: 30px;
        border: 2px solid #000000;
        background: #ffffff;
        page-break-inside: avoid;
    }}
    
    .question-number {{
        font-size: 18px;
        font-weight: bold;
        margin-bottom: 15px;
        color: #000000;
    }}
    
    .question-text {{
        font-size: 16px;
        margin-bottom: 20px;
        color: #000000;
        line-height: 1.8;
    }}
    
    .question-text img {{
        max-width: 100%;
        height: auto;
        display: block;
        margin: 15px 0;
    }}
    
    .answer-row {{
        padding: 15px;
        margin-bottom: 10px;
        border: 1px solid #000000;
        background: #ffffff;
        display: flex;
        align-items: center;
        gap: 15px;
        page-break-inside: avoid;
    }}
    
    .answer-row.correct {{
        background: #e0e0e0;
        border: 2px solid #000000;
        font-weight: bold;
    }}
    
    .option-letter {{
        display: inline-block;
        width: 35px;
        height: 35px;
        background: #000000;
        color: #ffffff;
        text-align: center;
        line-height: 35px;
        font-weight: bold;
        border-radius: 50%;
        flex-shrink: 0;
    }}
    
    .answer-row.correct .option-letter {{
        background: #000000;
        color: #ffffff;
    }}
    
    .option-text {{
        font-size: 15px;
        color: #000000;
        flex: 1;
    }}
    
    .option-text img {{
        max-width: 100%;
        height: auto;
        display: block;
        margin: 10px 0;
    }}
    
    @media print {{
        body {{
            padding: 20px;
        }}
        
        .question-block {{
            page-break-inside: avoid;
        }}
        
        .answer-row {{
            page-break-inside: avoid;
        }}
    }}
</style>
</head>
<body>
    """
    
    for idx, q in enumerate(data, 1):
        processed_body = process_html_content(q['body'])
        
        html += f"""
    <div class='question-block'>
        <div class='question-number'>Question {idx}</div>
        <div class='question-text'>{processed_body}</div>
        """
        
        # Process options with correct answer marking
        alternatives = q["alternatives"][:4]
        labels = ["A", "B", "C", "D"]
        
        for opt_idx, opt in enumerate(alternatives):
            if opt_idx < len(labels):
                label = labels[opt_idx]
                is_correct = str(opt.get("score_if_chosen")) == "1"
                row_class = "answer-row correct" if is_correct else "answer-row"
                processed_answer = process_html_content(opt['answer'])
                html += f"""
        <div class='{row_class}'>
            <span class='option-letter'>{label}</span>
            <span class='option-text'>{processed_answer}</span>
        </div>
                """
        
        html += """
    </div>
        """
    
    html += """
</body>
</html>
    """
    
    return html

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the conversation and asks for NID directly."""
    if update.effective_user.id not in AUTHORIZED_USER_IDS:
        await update.message.reply_text("❌ Access Denied. You are not authorized to use this bot.")
        logger.warning(f"Unauthorized access attempt by user ID: {update.effective_user.id}")
        return ConversationHandler.END

    await update.message.reply_text("📢 Please send the NID (Numerical ID) for the test you want to extract:")
    return ASK_NID

async def handle_nid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives the NID from the user."""
    nid = update.message.text.strip()
    if not nid.isdigit():
        await update.message.reply_text("❌ Invalid NID. Please send a numerical ID.")
        return ASK_NID

    context.user_data['nid'] = nid
    await update.message.reply_text("📝 Great! Now, please enter the desired name for the file (e.g., 'Physics_Test_1'):")
    return ASK_PDF_NAME

async def handle_pdf_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives the desired file name and generates the question paper."""
    nid = context.user_data.get('nid')
    if not nid:
        await update.message.reply_text("An error occurred: NID not found. Please start over with /start.")
        return ConversationHandler.END

    raw_name = update.message.text.strip()
    name = re.sub(r'[\\/*?:"<>|]', "_", raw_name)
    if not name:
        name = f"Extracted_Test_{nid}"

    await update.message.reply_text("⏳ Processing your request... Downloading images and generating question paper...")

    title, desc = fetch_test_title_and_description(nid)
    data = fetch_locale_json_from_api(nid)    

    if not data:
        await update.message.reply_text(
            "❌ Extraction failed: No questions found for the specified NID, or an API error occurred.\n"
            "Please verify the NID is correct and contains accessible English data."
        )
        return ConversationHandler.END

    html_content = generate_html_with_answers(data, title, desc)
    filename = f"{name}_QP_with_Answer_Key.html"

    try:
        await update.message.reply_document(
            document=BytesIO(html_content.encode("utf-8")),    
            filename=filename,
            caption="📄 Question Paper with Answer Key - Open in a web browser to view the content with embedded images."
        )
        await update.message.reply_text("🎉 Question paper with answer key has been generated and sent successfully!")
    except Exception as e:
        logger.error(f"Error sending {filename} for NID {nid}: {e}")
        await update.message.reply_text(f"❌ An error occurred while sending the file: {e}")
    
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels the current conversation."""
    await update.message.reply_text("Operation cancelled. You can start a new request with /start.")
    return ConversationHandler.END

def main():
    """
    Main function to set up and run the Telegram bot.
    """
    # Get your bot token from environment variable or replace directly
    # It's highly recommended to use environment variables for sensitive information
    # like bot tokens (e.g., BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN"))
    BOT_TOKEN = "7569082224:AAHKhpg_MfaMbdXtLYiD2nVlVWXPN9kz4JU"  # Replace with your actual bot token

    if not BOT_TOKEN:
        logger.critical("Bot token not found. Please set the BOT_TOKEN variable or environment variable.")
        return

    try:
        # Build the application
        app = ApplicationBuilder().token(BOT_TOKEN).build()

        # Define the conversation handler with all states
        conv_handler = ConversationHandler(
            entry_points=[CommandHandler("start", start)],
            states={
                ASK_NID: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_nid)],
                ASK_PDF_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_pdf_name)],
            },
            fallbacks=[CommandHandler("cancel", cancel)]
        )
        
        # Add the conversation handler to the application
        app.add_handler(conv_handler)

        logger.info("Bot started successfully and is now polling for updates...")
        logger.info(f"Authorized users: {AUTHORIZED_USER_IDS}")
        
        # Start polling with proper error handling
        app.run_polling(poll_interval=1.0, allowed_updates=Update.ALL_TYPES)
        
    except Conflict:
        logger.error("Conflict error: Another bot instance with the same token is already running.")
        logger.error("Please stop the other instance before starting this one.")
    except Exception as e:
        logger.critical(f"An unhandled error occurred while starting the bot: {e}", exc_info=True)

if __name__ == "__main__":
    main()
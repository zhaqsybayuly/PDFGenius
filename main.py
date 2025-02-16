import os
import json
import logging
import tempfile
import textwrap
import asyncio
import shutil
import re
from io import BytesIO
from typing import Dict, Any, List
from datetime import datetime

import fitz  # PyMuPDF
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Message
)
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters
)
from PIL import Image
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.utils import ImageReader
from PyPDF2 import PdfMerger

# --- Лог конфигурациясы ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Конфигурация ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = "5316060523"  # Өз админ ID-іңізді енгізіңіз
STATS_FILE = "stats.json"
USERS_FILE = "users.json"

# --- Тілдер ---
LANGUAGES = ["en", "kz", "ru", "uz", "tr", "ua"]
DEFAULT_LANG = "en"

# --- Conversation күйлері ---
STATE_ACCUMULATE = 1
GET_FILENAME_DECISION = 2   # Inline: Yes/No таңдауды сұрау
GET_FILENAME_INPUT = 3      # Файл атауын енгізу
# Admin панелі үшін conversation қолданбаймыз

# --- Шектеулер ---
MAX_USER_FILE_SIZE = 20 * 1024 * 1024  # 20 MB
MAX_OUTPUT_PDF_SIZE = 50 * 1024 * 1024   # 50 MB

# --- Глобалды деректер ---
user_data: Dict[int, Dict[str, Any]] = {}

# --- ReportLab қаріптері ---
# Эмодзилерді қолдау үшін Symbola.ttf пайдалануға тырысамыз
try:
    pdfmetrics.registerFont(TTFont('EmojiFont', 'Symbola.ttf'))
except Exception as e:
    logger.warning("Symbola.ttf not found, using NotoSans as fallback for EmojiFont")
    pdfmetrics.registerFont(TTFont('EmojiFont', 'fonts/NotoSans.ttf'))

# --- Файл атауын өңдеу (sanitize) ---
def sanitize_filename(name: str) -> str:
    """Атауды төменгі регистрге айналдырып, бос орындарды асты сызғышқа ауыстырады және тек рұқсат етілген символдарды қалдырады."""
    name = name.strip().lower().replace(" ", "_")
    name = re.sub(r'[^a-z0-9_\-\.]', '', name)
    if len(name) > 50:
        name = name[:50]
    return name

# --- Аударма және көмекші функциялар ---
def load_translations(lang_code: str) -> Dict[str, str]:
    try:
        with open(f"translations/{lang_code}.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        with open(f"translations/{DEFAULT_LANG}.json", "r", encoding="utf-8") as f:
            return json.load(f)

def get_user_lang(user_id: int) -> str:
    if not os.path.exists(USERS_FILE):
        return DEFAULT_LANG
    try:
        with open(USERS_FILE, "r") as f:
            users = json.load(f)
        return users.get(str(user_id), DEFAULT_LANG)
    except Exception as e:
        logger.error(f"Error reading USERS_FILE: {e}")
        return DEFAULT_LANG

def save_user_lang(user_id: int, lang_code: str):
    try:
        if os.path.exists(USERS_FILE):
            with open(USERS_FILE, "r") as f:
                users = json.load(f)
        else:
            users = {}
    except Exception:
        users = {}
    users[str(user_id)] = lang_code
    with open(USERS_FILE, "w") as f:
        json.dump(users, f)

def save_stats(action: str):
    stats = {"total": 0, "items": 0, "pdf_count": 0}
    try:
        if os.path.exists(STATS_FILE):
            with open(STATS_FILE, "r") as f:
                stats = json.load(f)
    except Exception:
        pass
    stats["total"] += 1
    if action == "item":
        stats["items"] += 1
    elif action == "pdf":
        stats["pdf_count"] += 1
    with open(STATS_FILE, "w") as f:
        json.dump(stats, f)

def get_all_users() -> List[int]:
    try:
        if os.path.exists(USERS_FILE):
            with open(USERS_FILE, "r") as f:
                users = json.load(f)
            return [int(uid) for uid in users.keys()]
        else:
            return []
    except Exception as e:
        logger.error(f"Error loading users: {e}")
        return []

# --- PDF өңдеу функциялары ---
def convert_pdf_item_to_images(bio: BytesIO) -> List[BytesIO]:
    """PyMuPDF арқылы PDF-тің әр бетінің суретін PNG форматында шығарып, тізім ретінде қайтарады."""
    images = []
    try:
        doc = fitz.open(stream=bio.getvalue(), filetype="pdf")
        for page_num in range(doc.page_count):
            page = doc.load_page(page_num)
            pix = page.get_pixmap()
            img_data = BytesIO(pix.tobytes("png"))
            images.append(img_data)
    except Exception as e:
        logger.error(f"Error converting PDF to images: {e}")
    return images

def generate_item_pdf(item: Dict[str, Any]) -> BytesIO:
    """Мәтін немесе сурет элементін жеке PDF бетіне айналдырады."""
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    # EmojiFont-ты қолданамыз (Symbola немесе NotoSans fallback)
    c.setFont("EmojiFont", 12)
    width, height = A4
    if item["type"] == "text":
        wrapped_text = []
        for line in item["content"].split("\n"):
            wrapped_text.extend(textwrap.wrap(line, width=80))
        y_position = height - 50
        for line in wrapped_text:
            c.drawString(40, y_position, line)
            y_position -= 20
            if y_position < 50:
                c.showPage()
                c.setFont("EmojiFont", 12)
                y_position = height - 50
        c.showPage()
    elif item["type"] == "photo":
        try:
            item["content"].seek(0)
            img = Image.open(item["content"])
            # Суретті кішірейту (thumbnail) қолданамыз
            max_size = (int(A4[0] - 80), int(A4[1] - 80))
            img.thumbnail(max_size, Image.LANCZOS)
            img_width, img_height = img.size
            x = (A4[0] - img_width) / 2
            y = (A4[1] - img_height) / 2
            c.drawImage(ImageReader(img), x, y, width=img_width, height=img_height)
        except Exception as e:
            c.drawString(40, A4[1] / 2, f"Error displaying image: {e}")
        c.showPage()
    c.save()
    buffer.seek(0)
    return buffer

def merge_pdfs(pdf_list: List[BytesIO]) -> BytesIO:
    """PDF файлдарын біріктіріп, біртұтас PDF-ке айналдырады."""
    merger = PdfMerger()
    for pdf_io in pdf_list:
        try:
            merger.append(pdf_io)
        except Exception as e:
            logger.error(f"Skipping invalid PDF file: {e}")
    output_buffer = BytesIO()
    merger.write(output_buffer)
    merger.close()
    output_buffer.seek(0)
    return output_buffer

async def loading_animation(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, stop_event: asyncio.Event):
    """PDF генерациясы кезінде хабарламада тек "⌛" эмодзи тұрақты көрсетіледі."""
    while not stop_event.is_set():
        await asyncio.sleep(1)

# --- Helper: effective message ---
def get_effective_message(update: Update) -> Message:
    """update.message болмаса, update.callback_query.message пайдаланылады."""
    return update.message if update.message is not None else update.callback_query.message

# --- Пайдаланушы интерфейсі ---
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang_code = get_user_lang(user_id)
    trans = load_translations(lang_code)
    save_user_lang(user_id, lang_code)  # /start кезінде тілді сақтаймыз
    user_data[user_id] = {"items": [], "instruction_sent": False}
    await update.message.reply_text(trans["welcome"], reply_markup=language_keyboard())

def language_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🇬🇧 English", callback_data="lang_en"),
         InlineKeyboardButton("🇰🇿 Қазақ", callback_data="lang_kz"),
         InlineKeyboardButton("🇷🇺 Русский", callback_data="lang_ru")],
        [InlineKeyboardButton("🇺🇿 O'zbek", callback_data="lang_uz"),
         InlineKeyboardButton("🇹🇷 Türkçe", callback_data="lang_tr"),
         InlineKeyboardButton("🇺🇦 Українська", callback_data="lang_ua")]
    ])

async def change_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang_code = query.data.split("_")[1]
    user_id = query.from_user.id
    save_user_lang(user_id, lang_code)
    trans = load_translations(lang_code)
    await query.edit_message_text(trans["lang_selected"])
    await send_initial_instruction(update, context, lang_code)

async def send_initial_instruction(update: Update, context: ContextTypes.DEFAULT_TYPE, lang_code: str):
    trans = load_translations(lang_code)
    keyboard = ReplyKeyboardMarkup([[trans["btn_change_lang"], trans["btn_help"]]], resize_keyboard=True)
    text = trans["instruction_initial"]
    msg = get_effective_message(update)
    await msg.reply_text(text, reply_markup=keyboard)

async def accumulate_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang_code = get_user_lang(user_id)
    trans = load_translations(lang_code)
    msg_text = update.message.text.strip() if update.message.text else ""
    if msg_text == trans["btn_convert_pdf"]:
        # Файл атауын сұрамастан, автоматты түрде PDF жасаймыз
        return await convert_pdf_handler(update, context)
    if msg_text == trans["btn_change_lang"]:
        return await trigger_change_lang(update, context)
    if msg_text == trans["btn_help"]:
        return await trigger_help(update, context)
    await process_incoming_item(update, context)
    if not user_data[user_id].get("instruction_sent", False):
        keyboard = ReplyKeyboardMarkup([[trans["btn_convert_pdf"]],
                                         [trans["btn_change_lang"], trans["btn_help"]]],
                                        resize_keyboard=True)
        await update.effective_chat.send_message(trans["instruction_accumulated"], reply_markup=keyboard)
        user_data[user_id]["instruction_sent"] = True
    return STATE_ACCUMULATE

async def process_incoming_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if "items" not in user_data.get(user_id, {}):
        user_data[user_id] = {"items": [], "instruction_sent": False}
    if update.message.text and not update.message.photo and not update.message.document:
        item = {"type": "text", "content": update.message.text}
        user_data[user_id]["items"].append(item)
    elif update.message.photo:
        photo_file = await update.message.photo[-1].get_file()
        bio = BytesIO()
        await photo_file.download_to_memory(bio)
        bio.seek(0)
        item = {"type": "photo", "content": bio}
        user_data[user_id]["items"].append(item)
    elif update.message.document:
        doc = update.message.document
        if doc.file_size and doc.file_size > MAX_USER_FILE_SIZE:
            await update.message.reply_text("Файлдың өлшемі 20 MB-тан аспауы керек.")
            return
        filename = doc.file_name.lower()
        ext = os.path.splitext(filename)[1]
        file_obj = await doc.get_file()
        bio = BytesIO()
        await file_obj.download_to_memory(bio)
        bio.seek(0)
        if ext in [".jpg", ".jpeg", ".png", ".gif"]:
            item = {"type": "photo", "content": bio}
        elif ext == ".pdf":
            images = convert_pdf_item_to_images(bio)
            if images:
                for img in images:
                    item = {"type": "photo", "content": img}
                    user_data[user_id]["items"].append(item)
                return
            else:
                item = {"type": "text", "content": f"Файл қосылды: {doc.file_name}"}
        else:
            item = {"type": "text", "content": f"Файл қосылды: {doc.file_name}"}
        user_data[user_id]["items"].append(item)
    save_stats("item")

# --- Файл атауын сұрау диалогы (inline) ---
async def ask_filename(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang_code = get_user_lang(user_id)
    trans = load_translations(lang_code)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(trans["filename_yes"], callback_data="filename_yes"),
         InlineKeyboardButton(trans["filename_no"], callback_data="filename_no")]
    ])
    await update.message.reply_text(trans["ask_filename"], reply_markup=keyboard)
    return GET_FILENAME_DECISION

async def filename_decision_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    lang_code = get_user_lang(user_id)
    trans = load_translations(lang_code)
    if query.data == "filename_yes":
        await query.edit_message_text(trans["enter_filename"])
        return GET_FILENAME_INPUT
    elif query.data == "filename_no":
        return await perform_pdf_conversion(update, context, None)
    else:
        await query.edit_message_text("Please choose one of the options: " + trans["filename_yes"] + " / " + trans["filename_no"])
        return GET_FILENAME_DECISION

async def filename_input_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file_name = update.message.text.strip()
    file_name = sanitize_filename(file_name)
    return await perform_pdf_conversion(update, context, file_name)

async def perform_pdf_conversion(update: Update, context: ContextTypes.DEFAULT_TYPE, file_name: str):
    return await convert_pdf_handler_with_name(update, context, file_name)

async def convert_pdf_handler_with_name(update: Update, context: ContextTypes.DEFAULT_TYPE, file_name: str):
    msg = get_effective_message(update)
    user_id = update.effective_user.id
    lang_code = get_user_lang(user_id)
    trans = load_translations(lang_code)
    items = user_data.get(user_id, {}).get("items", [])
    if not items:
        await msg.reply_text(trans["no_items_error"])
        return STATE_ACCUMULATE

    loading_msg = await msg.reply_text("⌛")
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    anim_task = loop.create_task(loading_animation(context, msg.chat.id, loading_msg.message_id, stop_event))

    pdf_list = []
    for item in items:
        try:
            if item["type"] in ["text", "photo"]:
                pdf_file = generate_item_pdf(item)
                pdf_list.append(pdf_file)
        except Exception as e:
            logger.error(f"Error generating PDF for item: {e}")
    try:
        merged_pdf = await loop.run_in_executor(None, merge_pdfs, pdf_list)
    except Exception as e:
        logger.error(f"Error merging PDFs: {e}")
        merged_pdf = None

    stop_event.set()
    try:
        await context.bot.delete_message(chat_id=msg.chat.id, message_id=loading_msg.message_id)
    except Exception as e:
        logger.error(f"Error deleting loading message: {e}")

    if not merged_pdf:
        await msg.reply_text("PDF генерациясында қате шықты, қайта көріңіз.")
        return STATE_ACCUMULATE

    merged_pdf.seek(0, os.SEEK_END)
    pdf_size = merged_pdf.tell()
    merged_pdf.seek(0)
    if pdf_size > MAX_OUTPUT_PDF_SIZE:
        await msg.reply_text("Жасалған PDF файлдың өлшемі 50 MB-тан көп, материалдарды азайтып көріңіз.")
        return STATE_ACCUMULATE

    file_name = f"combined_{datetime.now().strftime('%Y%m%d%H%M%S')}.pdf"

    await msg.reply_document(
        document=merged_pdf,
        filename=file_name,
        caption=trans["pdf_ready"]
    )
    save_stats("pdf")
    user_data[user_id]["items"] = []
    user_data[user_id]["instruction_sent"] = False
    await msg.reply_text(
        trans["instruction_initial"],
        reply_markup=ReplyKeyboardMarkup([[trans["btn_change_lang"], trans["btn_help"]]], resize_keyboard=True)
    )
    return STATE_ACCUMULATE

async def convert_pdf_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await convert_pdf_handler_with_name(update, context, None)

async def trigger_change_lang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang_code = get_user_lang(user_id)
    trans = load_translations(lang_code)
    await update.message.reply_text(trans["choose_language"], reply_markup=language_keyboard())
    return STATE_ACCUMULATE

async def trigger_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang_code = get_user_lang(user_id)
    trans = load_translations(lang_code)
    await update.message.reply_text(trans["help_text"])
    return STATE_ACCUMULATE

# --- Админ панелі ---
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if str(user_id) != ADMIN_ID:
        await update.message.reply_text("Сіз админ емессіз.")
        return
    # Admin командасы әр шақырылған сайын статистиканы шығарады
    await show_admin_stats(update, context)

async def show_admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        with open(STATS_FILE, "r") as f:
            stats = json.load(f)
    except Exception:
        stats = {"total": 0, "items": 0, "pdf_count": 0}
    try:
        with open(USERS_FILE, "r") as f:
            users = json.load(f)
    except Exception:
        users = {}
    total_users = len(users)  # Егер /start командасы пайдаланушыны сақтаса, бұл мән жаңартылады
    stat_text = (
        f"📊 Статистика:\n"
        f"• Жалпы әрекет саны: {stats.get('total', 0)}\n"
        f"• Жинақталған элементтер: {stats.get('items', 0)}\n"
        f"• PDF файлдар саны: {stats.get('pdf_count', 0)}\n"
        f"• Пайдаланушылар саны: {total_users}\n"
    )
    await update.message.reply_text(stat_text)

# --- Фоллбэк ---
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in user_data:
        del user_data[user_id]
    await update.message.reply_text("Операция тоқтатылды. /start арқылы қайта бастаңыз.")
    return STATE_ACCUMULATE

# --- Негізгі функция ---
if __name__ == "__main__":
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start_handler)],
        states={
            STATE_ACCUMULATE: [
                MessageHandler(filters.ALL & ~filters.COMMAND, accumulate_handler)
            ],
            GET_FILENAME_DECISION: [
                CallbackQueryHandler(filename_decision_callback, pattern="^filename_")
            ],
            GET_FILENAME_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, filename_input_handler)
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    application.add_handler(conv_handler)

    application.add_handler(CommandHandler("admin", admin_panel))

    application.add_handler(CallbackQueryHandler(change_language, pattern="^lang_"))
    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, accumulate_handler))

    if os.environ.get("WEBHOOK_URL"):
        application.run_webhook(
            listen="0.0.0.0",
            port=int(os.environ.get("PORT", 10000)),
            webhook_url=os.environ.get("WEBHOOK_URL")
        )
    else:
        application.run_polling()

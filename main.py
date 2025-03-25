import os
import json
import logging
import textwrap
import asyncio
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

# --- Logging configuration ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Configuration ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = "5316060523"  # Өз админ ID-іңізді енгізіңіз
STATS_FILE = "stats.json"
USERS_FILE = "users.json"

# --- Languages ---
LANGUAGES = ["en", "kz", "ru", "uz", "tr", "ua"]
DEFAULT_LANG = "en"

# --- Conversation states ---
STATE_ACCUMULATE = 1
ASK_FILENAME = 2
GET_FILENAME_INPUT = 3

# --- Limits ---
MAX_USER_FILE_SIZE = 20 * 1024 * 1024   # 20 MB
MAX_OUTPUT_PDF_SIZE = 50 * 1024 * 1024  # 50 MB

# --- Global data ---
user_data: Dict[int, Dict[str, Any]] = {}

# --- Register fonts ---
try:
    pdfmetrics.registerFont(TTFont('EmojiFont', 'fonts/Symbola.ttf'))
except Exception as e:
    logger.warning("Symbola.ttf not found, using NotoSans as fallback")
    pdfmetrics.registerFont(TTFont('EmojiFont', 'fonts/NotoSans.ttf'))

# --- Sanitize filename ---
def sanitize_filename(name: str) -> str:
    name = name.strip().lower().replace(" ", "_")
    name = re.sub(r'[^a-z0-9_\-\.]', '', name)
    if len(name) > 50:
        name = name[:50]
    return name

# --- Translation and helper functions ---
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

# --- PDF processing functions ---
def convert_pdf_item_to_images(bio: BytesIO) -> List[BytesIO]:
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
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
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
            if img.mode != "RGB":
                img = img.convert("RGB")
            margin = 40
            available_width = A4[0] - 2 * margin
            available_height = A4[1] - 2 * margin
            img_width, img_height = img.size
            scale = min(1.0, available_width / img_width, available_height / img_height)
            new_width = int(img_width * scale)
            new_height = int(img_height * scale)
            x = (A4[0] - new_width) / 2
            y = (A4[1] - new_height) / 2
            compressed = BytesIO()
            if scale < 1.0:
                img_resized = img.resize((new_width, new_height), Image.LANCZOS)
            else:
                img_resized = img
            img_resized.save(compressed, format="JPEG", quality=90, optimize=True)
            compressed.seek(0)
            comp_img = Image.open(compressed)
            c.drawImage(ImageReader(comp_img), x, y, width=new_width, height=new_height)
        except Exception as e:
            c.drawString(40, height/2, f"😢 {e}")
        c.showPage()
    c.save()
    buffer.seek(0)
    return buffer

async def merge_pdfs(pdf_list: List[BytesIO]) -> BytesIO:
    loop = asyncio.get_running_loop()
    merger = PdfMerger()
    for pdf_io in pdf_list:
        try:
            merger.append(pdf_io)
        except Exception as e:
            logger.error(f"Error merging PDF file: {e}")
    output_buffer = BytesIO()
    await loop.run_in_executor(None, merger.write, output_buffer)
    merger.close()
    output_buffer.seek(0)
    return output_buffer

def get_effective_message(update: Update) -> Message:
    return update.message if update.message is not None else update.callback_query.message

# --- User Interface Functions ---
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang_code = get_user_lang(user_id)
    trans = load_translations(lang_code)
    save_user_lang(user_id, lang_code)
    user_data[user_id] = {"items": [], "instruction_sent": False}
    await update.message.reply_text(f"👋 {trans['welcome']}", reply_markup=language_keyboard())

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
    await query.edit_message_text(f"✅ {trans['lang_selected']}")
    await send_initial_instruction(update, context, lang_code)

async def send_initial_instruction(update: Update, context: ContextTypes.DEFAULT_TYPE, lang_code: str):
    trans = load_translations(lang_code)
    keyboard = ReplyKeyboardMarkup(
        [[f"📄 {trans['btn_convert_pdf']}"],
         [f"🌐 {trans['btn_change_lang']}", f"❓ {trans['btn_help']}"]],
        resize_keyboard=True
    )
    text = trans["instruction_initial"]
    msg = get_effective_message(update)
    await msg.reply_text(text, reply_markup=keyboard)

async def accumulate_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang_code = get_user_lang(user_id)
    trans = load_translations(lang_code)
    msg_text = update.message.text.strip() if update.message.text else ""
    if msg_text == f"📄 {trans['btn_convert_pdf']}":
        items = user_data.get(user_id, {}).get("items", [])
        if not items:
            await update.message.reply_text("⚠️ " + trans["no_items_error"])
            return STATE_ACCUMULATE
        keyboard = ReplyKeyboardMarkup(
            [["✅ Иә"], ["❌ Жоқ"]],
            resize_keyboard=True,
            one_time_keyboard=True
        )
        await update.message.reply_text("Задать название файла?", reply_markup=keyboard)
        return ASK_FILENAME
    if msg_text == f"🌐 {trans['btn_change_lang']}":
        return await trigger_change_lang(update, context)
    if msg_text == f"❓ {trans['btn_help']}":
        return await trigger_help(update, context)
    await process_incoming_item(update, context)
    if not user_data[user_id].get("instruction_sent", False):
        await send_initial_instruction(update, context, lang_code)
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
            await update.message.reply_text("⚠️ Файлдың өлшемі 20 MB-тан аспауы керек.")
            return
        ext = os.path.splitext(doc.file_name)[1].lower()
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
                item = {"type": "text", "content": f"📎 Файл қосылды: {doc.file_name}"}
        else:
            item = {"type": "text", "content": f"📎 Файл қосылды: {doc.file_name}"}
        user_data[user_id]["items"].append(item)
    save_stats("item")

async def ask_filename_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang_code = get_user_lang(user_id)
    trans = load_translations(lang_code)
    choice = update.message.text.strip()
    if choice == "✅ Иә":
        await update.message.reply_text("Введите имя файла:")
        return GET_FILENAME_INPUT
    elif choice == "❌ Жоқ":
        await update.message.reply_text("⌛ Өңделуде...")
        await convert_pdf_handler_with_name(update, context, None)
        return STATE_ACCUMULATE
    else:
        await update.message.reply_text("⚠️ 'Иә' немесе 'Жоқ' таңдаңыз:")
        return ASK_FILENAME

async def filename_input_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text_input = update.message.text.strip()
    new_name = sanitize_filename(text_input) + ".pdf"
    await update.message.reply_text("⌛ Өңделуде...")
    await convert_pdf_handler_with_name(update, context, new_name)
    return STATE_ACCUMULATE

async def convert_pdf_handler_with_name(update: Update, context: ContextTypes.DEFAULT_TYPE, file_name: str):
    msg = get_effective_message(update)
    user_id = update.effective_user.id
    lang_code = get_user_lang(user_id)
    trans = load_translations(lang_code)
    items = user_data.get(user_id, {}).get("items", [])
    if not items:
        await msg.reply_text("⚠️ " + trans["no_items_error"])
        return STATE_ACCUMULATE

    pdf_list = []
    for item in items:
        try:
            pdf_file = generate_item_pdf(item)
            pdf_list.append(pdf_file)
        except Exception as e:
            logger.error(f"❌ PDF жасау қатесі: {e}")

    try:
        merged_pdf = await merge_pdfs(pdf_list)
    except Exception as e:
        logger.error(f"❌ PDF біріктіру қатесі: {e}")
        merged_pdf = None

    if not merged_pdf:
        await msg.reply_text("❌ PDF генерациясында қате шықты, қайта көріңіз.")
        return STATE_ACCUMULATE

    merged_pdf.seek(0, os.SEEK_END)
    pdf_size = merged_pdf.tell()
    merged_pdf.seek(0)
    if pdf_size > MAX_OUTPUT_PDF_SIZE:
        await msg.reply_text("⚠️ Жасалған PDF тым үлкен, материалдарды азайтыңыз.")
        return STATE_ACCUMULATE

    if not file_name:
        file_name = f"combined_{datetime.now().strftime('%Y%m%d%H%M%S')}.pdf"

    await msg.reply_document(
        document=merged_pdf,
        filename=file_name,
        caption=f"🎉 {trans['pdf_ready']}"
    )
    save_stats("pdf")
    user_data[user_id]["items"] = []
    user_data[user_id]["instruction_sent"] = False
    await msg.reply_text(
        trans["instruction_initial"],
        reply_markup=ReplyKeyboardMarkup(
            [[f"📄 {trans['btn_convert_pdf']}"],
             [f"🌐 {trans['btn_change_lang']}", f"❓ {trans['btn_help']}"]],
            resize_keyboard=True
        )
    )
    return STATE_ACCUMULATE

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

# --- Admin Panel ---
ADMIN_MENU = 10
ADMIN_BROADCAST = 11
ADMIN_FORWARD = 12

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if str(user_id) != ADMIN_ID:
        await update.message.reply_text("Сіз админ емессіз.")
        return
    trans = load_translations("kz")
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
    total_users = len(users)
    stat_text = (
        f"📊 Статистика:\n"
        f"• Жалпы әрекет саны: {stats.get('total', 0)}\n"
        f"• Жинақталған элементтер: {stats.get('items', 0)}\n"
        f"• PDF файлдар саны: {stats.get('pdf_count', 0)}\n"
        f"• Пайдаланушылар саны: {total_users}\n"
    )
    keyboard = ReplyKeyboardMarkup(
        [["📊 Статистика", "📢 Хабарлама жіберу"],
         ["🔀 Форвард хабарлама", "❌ Жабу"]],
        resize_keyboard=True
    )
    await update.message.reply_text(stat_text, reply_markup=keyboard)

async def admin_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd = update.message.text.strip().lower()
    if cmd == "📊 статистика":
        await show_admin_stats(update, context)
    elif cmd == "📢 хабарлама жіберу":
        await update.message.reply_text("📢 Хабарлама жіберу үшін мәтінді енгізіңіз:")
        context.user_data["admin_action"] = "broadcast"
    elif cmd == "🔀 форвард хабарлама":
        await update.message.reply_text("🔀 Форвардтау үшін хабарламаны енгізіңіз:")
        context.user_data["admin_action"] = "forward"
    elif cmd == "❌ жабу":
        await update.message.reply_text("Админ панелі жабылды.", reply_markup=ReplyKeyboardRemove())
    else:
        if context.user_data.get("admin_action") == "broadcast":
            user_ids = get_all_users()
            sent = 0
            for uid in user_ids:
                try:
                    await context.bot.send_message(chat_id=uid, text=f"[Админ хабарламасы]\n\n{update.message.text}")
                    sent += 1
                except Exception as e:
                    logger.error(f"Хабарлама жіберу қатесі {uid}: {e}")
            await update.message.reply_text(f"Хабарлама {sent} пайдаланушыға жіберілді.")
            context.user_data.pop("admin_action", None)
        elif context.user_data.get("admin_action") == "forward":
            admin_msg: Message = update.message
            user_ids = get_all_users()
            forwarded = 0
            for uid in user_ids:
                try:
                    await context.bot.copy_message(chat_id=uid, from_chat_id=admin_msg.chat.id, message_id=admin_msg.message_id)
                    forwarded += 1
                except Exception as e:
                    logger.error(f"Форвард қатесі {uid}: {e}")
            await update.message.reply_text(f"Хабарлама {forwarded} пайдаланушыға форвардталды.")
            context.user_data.pop("admin_action", None)
        else:
            await update.message.reply_text("Админ бұйрығын дұрыс енгізіңіз.")

admin_conv_handler = ConversationHandler(
    entry_points=[CommandHandler("admin", admin_panel)],
    states={
        ADMIN_MENU: [
            MessageHandler(filters.Regex("^(📊 Статистика|📢 Хабарлама жіберу|🔀 Форвард хабарлама|❌ Жабу)$"), admin_command_handler)
        ],
        ADMIN_BROADCAST: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, admin_command_handler)
        ],
        ADMIN_FORWARD: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, admin_command_handler)
        ]
    },
    fallbacks=[CommandHandler("cancel", admin_command_handler)]
)

# --- Fallback ---
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data.pop(user_id, None)
    await update.message.reply_text("❌ Операция тоқтатылды. /start арқылы қайта бастаңыз.")
    return STATE_ACCUMULATE

# --- Main ---
if __name__ == "__main__":
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start_handler)],
        states={
            STATE_ACCUMULATE: [
                MessageHandler(filters.ALL & ~filters.COMMAND, accumulate_handler)
            ],
            ASK_FILENAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, ask_filename_handler)
            ],
            GET_FILENAME_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, filename_input_handler)
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    application.add_handler(conv_handler)
    application.add_handler(admin_conv_handler)
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

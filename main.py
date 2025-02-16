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
STATE_ACCUMULATE = 1      # Негізгі күй: материалдар жинақталады және мәзір көрсетіледі.
GET_RENAME_FILE = 2       # Файл атауын өзгерту режимі: қайта файл жіберуді сұрау.
GET_FILENAME_INPUT = 3    # Файл атауын енгізу.

# --- Limits ---
MAX_USER_FILE_SIZE = 20 * 1024 * 1024   # 20 MB
MAX_OUTPUT_PDF_SIZE = 50 * 1024 * 1024    # 50 MB

# --- Global data ---
user_data: Dict[int, Dict[str, Any]] = {}

# --- Register fonts ---
try:
    pdfmetrics.registerFont(TTFont('EmojiFont', 'fonts/Symbola.ttf'))
except Exception as e:
    logger.warning("Symbola.ttf not found, using NotoSans as fallback for EmojiFont")
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
            # Есептелген масштабтау: қол жетімді кеңістік = A4 - (2*40)
            margin = 40
            available_width = A4[0] - 2 * margin
            available_height = A4[1] - 2 * margin
            img_width, img_height = img.size
            scale = min(1.0, available_width / img_width, available_height / img_height)
            new_width = int(img_width * scale)
            new_height = int(img_height * scale)
            # Орталыққа орналастыру
            x = (A4[0] - new_width) / 2
            y = (A4[1] - new_height) / 2
            # Суретті JPEG форматында қайта сақтау
            compressed = BytesIO()
            if scale < 1.0:
                img = img.resize((new_width, new_height), Image.LANCZOS)
            # Егер сурет кішірейтілсе сапасы 90, әйтпесе 95
            quality = 90 if scale < 1.0 else 95
            img.save(compressed, format="JPEG", quality=quality, optimize=True)
            compressed.seek(0)
            comp_img = Image.open(compressed)
            c.drawImage(ImageReader(comp_img), x, y, width=new_width, height=new_height)
        except Exception as e:
            c.drawString(40, height / 2, f"😢 {e}")
        c.showPage()
    c.save()
    buffer.seek(0)
    return buffer

def merge_pdfs(pdf_list: List[BytesIO]) -> BytesIO:
    merger = PdfMerger()
    for pdf_io in pdf_list:
        try:
            merger.append(pdf_io)
        except Exception as e:
            logger.error(f"Error merging PDF file: {e}")
    output_buffer = BytesIO()
    merger.write(output_buffer)
    merger.close()
    output_buffer.seek(0)
    return output_buffer

async def loading_animation(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, stop_event: asyncio.Event):
    while not stop_event.is_set():
        await asyncio.sleep(1)

def get_effective_message(update: Update) -> Message:
    return update.message if update.message is not None else update.callback_query.message

# --- User Interface Functions ---
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang_code = get_user_lang(user_id)
    trans = load_translations(lang_code)
    save_user_lang(user_id, lang_code)
    # Инициализация деректері: файл атауын өзгерту күйі тазаланады.
    user_data[user_id] = {"items": [], "instruction_sent": False, "rename_file": None}
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
        [[f"📄 {trans['btn_convert_pdf']}", f"✏️ {trans['btn_change_filename']}"],
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
    # Негізгі мәзір әрекеттері:
    if msg_text == f"📄 {trans['btn_convert_pdf']}":
        return await convert_pdf_handler(update, context)
    if msg_text == f"✏️ {trans['btn_change_filename']}":
        # Файл атауын өзгерту режиміне кіру: тек «↩️ Back» батырмасы көрсетіледі.
        await update.message.reply_text("✏️ Файл атауын өзгерту режимі. Өтінемін, қайтадан файлды жіберіңіз немесе '↩️ Back' деп теріңіз:",
                                        reply_markup=ReplyKeyboardMarkup([["↩️ Back"]], resize_keyboard=True))
        return GET_RENAME_FILE
    if msg_text == f"🌐 {trans['btn_change_lang']}":
        return await trigger_change_lang(update, context)
    if msg_text == f"❓ {trans['btn_help']}":
        return await trigger_help(update, context)
    await process_incoming_item(update, context)
    if not user_data[user_id].get("instruction_sent", False):
        keyboard = ReplyKeyboardMarkup(
            [[f"📄 {trans['btn_convert_pdf']}", f"✏️ {trans['btn_change_filename']}"],
             [f"🌐 {trans['btn_change_lang']}", f"❓ {trans['btn_help']}"]],
            resize_keyboard=True
        )
        await update.effective_chat.send_message(trans["instruction_accumulated"], reply_markup=keyboard)
        user_data[user_id]["instruction_sent"] = True
    return STATE_ACCUMULATE

# Файлдарды жинақтау кезінде, әдеттегі хабарламаларды өңдеу.
async def process_incoming_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if "items" not in user_data.get(user_id, {}):
        user_data[user_id] = {"items": [], "instruction_sent": False, "rename_file": None}
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

# Файл атауын өзгерту режимі: GET_RENAME_FILE – файл жіберуді күту.
async def get_rename_file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if update.message.document or update.message.photo:
        if update.message.document:
            doc = update.message.document
            file_obj = await doc.get_file()
            bio = BytesIO()
            await file_obj.download_to_memory(bio)
            bio.seek(0)
            user_data[user_id]["rename_file"] = {"file": bio, "original_name": doc.file_name}
        elif update.message.photo:
            photo_file = await update.message.photo[-1].get_file()
            bio = BytesIO()
            await photo_file.download_to_memory(bio)
            bio.seek(0)
            # Фотосуретке әдепкі кеңейткіш беріледі.
            user_data[user_id]["rename_file"] = {"file": bio, "original_name": "image.jpg"}
        await update.message.reply_text("✏️ Жаңа файл атауын енгізіңіз (немесе '↩️ Back' деп теріңіз):",
                                        reply_markup=ReplyKeyboardMarkup([["↩️ Back"]], resize_keyboard=True))
        return GET_FILENAME_INPUT
    else:
        await update.message.reply_text("⚠️ Өтінемін, файлды жіберіңіз немесе '↩️ Back' деп теріңіз:",
                                        reply_markup=ReplyKeyboardMarkup([["↩️ Back"]], resize_keyboard=True))
        return GET_RENAME_FILE

# Файл атауын өзгерту режимі: GET_FILENAME_INPUT – жаңа атауды енгізуді күту.
async def filename_input_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text_input = update.message.text.strip()
    if text_input.lower() == "↩️ back":
        await update.message.reply_text("↩️ Артқа қайтылды. Файл атауын өзгерту процесі тоқтатылды.",
                                        reply_markup=ReplyKeyboardRemove())
        user_data[user_id]["rename_file"] = None
        # PDF-ке айналдыру функциясы іске қосылмайды.
        return STATE_ACCUMULATE
    new_name = sanitize_filename(text_input)
    # Егер rename_file белсенді болса, оригиналдың кеңейткішін аламыз.
    if user_data[user_id].get("rename_file"):
        orig = user_data[user_id]["rename_file"].get("original_name", "")
        ext = os.path.splitext(orig)[1] if orig else ".pdf"
    else:
        ext = ".pdf"
    final_name = new_name + ext
    logger.info(f"📄 Жаңа файл атауы енгізілді: {final_name}")
    return await perform_pdf_conversion(update, context, final_name)

async def perform_pdf_conversion(update: Update, context: ContextTypes.DEFAULT_TYPE, file_name: str):
    return await convert_pdf_handler_with_name(update, context, file_name)

async def convert_pdf_handler_with_name(update: Update, context: ContextTypes.DEFAULT_TYPE, file_name: str):
    msg = get_effective_message(update)
    user_id = update.effective_user.id
    lang_code = get_user_lang(user_id)
    trans = load_translations(lang_code)
    # Егер rename_file белсенді болса, тек сол файлды қолданамыз.
    if user_data[user_id].get("rename_file"):
        items = [{"type": "photo", "content": user_data[user_id]["rename_file"]["file"]}]
        user_data[user_id]["rename_file"] = None
    else:
        items = user_data.get(user_id, {}).get("items", [])
    if not items:
        await msg.reply_text("⚠️ " + trans["no_items_error"])
        return STATE_ACCUMULATE

    loading_msg = await msg.reply_text("⌛")
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    task = loop.create_task(loading_animation(context, msg.chat.id, loading_msg.message_id, stop_event))

    pdf_list = []
    for item in items:
        try:
            pdf_file = generate_item_pdf(item)
            pdf_list.append(pdf_file)
        except Exception as e:
            logger.error(f"❌ PDF жасау қатесі: {e}")
    try:
        merged_pdf = await loop.run_in_executor(None, merge_pdfs, pdf_list)
    except Exception as e:
        logger.error(f"❌ PDF біріктіру қатесі: {e}")
        merged_pdf = None

    stop_event.set()
    try:
        await context.bot.delete_message(chat_id=msg.chat.id, message_id=loading_msg.message_id)
    except Exception as e:
        logger.error(f"❌ Жүктеу хабарламасын жою қатесі: {e}")

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
    else:
        if not file_name.lower().endswith(".pdf"):
            file_name += ".pdf"

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
            [[f"📄 {trans['btn_convert_pdf']}", f"✏️ {trans['btn_change_filename']}"],
             [f"🌐 {trans['btn_change_lang']}", f"❓ {trans['btn_help']}"]],
            resize_keyboard=True
        )
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

# --- Admin Panel ---
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

# Админ бұйрықтарын өңдеу (reply keyboard түрінде)
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
            forwarded = 0
            for uid in get_all_users():
                try:
                    await context.bot.copy_message(chat_id=uid, from_chat_id=admin_msg.chat.id, message_id=admin_msg.message_id)
                    forwarded += 1
                except Exception as e:
                    logger.error(f"Форвард қатесі {uid}: {e}")
            await update.message.reply_text(f"Хабарлама {forwarded} пайдаланушыға форвардталды.")
            context.user_data.pop("admin_action", None)
        else:
            await update.message.reply_text("Админ бұйрығын дұрыс енгізіңіз.")

# --- Fallback ---
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data.pop(user_id, None)
    await update.message.reply_text("❌ Операция тоқтатылды. /start арқылы қайта бастаңыз.")
    return STATE_ACCUMULATE

# --- Main ---
if __name__ == "__main__":
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    # Main user conversation handler
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start_handler)],
        states={
            STATE_ACCUMULATE: [
                MessageHandler(filters.ALL & ~filters.COMMAND, accumulate_handler)
            ],
            GET_RENAME_FILE: [
                MessageHandler(filters.ALL & ~filters.COMMAND, get_rename_file_handler)
            ],
            GET_FILENAME_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, filename_input_handler)
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    application.add_handler(conv_handler)

    # Admin conversation using reply keyboard
    application.add_handler(CommandHandler("admin", admin_panel))
    application.add_handler(MessageHandler(filters.Regex("^(📊 Статистика|📢 Хабарлама жіберу|🔀 Форвард хабарлама|❌ Жабу)$"), admin_command_handler))
    application.add_handler(CallbackQueryHandler(change_language, pattern="^lang_"))

    if os.environ.get("WEBHOOK_URL"):
        application.run_webhook(
            listen="0.0.0.0",
            port=int(os.environ.get("PORT", 10000)),
            webhook_url=os.environ.get("WEBHOOK_URL")
        )
    else:
        application.run_polling()

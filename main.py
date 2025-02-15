import os
import json
import logging
from io import BytesIO
from typing import Dict, Any, List
from datetime import datetime
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

# Логтарды қосу (debug үшін)
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Конфигурация ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = "5316060523"  # Өз админ ID-іңізді қойыңыз
STATS_FILE = "stats.json"
USERS_FILE = "users.json"

# --- Тілдер ---
LANGUAGES = ["en", "kz", "ru", "uz", "tr", "ua"]
DEFAULT_LANG = "en"

# --- Conversation күйлері ---
STATE_ACCUMULATE = 1

# Админ панелі conversation күйлері
ADMIN_MENU = 10
ADMIN_BROADCAST = 11
ADMIN_FORWARD = 12

# --- Глобалды деректер ---
# Әрбір пайдаланушының жинақталған элементтерін сақтаймыз
user_data: Dict[int, Dict[str, Any]] = {}

# ReportLab қаріптерін тіркеу (қаріп файлының жолын тексеріңіз!)
pdfmetrics.registerFont(TTFont('NotoSans', 'fonts/NotoSans.ttf'))

# --- Көмекші функциялар ---

def load_translations(lang_code: str) -> Dict[str, str]:
    try:
        with open(f"translations/{lang_code}.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        with open(f"translations/{DEFAULT_LANG}.json", "r", encoding="utf-8") as f:
            return json.load(f)

def get_user_lang(user_id: int) -> str:
    try:
        with open(USERS_FILE, "r") as f:
            users = json.load(f)
        return users.get(str(user_id), DEFAULT_LANG)
    except Exception as e:
        logger.error(f"Error reading USERS_FILE: {e}")
        return DEFAULT_LANG

def save_user_lang(user_id: int, lang_code: str):
    try:
        with open(USERS_FILE, "r") as f:
            users = json.load(f)
    except Exception:
        users = {}
    users[str(user_id)] = lang_code
    with open(USERS_FILE, "w") as f:
        json.dump(users, f)

def save_stats(action: str):
    stats = {"total": 0, "items": 0}
    try:
        with open(STATS_FILE, "r") as f:
            stats = json.load(f)
    except Exception:
        pass
    stats["total"] += 1
    stats["items"] += 1
    with open(STATS_FILE, "w") as f:
        json.dump(stats, f)

def get_all_users() -> List[int]:
    try:
        with open(USERS_FILE, "r") as f:
            users = json.load(f)
        return [int(uid) for uid in users.keys()]
    except Exception as e:
        logger.error(f"Error loading users: {e}")
        return []

# --- Пайдаланушы интерфейсі ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    # Пайдаланушының тілі анықталады, егер жоқ болса DEFAULT_LANG
    lang_code = get_user_lang(user_id)
    trans = load_translations(lang_code)
    # Жаңа сессия үшін жинақталған элементтерді тазалаймыз
    user_data[user_id] = {"items": []}
    # Алғашқы хабарлама: тіл таңдау және нұсқаулық
    await update.message.reply_text(
        trans["welcome"],
        reply_markup=language_keyboard()
    )

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
    keyboard = ReplyKeyboardMarkup(
        [[trans["btn_change_lang"], trans["btn_help"]]],
        resize_keyboard=True
    )
    text = trans["instruction_initial"]
    # Егер update.message жоқ болса (callbackQuery жағдайы)
    target = update.effective_message if update.effective_message else update.message
    await target.reply_text(text, reply_markup=keyboard)

# --- Жинақтау және PDF жасау жүйесі ---
async def accumulate_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang_code = get_user_lang(user_id)
    trans = load_translations(lang_code)
    
    # Егер "PDF-ке айналдыру" батырмасы басылса
    if update.message.text and update.message.text.strip() == trans["btn_convert_pdf"]:
        return await convert_pdf(update, context)
    # Егер "Тіл ауыстыру" немесе "Көмек" батырмасы басылса, тиісті функция шақырылсын
    if update.message.text and update.message.text.strip() == trans["btn_change_lang"]:
        return await trigger_change_lang(update, context)
    if update.message.text and update.message.text.strip() == trans["btn_help"]:
        return await trigger_help(update, context)
    
    # Кірген хабарламаны өңдеу (мәтін, фото, құжат)
    await process_incoming_item(update, context)
    
    # Жиналғаннан кейінгі хабарлама мен батырмалар:
    keyboard = ReplyKeyboardMarkup(
        [[trans["btn_convert_pdf"]],
         [trans["btn_change_lang"], trans["btn_help"]]],
        resize_keyboard=True
    )
    await update.effective_chat.send_message(trans["instruction_accumulated"], reply_markup=keyboard)
    return STATE_ACCUMULATE

async def process_incoming_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Кірген хабарламаны тиісті типке қарай жинаймыз."""
    user_id = update.effective_user.id
    lang_code = get_user_lang(user_id)
    if "items" not in user_data.get(user_id, {}):
        user_data[user_id] = {"items": []}
    
    # Мәтін
    if update.message.text and not update.message.photo and not update.message.document:
        item = {"type": "text", "content": update.message.text}
        user_data[user_id]["items"].append(item)
    # Сурет
    elif update.message.photo:
        photo_file = await update.message.photo[-1].get_file()
        bio = BytesIO()
        await photo_file.download_to_memory(bio)
        bio.seek(0)
        item = {"type": "photo", "content": bio}
        user_data[user_id]["items"].append(item)
    # Құжат
    elif update.message.document:
        doc = update.message.document
        filename = doc.file_name.lower()
        ext = os.path.splitext(filename)[1]
        file_obj = await doc.get_file()
        bio = BytesIO()
        await file_obj.download_to_memory(bio)
        bio.seek(0)
        if ext in [".jpg", ".jpeg", ".png", ".gif"]:
            item = {"type": "photo", "content": bio}
        elif ext == ".txt":
            try:
                content = bio.read().decode("utf-8")
            except Exception:
                content = "Мәтінді оқу мүмкін емес."
            item = {"type": "text", "content": content}
        else:
            item = {"type": "text", "content": f"Файл қосылды: {doc.file_name}"}
        user_data[user_id]["items"].append(item)
    save_stats("item")

async def convert_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Жинақталған барлық элементтерді PDF-ке біріктіріп, жіберу."""
    user_id = update.effective_user.id
    lang_code = get_user_lang(user_id)
    trans = load_translations(lang_code)
    items = user_data.get(user_id, {}).get("items", [])
    if not items:
        await update.message.reply_text(trans["no_items_error"])
        return STATE_ACCUMULATE

    pdf_buffer = BytesIO()
    c = canvas.Canvas(pdf_buffer, pagesize=A4)
    width, height = A4

    for item in items:
        if item["type"] == "text":
            c.setFont("NotoSans", 12)
            text_lines = item["content"].split("\n")
            y_position = height - 50
            for line in text_lines:
                c.drawString(40, y_position, line)
                y_position -= 20
                if y_position < 50:
                    c.showPage()
                    c.setFont("NotoSans", 12)
                    y_position = height - 50
            c.showPage()
        elif item["type"] == "photo":
            try:
                item["content"].seek(0)
                img = Image.open(item["content"])
                img_width, img_height = img.size
                max_width = width - 80
                max_height = height - 80
                scale = min(max_width / img_width, max_height / img_height, 1)
                new_width = img_width * scale
                new_height = img_height * scale
                x = (width - new_width) / 2
                y = (height - new_height) / 2
                c.drawImage(ImageReader(img), x, y, width=new_width, height=new_height)
            except Exception as e:
                c.setFont("NotoSans", 12)
                c.drawString(40, height / 2, f"Суретті шығару мүмкін емес: {e}")
            c.showPage()
    c.save()
    pdf_buffer.seek(0)
    
    filename = f"combined_{datetime.now().strftime('%Y%m%d%H%M%S')}.pdf"
    await update.message.reply_document(
        document=pdf_buffer,
        filename=filename,
        caption=trans["pdf_ready"]
    )
    # Буферді тазалау және бастапқы нұсқа қайта шығару
    user_data[user_id]["items"] = []
    await update.message.reply_text(
        trans["instruction_initial"],
        reply_markup=ReplyKeyboardMarkup([[trans["btn_change_lang"], trans["btn_help"]]], resize_keyboard=True)
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

# --- Жетілдірілген админ панелі ---

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if str(user_id) != ADMIN_ID:
        return
    lang_code = get_user_lang(user_id)
    trans = load_translations(lang_code)
    # Админ мәзірі үшін инлайн батырмалар:
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Хабарлама жіберу", callback_data="admin_broadcast")],
        [InlineKeyboardButton("🔀 Форвард хабарлама", callback_data="admin_forward")],
        [InlineKeyboardButton("📊 Толық статистика", callback_data="admin_stats")],
        [InlineKeyboardButton("❌ Жабу", callback_data="admin_cancel")]
    ])
    await update.message.reply_text("Админ панелі:", reply_markup=keyboard)
    return ADMIN_MENU

async def admin_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    lang_code = get_user_lang(user_id)
    trans = load_translations(lang_code)
    data = query.data

    if data == "admin_broadcast":
        await query.edit_message_text("Жібергіңіз келетін хабарламаны енгізіңіз (барлық пайдаланушыларға жіберіледі):")
        return ADMIN_BROADCAST
    elif data == "admin_forward":
        await query.edit_message_text("Форвардтайтын хабарламаны таңдаңыз (оны барлығына бағыттаймыз):")
        return ADMIN_FORWARD
    elif data == "admin_stats":
        await show_admin_stats(update, context)
        return ADMIN_MENU
    elif data == "admin_cancel":
        await query.edit_message_text("Админ панелі жабылды.")
        return ConversationHandler.END
    else:
        return ADMIN_MENU

async def admin_broadcast_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Админ жіберген хабарламаны барлық пайдаланушыларға таратады."""
    admin_msg = update.message.text
    user_ids = get_all_users()
    sent = 0
    for uid in user_ids:
        try:
            await context.bot.send_message(chat_id=uid, text=f"[Хабарлама админнен]\n\n{admin_msg}")
            sent += 1
        except Exception as e:
            logger.error(f"Error sending broadcast to {uid}: {e}")
    await update.message.reply_text(f"Хабарлама {sent} пайдаланушыға жіберілді.")
    return ADMIN_MENU

async def admin_forward_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Админ жіберген хабарламаны барлық пайдаланушыларға форвардтайды."""
    # Мұнда admin хабарламасы update.message арқылы келеді.
    admin_msg: Message = update.message
    user_ids = get_all_users()
    forwarded = 0
    for uid in user_ids:
        try:
            await admin_msg.forward(chat_id=uid)
            forwarded += 1
        except Exception as e:
            logger.error(f"Error forwarding message to {uid}: {e}")
    await update.message.reply_text(f"Хабарлама {forwarded} пайдаланушыға форвардталды.")
    return ADMIN_MENU

async def show_admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        with open(STATS_FILE, "r") as f:
            stats = json.load(f)
    except Exception:
        stats = {"total": 0, "items": 0}
    try:
        with open(USERS_FILE, "r") as f:
            users = json.load(f)
    except Exception:
        users = {}
    total_users = len(users)
    # Тілдер бойынша есептеу
    language_counts = {}
    for lang in users.values():
        language_counts[lang] = language_counts.get(lang, 0) + 1

    stat_text = (
        f"📊 Толық статистика:\n"
        f"• Жалпы әрекет саны: {stats.get('total', 0)}\n"
        f"• Жинақталған элементтер: {stats.get('items', 0)}\n"
        f"• Пайдаланушылар саны: {total_users}\n"
    )
    for lang, count in language_counts.items():
        stat_text += f"   - {lang.upper()}: {count}\n"
    await update.effective_chat.send_message(stat_text)
    return ADMIN_MENU

async def admin_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Админ панелі жабылды.")
    return ConversationHandler.END

# --- Фоллбэк (бас тарту) ---
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in user_data:
        del user_data[user_id]
    await update.message.reply_text("Операция тоқтатылды. /start арқылы қайта бастаңыз.")
    return ConversationHandler.END

# --- Негізгі функция ---
if __name__ == "__main__":
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    # Пайдаланушыға арналған ConversationHandler (PDF жинау)
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            STATE_ACCUMULATE: [
                MessageHandler(filters.ALL & ~filters.COMMAND, accumulate_handler)
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    application.add_handler(conv_handler)

    # Админ панелі ConversationHandler
    admin_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("admin", admin_panel)],
        states={
            ADMIN_MENU: [
                CallbackQueryHandler(admin_menu_handler, pattern="^admin_")
            ],
            ADMIN_BROADCAST: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_broadcast_handler)
            ],
            ADMIN_FORWARD: [
                MessageHandler(filters.ALL & ~filters.COMMAND, admin_forward_handler)
            ]
        },
        fallbacks=[CommandHandler("cancel", admin_cancel)]
    )
    application.add_handler(admin_conv_handler)

    # Тілді өзгерту үшін CallbackQueryHandler
    application.add_handler(CallbackQueryHandler(change_language, pattern="^lang_"))

    # Егер басқа хабарламалар келсе, оларды жинақтау режиміне бағыттаймыз
    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, accumulate_handler))

    # --- Сервермен байланыс ---
    # Егер WEBHOOK_URL анықталса, вебхук арқылы жұмыс істейді; әйтпесе, polling қолданылады.
    if os.environ.get("WEBHOOK_URL"):
        application.run_webhook(
            listen="0.0.0.0",
            port=int(os.environ.get("PORT", 10000)),
            webhook_url=os.environ.get("WEBHOOK_URL")
        )
    else:
        application.run_polling()

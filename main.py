import os
import json
import logging
from io import BytesIO
from typing import Dict, Any
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
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

# Конфигурация
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = "5316060523"  # @userinfobot арқылы алыңыз
STATS_FILE = "stats.json"
USERS_FILE = "users.json"

# Тілдік файлдар
LANGUAGES = ["en", "kz", "ru", "uz", "tr", "ua"]
DEFAULT_LANG = "en"

# Диалог күйлері
# PHOTO_COLLECTION күйі фотоларды жинау үшін
PHOTO_COLLECTION = 1

# Глобалды айнымалылар
user_data: Dict[int, Dict[str, Any]] = {}
pdfmetrics.registerFont(TTFont('ArialUnicode', 'Arial-Unicode.ttf'))  # Unicode қаріпі

# -------------------- Көмекші функциялар --------------------
def load_translations(lang_code: str) -> Dict[str, str]:
    try:
        with open(f"translations/{lang_code}.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return load_translations(DEFAULT_LANG)

def get_user_lang(user_id: int) -> str:
    try:
        with open(USERS_FILE, "r") as f:
            users = json.load(f)
        return users.get(str(user_id), DEFAULT_LANG)
    except:
        return DEFAULT_LANG

def save_stats(action: str):
    stats = {"total": 0, "photos": 0, "texts": 0}
    try:
        with open(STATS_FILE, "r") as f:
            stats = json.load(f)
    except:
        pass

    stats["total"] += 1
    if action == "photo":
        stats["photos"] += 1
    elif action == "text":
        stats["texts"] += 1

    with open(STATS_FILE, "w") as f:
        json.dump(stats, f)

def is_photo_pdf_trigger(text: str) -> bool:
    """
    Егер пайдаланушының жіберген мәтіні кез-келген тілдегі "Сурет PDF" батырмасына тең болса, True қайтарады.
    """
    for lang in LANGUAGES:
        trans = load_translations(lang)
        if text == trans.get("btn_photo", ""):
            return True
    return False

# Custom filter для фотосурет батырмасын бастау
class PhotoTriggerFilter(filters.BaseFilter):
    def filter(self, message):
        return message.text is not None and is_photo_pdf_trigger(message.text)

# -------------------- Бастапқы және Тіл таңдау --------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        with open(USERS_FILE, "r") as f:
            users = json.load(f)
        user_lang = users.get(str(user_id), DEFAULT_LANG)
    except:
        user_lang = DEFAULT_LANG

    trans = load_translations(user_lang)
    await update.message.reply_text(
        trans["welcome"],
        reply_markup=language_keyboard()
    )

async def change_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang_code = query.data.split("_")[1]
    user_id = query.from_user.id

    try:
        with open(USERS_FILE, "r") as f:
            users = json.load(f)
    except:
        users = {}

    users[str(user_id)] = lang_code
    with open(USERS_FILE, "w") as f:
        json.dump(users, f)

    trans = load_translations(lang_code)
    await query.edit_message_text(trans["lang_selected"])
    await show_main_menu(update, context, lang_code)

def language_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🇬🇧 English", callback_data="lang_en"),
         InlineKeyboardButton("🇰🇿 Қазақ", callback_data="lang_kz"),
         InlineKeyboardButton("🇷🇺 Русский", callback_data="lang_ru")],
        [InlineKeyboardButton("🇺🇿 O'zbek", callback_data="lang_uz"),
         InlineKeyboardButton("🇹🇷 Türkçe", callback_data="lang_tr"),
         InlineKeyboardButton("🇺🇦 Українська", callback_data="lang_ua")]
    ])

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, lang_code: str):
    trans = load_translations(lang_code)
    keyboard = [
        [trans["btn_photo"], trans["btn_text"]],
        [trans["btn_change_lang"], trans["btn_help"]]
    ]
    # Егер update.message жоқ болса (мысалы, callbackQuery-ден кейін), effective_message-ды қолданамыз
    target = update.effective_message if update.effective_message else update.message
    await target.reply_text(
        trans["main_menu"],
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )

# -------------------- Мәтін арқылы PDF жасау (Text-to-PDF) --------------------
async def process_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_lang = get_user_lang(user_id)
    trans = load_translations(user_lang)

    text = update.message.text
    pdf_buffer = BytesIO()
    c = canvas.Canvas(pdf_buffer, pagesize=A4)
    c.setFont("ArialUnicode", 12)

    y_position = 800
    for line in text.split("\n"):
        c.drawString(40, y_position, line)
        y_position -= 20

    c.save()
    pdf_buffer.seek(0)

    await update.message.reply_document(
        document=pdf_buffer,
        filename=f"text_{datetime.now().strftime('%Y%m%d%H%M%S')}.pdf",
        caption=trans["text_caption"]
    )
    save_stats("text")

# -------------------- Фотосуреттер арқылы PDF жасау диалогы --------------------
async def start_photo_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_lang = get_user_lang(user_id)
    trans = load_translations(user_lang)
    # Пайдаланушының фотолары үшін деректерді инициализациялау
    user_data[user_id] = {"photos": []}
    keyboard = ReplyKeyboardMarkup(
        [[trans["btn_create_pdf"], trans["btn_cancel"]]],
        resize_keyboard=True
    )
    await update.message.reply_text(trans["photo_prompt"], reply_markup=keyboard)
    return PHOTO_COLLECTION

async def add_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_lang = get_user_lang(user_id)
    trans = load_translations(user_lang)
    photo = await update.message.photo[-1].get_file()
    img_data = BytesIO()
    await photo.download_to_memory(img_data)
    img_data.seek(0)
    if user_id not in user_data:
        user_data[user_id] = {"photos": []}
    user_data[user_id]["photos"].append(img_data)
    keyboard = ReplyKeyboardMarkup(
        [[trans["btn_create_pdf"], trans["btn_cancel"]]],
        resize_keyboard=True
    )
    await update.message.reply_text(f"{trans['photo_added']}\n{trans['photo_more_prompt']}", reply_markup=keyboard)
    return PHOTO_COLLECTION

async def handle_photo_pdf_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_lang = get_user_lang(user_id)
    trans = load_translations(user_lang)
    text = update.message.text
    if text == trans["btn_create_pdf"]:
        return await finish_photo_pdf(update, context)
    elif text == trans["btn_cancel"]:
        return await cancel_photo_pdf(update, context)
    else:
        await update.message.reply_text(trans["invalid_option"])
        return PHOTO_COLLECTION

async def finish_photo_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_lang = get_user_lang(user_id)
    trans = load_translations(user_lang)
    if user_id not in user_data or not user_data[user_id].get("photos"):
        await update.message.reply_text(trans["no_photos_error"])
        return PHOTO_COLLECTION

    pdf_buffer = BytesIO()
    c = canvas.Canvas(pdf_buffer, pagesize=A4)
    # A4 бет өлшемі мен жиектер
    max_width = A4[0] - 80  # 40 пункт жиек екі жағынан
    max_height = A4[1] - 80
    for img_data in user_data[user_id]["photos"]:
        img_data.seek(0)
        img = Image.open(img_data)
        img_width, img_height = img.size
        # Фотосуретті A4 бетке сыйғызу үшін масштабтау
        scale = min(max_width / img_width, max_height / img_height, 1)
        new_width = img_width * scale
        new_height = img_height * scale
        x = (A4[0] - new_width) / 2
        y = (A4[1] - new_height) / 2
        c.drawImage(ImageReader(img), x, y, width=new_width, height=new_height)
        c.showPage()

    c.save()
    pdf_buffer.seek(0)

    filename = f"photos_{datetime.now().strftime('%Y%m%d%H%M%S')}.pdf"
    await update.message.reply_document(
        document=pdf_buffer,
        filename=filename,
        caption=trans["pdf_ready"]
    )
    save_stats("photo")
    del user_data[user_id]
    await show_main_menu(update, context, user_lang)
    return ConversationHandler.END

async def cancel_photo_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_lang = get_user_lang(user_id)
    trans = load_translations(user_lang)
    if user_id in user_data:
        del user_data[user_id]
    await update.message.reply_text(trans["operation_cancelled"])
    await show_main_menu(update, context, user_lang)
    return ConversationHandler.END

# -------------------- Админ панель --------------------
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if str(user_id) != ADMIN_ID:
        return

    try:
        with open(STATS_FILE, "r") as f:
            stats = json.load(f)
    except:
        stats = {"total": 0, "photos": 0, "texts": 0}

    try:
        with open(USERS_FILE, "r") as f:
            users = json.load(f)
    except:
        users = {}
    total_users = len(users)
    language_counts = {}
    for lang in users.values():
        language_counts[lang] = language_counts.get(lang, 0) + 1

    text = (
        f"📊 Статистика:\n"
        f"• Жалпы PDF: {stats['total']}\n"
        f"   - Суреттер: {stats['photos']}\n"
        f"   - Мәтіндер: {stats['texts']}\n"
        f"• Пайдаланушылар: {total_users}\n"
    )
    for lang, count in language_counts.items():
        text += f"   - {lang.upper()}: {count}\n"

    await update.message.reply_text(text)

async def reset_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if str(user_id) != ADMIN_ID:
        return
    stats = {"total": 0, "photos": 0, "texts": 0}
    with open(STATS_FILE, "w") as f:
        json.dump(stats, f)
    await update.message.reply_text("Статистика сброшена.")

# -------------------- Негізгі функция --------------------
if __name__ == "__main__":
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    # Хэндлерлер
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_panel))
    application.add_handler(CommandHandler("resetstats", reset_stats))
    application.add_handler(CallbackQueryHandler(change_language, pattern="^lang_"))

    # Фотосурет арқылы PDF жасауға арналған диалог (conversation)
    photo_conv_handler = ConversationHandler(
        entry_points=[MessageHandler(PhotoTriggerFilter(), start_photo_pdf)],
        states={
            PHOTO_COLLECTION: [
                MessageHandler(filters.PHOTO, add_photo),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_photo_pdf_text)
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel_photo_pdf)]
    )
    application.add_handler(photo_conv_handler)

    # Мәтін арқылы PDF жасау (егер фотосурет диалогы іске аспаса)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, process_text))

    # Вебхукты іске қосу
    application.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", 10000)),
        webhook_url=os.environ.get("WEBHOOK_URL")
    )

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

# Логтарды көрсету (debug үшін)
logging.basicConfig(level=logging.DEBUG)

# Конфигурация
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = "5316060523"  # Админ ID
STATS_FILE = "stats.json"
USERS_FILE = "users.json"

# Тілдер
LANGUAGES = ["en", "kz", "ru", "uz", "tr", "ua"]
DEFAULT_LANG = "en"

# Conversation күйлері
PHOTO_COLLECTION = 1
TEXT_COLLECTION = 2

# Глобалды деректер
user_data: Dict[int, Dict[str, Any]] = {}
pdfmetrics.registerFont(TTFont('NotoSans', 'fonts/NotoSans.ttf'))  # Қаріптің жолын тексеріңіз!

# --------------- Көмекші функциялар ---------------

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

# Триггер функциялары: батырма мәтіндерімен салыстырамыз

def is_photo_pdf_trigger(text: str) -> bool:
    for lang in LANGUAGES:
        trans = load_translations(lang)
        if text == trans.get("btn_photo", ""):
            return True
    return False

def is_text_pdf_trigger(text: str) -> bool:
    for lang in LANGUAGES:
        trans = load_translations(lang)
        if text == trans.get("btn_text", ""):
            return True
    return False

def is_change_lang_trigger(text: str) -> bool:
    for lang in LANGUAGES:
        trans = load_translations(lang)
        if text == trans.get("btn_change_lang", ""):
            return True
    return False

def is_help_trigger(text: str) -> bool:
    for lang in LANGUAGES:
        trans = load_translations(lang)
        if text == trans.get("btn_help", ""):
            return True
    return False

# Custom фильтрлер

class PhotoTriggerFilter(filters.BaseFilter):
    def filter(self, message):
        return message.text is not None and is_photo_pdf_trigger(message.text)

class TextTriggerFilter(filters.BaseFilter):
    def filter(self, message):
        return message.text is not None and is_text_pdf_trigger(message.text)

class ChangeLangTriggerFilter(filters.BaseFilter):
    def filter(self, message):
        return message.text is not None and is_change_lang_trigger(message.text)

class HelpTriggerFilter(filters.BaseFilter):
    def filter(self, message):
        return message.text is not None and is_help_trigger(message.text)

# --------------- Бастапқы және Тіл таңдау ---------------

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

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, lang_code: str):
    trans = load_translations(lang_code)
    keyboard = [
        [trans["btn_photo"], trans["btn_text"]],
        [trans["btn_change_lang"], trans["btn_help"]]
    ]
    target = update.effective_message if update.effective_message else update.message
    await target.reply_text(
        trans["main_menu"],
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )

# --------------- Фотодан PDF жасау (Photo PDF) ---------------

async def start_photo_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_lang = get_user_lang(user_id)
    trans = load_translations(user_lang)
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
    max_width = A4[0] - 80
    max_height = A4[1] - 80
    for img_data in user_data[user_id]["photos"]:
        img_data.seek(0)
        img = Image.open(img_data)
        img_width, img_height = img.size
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

# --------------- Мәтіннен PDF жасау (Text PDF) ---------------

async def start_text_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_lang = get_user_lang(user_id)
    trans = load_translations(user_lang)
    await update.message.reply_text(trans["text_prompt"])
    return TEXT_COLLECTION

async def process_text_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_lang = get_user_lang(user_id)
    trans = load_translations(user_lang)
    text = update.message.text
    pdf_buffer = BytesIO()
    c = canvas.Canvas(pdf_buffer, pagesize=A4)
    c.setFont("NotoSans", 12)
    y_position = A4[1] - 50
    for line in text.split("\n"):
        c.drawString(40, y_position, line)
        y_position -= 20
        if y_position < 50:
            c.showPage()
            c.setFont("NotoSans", 12)
            y_position = A4[1] - 50
    c.save()
    pdf_buffer.seek(0)
    filename = f"text_{datetime.now().strftime('%Y%m%d%H%M%S')}.pdf"
    await update.message.reply_document(
        document=pdf_buffer,
        filename=filename,
        caption=trans["text_caption"]
    )
    save_stats("text")
    await show_main_menu(update, context, user_lang)
    return ConversationHandler.END

# --------------- Бас мәзірдің батырмалары: Тілді өзгерту және Көмек ---------------

async def trigger_change_lang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_lang = get_user_lang(user_id)
    trans = load_translations(user_lang)
    await update.message.reply_text(trans["choose_language"], reply_markup=language_keyboard())

async def trigger_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_lang = get_user_lang(user_id)
    trans = load_translations(user_lang)
    await update.message.reply_text(trans["help_text"])

# --------------- Админ панель ---------------

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

# --------------- Fallback: Егер мәтін басқа командаларға жатпаса, негізгі мәзірді қайта көрсету ---------------

async def fallback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_lang = get_user_lang(user_id)
    trans = load_translations(user_lang)
    await update.message.reply_text(trans["main_menu"])
    await show_main_menu(update, context, user_lang)

# --------------- Негізгі функция ---------------

if __name__ == "__main__":
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Командалық хэндлерлер
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_panel))
    application.add_handler(CommandHandler("resetstats", reset_stats))
    application.add_handler(CallbackQueryHandler(change_language, pattern="^lang_"))
    
    # Фотодан PDF жасауға арналған ConversationHandler
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
    
    # Мәтіннен PDF жасауға арналған ConversationHandler
    text_conv_handler = ConversationHandler(
        entry_points=[MessageHandler(TextTriggerFilter(), start_text_pdf)],
        states={
            TEXT_COLLECTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, process_text_pdf)
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel_photo_pdf)]
    )
    application.add_handler(text_conv_handler)
    
    # Негізгі мәзір батырмалары үшін жеке хэндлерлер (Тілді өзгерту және Көмек)
    application.add_handler(MessageHandler(ChangeLangTriggerFilter(), trigger_change_lang))
    application.add_handler(MessageHandler(HelpTriggerFilter(), trigger_help))
    
    # Егер мәтін басқа командаларға жатпаса, fallback ретінде негізгі мәзірді көрсету
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, fallback_handler))
    
    # Вебхукты іске қосу (немесе тестілеу үшін polling қолдануға болады)
    application.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", 10000)),
        webhook_url=os.environ.get("WEBHOOK_URL")
    )

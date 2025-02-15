import os
import json
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters
)
from PIL import Image
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from io import BytesIO

# Конфигурация
BOT_TOKEN = os.getenv("7150129034:AAFAFeu2nuMKxUHYkVeoryrq0wHfZR6SnFg")
ADMIN_ID = "5316060523"  # Мысалы: "123456789"
STATS_FILE = "stats.json"
USERS_FILE = "users.json"

# Тілдерді жүктеу
def load_translations(lang_code):
    try:
        with open(f"translations/{lang_code}.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return load_translations("en")  # Әдепкі тіл

# Статистиканы сақтау
def save_stats(action):
    stats = {"total_files": 0, "photos": 0, "texts": 0}
    try:
        with open(STATS_FILE, "r") as f:
            stats = json.load(f)
    except: pass
    
    stats["total_files"] += 1
    if action == "photo": stats["photos"] += 1
    elif action == "text": stats["texts"] += 1
    
    with open(STATS_FILE, "w") as f:
        json.dump(stats, f)

# Админ панелі
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if str(user_id) != ADMIN_ID:
        return
    stats = {}
    try:
        with open(STATS_FILE, "r") as f:
            stats = json.load(f)
    except: pass
    
    text = (
        f"📊 Статистика:\n"
        f"• Жалпы файлдар: {stats.get('total_files', 0)}\n"
        f"• Суреттер: {stats.get('photos', 0)}\n"
        f"• Мәтіндер: {stats.get('texts', 0)}"
    )
    await context.bot.send_message(chat_id=ADMIN_ID, text=text)

# Тіл таңдау кнопкалары
def language_keyboard():
    keyboard = [
        [InlineKeyboardButton("🇬🇧 English", callback_data="lang_en"),
         InlineKeyboardButton("🇰🇿 Қазақ", callback_data="lang_kz"),
         InlineKeyboardButton("🇷🇺 Русский", callback_data="lang_ru")],
        [InlineKeyboardButton("🇺🇿 O'zbek", callback_data="lang_uz"),
         InlineKeyboardButton("🇹🇷 Türkçe", callback_data="lang_tr"),
         InlineKeyboardButton("🇺🇦 Українська", callback_data="lang_ua")]
    ]
    return InlineKeyboardMarkup(keyboard)

# Негізгі мәзір кнопкалары
def main_menu(user_lang):
    trans = load_translations(user_lang)
    keyboard = [
        [trans["btn_photo"], trans["btn_text"]],
        [trans["btn_change_lang"]]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# Старт командасы
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        with open(USERS_FILE, "r") as f:
            users = json.load(f)
        user_lang = users.get(str(user_id), "en")
    except:
        user_lang = "en"
    
    trans = load_translations(user_lang)
    await update.message.reply_text(
        trans["welcome"],
        reply_markup=language_keyboard()
    )

# Тілді өзгерту
async def change_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
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
    await query.message.reply_text(
        trans["main_menu"],
        reply_markup=main_menu(lang_code)
    )

# Суретті PDF-ке айналдыру
async def process_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        with open(USERS_FILE, "r") as f:
            users = json.load(f)
        user_lang = users.get(str(user_id), "en")
    except:
        user_lang = "en"
    trans = load_translations(user_lang)
    
    photo = await update.message.photo[-1].get_file()
    img_data = BytesIO()
    await photo.download_to_memory(img_data)
    
    img = Image.open(img_data)
    pdf_buffer = BytesIO()
    img.save(pdf_buffer, "PDF", resolution=100.0)
    pdf_buffer.seek(0)
    
    await update.message.reply_document(
        document=pdf_buffer,
        filename="photo.pdf",
        caption=trans["photo_caption"]
    )
    save_stats("photo")

# Мәтінді PDF-ке айналдыру
async def process_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        with open(USERS_FILE, "r") as f:
            users = json.load(f)
        user_lang = users.get(str(user_id), "en")
    except:
        user_lang = "en"
    trans = load_translations(user_lang)
    
    text = update.message.text
    pdf_buffer = BytesIO()
    c = canvas.Canvas(pdf_buffer, pagesize=A4)
    text_obj = c.beginText(40, 800)
    
    for line in text.split("\n"):
        text_obj.textLine(line)
    
    c.drawText(text_obj)
    c.save()
    pdf_buffer.seek(0)
    
    await update.message.reply_document(
        document=pdf_buffer,
        filename="text.pdf",
        caption=trans["text_caption"]
    )
    save_stats("text")

if __name__ == "__main__":
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Хэндлерлер
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_panel))
    application.add_handler(CallbackQueryHandler(change_language, pattern="^lang_"))
    application.add_handler(MessageHandler(filters.PHOTO, process_photo))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, process_text))
    
    # Серверді іске қосу
    application.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        secret_token=os.environ.get("SECRET_TOKEN"),
        webhook_url=os.environ.get("WEBHOOK_URL")
    )

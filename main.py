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
    filters,
    ConversationHandler
)
from PIL import Image
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# Конфигурация
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = "5316060523"  # @userinfobot арқылы алыңыз
STATS_FILE = "stats.json"
USERS_FILE = "users.json"

# Тілдік файлдар
LANGUAGES = ["en", "kz", "ru", "uz", "tr", "ua"]
DEFAULT_LANG = "en"

# Диалог күйлері
GET_FILENAME, ADD_MORE = range(2)

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

def save_stats(action: str):
    stats = {"total": 0, "photos": 0, "texts": 0}
    try:
        with open(STATS_FILE, "r") as f:
            stats = json.load(f)
    except: pass
    
    stats["total"] += 1
    if action == "photo": stats["photos"] += 1
    elif action == "text": stats["texts"] += 1
    
    with open(STATS_FILE, "w") as f:
        json.dump(stats, f)

# -------------------- Тілді таңдау --------------------
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

# -------------------- Негізгі мәзір --------------------
async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, lang_code: str):
    trans = load_translations(lang_code)
    keyboard = [
        [trans["btn_photo"], trans["btn_text"]],
        [trans["btn_change_lang"], trans["btn_help"]]
    ]
    await update.effective_message.reply_text(
        trans["main_menu"],
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )

# -------------------- PDF Генерация --------------------
async def process_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_lang = get_user_lang(user_id)
    trans = load_translations(user_lang)
    
    if user_id not in user_data:
        user_data[user_id] = {"photos": [], "filename": None}
    
    photo = await update.message.photo[-1].get_file()
    img_data = BytesIO()
    await photo.download_to_memory(img_data)
    user_data[user_id]["photos"].append(img_data)
    
    keyboard = [[trans["add_more"], trans["finish_pdf"]]]
    await update.message.reply_text(
        trans["photo_added"],
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )
    return ADD_MORE

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
        filename=f"text_{datetime.now().strftime('%Y%m%d%H%M')}.pdf",
        caption=trans["text_caption"]
    )
    save_stats("text")

async def ask_filename(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_lang = get_user_lang(update.effective_user.id)
    trans = load_translations(user_lang)
    await update.message.reply_text(trans["enter_filename"])
    return GET_FILENAME

async def generate_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_lang = get_user_lang(user_id)
    trans = load_translations(user_lang)
    
    if not user_data.get(user_id):
        await update.message.reply_text(trans["error_no_data"])
        return
    
    filename = user_data[user_id].get("filename", f"document_{datetime.now().strftime('%Y%m%d%H%M')}.pdf")
    pdf_buffer = BytesIO()
    c = canvas.Canvas(pdf_buffer)
    
    for img_data in user_data[user_id]["photos"]:
        img = Image.open(img_data)
        img_width, img_height = img.size
        c.setPageSize((img_width, img_height))
        c.drawImage(img, 0, 0)
        c.showPage()
    
    c.save()
    pdf_buffer.seek(0)
    
    await update.message.reply_document(
        document=pdf_buffer,
        filename=filename,
        caption=trans["pdf_ready"]
    )
    save_stats("photo")
    del user_data[user_id]

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
    
    text = (
        f"📊 Статистика:\n"
        f"• Жалпы PDF: {stats['total']}\n"
        f"• Суреттер: {stats['photos']}\n"
        f"• Мәтіндер: {stats['texts']}"
    )
    await update.message.reply_text(text)

# -------------------- Негізгі функция --------------------
if __name__ == "__main__":
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Хэндлерлер
    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.PHOTO, process_photo)],
        states={
            ADD_MORE: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_photo)],
            GET_FILENAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, generate_pdf)]
        },
        fallbacks=[]
    )
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_panel))
    application.add_handler(CallbackQueryHandler(change_language, pattern="^lang_"))
    application.add_handler(conv_handler)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, process_text))
    
    # Вебхукты іске қосу
    application.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", 10000)),
        webhook_url=os.environ.get("WEBHOOK_URL")
    )

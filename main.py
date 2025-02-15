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

# Логтарды қосу (debug үшін)
logging.basicConfig(level=logging.DEBUG)

# Конфигурация
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = "5316060523"  # Админ ID-іңізді енгізіңіз
STATS_FILE = "stats.json"
USERS_FILE = "users.json"

# Тілдер
LANGUAGES = ["en", "kz", "ru", "uz", "tr", "ua"]
DEFAULT_LANG = "en"

# Conversation күйлері
STATE_ACCUMULATE = 1

# Глобалды деректер (пайдаланушылардың жіберген элементтерін сақтаймыз)
user_data: Dict[int, Dict[str, Any]] = {}

# ReportLab үшін қаріптерді тіркеу (қаріп файлының жолын тексеріңіз!)
pdfmetrics.registerFont(TTFont('NotoSans', 'fonts/NotoSans.ttf'))

# --------------- Аудармаларды жүктеу және басқа көмекші функциялар ---------------

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

def save_user_lang(user_id: int, lang_code: str):
    try:
        with open(USERS_FILE, "r") as f:
            users = json.load(f)
    except:
        users = {}
    users[str(user_id)] = lang_code
    with open(USERS_FILE, "w") as f:
        json.dump(users, f)

def save_stats(action: str):
    stats = {"total": 0, "items": 0}
    try:
        with open(STATS_FILE, "r") as f:
            stats = json.load(f)
    except:
        pass
    stats["total"] += 1
    stats["items"] += 1
    with open(STATS_FILE, "w") as f:
        json.dump(stats, f)

# --------------- Бастапқы хабарлама және тілді таңдау ---------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    # Тіл таңдау деректерін жүктеу
    lang_code = get_user_lang(user_id)
    trans = load_translations(lang_code)
    # Conversation-ды бастап, пайдаланушының жинау буферін тазалаймыз
    user_data[user_id] = {"items": []}
    # Тіл таңдау батырмалары бар хабарлама жібереміз
    await update.message.reply_text(
        trans["welcome"],
        reply_markup=language_keyboard()
    )
    
async def change_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang_code = query.data.split("_")[1]
    user_id = query.from_user.id
    save_user_lang(user_id, lang_code)
    trans = load_translations(lang_code)
    await query.edit_message_text(trans["lang_selected"])
    # Қайта негізгі жинау режиміне өту
    await send_initial_instruction(update, context, lang_code)

def language_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🇬🇧 English", callback_data="lang_en"),
         InlineKeyboardButton("🇰🇿 Қазақ", callback_data="lang_kz"),
         InlineKeyboardButton("🇷🇺 Русский", callback_data="lang_ru")],
        [InlineKeyboardButton("🇺🇿 O'zbek", callback_data="lang_uz"),
         InlineKeyboardButton("🇹🇷 Türkçe", callback_data="lang_tr"),
         InlineKeyboardButton("🇺🇦 Українська", callback_data="lang_ua")]
    ])

async def send_initial_instruction(update: Update, context: ContextTypes.DEFAULT_TYPE, lang_code: str):
    """Пайдаланушыға бастапқы нұсқауды қайта жібереміз (элементдер буфері бос болғанда)"""
    trans = load_translations(lang_code)
    keyboard = ReplyKeyboardMarkup(
        [[trans["btn_change_lang"], trans["btn_help"]]],
        resize_keyboard=True
    )
    # Бастапқы нұсқау: файл, сурет немесе мәтін жіберіңіз...
    text = trans["instruction_initial"]
    # Егер update-тің көзі message болмаса, callbackQuery-ден жауап қайтарамыз
    target = update.effective_message if update.effective_message else update.message
    await target.reply_text(text, reply_markup=keyboard)

# --------------- Негізгі жинау функциясы (ACCUMULATE) ---------------

async def accumulate_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang_code = get_user_lang(user_id)
    trans = load_translations(lang_code)
    text = update.message.text.strip() if update.message.text else ""
    
    # Егер пайдаланушы "PDF-ке айналдыру" батырмасын басса:
    if text == trans["btn_convert_pdf"]:
        return await convert_pdf(update, context)
    # Егер пайдаланушы "Тіл ауыстыру" батырмасын басса:
    if text == trans["btn_change_lang"]:
        return await trigger_change_lang(update, context)
    # Егер пайдаланушы "Көмек" батырмасын басса:
    if text == trans["btn_help"]:
        return await trigger_help(update, context)
    
    # Хабарлама – файл, сурет немесе мәтін, оны өңдеп жинаймыз
    await process_incoming_item(update, context)
    # Жинақталғаннан кейін, жаңартылған нұсқау хабарламасын жібереміз
    keyboard = ReplyKeyboardMarkup(
        [[trans["btn_convert_pdf"]],
         [trans["btn_change_lang"], trans["btn_help"]]],
        resize_keyboard=True
    )
    # update.effective_chat.send_message() арқылы жібереміз:
    await update.effective_chat.send_message(trans["instruction_accumulated"], reply_markup=keyboard)
    return STATE_ACCUMULATE

async def process_incoming_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Кірген хабарламаны өңдеп, тиісті түрдегі элемент ретінде жинаймыз."""
    user_id = update.effective_user.id
    lang_code = get_user_lang(user_id)
    trans = load_translations(lang_code)
    # Әрбір пайдаланушының буфері бар деп есептейміз
    if "items" not in user_data.get(user_id, {}):
        user_data[user_id] = {"items": []}
    
    # Егер мәтін болса:
    if update.message.text and not update.message.photo and not update.message.document:
        item = {"type": "text", "content": update.message.text}
        user_data[user_id]["items"].append(item)
    # Егер фото болса:
    elif update.message.photo:
        photo_file = await update.message.photo[-1].get_file()
        bio = BytesIO()
        await photo_file.download_to_memory(bio)
        bio.seek(0)
        item = {"type": "photo", "content": bio}
        user_data[user_id]["items"].append(item)
    # Егер құжат болса:
    elif update.message.document:
        doc = update.message.document
        filename = doc.file_name.lower()
        ext = os.path.splitext(filename)[1]
        file_obj = await doc.get_file()
        bio = BytesIO()
        await file_obj.download_to_memory(bio)
        bio.seek(0)
        # Егер сурет кеңейтілімі болса – оны фото ретінде қарастырамыз
        if ext in [".jpg", ".jpeg", ".png", ".gif"]:
            item = {"type": "photo", "content": bio}
        # Егер мәтіндік файл (.txt) болса:
        elif ext == ".txt":
            try:
                content = bio.read().decode("utf-8")
            except Exception:
                content = "Мәтінді оқу мүмкін емес."
            item = {"type": "text", "content": content}
        else:
            # Басқа файлдарды атауы арқылы хабарламамен қосамыз
            item = {"type": "text", "content": f"Файл қосылды: {doc.file_name}"}
        user_data[user_id]["items"].append(item)
    # Статистикаға жазамыз
    save_stats("item")

async def convert_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Жинақталған барлық элементтерді біріктіріп PDF құрастырып, жібереміз."""
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
            # Әр мәтін элементін жаңа бетке шығарамыз
            c.setFont("NotoSans", 12)
            # Мәтінді жол бойынша бөліп шығару (аралығы 20 пункт)
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
            # Суретті бетке орналастыру (орталықтандыру және A4-ке сыйдыру)
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
                c.drawString(40, height/2, f"Суретті шығару мүмкін емес: {e}")
            c.showPage()
    c.save()
    pdf_buffer.seek(0)

    filename = f"combined_{datetime.now().strftime('%Y%m%d%H%M%S')}.pdf"
    await update.message.reply_document(
        document=pdf_buffer,
        filename=filename,
        caption=trans["pdf_ready"]
    )
    # Жіберілгеннен кейін буферді тазалаймыз
    user_data[user_id]["items"] = []
    # Қайта бастапқы нұсқауды жібереміз
    await update.message.reply_text(trans["instruction_initial"],
                                    reply_markup=ReplyKeyboardMarkup(
                                        [[trans["btn_change_lang"], trans["btn_help"]]],
                                        resize_keyboard=True))
    return STATE_ACCUMULATE

# --------------- Тіл ауыстыру және көмек функциялары ---------------

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

# --------------- Админ панель (қосымша) ---------------

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if str(user_id) != ADMIN_ID:
        return
    try:
        with open(STATS_FILE, "r") as f:
            stats = json.load(f)
    except:
        stats = {"total": 0, "items": 0}
    try:
        with open(USERS_FILE, "r") as f:
            users = json.load(f)
    except:
        users = {}
    total_users = len(users)
    text = (
        f"📊 Статистика:\n"
        f"• Жалпы әрекет: {stats['total']}\n"
        f"• Жіберілген элементтер: {stats['items']}\n"
        f"• Пайдаланушылар: {total_users}"
    )
    await update.message.reply_text(text)

async def reset_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if str(user_id) != ADMIN_ID:
        return
    stats = {"total": 0, "items": 0}
    with open(STATS_FILE, "w") as f:
        json.dump(stats, f)
    await update.message.reply_text("Статистика тазаланды.")

# --------------- ConversationHandler-ді тоқтату (мысалы, /cancel) ---------------

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in user_data:
        del user_data[user_id]
    await update.message.reply_text("Операция тоқтатылды. /start арқылы қайта бастаңыз.")
    return ConversationHandler.END

# --------------- Негізгі функция ---------------

if __name__ == "__main__":
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    # Стандартты командалар
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_panel))
    application.add_handler(CommandHandler("resetstats", reset_stats))
    application.add_handler(CallbackQueryHandler(change_language, pattern="^lang_"))

    # ConversationHandler: барлық элементтерді жинау және PDF жасау
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

    # Вебхук немесе polling (осы мысалда вебхук қолданылады)
    application.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", 10000)),
        webhook_url=os.environ.get("WEBHOOK_URL")
    )

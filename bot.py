import os
import sqlite3
import datetime
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
)

BOT_TOKEN = os.getenv("BOT_TOKEN")

# ================== CONSTANTS ==================
(
    BOOKING_NAME,
    BOOKING_PHONE,
    BOOKING_DATE,
    BOOKING_TIME,
    CONFIRM_BOOKING,
    CLIENT_TO_ADMIN_MESSAGE,
    ADMIN_TO_CLIENT_MESSAGE,
    BROADCAST_MESSAGE,
    SEARCH_PHONE,
) = range(9)

# ================== DATABASE ==================
def init_database():
    conn = sqlite3.connect("appointments.db")
    c = conn.cursor()
    c.execute(
        """CREATE TABLE IF NOT EXISTS appointments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            phone TEXT,
            date TEXT,
            time TEXT,
            status TEXT,
            created_at TEXT,
            chat_id INTEGER
        )"""
    )
    conn.commit()
    conn.close()

def get_client_appointments(chat_id):
    conn = sqlite3.connect("appointments.db")
    c = conn.cursor()
    c.execute("SELECT * FROM appointments WHERE chat_id=?", (chat_id,))
    rows = c.fetchall()
    conn.close()
    return rows

def get_appointment_by_id(appointment_id):
    conn = sqlite3.connect("appointments.db")
    c = conn.cursor()
    c.execute("SELECT * FROM appointments WHERE id=?", (appointment_id,))
    row = c.fetchone()
    conn.close()
    return row

# ================== HANDLERS ==================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Выберите действие:",
        reply_markup=ReplyKeyboardMarkup(
            [
                [KeyboardButton("📅 Записаться на маникюр")],
                [KeyboardButton("📋 Мои записи")],
                [KeyboardButton("✉️ Написать мастеру")],
            ],
            resize_keyboard=True,
        ),
    )

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔧 АДМИН-ПАНЕЛЬ\n\nВыберите действие:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 Все записи", callback_data="admin_all_appointments")],
            [InlineKeyboardButton("✉️ Сообщения от клиентов", callback_data="admin_back_to_messages")],
        ])
    )

# === Client to Admin message ===
async def start_client_to_admin_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✉️ Напишите ваше сообщение мастеру (или ❌ Отмена):")
    return CLIENT_TO_ADMIN_MESSAGE

async def handle_client_to_admin_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message.text
    admin_id = int(os.getenv("ADMIN_ID", "0"))
    if admin_id:
        await context.bot.send_message(admin_id, f"📩 Сообщение от клиента {update.effective_user.id}:\n\n{msg}")
    await update.message.reply_text("✅ Ваше сообщение отправлено мастеру!")
    return ConversationHandler.END

# === Admin to Client message ===
async def start_admin_to_client_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    client_chat_id = int(query.data.split("_")[-1])
    context.user_data["admin_message_client_id"] = client_chat_id
    client_appointments = get_client_appointments(client_chat_id)
    client_name = "Клиент"
    if client_appointments:
        client_name = client_appointments[0][1]
    context.user_data["admin_message_client_name"] = client_name
    await query.message.reply_text(
        f"💬 ОТВЕТ КЛИЕНТУ\n\n👤 Клиент: {client_name}\n🆔 Chat ID: {client_chat_id}\n\nНапишите ваше сообщение:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data="admin_back_to_messages")]]),
    )
    return ADMIN_TO_CLIENT_MESSAGE

# === Expired payments checker ===
async def check_expired_payments(context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect("appointments.db")
    c = conn.cursor()
    now = datetime.datetime.now()

    c.execute("SELECT id, name, phone, date, time, created_at, chat_id FROM appointments WHERE status='ожидание оплаты'")
    rows = c.fetchall()

    for row in rows:
        appointment_id, name, phone, date, time, created_at, chat_id = row
        try:
            created_at_dt = datetime.datetime.fromisoformat(created_at)
        except Exception:
            continue
        if (now - created_at_dt).total_seconds() > 15 * 60:  # прошло 15 минут
            c.execute("UPDATE appointments SET status='отменено' WHERE id=?", (appointment_id,))
            conn.commit()

            # уведомляем клиента
            try:
                await context.bot.send_message(
                    chat_id,
                    f"⏰ Ваша запись {date} в {time} была автоматически отменена, так как не была оплачена вовремя."
                )
            except Exception:
                pass

            # уведомляем админа
            admin_id = int(os.getenv("ADMIN_ID", "0"))
            if admin_id:
                try:
                    await context.bot.send_message(
                        admin_id,
                        f"❌ Запись {appointment_id} ({name}, {phone}, {date} {time}) отменена автоматически (нет оплаты 15 минут)."
                    )
                except Exception:
                    pass

    conn.close()

async def send_reminders(context: ContextTypes.DEFAULT_TYPE):
    # здесь логика отправки напоминаний
    pass

# === Handle admin callback ===
async def handle_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "admin_back":
        await query.edit_message_text("🔧 АДМИН-ПАНЕЛЬ\n\nВыберите действие:",
                                      reply_markup=InlineKeyboardMarkup([
                                          [InlineKeyboardButton("📋 Все записи", callback_data="admin_all_appointments")],
                                          [InlineKeyboardButton("✉️ Сообщения от клиентов", callback_data="admin_back_to_messages")],
                                      ]))
        return

    if data == "back_to_main":
        await query.message.reply_text(
            "Привет! Выберите действие:",
            reply_markup=ReplyKeyboardMarkup(
                [
                    [KeyboardButton("📅 Записаться на маникюр")],
                    [KeyboardButton("📋 Мои записи")],
                    [KeyboardButton("✉️ Написать мастеру")],
                ],
                resize_keyboard=True,
            ),
        )
        return

    if data == "admin_back_to_messages":
        await query.message.reply_text("📩 Сообщения от клиентов (пока пусто)")
        return

    if data == "back_to_appointments":
        await query.message.reply_text("📋 Мои записи (пока пусто)")
        return

    await query.message.reply_text("Неподдерживаемое действие.")

# ================== CONVERSATIONS ==================
client_to_admin_conv = ConversationHandler(
    entry_points=[MessageHandler(filters.Regex("^✉️ Написать мастеру$"), start_client_to_admin_message)],
    states={
        CLIENT_TO_ADMIN_MESSAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_client_to_admin_message)]
    },
    fallbacks=[MessageHandler(filters.Regex("^❌ Отмена$"), start_command)],
)

# ================== MAIN ==================
def main():
    init_database()
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(client_to_admin_conv)

    application.add_handler(CallbackQueryHandler(handle_admin_callback))

    # JobQueue
    job_queue = application.job_queue
    job_queue.run_repeating(check_expired_payments, interval=60, first=10)
    job_queue.run_daily(send_reminders, time=datetime.time(hour=10, minute=0))

    PORT = int(os.getenv("PORT", 10000))
    APP_NAME = os.getenv("RENDER_EXTERNAL_HOSTNAME")

    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=BOT_TOKEN,
        webhook_url=f"https://{APP_NAME}/{BOT_TOKEN}",
    )

if __name__ == "__main__":
    main()

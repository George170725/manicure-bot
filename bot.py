# bot.py
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
    CallbackQueryHandler,
)
import os
import sqlite3
import asyncio
from datetime import datetime, timedelta

# ------------------ Настройки ------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "465630314"))

if not BOT_TOKEN:
    print("❌ ОШИБКА: BOT_TOKEN не найден в переменных окружения")
    exit(1)

DB_PATH = "data/appointments.db"

# Conversation states
(
    SELECT_DATE,
    SELECT_TIME,
    ENTER_NAME,
    ENTER_PHONE,
    SEARCH_PHONE,
    ADMIN_SEARCH_CLIENT,
    CLIENT_TO_ADMIN_MESSAGE,
    ADMIN_TO_CLIENT_MESSAGE,
    BROADCAST_MESSAGE,
    CONFIRM_CANCELLATION,
) = range(10)

RUSSIAN_WEEKDAYS = {0: "Пн", 1: "Вт", 2: "Ср", 3: "Чт", 4: "Пт", 5: "Сб", 6: "Вс"}

# Ссылка на оплату (замени на свою)
YUMMY_PAYMENT_LINK = "https://yoomoney.ru/..."


# ------------------ DB init ------------------
def init_database():
    if not os.path.exists("data"):
        os.makedirs("data")

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS appointments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_name TEXT NOT NULL,
            client_phone TEXT NOT NULL,
            appointment_date TEXT NOT NULL,
            appointment_time TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'pending',
            client_chat_id INTEGER,
            payment_status TEXT DEFAULT 'not_paid',
            payment_confirmed_at TIMESTAMP DEFAULT NULL
        )
    """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_chat_id INTEGER NOT NULL,
            client_name TEXT NOT NULL,
            message_text TEXT NOT NULL,
            is_from_client BOOLEAN NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            replied_to INTEGER DEFAULT NULL
        )
    """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS blocked_slots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            blocked_date TEXT NOT NULL,
            blocked_time TEXT,
            is_all_day BOOLEAN DEFAULT 0,
            reason TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS bot_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER UNIQUE NOT NULL,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """
    )

    conn.commit()
    conn.close()
    print("База данных инициализирована")


# ------------------ DB helpers ------------------
def save_bot_user(chat_id, username=None, first_name=None, last_name=None):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT OR REPLACE INTO bot_users (chat_id, username, first_name, last_name)
        VALUES (?, ?, ?, ?)
    """,
        (chat_id, username, first_name, last_name),
    )
    conn.commit()
    conn.close()


def get_all_bot_users():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM bot_users")
    users = cursor.fetchall()
    conn.close()
    return users


def add_blocked_slot(date, time_slot=None, reason=""):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    is_all_day = 1 if time_slot is None else 0
    cursor.execute(
        """
        INSERT INTO blocked_slots (blocked_date, blocked_time, is_all_day, reason)
        VALUES (?, ?, ?, ?)
    """,
        (date, time_slot, is_all_day, reason),
    )
    conn.commit()
    conn.close()


def get_blocked_slots(date=None):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    if date:
        cursor.execute("SELECT * FROM blocked_slots WHERE blocked_date = ?", (date,))
    else:
        cursor.execute("SELECT * FROM blocked_slots")
    slots = cursor.fetchall()
    conn.close()
    return slots


def is_slot_blocked(date, time_slot):
    blocked_slots = get_blocked_slots(date)
    for slot in blocked_slots:
        # slot structure: (id, blocked_date, blocked_time, is_all_day, reason, created_at)
        if slot[3]:  # is_all_day (True)
            return True
        elif slot[2] == time_slot:  # blocked_time
            return True
    return False


def remove_blocked_slot(slot_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM blocked_slots WHERE id = ?", (slot_id,))
    conn.commit()
    conn.close()


def save_appointment_to_db(name, phone, date, time_slot, chat_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO appointments (client_name, client_phone, appointment_date, appointment_time, client_chat_id, status, payment_status)
        VALUES (?, ?, ?, ?, ?, 'pending', 'not_paid')
    """,
        (name, phone, date, time_slot, chat_id),
    )
    conn.commit()
    appointment_id = cursor.lastrowid
    conn.close()
    return appointment_id


def is_time_slot_taken(date, time_slot):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT COUNT(*) FROM appointments
        WHERE appointment_date = ? AND appointment_time = ? AND status IN ('pending', 'confirmed')
    """,
        (date, time_slot),
    )
    count = cursor.fetchone()[0]
    conn.close()
    return count > 0


def get_all_appointments():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT * FROM appointments WHERE status IN ('pending', 'confirmed')
        ORDER BY appointment_date, appointment_time
    """
    )
    appointments = cursor.fetchall()
    conn.close()
    return appointments


def get_appointments_by_date(date):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT * FROM appointments
        WHERE appointment_date = ? AND status IN ('pending', 'confirmed')
        ORDER BY appointment_time
    """,
        (date,),
    )
    appointments = cursor.fetchall()
    conn.close()
    return appointments


def get_appointment_by_id(appointment_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM appointments WHERE id = ?", (appointment_id,))
    appointment = cursor.fetchone()
    conn.close()
    return appointment


def confirm_payment(appointment_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE appointments
        SET status = 'confirmed', payment_status = 'paid', payment_confirmed_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """,
        (appointment_id,),
    )
    conn.commit()
    conn.close()
    return get_appointment_by_id(appointment_id)


def cancel_appointment(appointment_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE appointments SET status = 'cancelled' WHERE id = ?", (appointment_id,))
    conn.commit()
    conn.close()
    return get_appointment_by_id(appointment_id)


def expire_appointment(appointment_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE appointments SET status = 'expired' WHERE id = ?", (appointment_id,))
    conn.commit()
    conn.close()
    return get_appointment_by_id(appointment_id)


def get_pending_appointments():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT * FROM appointments
        WHERE status = 'pending' AND payment_status = 'not_paid'
    """
    )
    appointments = cursor.fetchall()
    conn.close()
    return appointments


def get_client_appointments(chat_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT * FROM appointments
        WHERE client_chat_id = ? AND status IN ('pending', 'confirmed')
        ORDER BY appointment_date, appointment_time
    """,
        (chat_id,),
    )
    appointments = cursor.fetchall()
    conn.close()
    return appointments


def search_appointments_by_phone(phone):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT * FROM appointments
        WHERE client_phone LIKE ? AND status IN ('pending', 'confirmed')
        ORDER BY appointment_date, appointment_time
    """,
        (f"%{phone}%",),
    )
    appointments = cursor.fetchall()
    conn.close()
    return appointments


def save_message(client_chat_id, client_name, message_text, is_from_client, replied_to=None):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO messages (client_chat_id, client_name, message_text, is_from_client, replied_to)
        VALUES (?, ?, ?, ?, ?)
    """,
        (client_chat_id, client_name, message_text, is_from_client, replied_to),
    )
    conn.commit()
    message_id = cursor.lastrowid
    conn.close()
    return message_id


def get_client_messages(limit=50):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT * FROM messages
        WHERE is_from_client = 1
        ORDER BY created_at DESC
        LIMIT ?
    """,
        (limit,),
    )
    messages = cursor.fetchall()
    conn.close()
    return messages


def get_conversation_history(client_chat_id, limit=20):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT * FROM messages
        WHERE client_chat_id = ?
        ORDER BY created_at ASC
        LIMIT ?
    """,
        (client_chat_id, limit),
    )
    messages = cursor.fetchall()
    conn.close()
    return messages


# ------------------ Дата/время хелперы ------------------
def format_date_for_storage(dt: datetime):
    weekday_russian = RUSSIAN_WEEKDAYS[dt.weekday()]
    return f"{weekday_russian} {dt.strftime('%d.%m')}"


def parse_day_month_from_button(selected_date: str):
    try:
        parts = selected_date.split(" ")
        if len(parts) < 2:
            raise ValueError("Неверный формат даты")
        day_part = parts[1]
        day, month = day_part.split(".")
        return int(day), int(month)
    except Exception:
        raise


def is_valid_datetime(selected_date, selected_time):
    try:
        now = datetime.now()
        day, month = parse_day_month_from_button(selected_date)
        year = now.year
        selected_month = int(month)
        current_month = now.month
        # годовой переход
        if selected_month < current_month and (current_month - selected_month) > 6:
            year += 1
        selected_datetime = datetime(year, selected_month, day)
        hours, minutes = map(int, selected_time.split(":"))
        selected_datetime = selected_datetime.replace(hour=hours, minute=minutes)
        return selected_datetime > now
    except Exception:
        return False


def is_future_date(selected_date):
    try:
        now = datetime.now()
        day, month = parse_day_month_from_button(selected_date)
        year = now.year
        selected_month = int(month)
        current_month = now.month
        if selected_month < current_month and (current_month - selected_month) > 6:
            year += 1
        selected_datetime = datetime(year, selected_month, day)
        return selected_datetime.date() >= now.date()
    except Exception:
        return False


def get_appointment_datetime(appointment):
    try:
        date_str = appointment[3]  # appointment_date
        time_str = appointment[4]  # appointment_time
        day, month = parse_day_month_from_button(date_str)
        year = datetime.now().year
        if month < datetime.now().month and (datetime.now().month - month) > 6:
            year += 1
        hours, minutes = map(int, time_str.split(":"))
        return datetime(year, month, day, hours, minutes)
    except Exception:
        return None


# ------------------ Keyboards ------------------
def create_dates_keyboard(days_ahead=30):
    buttons = []
    today = datetime.now()
    for i in range(days_ahead):
        current_date = today + timedelta(days=i)
        if current_date.date() < today.date():
            continue
        date_text = format_date_for_storage(current_date)
        if not is_slot_blocked(date_text, None):
            buttons.append(KeyboardButton(date_text))
    rows = [buttons[i : i + 4] for i in range(0, len(buttons), 4)]
    rows.append([KeyboardButton("❌ Отмена")])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def create_time_keyboard(selected_date):
    all_time_slots = [
        "09:00",
        "10:00",
        "11:00",
        "12:00",
        "13:00",
        "14:00",
        "15:00",
        "16:00",
        "17:00",
        "18:00",
        "19:00",
        "20:00",
    ]
    available_slots = []
    for time_slot in all_time_slots:
        if (not is_time_slot_taken(selected_date, time_slot) and is_valid_datetime(selected_date, time_slot) and not is_slot_blocked(selected_date, time_slot)):
            available_slots.append(time_slot)
    time_keyboard = []
    row = []
    for i, time_slot in enumerate(available_slots):
        row.append(KeyboardButton(time_slot))
        if len(row) == 3 or i == len(available_slots) - 1:
            time_keyboard.append(row)
            row = []
    time_keyboard.append([KeyboardButton("❌ Отмена")])
    return ReplyKeyboardMarkup(time_keyboard, resize_keyboard=True), available_slots


def create_my_appointments_keyboard(appointments):
    keyboard = []
    for app in appointments:
        status_icon = "✅" if app[6] == "confirmed" else "⏳"
        keyboard.append(
            [
                InlineKeyboardButton(
                    f"{status_icon} {app[3]} {app[4]} (Отменить)",
                    callback_data=f"client_cancel_{app[0]}",
                )
            ]
        )
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")])
    return InlineKeyboardMarkup(keyboard)


def create_admin_dates_keyboard():
    buttons = []
    today = datetime.now()
    for i in range(60):
        current_date = today + timedelta(days=i)
        date_text = format_date_for_storage(current_date)
        buttons.append(KeyboardButton(f"📅 {date_text}"))
    rows = [buttons[i : i + 3] for i in range(0, len(buttons), 3)]
    rows.append([KeyboardButton("🔙 Назад в админку")])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def create_admin_appointments_keyboard(appointments):
    keyboard = []
    for app in appointments:
        status_icon = "✅" if app[6] == "confirmed" else "⏳"
        keyboard.append(
            [
                InlineKeyboardButton(
                    f"{status_icon} {app[3]} {app[4]} - {app[1]}",
                    callback_data=f"admin_view_{app[0]}",
                )
            ]
        )
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="admin_back")])
    return InlineKeyboardMarkup(keyboard)


def create_admin_appointment_actions(appointment_id, is_expired=False):
    if is_expired:
        keyboard = [
            [InlineKeyboardButton("✉️ Написать клиенту", callback_data=f"admin_message_{appointment_id}")],
            [InlineKeyboardButton("🔙 Назад к списку", callback_data="admin_back_to_list")],
        ]
    else:
        keyboard = [
            [
                InlineKeyboardButton("✅ Подтвердить оплату", callback_data=f"confirm_payment_{appointment_id}"),
                InlineKeyboardButton("❌ Отменить запись", callback_data=f"admin_cancel_{appointment_id}"),
            ],
            [
                InlineKeyboardButton("✉️ Написать клиенту", callback_data=f"admin_message_{appointment_id}"),
                InlineKeyboardButton("🔙 Назад к списку", callback_data="admin_back_to_list"),
            ],
        ]
    return InlineKeyboardMarkup(keyboard)


def create_admin_main_keyboard():
    keyboard = [
        [KeyboardButton("📋 Все записи"), KeyboardButton("📅 Записи на сегодня")],
        [KeyboardButton("🗓️ Записи по дате"), KeyboardButton("🔍 Поиск по телефону")],
        [KeyboardButton("✉️ Сообщения от клиентов"), KeyboardButton("🚫 Управление выходными")],
        [KeyboardButton("📢 Сделать рассылку"), KeyboardButton("❌ Закрыть админ-панель")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def create_cancellation_confirmation_keyboard(appointment_id, is_less_than_24h):
    if is_less_than_24h:
        keyboard = [
            [
                InlineKeyboardButton("✅ Да, я понимаю", callback_data=f"confirm_cancel_{appointment_id}"),
                InlineKeyboardButton("❌ Нет, оставить запись", callback_data="back_to_appointments"),
            ]
        ]
    else:
        keyboard = [
            [
                InlineKeyboardButton("✉️ Написать мастеру", callback_data="client_to_admin_message"),
                InlineKeyboardButton("❌ Оставить запись", callback_data="back_to_appointments"),
            ]
        ]
    return InlineKeyboardMarkup(keyboard)


# ------------------ Уведомления и фоновые задачи ------------------
async def notify_admin_about_new_booking(context, appointment_data):
    try:
        message = (
            "📋 **НОВАЯ ЗАПИСЬ!**\n\n"
            f"👤 **Клиент:** {appointment_data['name']}\n"
            f"📞 **Телефон:** {appointment_data['phone']}\n"
            f"📅 **Дата:** {appointment_data['date']}\n"
            f"⏰ **Время:** {appointment_data['time']}\n"
            f"#️⃣ **Номер записи:** #{appointment_data['id']}\n"
            f"💰 **Статус оплаты:** ❌ Ожидает оплаты"
        )

        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=message,
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("✅ Подтвердить оплату", callback_data=f"confirm_payment_{appointment_data['id']}"),
                        InlineKeyboardButton("❌ Отменить запись", callback_data=f"admin_cancel_{appointment_data['id']}"),
                    ]
                ]
            ),
        )
    except Exception as e:
        print(f"Ошибка отправки уведомления мастеру: {e}")


async def notify_client_about_confirmation(context, appointment):
    try:
        if appointment and appointment[7]:
            message = (
                "✅ **ОПЛАТА ПОДТВЕРЖДЕНА!**\n\n"
                f"Ваша запись подтверждена:\n"
                f"📅 **Дата:** {appointment[3]}\n"
                f"⏰ **Время:** {appointment[4]}\n\n"
                "Ждем вас в салоне!"
            )
            await context.bot.send_message(
                chat_id=appointment[7],
                text=message,
                reply_markup=ReplyKeyboardMarkup(
                    [
                        [KeyboardButton("📅 Записаться на маникюр")],
                        [KeyboardButton("📋 Мои записи")],
                        [KeyboardButton("✉️ Написать мастеру")],
                    ],
                    resize_keyboard=True,
                ),
            )
    except Exception as e:
        print(f"Ошибка отправки уведомления клиенту: {e}")


async def notify_client_about_cancellation(context, appointment, cancelled_by_admin=False):
    try:
        if appointment and appointment[7]:
            if cancelled_by_admin:
                message = (
                    "⚠️ **ВАЖНОЕ УВЕДОМЛЕНИЕ**\n\n"
                    f"Мастер отменил вашу запись:\n"
                    f"📅 **Дата:** {appointment[3]}\n"
                    f"⏰ **Время:** {appointment[4]}\n\n"
                    "Для уточнения деталей напишите мастеру."
                )
            else:
                message = (
                    "⚠️ **ВАЖНОЕ УВЕДОМЛЕНИЕ**\n\n"
                    f"Ваша запись отменена:\n"
                    f"📅 **Дата:** {appointment[3]}\n"
                    f"⏰ **Время:** {appointment[4]}\n\n"
                    "Для новой записи нажмите кнопку ниже."
                )

            reply_markup = ReplyKeyboardMarkup(
                [
                    [KeyboardButton("📅 Записаться на маникюр")],
                    [KeyboardButton("✉️ Написать мастеру")],
                ],
                resize_keyboard=True,
            )

            await context.bot.send_message(chat_id=appointment[7], text=message, reply_markup=reply_markup)
    except Exception as e:
        print(f"Ошибка отправки уведомления клиенту: {e}")


async def notify_client_about_payment_expired(context, appointment):
    try:
        if appointment and appointment[7]:
            message = (
                "⏰ **ВРЕМЯ ОПЛАТЫ ИСТЕКЛО**\n\n"
                f"К сожалению, время на оплату записи истекло:\n"
                f"📅 **Дата:** {appointment[3]}\n"
                f"⏰ **Время:** {appointment[4]}\n\n"
                "Вы можете создать новую запись."
            )
            await context.bot.send_message(
                chat_id=appointment[7],
                text=message,
                reply_markup=ReplyKeyboardMarkup(
                    [
                        [KeyboardButton("📅 Записаться на маникюр")],
                        [KeyboardButton("✉️ Написать мастеру")],
                    ],
                    resize_keyboard=True,
                ),
            )
    except Exception as e:
        print(f"Ошибка отправки уведомления клиенту: {e}")


async def send_reminders(context: ContextTypes.DEFAULT_TYPE):
    try:
        tomorrow_dt = datetime.now() + timedelta(days=1)
        tomorrow = format_date_for_storage(tomorrow_dt)
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM appointments
            WHERE appointment_date = ? AND status = 'confirmed'
        """,
            (tomorrow,),
        )
        appointments = cursor.fetchall()
        conn.close()
        for appointment in appointments:
            if appointment[7]:
                message = (
                    "🔔 **НАПОМИНАНИЕ О ЗАПИСИ**\n\n"
                    f"Напоминаем, что завтра у вас запись на маникюр:\n"
                    f"📅 **Дата:** {appointment[3]}\n"
                    f"⏰ **Время:** {appointment[4]}\n\n"
                    "Ждем вас в салоне!"
                )
                await context.bot.send_message(chat_id=appointment[7], text=message)
                print(f"Отправлено напоминание для записи #{appointment[0]}")
    except Exception as e:
        print(f"Ошибка отправки напоминаний: {e}")


async def check_expired_payments(context: ContextTypes.DEFAULT_TYPE):
    try:
        pending_appointments = get_pending_appointments()
        now = datetime.now()
        for appointment in pending_appointments:
            created_at = datetime.strptime(appointment[5], "%Y-%m-%d %H:%M:%S")
            if (now - created_at).total_seconds() > 600:  # 10 minutes
                expired_appointment = expire_appointment(appointment[0])
                await notify_client_about_payment_expired(context, expired_appointment)
                try:
                    admin_message = (
                        f"⏰ **Автоматическая отмена записи**\n\n"
                        f"#️⃣ **Номер записи:** #{appointment[0]}\n"
                        f"👤 **Клиент:** {appointment[1]}\n"
                        f"📞 **Телефон:** {appointment[2]}\n"
                        f"📅 **Дата:** {appointment[3]}\n"
                        f"⏰ **Время:** {appointment[4]}\n"
                        f"💳 **Причина:** Истекло время оплаты"
                    )
                    await context.bot.send_message(
                        chat_id=ADMIN_ID,
                        text=admin_message,
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✉️ Написать клиенту", callback_data=f"admin_message_{appointment[0]}")]]),
                    )
                except Exception as e:
                    print(f"Ошибка уведомления администратора: {e}")
    except Exception as e:
        print(f"Ошибка проверки просроченных оплат: {e}")


# ------------------ Клиентские команды ------------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    save_bot_user(chat_id=update.message.chat.id, username=getattr(user, "username", None), first_name=getattr(user, "first_name", None), last_name=getattr(user, "last_name", None))
    keyboard = [
        [KeyboardButton("📅 Записаться на маникюр")],
        [KeyboardButton("📋 Мои записи")],
        [KeyboardButton("✉️ Написать мастеру")],
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("Привет! Я бот для записи на маникюр. Выберите действие:", reply_markup=reply_markup)


async def start_booking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Выберите удобную дату (доступны даты на месяц вперед):", reply_markup=create_dates_keyboard())
    return SELECT_DATE


async def select_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    selected_date = update.message.text
    if selected_date == "❌ Отмена":
        await update.message.reply_text("Запись отменена.", reply_markup=ReplyKeyboardMarkup([[KeyboardButton("📅 Записаться на маникюр")],[KeyboardButton("📋 Мои записи")],[KeyboardButton("✉️ Написать мастеру")]], resize_keyboard=True))
        return ConversationHandler.END
    if not is_future_date(selected_date):
        await update.message.reply_text("Нельзя выбрать прошедшую дату. Выберите другую дату:", reply_markup=create_dates_keyboard())
        return SELECT_DATE
    if is_slot_blocked(selected_date, None):
        await update.message.reply_text("На эту дату запись невозможна. Выберите другую дату:", reply_markup=create_dates_keyboard())
        return SELECT_DATE
    context.user_data["selected_date"] = selected_date
    time_keyboard, available_slots = create_time_keyboard(selected_date)
    if not available_slots:
        await update.message.reply_text(f"На {selected_date} нет свободного времени. Выберите другую дату.", reply_markup=create_dates_keyboard())
        return SELECT_DATE
    await update.message.reply_text(f"Вы выбрали: {selected_date}\nСвободное время:", reply_markup=time_keyboard)
    return SELECT_TIME


async def select_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    selected_time = update.message.text
    if selected_time == "❌ Отмена":
        await update.message.reply_text("Запись отменена.", reply_markup=ReplyKeyboardMarkup([[KeyboardButton("📅 Записаться на маникюр")],[KeyboardButton("📋 Мои записи")],[KeyboardButton("✉️ Написать мастеру")]], resize_keyboard=True))
        return ConversationHandler.END
    selected_date = context.user_data.get("selected_date")
    if not is_valid_datetime(selected_date, selected_time):
        await update.message.reply_text("Это время уже прошло. Пожалуйста, выберите другое время.", reply_markup=create_dates_keyboard())
        return SELECT_DATE
    if is_time_slot_taken(selected_date, selected_time):
        await update.message.reply_text("Это время только что заняли. Пожалуйста, выберите другое время.", reply_markup=create_dates_keyboard())
        return SELECT_DATE
    if is_slot_blocked(selected_date, selected_time):
        await update.message.reply_text("Это время недоступно для записи. Пожалуйста, выберите другое время.", reply_markup=create_dates_keyboard())
        return SELECT_DATE
    context.user_data["selected_time"] = selected_time
    await update.message.reply_text("Отлично! Теперь введите ваше имя:", reply_markup=ReplyKeyboardMarkup([[KeyboardButton("❌ Отмена")]], resize_keyboard=True))
    return ENTER_NAME


async def enter_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    client_name = update.message.text
    if client_name == "❌ Отмена":
        await update.message.reply_text("Запись отменена.", reply_markup=ReplyKeyboardMarkup([[KeyboardButton("📅 Записаться на маникюр")],[KeyboardButton("📋 Мои записи")],[KeyboardButton("✉️ Написать мастеру")]], resize_keyboard=True))
        return ConversationHandler.END
    context.user_data["client_name"] = client_name
    await update.message.reply_text("Введите ваш номер телефона:", reply_markup=ReplyKeyboardMarkup([[KeyboardButton("📱 Отправить номер", request_contact=True)],[KeyboardButton("❌ Отмена")]], resize_keyboard=True))
    return ENTER_PHONE


async def enter_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.contact:
        client_phone = update.message.contact.phone_number
    else:
        client_phone = update.message.text
    if client_phone == "❌ Отмена":
        await update.message.reply_text("Запись отменена.", reply_markup=ReplyKeyboardMarkup([[KeyboardButton("📅 Записаться на маникюр")],[KeyboardButton("📋 Мои записи")],[KeyboardButton("✉️ Написать мастеру")]], resize_keyboard=True))
        context.user_data.clear()
        return ConversationHandler.END
    client_name = context.user_data.get("client_name")
    selected_date = context.user_data.get("selected_date")
    selected_time = context.user_data.get("selected_time")
    client_chat_id = update.message.chat.id
    appointment_id = save_appointment_to_db(client_name, client_phone, selected_date, selected_time, client_chat_id)
    appointment_data = {"id": appointment_id, "name": client_name, "phone": client_phone, "date": selected_date, "time": selected_time}
    # notify admin
    await notify_admin_about_new_booking(context, appointment_data)
    message = (
        "✅ **ЗАПИСЬ СОЗДАНА!**\n\n"
        f"👤 **Имя:** {client_name}\n"
        f"📞 **Телефон:** {client_phone}\n"
        f"📅 **Дата:** {selected_date}\n"
        f"⏰ **Время:** {selected_time}\n\n"
        f"💳 **Для подтверждения записи необходимо внести предоплату.**\n"
        f"Ссылка для оплаты: {YUMMY_PAYMENT_LINK}\n\n"
        f"⏰ **Время на оплату:** 10 минут\n"
        f"После оплаты мастер подтвердит вашу запись."
    )
    await update.message.reply_text(message, reply_markup=ReplyKeyboardMarkup([[KeyboardButton("📅 Записаться на маникюр")],[KeyboardButton("📋 Мои записи")],[KeyboardButton("✉️ Написать мастеру")]], resize_keyboard=True))
    context.user_data.clear()
    return ConversationHandler.END


async def show_my_appointments(update: Update, context: ContextTypes.DEFAULT_TYPE):
    client_chat_id = update.message.chat.id
    appointments = get_client_appointments(client_chat_id)
    if not appointments:
        await update.message.reply_text("У вас нет активных записей.", reply_markup=ReplyKeyboardMarkup([[KeyboardButton("📅 Записаться на маникюр")],[KeyboardButton("📋 Мои записи")],[KeyboardButton("✉️ Написать мастеру")]], resize_keyboard=True))
        return
    message = "📋 **ВАШИ АКТИВНЫЕ ЗАПИСИ:**\n\n"
    for app in appointments:
        status_icon = "✅" if app[6] == "confirmed" else "⏳"
        message += f"{status_icon} **{app[3]} {app[4]}**\n"
        message += f"Статус: {'Подтверждена' if app[6] == 'confirmed' else 'Ожидает оплаты'}\n\n"
    await update.message.reply_text(message, reply_markup=create_my_appointments_keyboard(appointments))


# ------------------ Клиентская отмена ------------------
async def client_cancel_appointment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    appointment_id = int(query.data.split("_")[-1])
    appointment = get_appointment_by_id(appointment_id)
    if not appointment:
        await query.edit_message_text("Запись не найдена.")
        return
    if appointment[6] == "expired":
        await query.edit_message_text("Эта запись уже была автоматически отменена по истечении времени оплаты.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад к записям", callback_data="back_to_appointments")]]))
        return
    appointment_datetime = get_appointment_datetime(appointment)
    now = datetime.now()
    if appointment_datetime:
        time_diff = appointment_datetime - now
        is_less_than_24h = time_diff.total_seconds() < 24 * 3600
        if appointment[6] == "confirmed" and appointment[8] == "paid":
            if is_less_than_24h:
                message = (
                    "⚠️ **ВНИМАНИЕ!**\n\n"
                    f"До вашей записи осталось менее 24 часов.\n\n"
                    f"📅 **Дата:** {appointment[3]}\n"
                    f"⏰ **Время:** {appointment[4]}\n\n"
                    f"💰 **Условия отмены:**\n"
                    "При отмене записи менее чем за 24 часа предоплата не возвращается.\n\n"
                    "Вы уверены, что хотите отменить запись?"
                )
            else:
                message = (
                    "⚠️ **ВНИМАНИЕ!**\n\n"
                    f"📅 **Дата:** {appointment[3]}\n"
                    f"⏰ **Время:** {appointment[4]}\n\n"
                    "💰 **Условия отмены:**\n"
                    "Для возврата предоплаты необходимо написать мастеру.\n\n"
                    "Нажмите кнопку ниже, чтобы написать мастеру."
                )
        else:
            message = (
                "❓ **ПОДТВЕРЖДЕНИЕ ОТМЕНЫ**\n\n"
                f"📅 **Дата:** {appointment[3]}\n"
                f"⏰ **Время:** {appointment[4]}\n\n"
                "Вы уверены, что хотите отменить запись?"
            )
            is_less_than_24h = False
        await query.edit_message_text(message, reply_markup=create_cancellation_confirmation_keyboard(appointment_id, is_less_than_24h))
        context.user_data["pending_cancellation"] = appointment_id
        return
    await query.edit_message_text("Ошибка при обработке записи.")


async def confirm_cancellation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "back_to_appointments":
        # показать список обратно
        await show_my_appointments_back(query)
        return
    elif query.data == "client_to_admin_message":
        await start_client_to_admin_message(update, context)
        return
    appointment_id = int(query.data.split("_")[-1])
    appointment = get_appointment_by_id(appointment_id)
    if not appointment:
        await query.edit_message_text("Запись не найдена.")
        return
    cancelled_appointment = cancel_appointment(appointment_id)
    try:
        admin_message = (
            f"❌ **КЛИЕНТ ОТМЕНИЛ ЗАПИСЬ**\n\n"
            f"#️⃣ **Номер записи:** #{appointment[0]}\n"
            f"👤 **Клиент:** {appointment[1]}\n"
            f"📞 **Телефон:** {appointment[2]}\n"
            f"📅 **Дата:** {appointment[3]}\n"
            f"⏰ **Время:** {appointment[4]}"
        )
        await context.bot.send_message(chat_id=ADMIN_ID, text=admin_message, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✉️ Написать клиенту", callback_data=f"admin_reply_{appointment[7]}")]]))
    except Exception as e:
        print(f"Ошибка уведомления администратора: {e}")
    await query.edit_message_text("✅ Запись успешно отменена.", reply_markup=ReplyKeyboardMarkup([[KeyboardButton("📅 Записаться на маникюр")],[KeyboardButton("📋 Мои записи")],[KeyboardButton("✉️ Написать мастеру")]], resize_keyboard=True))


async def show_my_appointments_back(query):
    client_chat_id = query.message.chat.id
    appointments = get_client_appointments(client_chat_id)
    if not appointments:
        await query.edit_message_text("У вас нет активных записей.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]]))
        return
    message = "📋 **ВАШИ АКТИВНЫЕ ЗАПИСИ:**\n\n"
    for app in appointments:
        status_icon = "✅" if app[6] == "confirmed" else "⏳"
        message += f"{status_icon} **{app[3]} {app[4]}**\n"
        message += f"Статус: {'Подтверждена' if app[6] == 'confirmed' else 'Ожидает оплаты'}\n\n"
    await query.edit_message_text(message, reply_markup=create_my_appointments_keyboard(appointments))


# ------------------ Клиент → Мастер (сообщение) ------------------
async def start_client_to_admin_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # может быть вызван через callback_query или через кнопку
    query = getattr(update, "callback_query", None)
    if query:
        await query.answer()
        await query.edit_message_text("💬 **НАПИСАТЬ МАСТЕРУ**\n\nНапишите ваше сообщение мастеру. Он получит его и ответит вам здесь же.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data="back_to_main")]]))
    else:
        await update.message.reply_text("💬 **НАПИСАТЬ МАСТЕРУ**\n\nНапишите ваше сообщение мастеру. Он получит его и ответит вам здесь же.", reply_markup=ReplyKeyboardMarkup([[KeyboardButton("❌ Отмена")]], resize_keyboard=True))
    context.user_data["waiting_for_client_message"] = True
    return CLIENT_TO_ADMIN_MESSAGE


async def handle_client_to_admin_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ Отмена":
        context.user_data.pop("waiting_for_client_message", None)
        await update.message.reply_text("Сообщение отменено.", reply_markup=ReplyKeyboardMarkup([[KeyboardButton("📅 Записаться на маникюр")],[KeyboardButton("📋 Мои записи")],[KeyboardButton("✉️ Написать мастеру")]], resize_keyboard=True))
        return ConversationHandler.END
    client_message = update.message.text
    client_chat_id = update.message.chat.id
    client_name = update.message.from_user.first_name or "Клиент"
    save_message(client_chat_id, client_name, client_message, is_from_client=True)
    try:
        admin_message = f"💬 **НОВОЕ СООБЩЕНИЕ ОТ КЛИЕНТА**\n\n👤 **Клиент:** {client_name}\n🆔 **Chat ID:** {client_chat_id}\n💭 **Сообщение:**\n{client_message}"
        await context.bot.send_message(chat_id=ADMIN_ID, text=admin_message, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✉️ Ответить", callback_data=f"admin_reply_{client_chat_id}")]]))
        await update.message.reply_text("✅ Ваше сообщение отправлено мастеру. Ожидайте ответа здесь же.", reply_markup=ReplyKeyboardMarkup([[KeyboardButton("📅 Записаться на маникюр")],[KeyboardButton("📋 Мои записи")],[KeyboardButton("✉️ Написать мастеру")]], resize_keyboard=True))
    except Exception as e:
        await update.message.reply_text("❌ Ошибка отправки сообщения. Попробуйте позже.", reply_markup=ReplyKeyboardMarkup([[KeyboardButton("📅 Записаться на маникюр")],[KeyboardButton("📋 Мои записи")],[KeyboardButton("✉️ Написать мастеру")]], resize_keyboard=True))
        print(f"Ошибка отправки сообщения администратору: {e}")
    context.user_data.pop("waiting_for_client_message", None)
    return ConversationHandler.END


# ------------------ Админ → Клиент (сообщение) ------------------
async def start_admin_to_client_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # entry via CallbackQueryHandler (admin_reply_$chat_id)
    query = update.callback_query
    await query.answer()
    client_chat_id = int(query.data.split("_")[-1])
    context.user_data["admin_message_client_id"] = client_chat_id
    client_appointments = get_client_appointments(client_chat_id)
    client_name = "Клиент"
    if client_appointments:
        client_name = client_appointments[0][1]
    context.user_data["admin_message_client_name"] = client_name
    await query.edit_message_text(f"💬 **ОТВЕТ КЛИЕНТУ**\n\n👤 **Клиент:** {client_name}\n🆔 **Chat ID:** {client_chat_id}\n\nНапишите ваше сообщение:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data="admin_back_to_messages")]]))
    return ADMIN_TO_CLIENT_MESSAGE


async def handle_admin_to_client_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_message = update.message.text
    client_chat_id = context.user_data.get("admin_message_client_id")
    client_name = context.user_data.get("admin_message_client_name", "Клиент")
    if not client_chat_id:
        await update.message.reply_text("Ошибка: не найден ID клиента.")
        return ConversationHandler.END
    save_message(client_chat_id, client_name, admin_message, is_from_client=False)
    try:
        await context.bot.send_message(chat_id=client_chat_id, text=f"💬 **СООБЩЕНИЕ ОТ МАСТЕРА:**\n\n{admin_message}", reply_markup=ReplyKeyboardMarkup([[KeyboardButton("📅 Записаться на маникюр")],[KeyboardButton("📋 Мои записи")],[KeyboardButton("✉️ Написать мастеру")]], resize_keyboard=True))
        await update.message.reply_text("✅ Сообщение отправлено клиенту.", reply_markup=create_admin_main_keyboard())
    except Exception as e:
        error_message = f"❌ Ошибка отправки сообщения клиенту: {str(e)}"
        if "Chat not found" in str(e):
            error_message = "❌ Ошибка отправки сообщения клиенту: Клиент не найден или не начинал диалог с ботом"
        await update.message.reply_text(error_message, reply_markup=create_admin_main_keyboard())
    context.user_data.pop("admin_message_client_id", None)
    context.user_data.pop("admin_message_client_name", None)
    return ConversationHandler.END


# ------------------ Админ-панель ------------------
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.id != ADMIN_ID:
        await update.message.reply_text("У вас нет доступа к админ-панели.")
        return
    await update.message.reply_text("🔧 **АДМИН-ПАНЕЛЬ**\n\nВыберите действие:", reply_markup=create_admin_main_keyboard())


async def show_all_appointments(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.id != ADMIN_ID:
        return
    appointments = get_all_appointments()
    if not appointments:
        await update.message.reply_text("Нет активных записей.", reply_markup=create_admin_main_keyboard())
        return
    message = "📋 **ВСЕ АКТИВНЫЕ ЗАПИСИ:**\n\n"
    for app in appointments:
        status_icon = "✅" if app[6] == "confirmed" else "⏳"
        payment_status = "💳 Оплачено" if app[8] == "paid" else "❌ Ожидает оплаты"
        message += f"{status_icon} **{app[3]} {app[4]}** - {app[1]}\n"
        message += f"📞 {app[2]} | {payment_status}\n"
        message += f"🆔 #{app[0]}\n\n"
    await update.message.reply_text(message, reply_markup=create_admin_appointments_keyboard(appointments))


async def show_today_appointments(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.id != ADMIN_ID:
        return
    today = format_date_for_storage(datetime.now())
    appointments = get_appointments_by_date(today)
    if not appointments:
        await update.message.reply_text("На сегодня записей нет.", reply_markup=create_admin_main_keyboard())
        return
    message = f"📋 **ЗАПИСИ НА СЕГОДНЯ ({today}):**\n\n"
    for app in appointments:
        status_icon = "✅" if app[6] == "confirmed" else "⏳"
        payment_status = "💳 Оплачено" if app[8] == "paid" else "❌ Ожидает оплаты"
        message += f"{status_icon} **{app[4]}** - {app[1]}\n"
        message += f"📞 {app[2]} | {payment_status}\n"
        message += f"🆔 #{app[0]}\n\n"
    await update.message.reply_text(message, reply_markup=create_admin_appointments_keyboard(appointments))


async def show_appointments_by_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.id != ADMIN_ID:
        return
    await update.message.reply_text("Выберите дату для просмотра записей:", reply_markup=create_admin_dates_keyboard())


async def handle_date_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.id != ADMIN_ID:
        return
    selected_date = update.message.text.replace("📅 ", "")
    appointments = get_appointments_by_date(selected_date)
    if not appointments:
        await update.message.reply_text(f"На {selected_date} записей нет.", reply_markup=create_admin_main_keyboard())
        return
    message = f"📋 **ЗАПИСИ НА {selected_date.upper()}:**\n\n"
    for app in appointments:
        status_icon = "✅" if app[6] == "confirmed" else "⏳"
        payment_status = "💳 Оплачено" if app[8] == "paid" else "❌ Ожидает оплаты"
        message += f"{status_icon} **{app[4]}** - {app[1]}\n"
        message += f"📞 {app[2]} | {payment_status}\n"
        message += f"🆔 #{app[0]}\n\n"
    await update.message.reply_text(message, reply_markup=create_admin_appointments_keyboard(appointments))


async def search_by_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.id != ADMIN_ID:
        return
    await update.message.reply_text("Введите номер телефона для поиска (можно часть номера):", reply_markup=ReplyKeyboardMarkup([[KeyboardButton("🔙 Назад в админку")]], resize_keyboard=True))
    return ADMIN_SEARCH_CLIENT


async def handle_phone_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.id != ADMIN_ID:
        return ConversationHandler.END
    if update.message.text == "🔙 Назад в админку":
        await update.message.reply_text("Поиск отменен.", reply_markup=create_admin_main_keyboard())
        return ConversationHandler.END
    phone_query = update.message.text
    appointments = search_appointments_by_phone(phone_query)
    if not appointments:
        await update.message.reply_text(f"Записей с номером '{phone_query}' не найдено.", reply_markup=create_admin_main_keyboard())
        return ConversationHandler.END
    message = f"🔍 **РЕЗУЛЬТАТЫ ПОИСКА ПО '{phone_query}':**\n\n"
    for app in appointments:
        status_icon = "✅" if app[6] == "confirmed" else "⏳"
        payment_status = "💳 Оплачено" if app[8] == "paid" else "❌ Ожидает оплаты"
        message += f"{status_icon} **{app[3]} {app[4]}** - {app[1]}\n"
        message += f"📞 {app[2]} | {payment_status}\n"
        message += f"🆔 #{app[0]}\n\n"
    await update.message.reply_text(message, reply_markup=create_admin_appointments_keyboard(appointments))
    return ConversationHandler.END


async def show_client_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.id != ADMIN_ID:
        return
    messages = get_client_messages(limit=20)
    if not messages:
        await update.message.reply_text("Нет сообщений от клиентов.", reply_markup=create_admin_main_keyboard())
        return
    message = "💬 **ПОСЛЕДНИЕ СООБЩЕНИЯ ОТ КЛИЕНТОВ:**\n\n"
    for msg in messages:
        message += f"👤 **{msg[2]}** (ID: {msg[1]})\n"
        message += f"💭 {msg[3]}\n"
        message += f"📅 {msg[5]}\n\n"
        message += "─" * 30 + "\n\n"
    await update.message.reply_text(message[:4000], reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📋 Все записи", callback_data="admin_all_appointments"), InlineKeyboardButton("🔙 Назад", callback_data="admin_back")]]))


# ------------------ Работа с просмотром и callback'ами админки ------------------
async def handle_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "admin_back":
        await query.edit_message_text("🔧 **АДМИН-ПАНЕЛЬ**\n\nВыберите действие:", reply_markup=create_admin_main_keyboard())
        return

    if data == "admin_back_to_list":
        await show_all_appointments_from_callback(query, context)
        return

    if data == "admin_all_appointments":
        await show_all_appointments_from_callback(query, context)
        return

    if data.startswith("admin_view_"):
        appointment_id = int(data.split("_")[-1])
        await show_appointment_details(query, context, appointment_id)
        return

    if data.startswith("confirm_payment_"):
        appointment_id = int(data.split("_")[-1])
        await confirm_payment_callback(query, context, appointment_id)
        return

    if data.startswith("admin_cancel_"):
        appointment_id = int(data.split("_")[-1])
        await admin_cancel_appointment(query, context, appointment_id)
        return

    if data.startswith("admin_message_"):
        appointment_id = int(data.split("_")[-1])
        appointment = get_appointment_by_id(appointment_id)
        if appointment and appointment[7]:
            await start_admin_to_client_message_from_appointment(query, context, appointment)
        return

    if data.startswith("admin_reply_"):
        client_chat_id = int(data.split("_")[-1])
        context.user_data["admin_message_client_id"] = client_chat_id
        client_appointments = get_client_appointments(client_chat_id)
        client_name = "Клиент"
        if client_appointments:
            client_name = client_appointments[0][1]
        context.user_data["admin_message_client_name"] = client_name
        await query.edit_message_text(f"💬 **ОТВЕТ КЛИЕНТУ**\n\n👤 **Клиент:** {client_name}\n🆔 **Chat ID:** {client_chat_id}\n\nНапишите ваше сообщение:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data="admin_back_to_messages")]]))
        return

    if data == "admin_back_to_messages":
        await show_client_messages_from_callback(query, context)
        return

    if data == "back_to_appointments":
        await show_my_appointments_back(query)
        return

    # неизвестный callback — оставим
    await query.edit_message_text("Неподдерживаемое действие.")


async def show_all_appointments_from_callback(query, context):
    appointments = get_all_appointments()
    if not appointments:
        await query.edit_message_text("Нет активных записей.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="admin_back")]]))
        return
    message = "📋 **ВСЕ АКТИВНЫЕ ЗАПИСИ:**\n\n"
    for app in appointments:
        status_icon = "✅" if app[6] == "confirmed" else "⏳"
        payment_status = "💳 Оплачено" if app[8] == "paid" else "❌ Ожидает оплаты"
        message += f"{status_icon} **{app[3]} {app[4]}** - {app[1]}\n"
        message += f"📞 {app[2]} | {payment_status}\n"
        message += f"🆔 #{app[0]}\n\n"
    await query.edit_message_text(message, reply_markup=create_admin_appointments_keyboard(appointments))


async def show_appointment_details(query, context, appointment_id):
    appointment = get_appointment_by_id(appointment_id)
    if not appointment:
        await query.edit_message_text("Запись не найдена.")
        return
    status_text = "✅ Подтверждена" if appointment[6] == "confirmed" else "⏳ Ожидает оплаты"
    payment_status = "💳 Оплачено" if appointment[8] == "paid" else "❌ Ожидает оплаты"
    is_expired = appointment[6] == "expired"
    message = (
        f"📋 **ДЕТАЛИ ЗАПИСИ #{appointment[0]}**\n\n"
        f"👤 **Клиент:** {appointment[1]}\n"
        f"📞 **Телефон:** {appointment[2]}\n"
        f"📅 **Дата:** {appointment[3]}\n"
        f"⏰ **Время:** {appointment[4]}\n"
        f"📊 **Статус:** {status_text}\n"
        f"💰 **Оплата:** {payment_status}\n"
        f"🆔 **Chat ID:** {appointment[7]}"
    )
    await query.edit_message_text(message, reply_markup=create_admin_appointment_actions(appointment_id, is_expired))


async def confirm_payment_callback(query, context, appointment_id):
    appointment = confirm_payment(appointment_id)
    if appointment:
        # Блокируем слот (время) после подтверждения оплаты
        try:
            add_blocked_slot(appointment[3], appointment[4], reason="Подтвержденная запись")
        except Exception as e:
            print(f"Ошибка при блокировке слота: {e}")
        await notify_client_about_confirmation(context, appointment)
        await query.edit_message_text(
            f"✅ Оплата для записи #{appointment_id} подтверждена! Клиент уведомлен.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад к списку", callback_data="admin_back_to_list")]]),
        )
    else:
        await query.edit_message_text(
            "❌ Ошибка подтверждения оплаты.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад к списку", callback_data="admin_back_to_list")]]),
        )


async def admin_cancel_appointment(query, context, appointment_id):
    appointment = get_appointment_by_id(appointment_id)
    if not appointment:
        await query.edit_message_text("Запись не найдена.")
        return
    if appointment[6] == "expired":
        await query.edit_message_text("❌ Эту запись нельзя отменить - она уже автоматически отменена по истечении времени оплаты.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✉️ Написать клиенту", callback_data=f"admin_message_{appointment_id}"), InlineKeyboardButton("🔙 Назад", callback_data=f"admin_view_{appointment_id}")]]))
        return
    cancelled_appointment = cancel_appointment(appointment_id)
    await notify_client_about_cancellation(context, cancelled_appointment, cancelled_by_admin=True)
    await query.edit_message_text(f"✅ Запись #{appointment_id} отменена! Клиент уведомлен.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад к списку", callback_data="admin_back_to_list")]]))


async def start_admin_to_client_message_from_appointment(query, context, appointment):
    await query.answer()

    # Сохраняем ID и имя клиента в user_data, чтобы потом использовать в сообщении
    context.user_data["admin_message_client_id"] = appointment[7]   # chat_id клиента
    context.user_data["admin_message_client_name"] = appointment[1] # имя клиента

    message = (
        f"💬 **НАПИСАТЬ КЛИЕНТУ**\n\n"
        f"👤 **Клиент:** {appointment[1]}\n"
        f"📞 **Телефон:** {appointment[2]}\n"
        f"🆔 **Chat ID:** {appointment[7]}\n\n"
        "Напишите ваше сообщение клиенту:"
    )

    # Показываем кнопку "Отмена" → вернёт в просмотр записи
    await query.edit_message_text(
        message,
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("❌ Отмена", callback_data=f"admin_view_{appointment[0]}")]]
        ),
    )

    return ADMIN_TO_CLIENT_MESSAGE



async def show_client_messages_from_callback(query, context):
    messages = get_client_messages(limit=20)
    if not messages:
        await query.edit_message_text("Нет сообщений от клиентов.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="admin_back")]]))
        return
    message = "💬 **ПОСЛЕДНИЕ СООБЩЕНИЯ ОТ КЛИЕНТОВ:**\n\n"
    for msg in messages:
        message += f"👤 **{msg[2]}** (ID: {msg[1]})\n"
        message += f"💭 {msg[3]}\n"
        message += f"📅 {msg[5]}\n\n"
        message += "─" * 30 + "\n\n"
    await query.edit_message_text(message[:4000], reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📋 Все записи", callback_data="admin_all_appointments"), InlineKeyboardButton("🔙 Назад", callback_data="admin_back")]]))


# ------------------ Блокировка дней/времён (админ) ------------------
async def manage_blocked_slots(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.id != ADMIN_ID:
        return
    keyboard = [
        [InlineKeyboardButton("🚫 Заблокировать день", callback_data="block_day")],
        [InlineKeyboardButton("🚫 Заблокировать время", callback_data="block_time")],
        [InlineKeyboardButton("📋 Показать заблокированные", callback_data="show_blocked")],
        [InlineKeyboardButton("🔙 Назад", callback_data="admin_back")],
    ]
    await update.message.reply_text("🗓️ **УПРАВЛЕНИЕ ВЫХОДНЫМИ**\n\nВыберите действие:", reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_blocked_slots_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # 🚫 Блокировка целого дня
    if query.data == "block_day":
        await query.message.reply_text(
            "Выберите дату для блокировки:",
            reply_markup=create_admin_dates_keyboard()
        )
        context.user_data["blocking_day"] = True
        return

    # 🚫 Блокировка конкретного времени
    if query.data == "block_time":
        await query.message.reply_text(
            "Выберите дату для блокировки времени:",
            reply_markup=create_admin_dates_keyboard()
        )
        context.user_data["blocking_time"] = True
        return

    # 📋 Показать список всех заблокированных слотов
    if query.data == "show_blocked":
        blocked_slots = get_blocked_slots()
        if not blocked_slots:
            await query.edit_message_text(
                "Нет заблокированных слотов.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🔙 Назад", callback_data="admin_back")]]
                ),
            )
            return

        message = "🚫 **ЗАБЛОКИРОВАННЫЕ СЛОТЫ:**\n\n"
        for slot in blocked_slots:
            if slot[3]:  # is_all_day
                message += f"📅 **{slot[1]}** - весь день"
            else:
                message += f"📅 **{slot[1]} {slot[2]}**"
            if slot[4]:
                message += f" - {slot[4]}"
            message += f"\n🆔 #{slot[0]}\n\n"

        keyboard = []
        for slot in blocked_slots:
            button_text = f"❌ Удалить {slot[1]}" + (f" {slot[2]}" if slot[2] else "")
            keyboard.append([InlineKeyboardButton(button_text, callback_data=f"remove_blocked_{slot[0]}")])
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="admin_back")])

        await query.edit_message_text(message, reply_markup=InlineKeyboardMarkup(keyboard))
        return



async def handle_blocked_date_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.id != ADMIN_ID:
        return
    selected_date = update.message.text.replace("📅 ", "")
    if context.user_data.get("blocking_day"):
        add_blocked_slot(selected_date, reason="Выходной")
        await update.message.reply_text(f"✅ День {selected_date} заблокирован!", reply_markup=create_admin_main_keyboard())
        context.user_data.pop("blocking_day", None)
        return
    if context.user_data.get("blocking_time"):
        context.user_data["blocking_time_date"] = selected_date
        all_time_slots = ["09:00", "10:00", "11:00", "12:00", "13:00", "14:00", "15:00", "16:00", "17:00", "18:00", "19:00", "20:00"]
        keyboard = []
        row = []
        for time_slot in all_time_slots:
            row.append(KeyboardButton(time_slot))
            if len(row) == 3:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        keyboard.append([KeyboardButton("🔙 Назад")])
        await update.message.reply_text(f"Выберите время для блокировки на {selected_date}:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
        return


async def handle_time_blocking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.id != ADMIN_ID:
        return
    if update.message.text == "🔙 Назад":
        context.user_data.pop("blocking_time", None)
        context.user_data.pop("blocking_time_date", None)
        await update.message.reply_text("Блокировка отменена.", reply_markup=create_admin_main_keyboard())
        return
    selected_time = update.message.text
    selected_date = context.user_data.get("blocking_time_date")
    add_blocked_slot(selected_date, selected_time, reason="Занято")
    await update.message.reply_text(f"✅ Время {selected_time} на {selected_date} заблокировано!", reply_markup=create_admin_main_keyboard())
    context.user_data.pop("blocking_time", None)
    context.user_data.pop("blocking_time_date", None)


async def remove_blocked_slot_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    slot_id = int(query.data.split("_")[-1])
    remove_blocked_slot(slot_id)
    await query.edit_message_text("✅ Слот разблокирован!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="admin_back")]]))


# ------------------ Рассылка ------------------
async def start_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.id != ADMIN_ID:
        return
    await update.message.reply_text("📢 **РАССЫЛКА**\n\nВведите сообщение для рассылки всем пользователям бота:", reply_markup=ReplyKeyboardMarkup([[KeyboardButton("❌ Отмена")]], resize_keyboard=True))
    return BROADCAST_MESSAGE


async def handle_broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.id != ADMIN_ID:
        return ConversationHandler.END
    if update.message.text == "❌ Отмена":
        await update.message.reply_text("Рассылка отменена.", reply_markup=create_admin_main_keyboard())
        return ConversationHandler.END
    broadcast_message = update.message.text
    users = get_all_bot_users()
    if not users:
        await update.message.reply_text("Нет пользователей для рассылки.", reply_markup=create_admin_main_keyboard())
        return ConversationHandler.END
    success_count = 0
    fail_count = 0
    progress_message = await update.message.reply_text(f"📤 Начинаю рассылку... 0/{len(users)}")
    for i, user in enumerate(users):
        try:
            await context.bot.send_message(chat_id=user[1], text=f"📢 **СООБЩЕНИЕ ОТ МАСТЕРА:**\n\n{broadcast_message}", reply_markup=ReplyKeyboardMarkup([[KeyboardButton("📅 Записаться на маникюр")],[KeyboardButton("📋 Мои записи")],[KeyboardButton("✉️ Написать мастеру")]], resize_keyboard=True))
            success_count += 1
        except Exception as e:
            print(f"Ошибка отправки пользователю {user[1]}: {e}")
            fail_count += 1
        if (i + 1) % 5 == 0 or (i + 1) == len(users):
            try:
                await progress_message.edit_text(f"📤 Рассылка... {i + 1}/{len(users)}\n✅ Успешно: {success_count}\n❌ Ошибок: {fail_count}")
            except:
                pass
        await asyncio.sleep(0.1)
    await update.message.reply_text(f"✅ **РАССЫЛКА ЗАВЕРШЕНА**\n\n📤 Отправлено: {len(users)} пользователей\n✅ Успешно: {success_count}\n❌ Ошибок: {fail_count}", reply_markup=create_admin_main_keyboard())
    return ConversationHandler.END


# ------------------ Router for dates (чтобы не конфликтовать с блокировкой) ------------------
async def handle_dates_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Если админ в процессе блокировки — передаем в handler блокировок
    if context.user_data.get("blocking_day") or context.user_data.get("blocking_time"):
        return await handle_blocked_date_selection(update, context)
    # Иначе — это выбор даты для просмотра записей админа
    return await handle_date_selection(update, context)


# ------------------ Все ConversationHandler ------------------



# ------------------ Запись клиента ------------------
booking_handler = ConversationHandler(
    entry_points=[
        MessageHandler(filters.Regex("^📅 Записаться на маникюр$"), start_booking)
    ],
    states={
        SELECT_DATE: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, select_date)
        ],
        SELECT_TIME: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, select_time)
        ],
        ENTER_NAME: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, enter_name)
        ],
        ENTER_PHONE: [
            MessageHandler(filters.TEXT | filters.CONTACT, enter_phone)
        ],
    },
    fallbacks=[MessageHandler(filters.Regex("^❌ Отмена$"), start_command)],
)

# ------------------ Рассылка ------------------
broadcast_handler = ConversationHandler(
    entry_points=[
        MessageHandler(filters.Regex("^📢 Сделать рассылку$"), start_broadcast)
    ],
    states={
        BROADCAST_MESSAGE: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_broadcast_message)
        ],
    },
    fallbacks=[MessageHandler(filters.Regex("^❌ Отмена$"), start_command)],
)

# ------------------ Поиск по телефону ------------------
search_phone_conv = ConversationHandler(
    entry_points=[
        MessageHandler(filters.Regex("^🔍 Поиск по телефону$"), search_by_phone)
    ],
    states={
        ADMIN_SEARCH_CLIENT: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_phone_search)
        ],
    },
    fallbacks=[MessageHandler(filters.Regex("^❌ Отмена$"), start_command)],
)

# ------------------ Клиент пишет мастеру ------------------
client_to_admin_conv = ConversationHandler(
    entry_points=[
        MessageHandler(filters.Regex("^✉️ Написать мастеру$"), send_message_to_master)
    ],
    states={
        CLIENT_MESSAGE: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_client_message)
        ]
    },
    fallbacks=[MessageHandler(filters.Regex("^❌ Отмена$"), start_command)],
)

# ------------------ Админ пишет клиенту (инициатива) ------------------
admin_to_client_conv = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(start_admin_to_client_message_from_appointment, pattern="^admin_message_")
    ],
    states={
        ADMIN_TO_CLIENT_MESSAGE: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_to_client_message)
        ]
    },
    fallbacks=[MessageHandler(filters.Regex("^❌ Отмена$"), admin_command)],
)



# ------------------ Основная функция ------------------
def main():
    init_database()
    application = Application.builder().token(BOT_TOKEN).build()

    # === Команды ===
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("admin", admin_command))

    # в main() после init_database() и перед run_webhook()
    job_queue = application.job_queue
    job_queue.run_repeating(check_expired_payments, interval=60, first=10)
    job_queue.run_daily(send_reminders, time=datetime.time(hour=10, minute=0))  # например, напоминание в 10:00


    # === Конверсейшены ===
    application.add_handler(booking_handler)
    application.add_handler(broadcast_handler)
    application.add_handler(search_phone_conv)
    application.add_handler(client_to_admin_conv)
    application.add_handler(admin_to_client_conv)

    # === Клиентские кнопки ===
    application.add_handler(MessageHandler(filters.Regex("^📋 Мои записи$"), show_my_appointments))

    # === Админские кнопки ===
    application.add_handler(MessageHandler(filters.Regex("^📋 Все записи$"), show_all_appointments))
    application.add_handler(MessageHandler(filters.Regex("^📅 Записи на сегодня$"), show_today_appointments))
    application.add_handler(MessageHandler(filters.Regex("^🗓️ Записи по дате$"), show_appointments_by_date))
    application.add_handler(MessageHandler(filters.Regex("^✉️ Сообщения от клиентов$"), show_client_messages))
    application.add_handler(MessageHandler(filters.Regex("^🚫 Управление выходными$"), manage_blocked_slots))

    # === Выбор даты и времени (админ) ===
    application.add_handler(MessageHandler(filters.Regex("^📅 "), handle_dates_router))
    application.add_handler(MessageHandler(filters.Regex(r"^\d{2}:\d{2}$"), handle_time_blocking))

    # === Callback-и ===
    application.add_handler(CallbackQueryHandler(handle_blocked_slots_callback, pattern="^(block_day|block_time|show_blocked)$"))
    application.add_handler(CallbackQueryHandler(remove_blocked_slot_callback, pattern="^remove_blocked_"))
    application.add_handler(CallbackQueryHandler(handle_admin_callback))  # общий обработчик

    # === Запуск через webhook ===
    PORT = int(os.getenv("PORT", 10000))
    APP_NAME = os.getenv("RENDER_EXTERNAL_HOSTNAME")

    print("🚀 Бот запущен через Webhook...")

    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=BOT_TOKEN,
        webhook_url=f"https://{APP_NAME}/{BOT_TOKEN}",
    )


if __name__ == "__main__":
    main()

# bot.py
import os
import sqlite3
import asyncio
import logging
from datetime import datetime, timedelta

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

# ================== НАСТРОЙКИ ==================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8312388794:AAEBvJwzbz750q3AckSocpdGYSK9Gbv2eUI")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "465630314"))

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Проверка токена
if not BOT_TOKEN or BOT_TOKEN == "8312388794:AAEBvJwzbz750q3AckSocpdGYSK9Gbv2eUI":
    logger.error("❌ ОШИБКА: BOT_TOKEN не настроен")
    exit(1)

DB_PATH = "appointments.db"

# Состояния разговора
(
    SELECT_DATE,
    SELECT_TIME,
    ENTER_NAME,
    ENTER_PHONE,
    ADMIN_SEARCH_CLIENT,
    CLIENT_TO_ADMIN_MESSAGE,
    ADMIN_TO_CLIENT_MESSAGE,
    BROADCAST_MESSAGE,
) = range(8)

RUSSIAN_WEEKDAYS = {0: "Пн", 1: "Вт", 2: "Ср", 3: "Чт", 4: "Пт", 5: "Сб", 6: "Вс"}
YUMMY_PAYMENT_LINK = "https://yoomoney.ru/..."  # Замените на реальную ссылку

# ================== БАЗА ДАННЫХ ==================
def init_database():
    """Инициализация базы данных"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # Таблица записей
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS appointments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_name TEXT NOT NULL,
                client_phone TEXT NOT NULL,
                appointment_date TEXT NOT NULL,
                appointment_time TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'pending',
                client_chat_id INTEGER,
                payment_status TEXT DEFAULT 'not_paid'
            )
        ''')

        # Таблица сообщений
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_chat_id INTEGER NOT NULL,
                client_name TEXT NOT NULL,
                message_text TEXT NOT NULL,
                is_from_client BOOLEAN NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Таблица заблокированных слотов
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS blocked_slots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                blocked_date TEXT NOT NULL,
                blocked_time TEXT,
                is_all_day BOOLEAN DEFAULT 0,
                reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Таблица пользователей бота
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS bot_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER UNIQUE NOT NULL,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        conn.commit()
        conn.close()
        logger.info("✅ База данных инициализирована")
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации БД: {e}")

def save_bot_user(chat_id, username=None, first_name=None, last_name=None):
    """Сохранение пользователя бота"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO bot_users (chat_id, username, first_name, last_name) VALUES (?, ?, ?, ?)",
        (chat_id, username, first_name, last_name)
    )
    conn.commit()
    conn.close()

def get_all_bot_users():
    """Получение всех пользователей бота"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM bot_users")
    users = cursor.fetchall()
    conn.close()
    return users

def save_appointment_to_db(name, phone, date, time_slot, chat_id):
    """Сохранение записи в БД"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO appointments (client_name, client_phone, appointment_date, appointment_time, client_chat_id) 
           VALUES (?, ?, ?, ?, ?)""",
        (name, phone, date, time_slot, chat_id)
    )
    conn.commit()
    appointment_id = cursor.lastrowid
    conn.close()
    return appointment_id

def get_client_appointments(chat_id):
    """Получение записей клиента"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """SELECT * FROM appointments 
           WHERE client_chat_id = ? AND status IN ('pending', 'confirmed')
           ORDER BY appointment_date, appointment_time""",
        (chat_id,)
    )
    appointments = cursor.fetchall()
    conn.close()
    return appointments

def get_all_appointments():
    """Получение всех записей"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """SELECT * FROM appointments 
           WHERE status IN ('pending', 'confirmed')
           ORDER BY appointment_date, appointment_time"""
    )
    appointments = cursor.fetchall()
    conn.close()
    return appointments

def get_appointment_by_id(appointment_id):
    """Получение записи по ID"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM appointments WHERE id = ?", (appointment_id,))
    appointment = cursor.fetchone()
    conn.close()
    return appointment

def confirm_payment(appointment_id):
    """Подтверждение оплаты"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE appointments SET status = 'confirmed', payment_status = 'paid' WHERE id = ?",
        (appointment_id,)
    )
    conn.commit()
    conn.close()
    return get_appointment_by_id(appointment_id)

def cancel_appointment(appointment_id):
    """Отмена записи"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE appointments SET status = 'cancelled' WHERE id = ?", (appointment_id,))
    conn.commit()
    conn.close()
    return get_appointment_by_id(appointment_id)

def expire_appointment(appointment_id):
    """Просрочка записи"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE appointments SET status = 'expired' WHERE id = ?", (appointment_id,))
    conn.commit()
    conn.close()
    return get_appointment_by_id(appointment_id)

def get_pending_appointments():
    """Получение ожидающих оплаты записей"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM appointments WHERE status = 'pending' AND payment_status = 'not_paid'"
    )
    appointments = cursor.fetchall()
    conn.close()
    return appointments

def add_blocked_slot(date, time_slot=None, reason=""):
    """Добавление заблокированного слота"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    is_all_day = 1 if time_slot is None else 0
    cursor.execute(
        "INSERT INTO blocked_slots (blocked_date, blocked_time, is_all_day, reason) VALUES (?, ?, ?, ?)",
        (date, time_slot, is_all_day, reason)
    )
    conn.commit()
    conn.close()

def get_blocked_slots(date=None):
    """Получение заблокированных слотов"""
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
    """Проверка заблокирован ли слот"""
    blocked_slots = get_blocked_slots(date)
    for slot in blocked_slots:
        if slot[3]:  # is_all_day
            return True
        elif slot[2] == time_slot:
            return True
    return False

def remove_blocked_slot(slot_id):
    """Удаление заблокированного слота"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM blocked_slots WHERE id = ?", (slot_id,))
    conn.commit()
    conn.close()

def is_time_slot_taken(date, time_slot):
    """Проверка занятости времени"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """SELECT COUNT(*) FROM appointments 
           WHERE appointment_date = ? AND appointment_time = ? AND status IN ('pending', 'confirmed')""",
        (date, time_slot)
    )
    count = cursor.fetchone()[0]
    conn.close()
    return count > 0

def save_message(client_chat_id, client_name, message_text, is_from_client):
    """Сохранение сообщения"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO messages (client_chat_id, client_name, message_text, is_from_client) 
           VALUES (?, ?, ?, ?)""",
        (client_chat_id, client_name, message_text, is_from_client)
    )
    conn.commit()
    conn.close()

def get_client_messages(limit=20):
    """Получение сообщений от клиентов"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """SELECT * FROM messages 
           WHERE is_from_client = 1 
           ORDER BY created_at DESC 
           LIMIT ?""",
        (limit,)
    )
    messages = cursor.fetchall()
    conn.close()
    return messages

# ================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==================
def format_date_for_storage(dt):
    """Форматирование даты для хранения"""
    weekday_russian = RUSSIAN_WEEKDAYS[dt.weekday()]
    return f"{weekday_russian} {dt.strftime('%d.%m')}"

def parse_day_month_from_button(selected_date):
    """Парсинг даты из кнопки"""
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
    """Проверка валидности даты и времени"""
    try:
        now = datetime.now()
        day, month = parse_day_month_from_button(selected_date)
        year = now.year
        selected_month = int(month)
        current_month = now.month
        
        if selected_month < current_month and (current_month - selected_month) > 6:
            year += 1
            
        selected_datetime = datetime(year, selected_month, day)
        hours, minutes = map(int, selected_time.split(":"))
        selected_datetime = selected_datetime.replace(hour=hours, minute=minutes)
        return selected_datetime > now
    except Exception:
        return False

def is_future_date(selected_date):
    """Проверка что дата в будущем"""
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
    """Получение datetime записи"""
    try:
        date_str = appointment[3]
        time_str = appointment[4]
        day, month = parse_day_month_from_button(date_str)
        year = datetime.now().year
        
        if month < datetime.now().month and (datetime.now().month - month) > 6:
            year += 1
            
        hours, minutes = map(int, time_str.split(":"))
        return datetime(year, month, day, hours, minutes)
    except Exception:
        return None

# ================== КЛАВИАТУРЫ ==================
def create_dates_keyboard(days_ahead=30):
    """Клавиатура с датами"""
    buttons = []
    today = datetime.now()
    
    for i in range(days_ahead):
        current_date = today + timedelta(days=i)
        if current_date.date() < today.date():
            continue
        date_text = format_date_for_storage(current_date)
        if not is_slot_blocked(date_text, None):
            buttons.append(KeyboardButton(date_text))
    
    rows = [buttons[i:i+4] for i in range(0, len(buttons), 4)]
    rows.append([KeyboardButton("❌ Отмена")])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

def create_time_keyboard(selected_date):
    """Клавиатура со временем"""
    all_time_slots = [
        "09:00", "10:00", "11:00", "12:00", "13:00", "14:00", 
        "15:00", "16:00", "17:00", "18:00", "19:00", "20:00"
    ]
    
    available_slots = []
    for time_slot in all_time_slots:
        if (not is_time_slot_taken(selected_date, time_slot) and 
            is_valid_datetime(selected_date, time_slot) and 
            not is_slot_blocked(selected_date, time_slot)):
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
    """Клавиатура записей клиента"""
    keyboard = []
    for app in appointments:
        status_icon = "✅" if app[6] == "confirmed" else "⏳"
        keyboard.append([
            InlineKeyboardButton(
                f"{status_icon} {app[3]} {app[4]} (Отменить)",
                callback_data=f"client_cancel_{app[0]}"
            )
        ])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")])
    return InlineKeyboardMarkup(keyboard)

def create_admin_main_keyboard():
    """Основная клавиатура админа"""
    keyboard = [
        [KeyboardButton("📋 Все записи"), KeyboardButton("📅 Записи на сегодня")],
        [KeyboardButton("🗓️ Записи по дате"), KeyboardButton("🔍 Поиск по телефону")],
        [KeyboardButton("✉️ Сообщения от клиентов"), KeyboardButton("🚫 Управление выходными")],
        [KeyboardButton("📢 Сделать рассылку"), KeyboardButton("❌ Закрыть админ-панель")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def create_main_keyboard():
    """Основная клавиатура клиента"""
    keyboard = [
        [KeyboardButton("📅 Записаться на маникюр")],
        [KeyboardButton("📋 Мои записи")],
        [KeyboardButton("✉️ Написать мастеру")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# ================== ОСНОВНЫЕ КОМАНДЫ ==================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    user = update.message.from_user
    save_bot_user(
        chat_id=update.message.chat.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name
    )
    
    await update.message.reply_text(
        "Привет! Я бот для записи на маникюр. Выберите действие:",
        reply_markup=create_main_keyboard()
    )

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /admin"""
    if update.message.chat.id != ADMIN_ID:
        await update.message.reply_text("У вас нет доступа к админ-панели.")
        return
        
    await update.message.reply_text(
        "🔧 АДМИН-ПАНЕЛЬ\n\nВыберите действие:",
        reply_markup=create_admin_main_keyboard()
    )

# ================== ПРОЦЕСС ЗАПИСИ ==================
async def start_booking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало процесса записи"""
    await update.message.reply_text(
        "Выберите удобную дату (доступны даты на месяц вперед):",
        reply_markup=create_dates_keyboard()
    )
    return SELECT_DATE

async def select_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выбор даты"""
    selected_date = update.message.text
    
    if selected_date == "❌ Отмена":
        await update.message.reply_text("Запись отменена.", reply_markup=create_main_keyboard())
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
        
    await update.message.reply_text(
        f"Вы выбрали: {selected_date}\nСвободное время:",
        reply_markup=time_keyboard
    )
    return SELECT_TIME

async def select_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выбор времени"""
    selected_time = update.message.text
    
    if selected_time == "❌ Отмена":
        await update.message.reply_text("Запись отменена.", reply_markup=create_main_keyboard())
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
    await update.message.reply_text(
        "Отлично! Теперь введите ваше имя:",
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton("❌ Отмена")]], resize_keyboard=True)
    )
    return ENTER_NAME

async def enter_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ввод имени"""
    client_name = update.message.text
    
    if client_name == "❌ Отмена":
        await update.message.reply_text("Запись отменена.", reply_markup=create_main_keyboard())
        return ConversationHandler.END
        
    context.user_data["client_name"] = client_name
    await update.message.reply_text(
        "Введите ваш номер телефона:",
        reply_markup=ReplyKeyboardMarkup([
            [KeyboardButton("📱 Отправить номер", request_contact=True)],
            [KeyboardButton("❌ Отмена")]
        ], resize_keyboard=True)
    )
    return ENTER_PHONE

async def enter_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ввод телефона"""
    if update.message.contact:
        client_phone = update.message.contact.phone_number
    else:
        client_phone = update.message.text
        
    if client_phone == "❌ Отмена":
        await update.message.reply_text("Запись отменена.", reply_markup=create_main_keyboard())
        context.user_data.clear()
        return ConversationHandler.END
        
    client_name = context.user_data.get("client_name")
    selected_date = context.user_data.get("selected_date")
    selected_time = context.user_data.get("selected_time")
    client_chat_id = update.message.chat.id
    
    appointment_id = save_appointment_to_db(client_name, client_phone, selected_date, selected_time, client_chat_id)
    
    # Уведомление админу
    try:
        await context.bot.send_message(
            ADMIN_ID,
            f"📋 НОВАЯ ЗАПИСЬ!\n\n"
            f"👤 Клиент: {client_name}\n"
            f"📞 Телефон: {client_phone}\n"
            f"📅 Дата: {selected_date}\n"
            f"⏰ Время: {selected_time}\n"
            f"🆔 Номер записи: #{appointment_id}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Подтвердить оплату", callback_data=f"confirm_payment_{appointment_id}"),
                InlineKeyboardButton("❌ Отменить запись", callback_data=f"admin_cancel_{appointment_id}")
            ]])
        )
    except Exception as e:
        logger.error(f"Ошибка уведомления админа: {e}")
    
    await update.message.reply_text(
        f"✅ ЗАПИСЬ СОЗДАНА!\n\n"
        f"👤 Имя: {client_name}\n"
        f"📞 Телефон: {client_phone}\n"
        f"📅 Дата: {selected_date}\n"
        f"⏰ Время: {selected_time}\n\n"
        f"💳 Для подтверждения записи необходимо внести предоплату.\n"
        f"Ссылка для оплаты: {YUMMY_PAYMENT_LINK}\n\n"
        f"⏰ Время на оплату: 10 минут\n"
        f"После оплаты мастер подтвердит вашу запись.",
        reply_markup=create_main_keyboard()
    )
    
    context.user_data.clear()
    return ConversationHandler.END

# ================== МОИ ЗАПИСИ ==================
async def show_my_appointments(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать записи клиента"""
    client_chat_id = update.message.chat.id
    appointments = get_client_appointments(client_chat_id)
    
    if not appointments:
        await update.message.reply_text("У вас нет активных записей.", reply_markup=create_main_keyboard())
        return
        
    message = "📋 ВАШИ АКТИВНЫЕ ЗАПИСИ:\n\n"
    for app in appointments:
        status_icon = "✅" if app[6] == "confirmed" else "⏳"
        message += f"{status_icon} {app[3]} {app[4]}\n"
        message += f"Статус: {'Подтверждена' if app[6] == 'confirmed' else 'Ожидает оплаты'}\n\n"
        
    await update.message.reply_text(message, reply_markup=create_my_appointments_keyboard(appointments))

async def client_cancel_appointment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена записи клиентом"""
    query = update.callback_query
    await query.answer()
    
    appointment_id = int(query.data.split("_")[-1])
    appointment = get_appointment_by_id(appointment_id)
    
    if not appointment:
        await query.edit_message_text("Запись не найдена.")
        return
        
    cancelled_appointment = cancel_appointment(appointment_id)
    
    # Уведомление админу
    try:
        await context.bot.send_message(
            ADMIN_ID,
            f"❌ КЛИЕНТ ОТМЕНИЛ ЗАПИСЬ\n\n"
            f"🆔 Номер записи: #{appointment[0]}\n"
            f"👤 Клиент: {appointment[1]}\n"
            f"📞 Телефон: {appointment[2]}\n"
            f"📅 Дата: {appointment[3]}\n"
            f"⏰ Время: {appointment[4]}"
        )
    except Exception as e:
        logger.error(f"Ошибка уведомления админа: {e}")
        
    await query.edit_message_text(
        "✅ Запись успешно отменена.",
        reply_markup=create_main_keyboard()
    )

# ================== СООБЩЕНИЯ ==================
async def start_client_to_admin_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало сообщения мастеру"""
    await update.message.reply_text(
        "💬 НАПИСАТЬ МАСТЕРУ\n\nНапишите ваше сообщение мастеру. Он получит его и ответит вам здесь же.",
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton("❌ Отмена")]], resize_keyboard=True)
    )
    return CLIENT_TO_ADMIN_MESSAGE

async def handle_client_to_admin_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка сообщения мастеру"""
    if update.message.text == "❌ Отмена":
        await update.message.reply_text("Сообщение отменено.", reply_markup=create_main_keyboard())
        return ConversationHandler.END
        
    client_message = update.message.text
    client_chat_id = update.message.chat.id
    client_name = update.message.from_user.first_name or "Клиент"
    
    save_message(client_chat_id, client_name, client_message, is_from_client=True)
    
    try:
        await context.bot.send_message(
            ADMIN_ID,
            f"💬 НОВОЕ СООБЩЕНИЕ ОТ КЛИЕНТА\n\n"
            f"👤 Клиент: {client_name}\n"
            f"🆔 Chat ID: {client_chat_id}\n"
            f"💭 Сообщение:\n{client_message}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✉️ Ответить", callback_data=f"admin_reply_{client_chat_id}")
            ]])
        )
        await update.message.reply_text(
            "✅ Ваше сообщение отправлено мастеру. Ожидайте ответа здесь же.",
            reply_markup=create_main_keyboard()
        )
    except Exception as e:
        await update.message.reply_text(
            "❌ Ошибка отправки сообщения. Попробуйте позже.",
            reply_markup=create_main_keyboard()
        )
        logger.error(f"Ошибка отправки сообщения админу: {e}")
        
    return ConversationHandler.END

async def start_admin_to_client_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало ответа клиенту"""
    query = update.callback_query
    await query.answer()
    
    client_chat_id = int(query.data.split("_")[-1])
    context.user_data["admin_message_client_id"] = client_chat_id
    
    await query.message.reply_text(
        f"💬 ОТВЕТ КЛИЕНТУ\n\nНапишите ваше сообщение:",
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton("❌ Отмена")]], resize_keyboard=True)
    )
    return ADMIN_TO_CLIENT_MESSAGE

async def handle_admin_to_client_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка ответа клиенту"""
    admin_message = update.message.text
    client_chat_id = context.user_data.get("admin_message_client_id")
    
    if not client_chat_id:
        await update.message.reply_text("Ошибка: не найден ID клиента.")
        return ConversationHandler.END
        
    try:
        await context.bot.send_message(
            client_chat_id,
            f"💬 СООБЩЕНИЕ ОТ МАСТЕРА:\n\n{admin_message}",
            reply_markup=create_main_keyboard()
        )
        await update.message.reply_text(
            "✅ Сообщение отправлено клиенту.",
            reply_markup=create_admin_main_keyboard()
        )
    except Exception as e:
        await update.message.reply_text(
            f"❌ Ошибка отправки сообщения клиенту: {e}",
            reply_markup=create_admin_main_keyboard()
        )
        
    context.user_data.pop("admin_message_client_id", None)
    return ConversationHandler.END

# ================== АДМИН-ФУНКЦИИ ==================
async def show_all_appointments(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать все записи"""
    if update.message.chat.id != ADMIN_ID:
        return
        
    appointments = get_all_appointments()
    
    if not appointments:
        await update.message.reply_text("Нет активных записей.", reply_markup=create_admin_main_keyboard())
        return
        
    message = "📋 ВСЕ АКТИВНЫЕ ЗАПИСИ:\n\n"
    for app in appointments:
        status_icon = "✅" if app[6] == "confirmed" else "⏳"
        payment_status = "💳 Оплачено" if app[8] == "paid" else "❌ Ожидает оплаты"
        message += f"{status_icon} {app[3]} {app[4]} - {app[1]}\n"
        message += f"📞 {app[2]} | {payment_status}\n"
        message += f"🆔 #{app[0]}\n\n"
        
    await update.message.reply_text(message)

async def show_client_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать сообщения от клиентов"""
    if update.message.chat.id != ADMIN_ID:
        return
        
    messages = get_client_messages(limit=10)
    
    if not messages:
        await update.message.reply_text("Нет сообщений от клиентов.", reply_markup=create_admin_main_keyboard())
        return
        
    message = "💬 ПОСЛЕДНИЕ СООБЩЕНИЯ ОТ КЛИЕНТОВ:\n\n"
    for msg in messages:
        message += f"👤 {msg[2]} (ID: {msg[1]})\n"
        message += f"💭 {msg[3]}\n"
        message += f"📅 {msg[5]}\n\n"
        
    await update.message.reply_text(message[:4000])

# ================== CALLBACK ОБРАБОТЧИКИ ==================
async def handle_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка callback от админ-кнопок"""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "admin_back":
        await query.edit_message_text(
            "🔧 АДМИН-ПАНЕЛЬ\n\nВыберите действие:",
            reply_markup=create_admin_main_keyboard()
        )
        return

    if data == "back_to_main":
        await query.edit_message_text(
            "Главное меню:",
            reply_markup=create_main_keyboard()
        )
        return

    if data.startswith("confirm_payment_"):
        appointment_id = int(data.split("_")[-1])
        appointment = confirm_payment(appointment_id)
        
        if appointment:
            # Уведомление клиенту
            try:
                await context.bot.send_message(
                    appointment[7],
                    f"✅ ОПЛАТА ПОДТВЕРЖДЕНА!\n\n"
                    f"Ваша запись подтверждена:\n"
                    f"📅 Дата: {appointment[3]}\n"
                    f"⏰ Время: {appointment[4]}\n\n"
                    f"Ждем вас в салоне!",
                    reply_markup=create_main_keyboard()
                )
            except Exception as e:
                logger.error(f"Ошибка уведомления клиента: {e}")
                
            await query.edit_message_text(
                f"✅ Оплата для записи #{appointment_id} подтверждена! Клиент уведомлен."
            )
        return

    if data.startswith("admin_cancel_"):
        appointment_id = int(data.split("_")[-1])
        appointment = cancel_appointment(appointment_id)
        
        if appointment:
            # Уведомление клиенту
            try:
                await context.bot.send_message(
                    appointment[7],
                    f"⚠️ ВАЖНОЕ УВЕДОМЛЕНИЕ\n\n"
                    f"Мастер отменил вашу запись:\n"
                    f"📅 Дата: {appointment[3]}\n"
                    f"⏰ Время: {appointment[4]}\n\n"
                    f"Для уточнения деталей напишите мастеру.",
                    reply_markup=create_main_keyboard()
                )
            except Exception as e:
                logger.error(f"Ошибка уведомления клиента: {e}")
                
            await query.edit_message_text(
                f"✅ Запись #{appointment_id} отменена! Клиент уведомлен."
            )
        return

# ================== ФОНОВЫЕ ЗАДАЧИ ==================
async def check_expired_payments(context: ContextTypes.DEFAULT_TYPE):
    """Проверка просроченных оплат"""
    try:
        pending_appointments = get_pending_appointments()
        now = datetime.now()
        
        for appointment in pending_appointments:
            created_at = datetime.strptime(appointment[5], "%Y-%m-%d %H:%M:%S")
            if (now - created_at).total_seconds() > 600:  # 10 minutes
                expired_appointment = expire_appointment(appointment[0])
                
                # Уведомление клиенту
                try:
                    await context.bot.send_message(
                        appointment[7],
                        f"⏰ ВРЕМЯ ОПЛАТЫ ИСТЕКЛО\n\n"
                        f"К сожалению, время на оплату записи истекло:\n"
                        f"📅 Дата: {appointment[3]}\n"
                        f"⏰ Время: {appointment[4]}\n\n"
                        f"Вы можете создать новую запись.",
                        reply_markup=create_main_keyboard()
                    )
                except Exception as e:
                    logger.error(f"Ошибка уведомления клиента: {e}")
                    
    except Exception as e:
        logger.error(f"Ошибка проверки просроченных оплат: {e}")

async def send_reminders(context: ContextTypes.DEFAULT_TYPE):
    """Отправка напоминаний"""
    try:
        tomorrow_dt = datetime.now() + timedelta(days=1)
        tomorrow = format_date_for_storage(tomorrow_dt)
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM appointments WHERE appointment_date = ? AND status = 'confirmed'",
            (tomorrow,)
        )
        appointments = cursor.fetchall()
        conn.close()
        
        for appointment in appointments:
            if appointment[7]:
                await context.bot.send_message(
                    appointment[7],
                    f"🔔 НАПОМИНАНИЕ О ЗАПИСИ\n\n"
                    f"Напоминаем, что завтра у вас запись на маникюр:\n"
                    f"📅 Дата: {appointment[3]}\n"
                    f"⏰ Время: {appointment[4]}\n\n"
                    f"Ждем вас в салоне!"
                )
                
    except Exception as e:
        logger.error(f"Ошибка отправки напоминаний: {e}")

# ================== CONVERSATION HANDLERS ==================
def setup_conversation_handlers(application):
    """Настройка обработчиков разговоров"""
    
    # Процесс записи
    booking_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📅 Записаться на маникюр$"), start_booking)],
        states={
            SELECT_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, select_date)],
            SELECT_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, select_time)],
            ENTER_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_name)],
            ENTER_PHONE: [MessageHandler(filters.TEXT | filters.CONTACT, enter_phone)],
        },
        fallbacks=[MessageHandler(filters.Regex("^❌ Отмена$"), start_command)],
    )

    # Сообщения клиента мастеру
    client_to_admin_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^✉️ Написать мастеру$"), start_client_to_admin_message)],
        states={
            CLIENT_TO_ADMIN_MESSAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_client_to_admin_message)]
        },
        fallbacks=[MessageHandler(filters.Regex("^❌ Отмена$"), start_command)],
    )

    # Ответы мастера клиенту
    admin_to_client_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_admin_to_client_message, pattern="^admin_reply_")],
        states={
            ADMIN_TO_CLIENT_MESSAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_to_client_message)]
        },
        fallbacks=[MessageHandler(filters.Regex("^❌ Отмена$"), admin_command)],
    )

    application.add_handler(booking_handler)
    application.add_handler(client_to_admin_handler)
    application.add_handler(admin_to_client_handler)

# ================== ОСНОВНАЯ ФУНКЦИЯ ==================
def main():
    """Основная функция запуска бота"""
    logger.info("🚀 Запуск бота маникюрного салона...")
    
    # Инициализация базы данных
    init_database()
    
    try:
        # Создание приложения
        application = Application.builder().token(BOT_TOKEN).build()
        
        # Базовые команды
        application.add_handler(CommandHandler("start", start_command))
        application.add_handler(CommandHandler("admin", admin_command))
        
        # Настройка обработчиков разговоров
        setup_conversation_handlers(application)
        
        # Обработчики кнопок
        application.add_handler(MessageHandler(filters.Regex("^📋 Мои записи$"), show_my_appointments))
        application.add_handler(MessageHandler(filters.Regex("^📋 Все записи$"), show_all_appointments))
        application.add_handler(MessageHandler(filters.Regex("^✉️ Сообщения от клиентов$"), show_client_messages))
        
        # Callback обработчики
        application.add_handler(CallbackQueryHandler(client_cancel_appointment, pattern="^client_cancel_"))
        application.add_handler(CallbackQueryHandler(handle_admin_callback))
        
        # Настройка фоновых задач
        job_queue = application.job_queue
        if job_queue:
            job_queue.run_repeating(check_expired_payments, interval=300, first=10)  # Каждые 5 минут
            job_queue.run_repeating(send_reminders, interval=3600, first=60)  # Каждый час
        
        # Определение способа запуска
        PORT = int(os.environ.get("PORT", 10000))
        APP_NAME = os.environ.get("RENDER_EXTERNAL_HOSTNAME")
        
        if APP_NAME:
            # Запуск на Render через webhook
            logger.info(f"🌐 Запуск через Webhook на {APP_NAME}:{PORT}")
            application.run_webhook(
                listen="0.0.0.0",
                port=PORT,
                url_path=BOT_TOKEN,
                webhook_url=f"https://{APP_NAME}/{BOT_TOKEN}",
                drop_pending_updates=True
            )
        else:
            # Локальный запуск через polling
            logger.info("🔍 Локальный запуск через Polling")
            application.run_polling(
                drop_pending_updates=True,
                allowed_updates=Update.ALL_TYPES
            )
            
    except Exception as e:
        logger.error(f"❌ Критическая ошибка при запуске бота: {e}")
        raise

if __name__ == "__main__":
    main()

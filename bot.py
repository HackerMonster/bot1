import asyncio
import logging
import aiohttp
import sqlite3
import re
import random
import string
from datetime import datetime

from aiogram import Bot, Dispatcher, Router, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage

# ================== НАСТРОЙКИ ==================

BOT_TOKEN = "8549573387:AAGJynndMV16Z_Rr0YgbnTd6nWahzkw221g"
SUBGRAM_API_KEY = "f5d4e6567b52e995ebf408cb75ac22740e25c9a02a0427941386c97e8843e891"
SUBGRAM_URL = "https://api.subgram.org/get-sponsors"

CHANNEL_URL = "https://t.me/script_f"
ADMIN_ID = 5870949629
BOT_USERNAME = "LinksSecret_Bot"

# Настройки для группы/топика
GROUP_ID = -1001897612345  # Замените на ID вашей группы (должно быть отрицательным числом)
TOPIC_ID = 2  # ID топика, где будут отправляться скрипты

# ===============================================

logging.basicConfig(level=logging.INFO)
router = Router()

# Хранилище пользователей
USERS = set()

# ================== БАЗА ДАННЫХ ДЛЯ ССЫЛОК ==================

def init_database():
    """Инициализация базы данных для скриптов"""
    conn = sqlite3.connect('scripts.db')
    cursor = conn.cursor()
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS scripts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        unique_code TEXT UNIQUE NOT NULL,
        script_content TEXT NOT NULL,
        created_by INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        is_public BOOLEAN DEFAULT 0,
        original_message_id INTEGER
    )
    ''')
    
    conn.commit()
    conn.close()

init_database()

# ================== FSM СОСТОЯНИЯ ==================

class UploadScriptState(StatesGroup):
    waiting_script = State()

class BroadcastState(StatesGroup):
    waiting_content = State()
    waiting_buttons = State()
    waiting_confirmation = State()

# ================== ФУНКЦИИ ДЛЯ РАБОТЫ С БД ==================

def generate_unique_code():
    """Генерация уникального кода для ссылки от 7 до 25 символов"""
    # Длина случайная от 7 до 25 символов
    length = random.randint(7, 25)
    
    # Используем английские буквы, цифры и дефис
    characters = string.ascii_letters + string.digits + "-"
    
    # Генерируем код
    code = ''.join(random.choice(characters) for _ in range(length))
    
    return code

def save_script_to_db(script_content: str, created_by: int, is_public=False, original_message_id=None):
    """Сохранение скрипта в базу данных и возврат уникального кода"""
    conn = sqlite3.connect('scripts.db')
    cursor = conn.cursor()
    
    # Генерируем уникальный код пока не найдем свободный
    while True:
        unique_code = generate_unique_code()
        cursor.execute('SELECT 1 FROM scripts WHERE unique_code = ?', (unique_code,))
        if not cursor.fetchone():
            break
    
    cursor.execute('''
    INSERT INTO scripts (unique_code, script_content, created_by, is_public, original_message_id)
    VALUES (?, ?, ?, ?, ?)
    ''', (unique_code, script_content, created_by, 1 if is_public else 0, original_message_id))
    
    conn.commit()
    conn.close()
    
    return unique_code

def get_script_content(unique_code):
    """Получение скрипта по уникальному коду"""
    conn = sqlite3.connect('scripts.db')
    cursor = conn.cursor()
    
    cursor.execute('''
    SELECT script_content FROM scripts WHERE unique_code = ?
    ''', (unique_code,))
    
    result = cursor.fetchone()
    conn.close()
    
    if result:
        return result[0]
    return None

def get_statistics():
    """Получение статистики"""
    conn = sqlite3.connect('scripts.db')
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM scripts")
    total_scripts = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM scripts WHERE created_by = ?", (ADMIN_ID,))
    admin_scripts = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM scripts WHERE created_by != ?", (ADMIN_ID,))
    user_scripts = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM scripts WHERE is_public = 1")
    public_scripts = cursor.fetchone()[0]
    
    conn.close()
    
    return {
        'total_scripts': total_scripts,
        'admin_scripts': admin_scripts,
        'user_scripts': user_scripts,
        'public_scripts': public_scripts,
        'total_users': len(USERS)
    }

# ================== SubGram ФУНКЦИИ ==================

async def get_subgram_sponsors(user_id: int, chat_id: int) -> dict | None:
    headers = {"Auth": SUBGRAM_API_KEY}
    payload = {"user_id": user_id, "chat_id": chat_id}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                SUBGRAM_URL,
                headers=headers,
                json=payload,
                timeout=10
            ) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    logging.error(f"SubGram API error: {response.status}")
                    return None
    except asyncio.TimeoutError:
        logging.error("SubGram API timeout")
        return None
    except Exception as e:
        logging.error(f"SubGram API error: {e}")
        return None

def create_subscription_keyboard(sponsors_data):
    """Создание клавиатуры с каналами для подписки"""
    sponsors = sponsors_data.get("sponsors", [])
    keyboard = []
    
    for sponsor in sponsors:
        button = InlineKeyboardButton(
            text=sponsor.get("name", "Канал"),
            url=sponsor.get("url", "#")
        )
        keyboard.append([button])
    
    keyboard.append([InlineKeyboardButton(
        text="✅ Проверить подписку",
        callback_data="check_subscription"
    )])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

# ================== ФУНКЦИИ ФОРМАТИРОВАНИЯ ==================

def format_script_for_display(script_content: str) -> str:
    """Форматирование скрипта для отображения"""
    # Убедимся, что скрипт начинается с $ для моноширинного текста
    if not script_content.startswith('$'):
        script_content = f"${script_content}"
    
    # Экранируем HTML символы
    script_content = script_content.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    
    # Форматируем как код
    lines = script_content.split('\n')
    formatted_lines = []
    
    for line in lines:
        if line.startswith('$'):
            # Если строка начинается с $, форматируем как <code>
            code_content = line[1:].strip()
            if code_content:
                formatted_lines.append(f"<code>{code_content}</code>")
            else:
                formatted_lines.append(line)
        else:
            # Если строка не начинается с $, проверяем, есть ли $ внутри
            if '$' in line:
                # Заменяем $text$ на <code>text</code>
                line = re.sub(r'\$([^$\n]+)\$', r'<code>\1</code>', line)
            formatted_lines.append(line)
    
    return '\n'.join(formatted_lines)

# ================== ПРИВЕТСТВИЕ ==================

async def send_welcome(target: types.Message | types.CallbackQuery):
    if isinstance(target, types.Message):
        user_id = target.from_user.id
        chat = target
    else:
        user_id = target.from_user.id
        chat = target.message
    
    USERS.add(user_id)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔥 Наш канал", url=CHANNEL_URL)]
        ]
    )

    text = (
        "<b>👋 Приветствуем {nick}</b>\n\n"
        "<b>Добро пожаловать в Secret Link — место, где ты можешь быстро и безопасно "
        "получить свой скрипт для Roblox.</b>\n\n"
        "🔹 <b>Что тебя ждёт:</b>\n"
        "• ⚡️ <b>Только лучшие скрипты — без вирусов, рекламы и переходников</b>\n"
        "• 🛡 <b>Проверены вручную — гарантированная безопасность</b>\n"
        "• 🔁 <b>Постоянные обновления — всё актуально и стабильно работает</b>\n\n"
        "❗️ <b>Важно:</b>\n"
        "Чтобы получить скрипт — просто перейди в нужный канал и нажми кнопку «Получить скрипт 🚀»\n\n"
        "<b>Для сотрудничества:</b> @SecretLinkAds"
    )

    if isinstance(target, types.Message):
        await target.answer(
            text.format(nick=target.from_user.full_name),
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    else:
        await target.message.edit_text(
            text.format(nick=target.from_user.full_name),
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        await target.answer()

# ================== ОБРАБОТКА СООБЩЕНИЙ В ГРУППЕ ==================

@router.message(F.chat.id == GROUP_ID)
async def handle_group_message(message: types.Message, bot: Bot):
    """Обработка сообщений в группе"""
    
    # Проверяем, что сообщение в нужном топике (если это форум)
    if message.message_thread_id and message.message_thread_id != TOPIC_ID:
        return
    
    # Проверяем, что сообщение не от бота
    if message.from_user.id == bot.id:
        return
    
    # Проверяем, что есть текст
    if not message.text and not message.caption:
        return
    
    # Получаем текст сообщения
    text_content = message.text or message.caption
    
    # Проверяем, содержит ли сообщение loadstring или просто любой текст
    # Можно настроить фильтры по вашему усмотрению
    if "loadstring" in text_content.lower() or "game:HttpGet" in text_content.lower() or len(text_content) > 10:
        # Генерируем уникальную ссылку для скрипта
        unique_code = save_script_to_db(
            script_content=text_content,
            created_by=message.from_user.id,
            is_public=True,
            original_message_id=message.message_id
        )
        
        # Создаем ссылку
        link = f"https://t.me/{BOT_USERNAME}?start={unique_code}"
        
        # Форматируем ответ
        response_text = (
            f"✅ <b>Уникальная ссылка создана!</b>\n\n"
            f"👤 <b>Отправитель:</b> {message.from_user.full_name}\n"
            f"🔗 <b>Ссылка:</b> {link}\n\n"
            f"<i>Нажмите на ссылку, чтобы получить доступ к контенту</i>"
        )
        
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔗 Получить доступ", url=link)],
                [InlineKeyboardButton(text="📢 Наш канал", url=CHANNEL_URL)]
            ]
        )
        
        # Отправляем ответ в группу
        await message.reply(
            text=response_text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )

# ================== /start с уникальными ссылками ==================

@router.message(CommandStart())
async def start_handler(message: types.Message, state: FSMContext):
    if message.chat.type != "private":
        return
    
    USERS.add(message.from_user.id)
    
    # Если есть код в команде
    if len(message.text.split()) > 1:
        unique_code = message.text.split()[1]
        await state.update_data(unique_code=unique_code)
        
        # Проверяем подписку через SubGram
        response = await get_subgram_sponsors(message.from_user.id, message.chat.id)
        
        if response and response.get("status") == "warning":
            keyboard = create_subscription_keyboard(response)
            warning_message = "❗ Чтобы получить доступ к боту, подпишитесь на следующие каналы:"
            
            await message.answer(
                warning_message,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            return
        
        # Если подписка есть, получаем скрипт
        script_content = get_script_content(unique_code)
        
        if script_content:
            await show_script_content(message, script_content)
            await state.clear()
            return
        else:
            await message.answer("❌ Ссылка не найдена или устарела")
            await state.clear()
            return
    
    # Обычный /start без кода
    response = await get_subgram_sponsors(message.from_user.id, message.chat.id)

    if response and response.get("status") == "warning":
        keyboard = create_subscription_keyboard(response)
        warning_message = "❗ Чтобы получить доступ к боту, подпишитесь на следующие каналы:"
        
        await message.answer(
            warning_message,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        return

    await send_welcome(message)

async def show_script_content(message: types.Message, script_content: str):
    """Показать скрипт с форматированием"""
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text="⚡️ Больше скриптов ⚡️", 
                url="https://t.me/script_f"
            )],
            [InlineKeyboardButton(
                text="📤 Поделиться своим скриптом",
                url=f"https://t.me/c/{abs(GROUP_ID) - 1000000000000}/{TOPIC_ID}"
            )]
        ]
    )
    
    # Форматируем скрипт
    formatted_script = format_script_for_display(script_content)
    
    # Создаем финальное сообщение
    header_text = "<b>✅ | Спасибо за подписки!</b>\n\n"
    footer_text = f"\n\n@{BOT_USERNAME}"
    
    final_text = header_text + formatted_script + footer_text
    
    await message.answer(
        final_text,
        reply_markup=keyboard,
        parse_mode="HTML"
    )

@router.callback_query(F.data == "check_subscription")
async def check_subscription_callback(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer("⏳ Проверяем подписку...")
    
    # Удаляем сообщение с кнопками
    try:
        await callback.message.delete()
    except:
        pass
    
    response = await get_subgram_sponsors(callback.from_user.id, callback.message.chat.id)
    
    if response and response.get("status") == "warning":
        keyboard = create_subscription_keyboard(response)
        warning_message = "❌ Вы еще не подписались на все каналы!\n\n❗ Чтобы получить доступ к боту, подпишитесь на следующие каналы:"
        
        await callback.message.answer(
            warning_message,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        return
    
    data = await state.get_data()
    unique_code = data.get('unique_code')
    
    if unique_code:
        script_content = get_script_content(unique_code)
        
        if script_content:
            await show_script_content(callback.message, script_content)
        else:
            await callback.message.answer("❌ Ссылка не найдена или устарела")
    else:
        await send_welcome(callback)
    
    await state.clear()

# ================== АДМИН ПАНЕЛЬ ==================

@router.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.chat.type != "private":
        return
        
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ У вас нет доступа к админ-панели")
        return

    stats = get_statistics()
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
            [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")],
            [InlineKeyboardButton(text="📤 Загрузка скрипта", callback_data="admin_upload_script")],
            [InlineKeyboardButton(text="👥 Публичные скрипты", callback_data="admin_public_scripts")]
        ]
    )

    admin_text = (
        "👑 <b>Админ-панель</b>\n\n"
        f"📊 <b>Статистика:</b>\n"
        f"• 👥 Пользователей: {stats['total_users']}\n"
        f"• 📜 Всего скриптов: {stats['total_scripts']}\n"
        f"• 👤 Публичных: {stats['public_scripts']}\n"
        f"• 👑 Админских: {stats['admin_scripts']}\n\n"
        f"🔗 <b>Группа для скриптов:</b>\n"
        f"ID: {GROUP_ID}\n"
        f"Топик: {TOPIC_ID}"
    )

    await message.answer(admin_text, reply_markup=keyboard, parse_mode="HTML")

@router.callback_query(F.data == "admin_public_scripts")
async def admin_public_scripts(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    
    conn = sqlite3.connect('scripts.db')
    cursor = conn.cursor()
    
    cursor.execute('''
    SELECT COUNT(*) FROM scripts WHERE is_public = 1
    ''')
    total_public = cursor.fetchone()[0]
    
    cursor.execute('''
    SELECT unique_code, script_content, created_by, created_at 
    FROM scripts WHERE is_public = 1 
    ORDER BY created_at DESC LIMIT 10
    ''')
    
    recent_scripts = cursor.fetchall()
    conn.close()
    
    stats_text = f"📜 <b>Публичные скрипты: {total_public}</b>\n\n"
    
    if recent_scripts:
        stats_text += "<b>Последние 10 скриптов:</b>\n"
        for i, (code, content, user_id, created_at) in enumerate(recent_scripts, 1):
            preview = content[:30] + "..." if len(content) > 30 else content
            stats_text += f"{i}. <code>{code}</code>\n"
            stats_text += f"   👤 ID: {user_id}\n"
            stats_text += f"   📝 {preview}\n"
            stats_text += f"   🕐 {created_at}\n\n"
    else:
        stats_text += "📭 Нет публичных скриптов\n"
    
    stats_text += f"\n🔗 <b>Ссылка на группу:</b>\nhttps://t.me/c/{abs(GROUP_ID) - 1000000000000}/{TOPIC_ID}"
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад в админку", callback_data="back_to_admin")]
        ]
    )
    
    await callback.message.edit_text(stats_text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data == "admin_upload_script")
async def upload_script_callback(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    
    await callback.message.edit_text(
        "📤 <b>Загрузка скрипта</b>\n\n"
        "Отправьте скрипт для Roblox.\n\n"
        "<b>📌 Автоматическое форматирование:</b>\n"
        "• Скрипт будет отображаться как моноширинный текст\n"
        "• Перед скриптом автоматически добавится $\n\n"
        "<i>Пример скрипта:</i>\n"
        "<code>loadstring(game:HttpGet('https://raw.githubusercontent.com/...'))()</code>\n\n"
        "После отправки бот создаст уникальную ссылку на скрипт.",
        parse_mode="HTML"
    )
    
    await state.set_state(UploadScriptState.waiting_script)
    await callback.answer()

@router.message(UploadScriptState.waiting_script)
async def process_script_upload(message: types.Message, state: FSMContext):
    if message.chat.type != "private":
        await state.clear()
        return
    
    if message.from_user.id != ADMIN_ID:
        await state.clear()
        return
    
    # Получаем текст скрипта
    script_content = ""
    
    if message.content_type == 'text':
        script_content = message.text.strip()
    elif message.content_type == 'document' and message.document:
        # Если это файл, попробуем прочитать как текстовый
        try:
            file = await message.bot.download(message.document)
            script_content = file.read().decode('utf-8')
        except:
            await message.answer("❌ Не удалось прочитать файл. Отправьте скрипт как текст.")
            return
    else:
        await message.answer("❌ Отправьте скрипт в виде текста или текстового файла.")
        return
    
    if not script_content:
        await message.answer("❌ Скрипт не может быть пустым.")
        return
    
    # Сохраняем скрипт в базу данных
    unique_code = save_script_to_db(script_content, message.from_user.id)
    
    # Создаем ссылку
    link = f"https://t.me/{BOT_USERNAME}?start={unique_code}"
    
    # Показываем результат
    preview_text = "<b>✅ Скрипт загружен!</b>\n\n"
    preview_text += f"<b>Уникальная ссылка:</b>\n<code>{link}</code>\n\n"
    
    # Показываем предпросмотр скрипта
    script_preview = script_content[:200] + "..." if len(script_content) > 200 else script_content
    formatted_preview = format_script_for_display(script_preview)
    
    preview_text += "<b>📝 Предпросмотр скрипта:</b>\n"
    preview_text += formatted_preview
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад в админку", callback_data="back_to_admin")]
        ]
    )
    
    await message.answer(preview_text, reply_markup=keyboard, parse_mode="HTML")
    await state.clear()

# ================== ОСТАЛЬНЫЕ АДМИН ФУНКЦИИ ==================

@router.callback_query(F.data.startswith("admin_"))
async def admin_callbacks(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return

    if callback.data == "admin_stats":
        stats = get_statistics()
        
        stats_text = (
            "📊 <b>Статистика бота</b>\n\n"
            f"👥 Всего пользователей: <b>{stats['total_users']}</b>\n"
            f"📜 Всего скриптов: <b>{stats['total_scripts']}</b>\n"
            f"👤 Публичных скриптов: <b>{stats['public_scripts']}</b>\n"
            f"👑 Загружено админом: <b>{stats['admin_scripts']}</b>\n"
            f"👥 Загружено пользователями: <b>{stats['user_scripts']}</b>\n\n"
            f"🔗 Используется SubGram: <b>✅ Да</b>\n"
            f"👥 Группа: <b>{GROUP_ID}</b>\n"
            f"📌 Топик: <b>{TOPIC_ID}</b>"
        )
        
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад в админку", callback_data="back_to_admin")]
            ]
        )
        
        await callback.message.edit_text(stats_text, reply_markup=keyboard, parse_mode="HTML")

    elif callback.data == "admin_broadcast":
        await callback.message.edit_text(
            "📢 <b>Начало рассылки</b>\n\n"
            "Отправьте сообщение для рассылки:\n"
            "• Текст\n"
            "• Фото с подписью\n"
            "• Видео с подписью\n"
            "• Документ с подписью\n\n"
            "После отправки контента вы сможете добавить URL-кнопки.",
            parse_mode="HTML"
        )
        await state.set_state(BroadcastState.waiting_content)

    await callback.answer()

@router.callback_query(F.data == "back_to_admin")
async def back_to_admin_callback(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    
    stats = get_statistics()
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
            [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")],
            [InlineKeyboardButton(text="📤 Загрузка скрипта", callback_data="admin_upload_script")],
            [InlineKeyboardButton(text="👥 Публичные скрипты", callback_data="admin_public_scripts")]
        ]
    )
    
    admin_text = (
        "👑 <b>Админ-панель</b>\n\n"
        f"📊 <b>Статистика:</b>\n"
        f"• 👥 Пользователей: {stats['total_users']}\n"
        f"• 📜 Всего скриптов: {stats['total_scripts']}\n"
        f"• 👤 Публичных: {stats['public_scripts']}\n"
        f"• 👑 Админских: {stats['admin_scripts']}\n\n"
        f"🔗 <b>Группа для скриптов:</b>\n"
        f"ID: {GROUP_ID}\n"
        f"Топик: {TOPIC_ID}"
    )
    
    await callback.message.edit_text(admin_text, reply_markup=keyboard, parse_mode="HTML")

# ================== ФУНКЦИИ РАССЫЛКИ ==================

def parse_buttons(text: str) -> InlineKeyboardMarkup | None:
    """Парсинг URL-кнопок из текста"""
    if not text or text.lower().strip() == "нет":
        return None
    
    keyboard = []
    rows = text.strip().split('\n')
    
    for row in rows[:15]:  # Максимум 15 рядов
        buttons = []
        # Разделяем кнопки в ряду через |
        button_parts = row.split('|')
        
        for part in button_parts[:8]:  # Максимум 8 кнопок в ряду
            part = part.strip()
            if '-' not in part:
                continue
            
            # Разделяем на название и URL
            name_url = part.split('-', 1)
            if len(name_url) != 2:
                continue
            
            name, url = name_url
            name = name.strip()
            url = url.strip()
            
            # Проверяем, что URL начинается с http:// или https://
            if url and (url.startswith('http://') or url.startswith('https://')):
                buttons.append(InlineKeyboardButton(text=name, url=url))
        
        if buttons:
            keyboard.append(buttons)
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard) if keyboard else None

@router.message(BroadcastState.waiting_content)
async def broadcast_get_content(message: types.Message, state: FSMContext):
    """Получение контента для рассылки"""
    if message.from_user.id != ADMIN_ID:
        await state.clear()
        return
    
    # Сохраняем данные сообщения
    content_data = {
        'content_type': message.content_type,
        'text': message.text,
        'caption': message.caption,
        'photo': message.photo[-1].file_id if message.photo else None,
        'video': message.video.file_id if message.video else None,
        'document': message.document.file_id if message.document else None,
    }
    
    await state.update_data(content_data=content_data)
    
    await message.answer(
        "⛓ <b>КНОПКИ: URL</b>\n\n"
        "Отправьте боту список URL-кнопок в следующем формате:\n\n"
        "<code>Кнопка 1 - http://link.com\n"
        "Кнопка 2 - http://link.com</code>\n\n"
        "Используйте разделитель « | », чтобы добавить до 8 кнопок в один ряд (допустимо 15 рядов):\n\n"
        "<code>Кнопка 1 - http://link.com | Кнопка 2 - http://link.com</code>\n\n"
        "Или напишите <b>нет</b> для рассылки без кнопок",
        parse_mode="HTML"
    )
    
    await state.set_state(BroadcastState.waiting_buttons)

@router.message(BroadcastState.waiting_buttons)
async def broadcast_get_buttons(message: types.Message, state: FSMContext):
    """Получение кнопок для рассылки"""
    if message.from_user.id != ADMIN_ID:
        await state.clear()
        return
    
    keyboard = parse_buttons(message.text)
    
    await state.update_data(keyboard=keyboard)
    
    # Получаем сохраненные данные
    data = await state.get_data()
    content_data = data.get('content_data', {})
    
    # Показываем предпросмотр
    preview_text = "👁 <b>Предпросмотр рассылки:</b>\n\n"
    
    if content_data['content_type'] == 'text':
        text_preview = content_data['text'][:200] + "..." if content_data['text'] and len(content_data['text']) > 200 else content_data['text']
        preview_text += f"📝 <b>Текст:</b>\n{text_preview}\n\n"
    else:
        preview_text += f"📷 <b>Тип:</b> {content_data['content_type']}\n"
        if content_data['caption']:
            caption_preview = content_data['caption'][:200] + "..." if len(content_data['caption']) > 200 else content_data['caption']
            preview_text += f"📝 <b>Подпись:</b>\n{caption_preview}\n\n"
        else:
            preview_text += "📝 <b>Подпись:</b> (нет подписи)\n\n"
    
    preview_text += f"🔘 <b>Кнопки:</b> {'✅ Есть' if keyboard else '❌ Нет'}\n\n"
    preview_text += "Отправить рассылку? Напишите <b>да</b> для подтверждения или <b>нет</b> для отмены."
    
    await message.answer(preview_text, parse_mode="HTML")
    
    await state.set_state(BroadcastState.waiting_confirmation)

@router.message(BroadcastState.waiting_confirmation)
async def broadcast_confirm(message: types.Message, state: FSMContext, bot: Bot):
    """Подтверждение и отправка рассылки"""
    if message.from_user.id != ADMIN_ID:
        await state.clear()
        return
    
    if message.text.lower() != 'да':
        await message.answer("❌ Рассылка отменена")
        await state.clear()
        return
    
    # Получаем данные
    data = await state.get_data()
    content_data = data.get('content_data', {})
    keyboard = data.get('keyboard')
    
    sent = 0
    failed = 0
    
    # Отправляем всем пользователям
    for user_id in USERS:
        try:
            if content_data['content_type'] == 'text':
                await bot.send_message(
                    chat_id=user_id,
                    text=content_data['text'],
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
            elif content_data['content_type'] == 'photo':
                await bot.send_photo(
                    chat_id=user_id,
                    photo=content_data['photo'],
                    caption=content_data.get('caption'),
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
            elif content_data['content_type'] == 'video':
                await bot.send_video(
                    chat_id=user_id,
                    video=content_data['video'],
                    caption=content_data.get('caption'),
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
            elif content_data['content_type'] == 'document':
                await bot.send_document(
                    chat_id=user_id,
                    document=content_data['document'],
                    caption=content_data.get('caption'),
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
            
            sent += 1
            await asyncio.sleep(0.05)  # Задержка между отправками
            
        except Exception as e:
            failed += 1
            logging.error(f"Не удалось отправить пользователю {user_id}: {e}")
    
    result_text = (
        f"✅ <b>Рассылка завершена!</b>\n\n"
        f"📊 <b>Статистика:</b>\n"
        f"• Отправлено: {sent}\n"
        f"• Не удалось: {failed}\n"
        f"• Всего пользователей: {len(USERS)}\n\n"
        f"📈 <b>Эффективность:</b> {(sent/(sent+failed)*100 if (sent+failed) > 0 else 0):.1f}%"
    )
    
    await message.answer(result_text, parse_mode="HTML")
    await state.clear()

# ================== ОТМЕНА ==================

@router.message(Command("cancel"))
async def cancel_command(message: types.Message, state: FSMContext):
    """Отмена текущего действия"""
    current_state = await state.get_state()
    if current_state is None:
        return
    
    await state.clear()
    await message.answer("❌ Действие отменено")

# ================== ОБРАБОТКА ДРУГИХ СООБЩЕНИЙ ==================

@router.message()
async def handle_other_messages(message: types.Message):
    """Обработка других сообщений"""
    if message.chat.type != "private":
        return
    
    # Если это не команда и не админ
    if not message.text or not message.text.startswith('/'):
        if message.from_user.id != ADMIN_ID:
            await message.answer(
                "🤖 <b>Этот бот работает с уникальными ссылками на скрипты</b>\n\n"
                "🔗 <b>Как получить скрипт:</b>\n"
                "1. Отправьте свой скрипт в нашу группу\n"
                "2. Бот автоматически создаст уникальную ссылку\n"
                "3. Перейдите по ссылке для доступа к скрипту\n\n"
                f"📢 <b>Наша группа:</b> https://t.me/c/{abs(GROUP_ID) - 1000000000000}/{TOPIC_ID}\n\n"
                "Или используйте команду /start",
                parse_mode="HTML"
            )

# ================== RUN ==================

async def main():
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    
    # Добавляем бота в группу
    try:
        # Попробуем получить информацию о боте
        me = await bot.get_me()
        logging.info(f"Бот запущен: @{me.username}")
        logging.info(f"ID группы для мониторинга: {GROUP_ID}")
        logging.info(f"ID топика: {TOPIC_ID}")
    except Exception as e:
        logging.error(f"Ошибка при получении информации о боте: {e}")
    
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

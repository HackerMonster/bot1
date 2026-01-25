import asyncio
import logging
import aiohttp
import sqlite3
import os
import re
import random
import string
from datetime import datetime
from typing import List, Dict, Optional

from aiogram import Bot, Dispatcher, Router, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage

# ================== НАСТРОЙКИ ==================

BOT_TOKEN = "8549573387:AAG6oAmjI-w8niZScnzNz42OX_5tiHnLw_k"
SUBGRAM_API_KEY = "f5d4e6567b52e995ebf408cb75ac22740e25c9a02a0427941386c97e8843e891"
SUBGRAM_URL = "https://api.subgram.org/get-sponsors"

CHANNEL_URL = "https://t.me/script_f"
ADMIN_ID = 5870949629
BOT_USERNAME = "LinksSecret_Bot"

# ===============================================

logging.basicConfig(level=logging.INFO)
router = Router()

# Хранилище пользователей
USERS = set()
# Хранилище состояний загрузки скриптов
UPLOADING_USERS = set()

# Состояния для рассылки
class BroadcastStates(StatesGroup):
    waiting_for_message = State()
    waiting_for_buttons = State()

# ================== БАЗА ДАННЫХ ДЛЯ ОБЯЗАТЕЛЬНОЙ ПОДПИСКИ ==================

def init_subscription_db():
    """Инициализация базы данных для обязательной подписки"""
    conn = sqlite3.connect('subscriptions.db')
    cursor = conn.cursor()
    
    # Таблица для каналов обязательной подписки
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS required_channels (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        channel_id TEXT UNIQUE NOT NULL,
        channel_username TEXT,
        channel_title TEXT,
        channel_url TEXT NOT NULL,
        is_active BOOLEAN DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        added_by INTEGER,
        priority INTEGER DEFAULT 0
    )
    ''')
    
    # Таблица для отслеживания подписок пользователей
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS user_subscriptions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        channel_id TEXT NOT NULL,
        is_subscribed BOOLEAN DEFAULT 0,
        last_check TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(user_id, channel_id)
    )
    ''')
    
    # Таблица для пользователей, которые прошли проверку
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS verified_users (
        user_id INTEGER PRIMARY KEY,
        verified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    
    conn.commit()
    conn.close()
    print("База данных подписок инициализирована")

init_subscription_db()

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
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS script_creators (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER UNIQUE NOT NULL,
        username TEXT,
        full_name TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        is_active BOOLEAN DEFAULT 1
    )
    ''')
    
    try:
        cursor.execute('INSERT OR IGNORE INTO script_creators (user_id, username, full_name) VALUES (?, ?, ?)',
                      (ADMIN_ID, 'admin', 'Главный Администратор'))
    except:
        pass
    
    conn.commit()
    conn.close()
    print("База данных скриптов инициализирована")

init_database()

# ================== ФУНКЦИИ ДЛЯ РАБОТЫ С ОБЯЗАТЕЛЬНОЙ ПОДПИСКОЙ ==================

def add_required_channel(channel_url: str, added_by: int, channel_id: str = None, channel_username: str = None, channel_title: str = None) -> bool:
    """Добавление канала в обязательную подписку"""
    conn = sqlite3.connect('subscriptions.db')
    cursor = conn.cursor()
    
    try:
        # Извлекаем ID канала из ссылки, если не указан
        if not channel_id and channel_url:
            # Пытаемся извлечь username или ID из ссылки
            match = re.search(r't\.me/([a-zA-Z0-9_\-]+)', channel_url)
            if match:
                channel_username_or_id = match.group(1)
                if channel_username_or_id.startswith('-100'):
                    channel_id = channel_username_or_id
                else:
                    channel_id = f"@{channel_username_or_id}"
                    channel_username = channel_username_or_id
        
        cursor.execute('''
        INSERT OR REPLACE INTO required_channels 
        (channel_id, channel_username, channel_title, channel_url, is_active, added_by)
        VALUES (?, ?, ?, ?, 1, ?)
        ''', (channel_id, channel_username, channel_title, channel_url, added_by))
        
        conn.commit()
        return True
    except Exception as e:
        logging.error(f"Error adding required channel: {e}")
        return False
    finally:
        conn.close()

def remove_required_channel(channel_url: str) -> bool:
    """Удаление канала из обязательной подписки"""
    conn = sqlite3.connect('subscriptions.db')
    cursor = conn.cursor()
    
    try:
        cursor.execute('DELETE FROM required_channels WHERE channel_url = ?', (channel_url,))
        conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        logging.error(f"Error removing required channel: {e}")
        return False
    finally:
        conn.close()

def get_all_active_channels() -> List[Dict]:
    """Получение всех активных каналов для подписки"""
    conn = sqlite3.connect('subscriptions.db')
    cursor = conn.cursor()
    
    cursor.execute('''
    SELECT channel_id, channel_username, channel_title, channel_url 
    FROM required_channels 
    WHERE is_active = 1 
    ORDER BY priority DESC, created_at
    ''')
    
    channels = []
    for row in cursor.fetchall():
        channels.append({
            'channel_id': row[0],
            'channel_username': row[1],
            'channel_title': row[2],
            'channel_url': row[3]
        })
    
    conn.close()
    return channels

def get_user_subscription_status(user_id: int, channel_id: str) -> bool:
    """Проверка подписки пользователя на конкретный канал"""
    conn = sqlite3.connect('subscriptions.db')
    cursor = conn.cursor()
    
    cursor.execute('''
    SELECT is_subscribed FROM user_subscriptions 
    WHERE user_id = ? AND channel_id = ?
    ''', (user_id, channel_id))
    
    result = cursor.fetchone()
    conn.close()
    
    return result[0] if result else False

def update_user_subscription(user_id: int, channel_id: str, is_subscribed: bool):
    """Обновление статуса подписки пользователя"""
    conn = sqlite3.connect('subscriptions.db')
    cursor = conn.cursor()
    
    cursor.execute('''
    INSERT OR REPLACE INTO user_subscriptions (user_id, channel_id, is_subscribed, last_check)
    VALUES (?, ?, ?, datetime('now'))
    ''', (user_id, channel_id, 1 if is_subscribed else 0))
    
    conn.commit()
    conn.close()

def mark_user_as_verified(user_id: int):
    """Отметка пользователя как прошедшего проверку подписки"""
    conn = sqlite3.connect('subscriptions.db')
    cursor = conn.cursor()
    
    cursor.execute('''
    INSERT OR REPLACE INTO verified_users (user_id, verified_at)
    VALUES (?, datetime('now'))
    ''', (user_id,))
    
    conn.commit()
    conn.close()

def is_user_verified(user_id: int) -> bool:
    """Проверка, прошел ли пользователь проверку подписки"""
    conn = sqlite3.connect('subscriptions.db')
    cursor = conn.cursor()
    
    cursor.execute('SELECT 1 FROM verified_users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    
    conn.close()
    return result is not None

def get_unsubscribed_channels_for_user(user_id: int) -> List[Dict]:
    """
    Получение каналов, на которые пользователь не подписан
    ВСЕГДА проверяет актуальный статус подписки
    """
    all_channels = get_all_active_channels()
    unsubscribed = []
    
    # Получаем текущий статус подписки из базы
    conn = sqlite3.connect('subscriptions.db')
    cursor = conn.cursor()
    
    for channel in all_channels:
        cursor.execute('''
        SELECT is_subscribed FROM user_subscriptions 
        WHERE user_id = ? AND channel_id = ?
        ''', (user_id, channel['channel_id']))
        
        result = cursor.fetchone()
        # Если нет записи или подписка = 0 (False), добавляем в список
        if not result or not result[0]:
            unsubscribed.append(channel)
    
    conn.close()
    return unsubscribed

def clear_user_subscriptions(user_id: int):
    """Очистка истории подписок пользователя"""
    conn = sqlite3.connect('subscriptions.db')
    cursor = conn.cursor()
    
    cursor.execute('DELETE FROM user_subscriptions WHERE user_id = ?', (user_id,))
    cursor.execute('DELETE FROM verified_users WHERE user_id = ?', (user_id,))
    
    conn.commit()
    conn.close()

# ================== ФУНКЦИИ ПРОВЕРКИ ПОДПИСКИ ==================

async def check_channel_subscription(bot: Bot, user_id: int, channel_id: str) -> bool:
    """
    Проверка подписки пользователя на канал
    Бот должен быть администратором в канале!
    """
    try:
        # Если channel_id начинается с @, используем username
        if channel_id.startswith('@'):
            chat_member = await bot.get_chat_member(chat_id=channel_id, user_id=user_id)
            return chat_member.status in ['member', 'administrator', 'creator']
        
        # Если это числовой ID (может быть отрицательным для каналов/супергрупп)
        try:
            chat_id = int(channel_id)
            chat_member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
            return chat_member.status in ['member', 'administrator', 'creator']
        except ValueError:
            # Если не удалось преобразовать в число, пробуем как username без @
            chat_member = await bot.get_chat_member(chat_id=f"@{channel_id}", user_id=user_id)
            return chat_member.status in ['member', 'administrator', 'creator']
            
    except Exception as e:
        logging.error(f"Error checking subscription for user {user_id} in channel {channel_id}: {e}")
        return False

async def check_all_subscriptions_and_update(bot: Bot, user_id: int) -> Dict[str, bool]:
    """
    Проверка подписки на все обязательные каналы и обновление статуса
    ВСЕГДА делает актуальную проверку
    """
    channels = get_all_active_channels()
    results = {}
    
    for channel in channels:
        # ВСЕГДА делаем актуальную проверку
        is_subscribed = await check_channel_subscription(bot, user_id, channel['channel_id'])
        results[channel['channel_id']] = is_subscribed
        # Обновляем статус в базе данных
        update_user_subscription(user_id, channel['channel_id'], is_subscribed)
    
    # Если все подписки есть, отмечаем пользователя как верифицированного
    if all(results.values()) and results:
        mark_user_as_verified(user_id)
    else:
        # Если не подписан на все, удаляем из верифицированных
        conn = sqlite3.connect('subscriptions.db')
        cursor = conn.cursor()
        cursor.execute('DELETE FROM verified_users WHERE user_id = ?', (user_id,))
        conn.commit()
        conn.close()
    
    return results

def create_subscription_keyboard(unsubscribed_channels: List[Dict]) -> InlineKeyboardMarkup:
    """
    Создание клавиатуры для подписки на каналы
    До 10 кнопок в ряд для SubGram спонсоров
    """
    keyboard = []
    
    if not unsubscribed_channels:
        return None
    
    # Определяем количество кнопок в ряду (максимум 10)
    buttons_per_row = 10 if len(unsubscribed_channels) > 10 else 3 if len(unsubscribed_channels) > 3 else len(unsubscribed_channels)
    
    # Создаем ряды с нужным количеством кнопок
    for i in range(0, len(unsubscribed_channels), buttons_per_row):
        row = []
        # Добавляем кнопки в текущий ряд
        for j in range(buttons_per_row):
            if i + j < len(unsubscribed_channels):
                channel = unsubscribed_channels[i + j]
                button_text = f"➕ Подписаться"
                row.append(InlineKeyboardButton(
                    text=button_text,
                    url=channel['channel_url']
                ))
        
        if row:
            keyboard.append(row)
    
    # Добавляем кнопку проверки подписки
    keyboard.append([InlineKeyboardButton(
        text="🔄 Проверить подписку",
        callback_data="check_subscription"
    )])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

# ================== ФУНКЦИИ ДЛЯ РАБОТЫ С БД СКРИПТОВ ==================

def generate_unique_code():
    """Генерация уникального кода для ссылки"""
    length = random.randint(7, 25)
    characters = string.ascii_letters + string.digits + "-"
    code = ''.join(random.choice(characters) for _ in range(length))
    return code

def save_script_to_db(script_content: str, created_by: int, is_public=True, original_message_id=None):
    """Сохранение скрипта в базу данных"""
    conn = sqlite3.connect('scripts.db')
    cursor = conn.cursor()
    
    while True:
        unique_code = generate_unique_code()
        cursor.execute('SELECT 1 FROM scripts WHERE unique_code = ?', (unique_code,))
        if not cursor.fetchone():
            break
    
    cursor.execute('''
    INSERT INTO scripts (unique_code, script_content, created_by, is_public, original_message_id, created_at)
    VALUES (?, ?, ?, ?, ?, datetime('now'))
    ''', (unique_code, script_content, created_by, 1 if is_public else 0, original_message_id))
    
    conn.commit()
    conn.close()
    
    return unique_code

def get_script_content(unique_code):
    """Получение скрипта по уникальному коду"""
    conn = sqlite3.connect('scripts.db')
    cursor = conn.cursor()
    
    cursor.execute('SELECT script_content FROM scripts WHERE unique_code = ?', (unique_code,))
    result = cursor.fetchone()
    conn.close()
    
    return result[0] if result else None

def is_script_creator(user_id: int) -> bool:
    """Проверяет, является ли пользователь создателем скриптов"""
    if user_id == ADMIN_ID:
        return True
    
    conn = sqlite3.connect('scripts.db')
    cursor = conn.cursor()
    
    cursor.execute('SELECT 1 FROM script_creators WHERE user_id = ? AND is_active = 1', (user_id,))
    result = cursor.fetchone()
    
    conn.close()
    
    return result is not None

def get_statistics():
    """Получение статистики"""
    conn = sqlite3.connect('scripts.db')
    cursor = conn.cursor()
    
    try:
        cursor.execute("SELECT COUNT(*) FROM scripts")
        total_scripts = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM scripts WHERE created_by = ?", (ADMIN_ID,))
        admin_scripts = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM scripts WHERE created_by != ?", (ADMIN_ID,))
        user_scripts = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM scripts WHERE is_public = 1")
        public_scripts = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM script_creators WHERE is_active = 1")
        active_creators = cursor.fetchone()[0]
        
    except Exception as e:
        logging.error(f"Ошибка при получении статистики: {e}")
        total_scripts = admin_scripts = user_scripts = public_scripts = 0
        active_creators = 0
    
    conn.close()
    
    return {
        'total_scripts': total_scripts,
        'admin_scripts': admin_scripts,
        'user_scripts': user_scripts,
        'public_scripts': public_scripts,
        'total_users': len(USERS),
        'active_creators': active_creators
    }

# ================== SubGram ФУНКЦИИ ==================

async def get_subgram_sponsors(user_id: int, chat_id: int) -> dict | None:
    """Получение спонсоров из SubGram"""
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
    except Exception as e:
        logging.error(f"SubGram API error: {e}")
        return None

# ================== ФУНКЦИИ ДЛЯ РАССЫЛКИ ==================

def parse_broadcast_buttons(buttons_text: str) -> Optional[List[List[InlineKeyboardButton]]]:
    """
    Парсинг URL-кнопок для рассылки
    Формат: Кнопка 1 — http://link.com | Кнопка 2 — http://link.com
    """
    if not buttons_text.strip():
        return None
    
    rows = buttons_text.strip().split('\n')
    keyboard = []
    
    for row in rows:
        row_buttons = []
        # Разделяем кнопки в ряду по разделителю "|"
        button_strings = [btn.strip() for btn in row.split('|') if btn.strip()]
        
        for button_str in button_strings:
            # Разделяем текст и ссылку по разделителю "—"
            if '—' in button_str:
                parts = button_str.split('—', 1)
                if len(parts) == 2:
                    text = parts[0].strip()
                    url = parts[1].strip()
                    
                    # Проверяем, что URL начинается с http/https
                    if text and url and (url.startswith('http://') or url.startswith('https://')):
                        row_buttons.append(InlineKeyboardButton(text=text, url=url))
                    else:
                        logging.warning(f"Некорректная кнопка: {button_str}")
        
        if row_buttons:
            keyboard.append(row_buttons)
        
        # Максимум 15 рядов
        if len(keyboard) >= 15:
            break
    
    return keyboard if keyboard else None

async def send_broadcast_to_user(bot: Bot, user_id: int, message_data: dict, keyboard_markup: Optional[InlineKeyboardMarkup] = None):
    """Отправка сообщения рассылки конкретному пользователю"""
    try:
        content_type = message_data.get('content_type', 'text')
        
        if content_type == 'text':
            await bot.send_message(
                chat_id=user_id,
                text=message_data.get('text', ''),
                reply_markup=keyboard_markup,
                parse_mode="HTML",
                disable_web_page_preview=True
            )
            
        elif content_type == 'photo':
            await bot.send_photo(
                chat_id=user_id,
                photo=message_data.get('photo'),
                caption=message_data.get('text', ''),
                reply_markup=keyboard_markup,
                parse_mode="HTML"
            )
            
        elif content_type == 'video':
            await bot.send_video(
                chat_id=user_id,
                video=message_data.get('video'),
                caption=message_data.get('text', ''),
                reply_markup=keyboard_markup,
                parse_mode="HTML"
            )
            
        elif content_type == 'document':
            await bot.send_document(
                chat_id=user_id,
                document=message_data.get('document'),
                caption=message_data.get('text', ''),
                reply_markup=keyboard_markup,
                parse_mode="HTML"
            )
        
        return True
    except Exception as e:
        logging.error(f"Ошибка при отправке рассылки пользователю {user_id}: {e}")
        return False

# ================== ОБРАБОТКА КОМАНД АДМИНИСТРИРОВАНИЯ ==================

@router.message(Command("op"))
async def enable_channel_command(message: types.Message):
    """Включение обязательной подписки на канал"""
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ У вас нет прав для выполнения этой команды")
        return
    
    args = message.text.split()
    if len(args) < 2:
        await message.answer(
            "❌ <b>Использование:</b> /op [ссылка на канал]\n\n"
            "<b>Пример:</b>\n"
            "<code>/op https://t.me/script_f</code>\n\n"
            "Бот должен быть администратором в канале!",
            parse_mode="HTML"
        )
        return
    
    channel_url = args[1]
    
    # Проверяем формат ссылки
    if not channel_url.startswith('https://t.me/'):
        await message.answer("❌ Неверный формат ссылки. Используйте https://t.me/username")
        return
    
    try:
        # Получаем информацию о канале
        bot = message.bot
        channel_username = channel_url.replace('https://t.me/', '').strip('/')
        
        try:
            chat = await bot.get_chat(f"@{channel_username}")
            channel_id = f"@{channel_username}"
            channel_title = chat.title
        except:
            await message.answer("❌ Не удалось получить информацию о канале. Убедитесь, что бот добавлен в канал как администратор!")
            return
        
        # Добавляем канал в обязательную подписку
        if add_required_channel(
            channel_url=channel_url,
            added_by=message.from_user.id,
            channel_id=channel_id,
            channel_username=channel_username,
            channel_title=channel_title
        ):
            await message.answer(
                f"✅ <b>Канал добавлен в обязательную подписку!</b>\n\n"
                f"📢 <b>Название:</b> {channel_title}\n"
                f"🔗 <b>Ссылка:</b> {channel_url}\n"
                f"👤 <b>Username:</b> @{channel_username}\n\n"
                f"Теперь все пользователи должны подписаться на этот канал, чтобы получить скрипты.",
                parse_mode="HTML"
            )
        else:
            await message.answer("❌ Не удалось добавить канал. Возможно, он уже добавлен.")
            
    except Exception as e:
        logging.error(f"Error adding channel: {e}")
        await message.answer(f"❌ Ошибка при добавлении канала: {str(e)}")

@router.message(Command("stop"))
async def disable_channel_command(message: types.Message):
    """Отключение обязательной подписки на канал"""
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ У вас нет прав для выполнения этой команды")
        return
    
    args = message.text.split()
    if len(args) < 2:
        await message.answer(
            "❌ <b>Использование:</b> /stop [ссылка на канал]\n\n"
            "<b>Пример:</b>\n"
            "<code>/stop https://t.me/script_f</code>",
            parse_mode="HTML"
        )
        return
    
    channel_url = args[1]
    
    if remove_required_channel(channel_url):
        await message.answer(
            f"✅ <b>Канал удален из обязательной подписки!</b>\n\n"
            f"🔗 <b>Ссылка:</b> {channel_url}\n\n"
            f"Теперь пользователям не требуется подписываться на этот канал.",
            parse_mode="HTML"
        )
    else:
        await message.answer("❌ Канал не найден в списке обязательной подписки")

@router.message(Command("eop"))
async def list_channels_command(message: types.Message):
    """Показать список каналов обязательной подписки"""
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ У вас нет прав для выполнения этой команды")
        return
    
    channels = get_all_active_channels()
    
    if not channels:
        await message.answer("📭 <b>Список каналов обязательной подписки пуст</b>", parse_mode="HTML")
        return
    
    text = "<b>📢 Список каналов обязательной подписки:</b>\n\n"
    
    for i, channel in enumerate(channels, 1):
        text += f"{i}. <b>{channel.get('channel_title', 'Без названия')}</b>\n"
        text += f"   🔗 <b>Ссылка:</b> {channel['channel_url']}\n"
        if channel.get('channel_username'):
            text += f"   👤 <b>Username:</b> @{channel['channel_username']}\n"
        text += f"   🆔 <b>ID:</b> {channel['channel_id']}\n\n"
    
    text += f"<b>Всего каналов:</b> {len(channels)}\n"
    text += "<b>Команды для управления:</b>\n"
    text += "/op [ссылка] - добавить канал\n"
    text += "/stop [ссылка] - удалить канал"
    
    await message.answer(text, parse_mode="HTML")

# ================== ОСНОВНАЯ ЛОГИКА ПРОВЕРКИ ПОДПИСКИ ==================

async def check_subscriptions_and_respond(bot: Bot, user_id: int, chat_id: int, unique_code: str = None, message: types.Message = None, force_check: bool = False):
    """
    Основная функция проверки подписок и отправки соответствующего сообщения
    force_check: если True, всегда проверяет актуальный статус подписки
    """
    # Сначала проверяем SubGram
    subgram_response = await get_subgram_sponsors(user_id, chat_id)
    
    if subgram_response and subgram_response.get("status") == "warning":
        # Если есть требования от SubGram, показываем их
        sponsors = subgram_response.get("sponsors", [])
        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        
        # Добавляем кнопки для спонсоров (до 10 в ряд)
        buttons_per_row = 10 if len(sponsors) > 10 else 3 if len(sponsors) > 3 else len(sponsors)
        
        for i in range(0, len(sponsors), buttons_per_row):
            row = []
            for j in range(buttons_per_row):
                if i + j < len(sponsors):
                    sponsor = sponsors[i + j]
                    row.append(InlineKeyboardButton(
                        text=sponsor.get("name", "Канал"),
                        url=sponsor.get("url", "#")
                    ))
            if row:
                keyboard.inline_keyboard.append(row)
        
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(
                text="🔄 Проверить подписку",
                callback_data="check_subscription"
            )
        ])
        
        warning_message = (
            "✅ SubGram подписки подтверждены!\n\n"
            "ℹ️ Теперь подпишитесь на наших спонсоров\n"
            "❕ Подпишитесь и нажмите \"🔄 Проверить подписку\"."
        )
        
        if message:
            await message.answer(warning_message, reply_markup=keyboard, parse_mode="HTML")
        else:
            await bot.send_message(chat_id, warning_message, reply_markup=keyboard, parse_mode="HTML")
        return False
    
    # ВСЕГДА проверяем актуальный статус подписок при переходе по ссылке
    if unique_code or force_check:
        # При переходе по ссылке или принудительной проверке делаем актуальную проверку
        await check_all_subscriptions_and_update(bot, user_id)
    
    # Получаем неподписанные каналы (с актуальными данными)
    unsubscribed_channels = get_unsubscribed_channels_for_user(user_id)
    
    if unsubscribed_channels:
        # Если пользователь еще не подписался на все каналы
        keyboard = create_subscription_keyboard(unsubscribed_channels)
        
        warning_message = (
            "✅ SubGram подписки подтверждены!\n\n"
            "ℹ️ Теперь подпишитесь на наших спонсоров\n"
            "❕ Подпишитесь и нажмите \"🔄 Проверить подписку\"."
        )
        
        if message:
            await message.answer(warning_message, reply_markup=keyboard, parse_mode="HTML")
        else:
            await bot.send_message(chat_id, warning_message, reply_markup=keyboard, parse_mode="HTML")
        return False
    
    # Если все проверки пройдены
    return True

# ================== ОБРАБОТКА КОМАНДЫ /start ==================

@router.message(CommandStart())
async def start_handler(message: types.Message):
    """Обработка команды /start"""
    if message.chat.type != "private":
        return
    
    USERS.add(message.from_user.id)
    
    # Проверяем, есть ли уникальный код в ссылке
    if len(message.text.split()) > 1:
        unique_code = message.text.split()[1]
        
        # ПРИ ПЕРЕХОДЕ ПО ССЫЛКЕ ВСЕГДА ДЕЛАЕМ АКТУАЛЬНУЮ ПРОВЕРКУ
        all_subscribed = await check_subscriptions_and_respond(
            bot=message.bot,
            user_id=message.from_user.id,
            chat_id=message.chat.id,
            unique_code=unique_code,
            message=message,
            force_check=True  # ВСЕГДА проверяем актуальный статус!
        )
        
        if not all_subscribed:
            return
        
        # Если все подписки есть, показываем скрипт
        script_content = get_script_content(unique_code)
        
        if script_content:
            await show_script_content(message, script_content)
        else:
            await message.answer("❌ Ссылка не найдена или устарела")
        return
    
    # Если нет уникального кода, проверяем подписки (тоже с актуальной проверкой)
    all_subscribed = await check_subscriptions_and_respond(
        bot=message.bot,
        user_id=message.from_user.id,
        chat_id=message.chat.id,
        message=message,
        force_check=True
    )
    
    if all_subscribed:
        # Если все подписки есть, показываем приветствие
        await send_welcome(message)

@router.callback_query(F.data == "check_subscription")
async def check_subscription_callback(callback: types.CallbackQuery):
    """Обработка нажатия кнопки проверки подписки"""
    await callback.answer("⏳ Проверяем подписку...")
    
    user_id = callback.from_user.id
    chat_id = callback.message.chat.id
    
    # Удаляем старое сообщение
    try:
        await callback.message.delete()
    except:
        pass
    
    # Проверяем SubGram
    subgram_response = await get_subgram_sponsors(user_id, chat_id)
    
    if subgram_response and subgram_response.get("status") == "warning":
        # Обновляем кнопки SubGram
        sponsors = subgram_response.get("sponsors", [])
        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        
        # Добавляем кнопки для спонсоров (до 10 в ряд)
        buttons_per_row = 10 if len(sponsors) > 10 else 3 if len(sponsors) > 3 else len(sponsors)
        
        for i in range(0, len(sponsors), buttons_per_row):
            row = []
            for j in range(buttons_per_row):
                if i + j < len(sponsors):
                    sponsor = sponsors[i + j]
                    row.append(InlineKeyboardButton(
                        text=sponsor.get("name", "Канал"),
                        url=sponsor.get("url", "#")
                    ))
            if row:
                keyboard.inline_keyboard.append(row)
        
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(
                text="🔄 Проверить подписку",
                callback_data="check_subscription"
            )
        ])
        
        await callback.message.answer(
            "✅ SubGram подписки подтверждены!\n\n"
            "ℹ️ Теперь подпишитесь на наших спонсоров\n"
            "❕ Подпишитесь и нажмите \"🔄 Проверить подписку\".",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        return
    
    # ВСЕГДА делаем актуальную проверку подписки
    results = await check_all_subscriptions_and_update(callback.bot, user_id)
    unsubscribed_channels = get_unsubscribed_channels_for_user(user_id)
    
    if unsubscribed_channels:
        # Обновляем клавиатуру с оставшимися каналами
        keyboard = create_subscription_keyboard(unsubscribed_channels)
        
        warning_message = (
            "✅ SubGram подписки подтверждены!\n\n"
            "ℹ️ Теперь подпишитесь на наших спонсоров\n"
            "❕ Подпишитесь и нажмите \"🔄 Проверить подписку\"."
        )
        
        await callback.message.answer(warning_message, reply_markup=keyboard, parse_mode="HTML")
    else:
        # Все подписки выполнены
        await send_welcome(callback.message)

async def send_welcome(message: types.Message):
    """Отправка приветственного сообщения"""
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔥 Наш канал", url=CHANNEL_URL)],
            [InlineKeyboardButton(text="📤 Загрузить скрипт", callback_data="upload_script")]
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
        "📤 <b>Загрузить свой скрипт:</b>\n"
        "Нажми кнопку ниже чтобы загрузить свой скрипт и получить уникальную ссылку!\n\n"
        "❗️ <b>Важно:</b>\n"
        "Чтобы получить скрипт — просто перейди в нужный канал и нажми кнопку «Получить скрипт 🚀»\n\n"
        "<b>Для сотрудничества:</b> @SecretLinkAds"
    )

    await message.answer(
        text.format(nick=message.from_user.full_name),
        reply_markup=keyboard,
        parse_mode="HTML"
    )

async def show_script_content(message: types.Message, script_content: str):
    """Показать скрипт с форматированием"""
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text="⚡️ Больше скриптов ⚡️", 
                url="https://t.me/script_f"
            )],
            [InlineKeyboardButton(
                text="📤 Загрузить свой скрипт", 
                callback_data="upload_script"
            )]
        ]
    )
    
    # Форматирование скрипта
    if script_content.startswith('$'):
        script_content = script_content[1:]
    
    script_content = script_content.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    
    header_text = "<b>✅ | Спасибо за подписки!</b>\n\n"
    footer_text = f"\n\n<b>@</b>{BOT_USERNAME}"
    
    final_text = header_text + f"<code>{script_content}</code>" + footer_text
    
    await message.answer(
        final_text,
        reply_markup=keyboard,
        parse_mode="HTML"
    )

# ================== КОМАНДЫ ДЛЯ СОЗДАТЕЛЕЙ СКРИПТОВ ==================

@router.message(Command("oplink"))
async def add_creator_command(message: types.Message):
    """Добавление создателя скриптов"""
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ У вас нет прав для выполнения этой команды")
        return
    
    if len(message.text.split()) < 2:
        await message.answer(
            "❌ <b>Использование:</b> /oplink [id]\n\n"
            "<b>Пример:</b>\n"
            "<code>/oplink 123456789</code>\n\n"
            "Чтобы получить ID пользователя, перешлите его сообщение боту @userinfobot",
            parse_mode="HTML"
        )
        return
    
    try:
        user_id = int(message.text.split()[1])
        
        if is_script_creator(user_id):
            await message.answer(f"❌ Пользователь с ID {user_id} уже является создателем скриптов")
            return
        
        try:
            user = await message.bot.get_chat(user_id)
            username = user.username
            full_name = user.full_name
        except:
            username = None
            full_name = f"User_{user_id}"
        
        conn = sqlite3.connect('scripts.db')
        cursor = conn.cursor()
        
        cursor.execute('''
        INSERT OR REPLACE INTO script_creators (user_id, username, full_name, is_active)
        VALUES (?, ?, ?, 1)
        ''', (user_id, username, full_name))
        
        conn.commit()
        conn.close()
        
        await message.answer(
            f"✅ <b>Создатель скриптов добавлен!</b>\n\n"
            f"👤 <b>ID:</b> <code>{user_id}</code>\n"
            f"📛 <b>Имя:</b> {full_name}\n"
            f"🔗 <b>Username:</b> @{username if username else 'нет'}\n\n"
            f"Теперь этот пользователь может загружать скрипты через админ-панель.",
            parse_mode="HTML"
        )
            
    except ValueError:
        await message.answer("❌ Неверный формат ID. ID должен быть числом")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}")

@router.message(Command("stoplink"))
async def remove_creator_command(message: types.Message):
    """Удаление создателя скриптов"""
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ У вас нет прав для выполнения этой команды")
        return
    
    if len(message.text.split()) < 2:
        await message.answer(
            "❌ <b>Использование:</b> /stoplink [id]\n\n"
            "<b>Пример:</b>\n"
            "<code>/stoplink 123456789</code>\n\n"
            "Чтобы посмотреть всех создателей, используйте /creators",
            parse_mode="HTML"
        )
        return
    
    try:
        user_id = int(message.text.split()[1])
        
        if not is_script_creator(user_id) or user_id == ADMIN_ID:
            await message.answer(f"❌ Пользователь с ID {user_id} не является создателем скриптов")
            return
        
        conn = sqlite3.connect('scripts.db')
        cursor = conn.cursor()
        
        cursor.execute('DELETE FROM script_creators WHERE user_id = ?', (user_id,))
        
        conn.commit()
        conn.close()
        
        await message.answer(
            f"✅ <b>Создатель скриптов удален!</b>\n\n"
            f"👤 <b>ID:</b> <code>{user_id}</code>\n\n"
            f"Теперь этот пользователь больше не может загружать скрипты.",
            parse_mode="HTML"
        )
            
    except ValueError:
        await message.answer("❌ Неверный формат ID. ID должен быть числом")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}")

@router.message(Command("creators"))
async def list_creators_command(message: types.Message):
    """Показывает список всех создателей скриптов"""
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ У вас нет прав для выполнения этой команды")
        return
    
    conn = sqlite3.connect('scripts.db')
    cursor = conn.cursor()
    
    cursor.execute('''
    SELECT user_id, username, full_name, created_at, is_active 
    FROM script_creators 
    ORDER BY created_at DESC
    ''')
    
    creators = cursor.fetchall()
    conn.close()
    
    if not creators:
        await message.answer("📭 <b>Список создателей пуст</b>", parse_mode="HTML")
        return
    
    text = "<b>👥 Список создателей скриптов:</b>\n\n"
    
    for i, (user_id, username, full_name, created_at, is_active) in enumerate(creators, 1):
        status = "🟢 Активен" if is_active else "🔴 Неактивен"
        text += f"{i}. <b>ID:</b> <code>{user_id}</code>\n"
        text += f"   <b>Имя:</b> {full_name}\n"
        text += f"   <b>Username:</b> @{username if username else 'нет'}\n"
        text += f"   <b>Статус:</b> {status}\n"
        text += f"   <b>Добавлен:</b> {created_at}\n\n"
    
    text += f"<b>Всего:</b> {len(creators)} создателей\n"
    text += "<b>Используйте:</b>\n"
    text += "/oplink [id] - добавить создателя\n"
    text += "/stoplink [id] - удалить создателя"
    
    await message.answer(text, parse_mode="HTML")

# ================== АДМИН ПАНЕЛЬ ==================

@router.message(Command("admin"))
async def admin_panel(message: types.Message):
    """Админ панель для главного админа и создателей скриптов"""
    if message.chat.type != "private":
        return
        
    if not (message.from_user.id == ADMIN_ID or is_script_creator(message.from_user.id)):
        await message.answer("⛔ У вас нет доступа к админ-панели")
        return

    stats = get_statistics()
    
    if message.from_user.id == ADMIN_ID:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
                [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")],
                [InlineKeyboardButton(text="📤 Загрузка скрипта", callback_data="admin_upload_script")],
                [InlineKeyboardButton(text="👥 Публичные скрипты", callback_data="admin_public_scripts")],
                [InlineKeyboardButton(text="👑 Управление создателями", callback_data="admin_manage_creators")],
                [InlineKeyboardButton(text="📢 Управление подписками", callback_data="admin_manage_subscriptions")]
            ]
        )
    else:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📤 Загрузка скрипта", callback_data="admin_upload_script")]
            ]
        )

    admin_text = (
        "👑 <b>Админ-панель</b>\n\n"
        f"📊 <b>Статистика:</b>\n"
        f"• 👥 Пользователей: {stats['total_users']}\n"
        f"• 📜 Всего скриптов: {stats['total_scripts']}\n"
        f"• 👤 Публичных: {stats['public_scripts']}\n"
        f"• 👑 Админских: {stats['admin_scripts']}\n"
    )
    
    if message.from_user.id == ADMIN_ID:
        admin_text += f"• 👥 Создателей: {stats['active_creators']}\n"
    
    admin_text += f"\n🔗 <b>Подписки:</b>\n"
    admin_text += f"Каналов для подписки: {len(get_all_active_channels())}"

    await message.answer(admin_text, reply_markup=keyboard, parse_mode="HTML")

# ================== ОБРАБОТКА КНОПОК АДМИН-ПАНЕЛИ ==================

@router.callback_query(F.data == "admin_stats")
async def admin_stats_callback(callback: types.CallbackQuery):
    """Статистика бота"""
    if not (callback.from_user.id == ADMIN_ID or is_script_creator(callback.from_user.id)):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    
    stats = get_statistics()
    
    stats_text = (
        "📊 <b>Статистика бота</b>\n\n"
        f"👥 Всего пользователей: <b>{stats['total_users']}</b>\n"
        f"📜 Всего скриптов: <b>{stats['total_scripts']}</b>\n"
        f"👤 Публичных скриптов: <b>{stats['public_scripts']}</b>\n"
        f"👑 Загружено админом: <b>{stats['admin_scripts']}</b>\n"
        f"👥 Загружено пользователями: <b>{stats['user_scripts']}</b>\n"
    )
    
    if callback.from_user.id == ADMIN_ID:
        stats_text += f"👥 Создателей скриптов: <b>{stats['active_creators']}</b>\n\n"
    else:
        stats_text += "\n"
    
    channels = get_all_active_channels()
    stats_text += f"📢 Каналов для подписки: <b>{len(channels)}</b>\n"
    stats_text += f"📤 Загрузка скриптов: <b>✅ Доступна всем</b>\n"
    stats_text += f"🔗 Используется SubGram: <b>✅ Да</b>"
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад в админку", callback_data="back_to_admin")]
        ]
    )
    
    await callback.message.edit_text(stats_text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data == "upload_script")
async def upload_script_callback(callback: types.CallbackQuery):
    """Загрузка скрипта - доступна всем пользователям"""
    UPLOADING_USERS.add(callback.from_user.id)
    
    await callback.message.answer(
        "📤 <b>Загрузка скрипта</b>\n\n"
        "Отправьте скрипт для Roblox.\n\n"
        "После отправки бот создаст уникальную ссылку на скрипт.\n\n"
        "<i>Просто отправьте скрипт следующим сообщением</i>",
        parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(F.data == "admin_upload_script")
async def admin_upload_script_callback(callback: types.CallbackQuery):
    """Загрузка скрипта из админ-панели"""
    if not (callback.from_user.id == ADMIN_ID or is_script_creator(callback.from_user.id)):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    
    UPLOADING_USERS.add(callback.from_user.id)
    
    await callback.message.edit_text(
        "📤 <b>Загрузка скрипта</b>\n\n"
        "Отправьте скрипт для Roblox.\n\n"
        "После отправки бот создаст уникальную ссылку на скрипт.\n\n"
        "<i>Просто отправьте скрипт следующим сообщением</i>",
        parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(F.data == "admin_public_scripts")
async def admin_public_scripts(callback: types.CallbackQuery):
    """Публичные скрипты"""
    if not (callback.from_user.id == ADMIN_ID or is_script_creator(callback.from_user.id)):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    
    conn = sqlite3.connect('scripts.db')
    cursor = conn.cursor()
    
    cursor.execute('SELECT COUNT(*) FROM scripts WHERE is_public = 1')
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
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад в админку", callback_data="back_to_admin")]
        ]
    )
    
    await callback.message.edit_text(stats_text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data == "admin_manage_creators")
async def admin_manage_creators(callback: types.CallbackQuery):
    """Управление создателями скриптов (только для главного админа)"""
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    
    conn = sqlite3.connect('scripts.db')
    cursor = conn.cursor()
    
    cursor.execute('''
    SELECT user_id, username, full_name, created_at, is_active 
    FROM script_creators 
    ORDER BY created_at DESC
    ''')
    
    creators = cursor.fetchall()
    conn.close()
    
    text = "<b>👑 Управление создателями скриптов</b>\n\n"
    
    if creators:
        text += "<b>Текущие создатели:</b>\n"
        for i, (user_id, username, full_name, created_at, is_active) in enumerate(creators, 1):
            status = "🟢" if is_active else "🔴"
            text += f"{i}. {status} <b>ID:</b> <code>{user_id}</code>\n"
            text += f"   👤 {full_name}\n"
            if username:
                text += f"   📱 @{username}\n"
            text += f"   📅 {created_at}\n\n"
    else:
        text += "📭 Создателей нет\n\n"
    
    text += "<b>Команды:</b>\n"
    text += "<code>/oplink [id]</code> - добавить создателя\n"
    text += "<code>/stoplink [id]</code> - удалить создателя\n"
    text += "<code>/creators</code> - список всех создателей"
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад в админку", callback_data="back_to_admin")]
        ]
    )
    
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data == "admin_manage_subscriptions")
async def admin_manage_subscriptions(callback: types.CallbackQuery):
    """Управление обязательными подписками"""
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    
    channels = get_all_active_channels()
    
    text = "<b>📢 Управление обязательными подписками</b>\n\n"
    
    if channels:
        text += "<b>Текущие каналы для подписки:</b>\n"
        for i, channel in enumerate(channels, 1):
            text += f"{i}. <b>{channel.get('channel_title', 'Без названия')}</b>\n"
            text += f"   🔗 {channel['channel_url']}\n"
            if channel.get('channel_username'):
                text += f"   👤 @{channel['channel_username']}\n"
            text += f"   🆔 {channel['channel_id']}\n\n"
    else:
        text += "📭 Нет каналов для подписки\n\n"
    
    text += "<b>Команды:</b>\n"
    text += "<code>/op [ссылка]</code> - добавить канал\n"
    text += "<code>/stop [ссылка]</code> - удалить канал\n"
    text += "<code>/eop</code> - список всех каналов"
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад в админку", callback_data="back_to_admin")]
        ]
    )
    
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()

# ================== РАССЫЛКА ==================

@router.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_callback(callback: types.CallbackQuery, state: FSMContext):
    """Начало процесса рассылки"""
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ Нет доступа к рассылке", show_alert=True)
        return
    
    await callback.message.edit_text(
        "📢 <b>Начало рассылки</b>\n\n"
        "Отправьте сообщение для рассылки:\n"
        "• Текст\n"
        "• Фото с подписью\n"
        "• Видео с подписью\n"
        "• Документ с подписью\n\n"
        "<i>Просто отправьте сообщение следующим сообщением</i>\n\n"
        "Чтобы отменить рассылку, отправьте /cancel",
        parse_mode="HTML"
    )
    
    await state.set_state(BroadcastStates.waiting_for_message)
    await callback.answer()

@router.message(BroadcastStates.waiting_for_message)
async def handle_broadcast_message(message: types.Message, state: FSMContext):
    """Обработка сообщения для рассылки"""
    if message.from_user.id != ADMIN_ID:
        return
    
    # Сохраняем данные сообщения
    message_data = {
        'content_type': message.content_type,
        'text': message.text or message.caption or "",
    }
    
    if message.photo:
        message_data['photo'] = message.photo[-1].file_id
    elif message.video:
        message_data['video'] = message.video.file_id
    elif message.document:
        message_data['document'] = message.document.file_id
    
    await state.update_data(message_data=message_data)
    
    # Запрашиваем кнопки
    await message.answer(
        "✅ <b>Сообщение для рассылки принято!</b>\n\n"
        "⛓ <b>КНОПКИ: URL-кнопки</b>\n\n"
        "Отправьте боту список URL-кнопок в следующем формате:\n\n"
        "<code>Кнопка 1 — http://link.com</code>\n"
        "<code>Кнопка 2 — http://link.com</code>\n\n"
        "<b>Используйте разделитель «|», чтобы добавить до 8 кнопок в один ряд (допустимо 15 рядов):</b>\n\n"
        "<code>Кнопка 1 — http://link.com | Кнопка 2 — http://link.com</code>\n\n"
        "<b>Пример с несколькими рядами:</b>\n"
        "<code>Наш канал — https://t.me/script_f</code>\n"
        "<code>Поддержка — https://t.me/support | Донат — https://donate.com</code>\n\n"
        "Если кнопки не нужны, отправьте: <code>/no_buttons</code>\n"
        "Чтобы отменить рассылку, отправьте: <code>/cancel</code>",
        parse_mode="HTML"
    )
    
    await state.set_state(BroadcastStates.waiting_for_buttons)

@router.message(BroadcastStates.waiting_for_buttons, Command("no_buttons"))
async def broadcast_no_buttons(message: types.Message, state: FSMContext):
    """Рассылка без кнопок"""
    if message.from_user.id != ADMIN_ID:
        return
    
    user_data = await state.get_data()
    message_data = user_data.get('message_data', {})
    
    await state.clear()
    
    # Начинаем рассылку без кнопок
    await start_broadcast(message.bot, message.chat.id, message_data, None)

@router.message(BroadcastStates.waiting_for_buttons, Command("cancel"))
async def cancel_broadcast(message: types.Message, state: FSMContext):
    """Отмена рассылки"""
    if message.from_user.id != ADMIN_ID:
        return
    
    await state.clear()
    await message.answer("❌ Рассылка отменена")

@router.message(BroadcastStates.waiting_for_buttons)
async def handle_broadcast_buttons(message: types.Message, state: FSMContext):
    """Обработка кнопок для рассылки"""
    if message.from_user.id != ADMIN_ID:
        return
    
    if not message.text:
        await message.answer("❌ Отправьте текст с кнопками в указанном формате")
        return
    
    user_data = await state.get_data()
    message_data = user_data.get('message_data', {})
    
    # Парсим кнопки
    keyboard_buttons = parse_broadcast_buttons(message.text)
    
    if keyboard_buttons:
        keyboard_markup = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    else:
        keyboard_markup = None
        await message.answer("⚠️ Кнопки не распознаны. Начинаю рассылку без кнопок.")
    
    await state.clear()
    
    # Начинаем рассылку
    await start_broadcast(message.bot, message.chat.id, message_data, keyboard_markup)

async def start_broadcast(bot: Bot, chat_id: int, message_data: dict, keyboard_markup: Optional[InlineKeyboardMarkup] = None):
    """Запуск рассылки"""
    total_users = len(USERS)
    if total_users == 0:
        await bot.send_message(chat_id, "❌ Нет пользователей для рассылки")
        return
    
    progress_msg = await bot.send_message(
        chat_id,
        f"📢 <b>Начинаю рассылку для {total_users} пользователей...</b>\n"
        f"⏳ Отправлено: 0/{total_users}\n"
        f"📊 Успешно: 0\n"
        f"❌ Ошибок: 0",
        parse_mode="HTML"
    )
    
    sent_success = 0
    sent_failed = 0
    sent_total = 0
    
    for user_id in list(USERS):
        try:
            success = await send_broadcast_to_user(bot, user_id, message_data, keyboard_markup)
            
            if success:
                sent_success += 1
            else:
                sent_failed += 1
            
            sent_total += 1
            
            # Обновляем прогресс каждые 10 сообщений
            if sent_total % 10 == 0 or sent_total == total_users:
                await progress_msg.edit_text(
                    f"📢 <b>Рассылка в процессе...</b>\n"
                    f"⏳ Отправлено: {sent_total}/{total_users}\n"
                    f"📊 Успешно: {sent_success}\n"
                    f"❌ Ошибок: {sent_failed}",
                    parse_mode="HTML"
                )
            
            # Небольшая задержка, чтобы не перегружать сервер
            await asyncio.sleep(0.1)
            
        except Exception as e:
            logging.error(f"Критическая ошибка при рассылке пользователю {user_id}: {e}")
            sent_failed += 1
            sent_total += 1
    
    # Отправляем финальный отчет
    success_rate = (sent_success / total_users * 100) if total_users > 0 else 0
    
    await progress_msg.edit_text(
        f"✅ <b>Рассылка завершена!</b>\n\n"
        f"📊 <b>Статистика:</b>\n"
        f"👥 Всего пользователей: {total_users}\n"
        f"📨 Отправлено: {sent_total}\n"
        f"✅ Успешно: {sent_success}\n"
        f"❌ Ошибок: {sent_failed}\n"
        f"📈 Эффективность: {success_rate:.1f}%",
        parse_mode="HTML"
    )

@router.callback_query(F.data == "back_to_admin")
async def back_to_admin_callback(callback: types.CallbackQuery):
    """Назад в админ-панель"""
    if not (callback.from_user.id == ADMIN_ID or is_script_creator(callback.from_user.id)):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    
    stats = get_statistics()
    
    if callback.from_user.id == ADMIN_ID:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
                [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")],
                [InlineKeyboardButton(text="📤 Загрузка скрипта", callback_data="admin_upload_script")],
                [InlineKeyboardButton(text="👥 Публичные скрипты", callback_data="admin_public_scripts")],
                [InlineKeyboardButton(text="👑 Управление создателями", callback_data="admin_manage_creators")],
                [InlineKeyboardButton(text="📢 Управление подписками", callback_data="admin_manage_subscriptions")]
            ]
        )
    else:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📤 Загрузка скрипта", callback_data="admin_upload_script")]
            ]
        )
    
    admin_text = (
        "👑 <b>Админ-панель</b>\n\n"
        f"📊 <b>Статистика:</b>\n"
        f"• 👥 Пользователей: {stats['total_users']}\n"
        f"• 📜 Всего скриптов: {stats['total_scripts']}\n"
        f"• 👤 Публичных: {stats['public_scripts']}\n"
        f"• 👑 Админских: {stats['admin_scripts']}\n"
    )
    
    if callback.from_user.id == ADMIN_ID:
        admin_text += f"• 👥 Создателей: {stats['active_creators']}\n"
    
    admin_text += f"\n🔗 <b>Подписки:</b>\n"
    admin_text += f"Каналов для подписки: {len(get_all_active_channels())}"
    
    await callback.message.edit_text(admin_text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()

# ================== ЗАГРУЗКА СКРИПТОВ ==================

@router.message(F.content_type.in_({'text', 'document'}))
async def handle_script_upload(message: types.Message):
    """Обработка загрузки скрипта - доступна всем пользователям"""
    if message.chat.type != "private":
        return
    
    if message.from_user.id not in UPLOADING_USERS:
        return
    
    UPLOADING_USERS.discard(message.from_user.id)
    
    script_content = ""
    
    if message.content_type == 'text':
        script_content = message.text.strip()
    elif message.content_type == 'document' and message.document:
        try:
            file = await message.bot.download(message.document)
            content = file.read()
            if isinstance(content, bytes):
                script_content = content.decode('utf-8', errors='ignore')
            else:
                script_content = str(content)
        except Exception as e:
            await message.answer(f"❌ Не удалось прочитать файл: {str(e)}")
            return
    
    if not script_content or len(script_content.strip()) < 10:
        await message.answer("❌ Скрипт не может быть пустым или слишком коротким")
        return
    
    unique_code = save_script_to_db(script_content, message.from_user.id, is_public=True)
    link = f"https://t.me/{BOT_USERNAME}?start={unique_code}"
    
    preview_text = "<b>✅ Скрипт успешно загружен!</b>\n\n"
    preview_text += f"<b>🎯 Ваша уникальная ссылка:</b>\n<code>{link}</code>\n\n"
    
    script_preview = script_content[:200] + "..." if len(script_content) > 200 else script_content
    preview_text += f"<b>📝 Предпросмотр скрипта:</b>\n<code>{script_preview}</code>\n\n"
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Загрузить еще", callback_data="upload_script")],
            [InlineKeyboardButton(text="📢 Поделиться", url=f"https://t.me/share/url?url={link}")]
        ]
    )
    
    await message.answer(preview_text, reply_markup=keyboard, parse_mode="HTML")

# ================== ОБРАБОТКА ДРУГИХ СООБЩЕНИЙ ==================

@router.message()
async def handle_other_messages(message: types.Message):
    """Обработка других сообщений"""
    if message.chat.type != "private":
        return
    
    if not message.text or not message.text.startswith('/'):
        if message.from_user.id != ADMIN_ID and not is_script_creator(message.from_user.id):
            await message.answer(
                "🤖 <b>Неизвестная команда, напишите /start</b>\n\n",
                parse_mode="HTML"
            )

# ================== RUN ==================

async def main():
    bot = Bot(token=BOT_TOKEN)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)
    dp.include_router(router)
    
    try:
        me = await bot.get_me()
        logging.info(f"Бот запущен: @{me.username}")
        logging.info(f"ID администратора: {ADMIN_ID}")
    except Exception as e:
        logging.error(f"Ошибка при получении информации о боте: {e}")
    
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
